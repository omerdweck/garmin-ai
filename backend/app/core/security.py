"""
פונקציות hashing לסיסמאות. משתמשים ב-bcrypt (דרך passlib) - אלגוריתם
איטי-בכוונה עם salt מובנה, כדי שגם דליפת ה-DB לא תחשוף סיסמאות בפועל.
לעולם לא שומרים/משווים סיסמה בטקסט גלוי מעבר לרגע הבקשה עצמה.
"""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)
