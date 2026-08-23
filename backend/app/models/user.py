"""
מודל ה-User - טבלת המשתמשים של האתר עצמו (לא לבלבל עם חשבון גרמין המקושר,
שיקבל טבלה נפרדת בשלב מאוחר יותר, כי משתמש אחד יכול תיאורטית לקשר/לנתק
חשבון גרמין בלי שזה נוגע לזהות שלו באתר).
`table=True` הופך את זה מ-Pydantic model רגיל לטבלת Postgres אמיתית.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str

    # דגלים לניהול: is_active ישמש לאימות מייל (שלב הבא), is_admin לפאנל הניהול
    is_active: bool = Field(default=False)
    is_admin: bool = Field(default=False)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
