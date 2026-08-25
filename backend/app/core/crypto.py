"""
Symmetric encryption for data we must be able to recover in its original
form later (e.g. a Garmin session token we need to actually use, not just
verify) - unlike password hashing, which is intentionally one-way. Fernet
(from the `cryptography` library) uses the same key to encrypt and decrypt,
and includes a timestamp + HMAC so tampered/corrupted ciphertext is
rejected on decrypt rather than silently returning garbage.
"""

from cryptography.fernet import Fernet

from app.core.config import settings

_fernet = Fernet(settings.fernet_key.encode())


def encrypt(plain_text: str) -> str:
    return _fernet.encrypt(plain_text.encode()).decode()


def decrypt(cipher_text: str) -> str:
    return _fernet.decrypt(cipher_text.encode()).decode()
