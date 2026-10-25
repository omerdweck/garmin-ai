"""
Verifies that a Mini App launch really came from Telegram.

When a user opens the Mini App, Telegram hands the page a string called
initData: who the user is, when they opened it, and a `hash` - an HMAC
that only someone holding this bot's token could have produced. The page
sends that string to our API; this module recomputes the HMAC and compares.

Nothing in initData can be trusted before this check passes. The page can
read initDataUnsafe in JavaScript, but anyone can type any user id into a
browser - the signature is the only thing that ties the id to Telegram.

Algorithm (Telegram's own spec, "Validating data received via the Mini App"):
    data_check_string = every field except `hash`, as "key=value",
                        sorted by key, joined with "\\n"
    secret_key        = HMAC_SHA256(key="WebAppData", msg=bot_token)
    expected          = hex(HMAC_SHA256(key=secret_key, msg=data_check_string))
"""

import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qsl

# A launch stamped this far in the future is rejected too: a few seconds of
# clock drift between Telegram and this server is normal, minutes is not.
_FUTURE_SKEW_SECONDS = 60


class InitDataError(Exception):
    """initData is missing, malformed, forged, expired, or for another bot."""


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int, now: Optional[float] = None) -> dict:
    """Returns the verified Telegram user (a dict with at least `id`), or raises InitDataError."""
    if not init_data:
        raise InitDataError("missing")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("malformed") from exc

    params = dict(pairs)
    if len(params) != len(pairs):
        # A repeated key means two different strings could be "the" value -
        # refuse rather than guess which one the signature covered.
        raise InitDataError("duplicate field")

    received_hash = params.pop("hash", None)
    if not received_hash:
        raise InitDataError("no hash")

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    # compare_digest, not ==: == stops at the first differing character, and
    # how long it took leaks how many leading characters were right.
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("bad signature")

    try:
        auth_date = int(params["auth_date"])
    except (KeyError, ValueError) as exc:
        raise InitDataError("no auth_date") from exc
    now = time.time() if now is None else now
    if now - auth_date > max_age_seconds:
        raise InitDataError("expired")
    if auth_date - now > _FUTURE_SKEW_SECONDS:
        raise InitDataError("from the future")

    try:
        user = json.loads(params["user"])
        int(user["id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InitDataError("no user") from exc
    return user
