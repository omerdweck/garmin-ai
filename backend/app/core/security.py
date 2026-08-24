"""
Password hashing functions. Uses bcrypt (via passlib) - a deliberately
slow algorithm with a built-in salt, so that even a DB leak doesn't
expose actual passwords. We never store/compare a plaintext password
beyond the moment of the request itself.
"""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)
