"""
Application entry point. Currently only one endpoint (/health) whose
purpose is to confirm the whole skeleton - API, Postgres, Redis - is
wired together correctly.
More endpoints (auth, garmin, chat...) will be added as separate routers
in later stages, so this file stays small and clear.
"""

import redis
from fastapi import FastAPI

from app import models  # noqa: F401 - registers all tables on SQLModel.metadata, e.g. for Alembic autogenerate
from app.core.config import settings
from app.db.session import check_db_connection
from app.routers import auth, garmin

app = FastAPI(title="Garmin AI - Backend", version="0.1.0")
app.include_router(auth.router)
app.include_router(garmin.router)

# Schema creation/changes are handled by Alembic migrations (see backend/alembic/),
# not by the app itself - run `alembic upgrade head` to bring the DB up to date.


def check_redis_connection() -> bool:
    try:
        client = redis.Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=2)
        return client.ping()
    except Exception:
        return False


@app.get("/health")
def health() -> dict:
    db_ok = check_db_connection()
    redis_ok = check_redis_connection()
    return {
        "status": "ok" if (db_ok and redis_ok) else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "redis": "connected" if redis_ok else "unreachable",
    }
