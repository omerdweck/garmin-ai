"""
The Mini App's front door: who gets a token, and who gets turned away.

initData strings are signed here with the same algorithm Telegram uses, so
the tests exercise the real check - not a mock of it. The one thing that
makes a string "from Telegram" is that it was signed with this bot's token;
signing with any other token is exactly what a forger (or another bot's
Mini App) would produce.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token, create_miniapp_token
from app.core.telegram_webapp import InitDataError, validate_init_data
from app.db.session import get_session
from app.webapp import app

OTHER_BOT_TOKEN = "999999:another-bot-entirely"


def sign_init_data(telegram_id: int, bot_token: str = None, auth_date: int = None, **extra) -> str:
    """Builds initData the way Telegram does, signed with bot_token."""
    bot_token = bot_token or settings.telegram_bot_token
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": telegram_id, "first_name": "עומר", "language_code": "he"}, ensure_ascii=False),
        **extra,
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth(client, init_data: str):
    return client.post("/api/auth", json={"init_data": init_data})


# --- the signature check itself ------------------------------------------


def test_valid_init_data_returns_the_user():
    user = validate_init_data(sign_init_data(123), settings.telegram_bot_token, max_age_seconds=3600)
    assert user["id"] == 123


@pytest.mark.parametrize(
    "tamper",
    [
        lambda s: s.replace("123", "124"),          # someone else's id, old hash
        lambda s: s + "&admin=1",                    # a field Telegram never signed
        lambda s: s.split("&hash=")[0],              # hash removed
        lambda s: "",                                # nothing at all
        lambda s: "not a query string %%%",
    ],
)
def test_tampered_init_data_is_rejected(tamper):
    with pytest.raises(InitDataError):
        validate_init_data(tamper(sign_init_data(123)), settings.telegram_bot_token, max_age_seconds=3600)


def test_duplicate_fields_are_rejected():
    signed = sign_init_data(123)
    with pytest.raises(InitDataError):
        validate_init_data(signed + "&auth_date=1", settings.telegram_bot_token, max_age_seconds=3600)


# --- POST /api/auth ---------------------------------------------------------


def test_approved_user_gets_a_token(client, make_user):
    user = make_user()
    res = auth(client, sign_init_data(user.telegram_chat_id))
    assert res.status_code == 200
    body = res.json()
    assert body["first_name"] == "עומר"
    assert body["garmin_linked"] is True
    me = client.get("/api/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["user_id"] == user.id


def test_user_without_garmin_still_gets_in(client, make_user):
    user = make_user(linked=False)
    res = auth(client, sign_init_data(user.telegram_chat_id))
    assert res.status_code == 200
    assert res.json()["garmin_linked"] is False


def test_forged_init_data_is_401(client, make_user):
    user = make_user()
    forged = sign_init_data(user.telegram_chat_id).replace("hash=", "hash=0")
    assert auth(client, forged).status_code == 401


def test_expired_init_data_is_401(client, make_user):
    user = make_user()
    two_hours_ago = int(time.time()) - 2 * 3600
    assert auth(client, sign_init_data(user.telegram_chat_id, auth_date=two_hours_ago)).status_code == 401


def test_init_data_from_the_future_is_401(client, make_user):
    user = make_user()
    in_ten_minutes = int(time.time()) + 600
    assert auth(client, sign_init_data(user.telegram_chat_id, auth_date=in_ten_minutes)).status_code == 401


def test_another_bots_init_data_is_401(client, make_user):
    user = make_user()
    assert auth(client, sign_init_data(user.telegram_chat_id, bot_token=OTHER_BOT_TOKEN)).status_code == 401


def test_unknown_telegram_user_is_403(client, make_user):
    make_user()
    assert auth(client, sign_init_data(1)).status_code == 403


def test_inactive_user_is_403(client, make_user, session):
    user = make_user()
    user.is_active = False
    session.add(user)
    session.commit()
    assert auth(client, sign_init_data(user.telegram_chat_id)).status_code == 403


# --- the token afterwards ---------------------------------------------------


def test_me_without_a_token_is_401(client):
    assert client.get("/api/me").status_code == 401


def test_website_token_does_not_open_the_miniapp(client, make_user):
    user = make_user()
    website_token = create_access_token(user.id)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {website_token}"}).status_code == 401


def test_expired_miniapp_token_is_401(client, make_user):
    user = make_user()
    expired = jwt.encode(
        {"sub": str(user.id), "aud": "miniapp", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    assert client.get("/api/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_user_deactivated_after_login_is_403(client, make_user, session):
    user = make_user()
    token = create_miniapp_token(user.id)
    user.is_active = False
    session.add(user)
    session.commit()
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 403


# --- what the public process exposes ----------------------------------------


def test_only_api_routes_are_exposed():
    """
    Caddy forwards /api/* to this app. If the website's /auth/login or
    /garmin/link were ever registered here, they'd be on the internet.
    """
    paths = {route.path for route in app.routes if hasattr(route, "methods")}
    assert paths, "no routes found"
    assert all(path.startswith("/api/") for path in paths), sorted(paths)
