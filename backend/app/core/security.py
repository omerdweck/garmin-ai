"""
Password hashing and JWT helpers. Password hashing uses bcrypt (via
passlib) - a deliberately slow algorithm with a built-in salt, so that
even a DB leak doesn't expose actual passwords. JWTs are how a logged-in
user proves their identity on later requests without the server having
to remember any session state: the token is signed with secret_key, so
the server can verify it wasn't tampered with just by checking the
signature, with no DB lookup needed.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: int) -> str:
    """Builds a signed JWT whose payload identifies the user and expires after settings.access_token_expire_minutes."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    # "sub" (subject) is the standard JWT claim name for "who this token is about" (RFC 7519)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[int]:
    """Verifies a JWT's signature and expiry. Returns the user id if valid, None if invalid/expired/tampered."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return int(payload["sub"])
    except jwt.PyJWTError:
        return None
