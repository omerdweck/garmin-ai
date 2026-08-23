"""
ה-engine הוא אובייקט יחיד שמנהל את "בריכת" החיבורים ל-Postgres -
לא פותחים חיבור חדש בכל בקשה, אלא שואלים חיבור פנוי מהבריכה ומחזירים
אותו בסוף. זה ה-pattern הסטנדרטי לעבודה עם DB בכל framework רציני.
"""

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

engine = create_engine(settings.database_url, echo=False)


def init_db() -> None:
    """יוצר את הטבלאות שמוגדרות (כרגע אין עדיין מודלים - זה יתמלא בשלב 2)."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """
    Dependency ל-FastAPI: כל endpoint שצריך גישה ל-DB יבקש את זה כפרמטר,
    ו-FastAPI ידאג לפתוח session, למסור אותו, ולסגור אותו אוטומטית בסוף.
    """
    with Session(engine) as session:
        yield session


def check_db_connection() -> bool:
    """בדיקת תקינות - משמש את /health כדי לוודא שה-DB באמת נגיש."""
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
        return True
    except Exception:
        return False
