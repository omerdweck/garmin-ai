"""
נקודת הכניסה של האפליקציה. כרגע יש רק endpoint אחד (/health) שמטרתו
לוודא שהשלד כולו - API, Postgres, Redis - מחוברים נכון זה לזה.
Endpoints נוספים (auth, garmin, chat...) יתווספו כ-routers נפרדים
בשלבים הבאים, כדי שהקובץ הזה יישאר קטן וברור.
"""

import redis
from fastapi import FastAPI

from app.core.config import settings
from app.db.session import check_db_connection, init_db

app = FastAPI(title="Garmin AI - Backend", version="0.1.0")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


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
