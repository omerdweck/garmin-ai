"""
The Mini App's API: what the dashboard page inside Telegram talks to.

Served by its own process (app/webapp.py), not by app/main.py, and under
/api only - Caddy forwards /api/* and nothing else, so the website's paused
/auth and /garmin routes are not reachable from the internet at all.

Two steps:
  1. POST /api/auth with Telegram's signed initData. Verified against the
     bot token (app/core/telegram_webapp.py); a valid launch from an
     approved user gets a short-lived JWT.
  2. Every other endpoint takes that JWT as `Authorization: Bearer ...`
     and runs for that user only.

Status codes, deliberately distinct:
  401 - we don't know it came from Telegram (forged, expired, other bot,
        bad or expired token). The page should ask Telegram again.
  403 - Telegram vouches for this person, but they aren't an approved user
        here (never joined, deleted their account, or not yet approved).
        The page shows "open the bot to connect" instead of an error.

Queries name their columns (select(User.id, ...)) instead of loading whole
rows: this process connects as a read-only role that may read only the
columns it needs from user and garmin_account (see the miniapp_readonly
migration), and a whole-row load would ask for the rest.
"""

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core import clock
from app.core.config import settings
from app.core.security import create_miniapp_token, decode_miniapp_token
from app.core.telegram_webapp import InitDataError, validate_init_data
from app.db.session import get_session
from app.models.garmin_account import GarminAccount
from app.models.history_import import HistoryImport
from app.models.user import User
from app.services import workout_stats

router = APIRouter(prefix="/api", tags=["miniapp"])

# auto_error=False: a missing header should be our 401, not FastAPI's 403.
_bearer = HTTPBearer(auto_error=False)


class AuthRequest(BaseModel):
    init_data: str


class AuthResponse(BaseModel):
    token: str
    first_name: str
    garmin_linked: bool


class MeResponse(BaseModel):
    user_id: int
    garmin_linked: bool


def _active_user_id(session: Session, telegram_id: int) -> Optional[int]:
    row = session.exec(
        select(User.id, User.is_active).where(User.telegram_chat_id == telegram_id)
    ).first()
    if row is None or not row.is_active:
        return None
    return row.id


def _garmin_linked(session: Session, user_id: int) -> bool:
    row = session.exec(
        select(GarminAccount.id, GarminAccount.disconnected_at).where(GarminAccount.user_id == user_id)
    ).first()
    return row is not None and row.disconnected_at is None


def get_miniapp_user_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    session: Session = Depends(get_session),
) -> int:
    """
    The dependency every data endpoint uses. Re-checks the user on each
    request rather than trusting the token alone: an account deleted while
    the page is open must stop answering immediately, not in an hour.
    """
    user_id = decode_miniapp_token(credentials.credentials) if credentials else None
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token")
    # A single-column select returns the value itself: True, False or None.
    is_active = session.exec(select(User.is_active).where(User.id == user_id)).first()
    if not is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not an active user")
    return user_id


@router.post("/auth", response_model=AuthResponse)
def auth(body: AuthRequest, session: Session = Depends(get_session)) -> AuthResponse:
    try:
        tg_user = validate_init_data(
            body.init_data, settings.telegram_bot_token, settings.miniapp_init_data_max_age_seconds
        )
    except InitDataError:
        # One message for every reason: telling a caller *why* a forgery
        # failed only helps them make a better one. The reason is in the
        # exception for our own logs and tests.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid init data")

    # In a private chat the chat id equals the user id - that's what the bot
    # stored as telegram_chat_id when this person onboarded.
    user_id = _active_user_id(session, int(tg_user["id"]))
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not an active user")

    return AuthResponse(
        token=create_miniapp_token(user_id),
        first_name=str(tg_user.get("first_name") or ""),
        garmin_linked=_garmin_linked(session, user_id),
    )


@router.get("/me", response_model=MeResponse)
def me(user_id: int = Depends(get_miniapp_user_id), session: Session = Depends(get_session)) -> MeResponse:
    return MeResponse(user_id=user_id, garmin_linked=_garmin_linked(session, user_id))


class HistoryStatus(BaseModel):
    # "none" until the first night run creates the row; then pending,
    # running, done or paused (see app/models/history_import.py).
    status: str
    percent: int
    # The day the import has reached so far: the year view can grey out
    # everything older than this instead of drawing it as empty.
    loaded_from: Optional[str] = None


@router.get("/history-status", response_model=HistoryStatus)
def history_status(
    user_id: int = Depends(get_miniapp_user_id), session: Session = Depends(get_session)
) -> HistoryStatus:
    record = session.exec(select(HistoryImport).where(HistoryImport.user_id == user_id)).first()
    if record is None:
        return HistoryStatus(status="none", percent=0)
    # next_date is the next day still to fetch, so the loaded range starts the day after it.
    loaded_from = record.oldest_date if record.status == "done" else record.next_date + timedelta(days=1)
    return HistoryStatus(status=record.status, percent=record.percent, loaded_from=loaded_from.isoformat())


# --- the workouts tab -------------------------------------------------------
# kind/offset as in the page's period selector; the year's offset is fixed
# at 0 there, but accepted here so a later "last year" view needs no change.
_KIND = Query("week", pattern="^(week|month|year)$")
_OFFSET = Query(0, ge=0, le=60)


@router.get("/workouts")
def workouts(
    kind: str = _KIND, offset: int = _OFFSET,
    user_id: int = Depends(get_miniapp_user_id), session: Session = Depends(get_session),
) -> dict:
    return workout_stats.summary(session, user_id, kind, offset, clock.today())


@router.get("/load")
def load(
    kind: str = _KIND, offset: int = _OFFSET,
    user_id: int = Depends(get_miniapp_user_id), session: Session = Depends(get_session),
) -> dict:
    return workout_stats.load_series(session, user_id, kind, offset, clock.today())


@router.get("/calendar")
def calendar(user_id: int = Depends(get_miniapp_user_id), session: Session = Depends(get_session)) -> dict:
    return workout_stats.calendar(session, user_id, clock.today())
