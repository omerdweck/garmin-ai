"""
The engine is a single object that manages a "pool" of connections to
Postgres - we don't open a new connection on every request, we borrow
a free one from the pool and return it when done. This is the standard
pattern for working with a DB in any serious framework.
"""

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.core.config import settings

engine = create_engine(settings.database_url, echo=False)


def get_session():
    """
    FastAPI dependency: any endpoint that needs DB access requests this as
    a parameter, and FastAPI takes care of opening the session, handing it
    over, and closing it automatically afterwards.
    """
    with Session(engine) as session:
        yield session


def check_db_connection() -> bool:
    """Health check - used by /health to confirm the DB is actually reachable."""
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
        return True
    except Exception:
        return False
