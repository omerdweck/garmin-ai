"""
Endpoints for linking/unlinking a user's Garmin Connect account, plus a
minimal test-pull endpoint to prove a linked session actually works.
Fetching and storing real health data on a schedule is a separate,
later stage (Celery) - this stage only establishes and verifies the link.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.crypto import decrypt, encrypt
from app.core.garmin_client import (
    GarminAuthError,
    GarminRateLimitError,
    get_daily_summary,
    login_to_garmin,
    resume_garmin_session,
)
from app.db.session import get_session
from app.models.garmin_account import GarminAccount
from app.models.user import User
from app.schemas.garmin import GarminLinkRequest, GarminStatus
from app.services.garmin_sync import sync_user_garmin_data

router = APIRouter(prefix="/garmin", tags=["garmin"])


@router.post("/link", response_model=GarminStatus, status_code=status.HTTP_201_CREATED)
def link_garmin_account(
    data: GarminLinkRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> GarminStatus:
    try:
        token_bundle = login_to_garmin(email=data.email, password=data.password)
    except GarminAuthError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Garmin credentials")
    except GarminRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Garmin is temporarily rate-limiting login attempts - wait a while and try again",
        )
    # `data` (including the raw Garmin password) goes out of scope when this
    # function returns - nothing beyond this point ever touches it again.

    existing = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()

    if existing is not None:
        existing.encrypted_token = encrypt(token_bundle)
        existing.linked_at = datetime.now(timezone.utc)
        garmin_account = existing
    else:
        garmin_account = GarminAccount(user_id=user.id, encrypted_token=encrypt(token_bundle))

    session.add(garmin_account)
    session.commit()
    session.refresh(garmin_account)

    # GarminAccount (the DB table) has no "linked" column - it's a synthetic
    # field that only exists in the response schema, so we build GarminStatus
    # explicitly here instead of letting FastAPI try to read it off the ORM object.
    return GarminStatus(
        linked=True,
        linked_at=garmin_account.linked_at,
        last_sync_at=garmin_account.last_sync_at,
        last_sync_error=garmin_account.last_sync_error,
    )


@router.get("/status", response_model=GarminStatus)
def garmin_status(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> GarminStatus:
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()

    if account is None:
        return GarminStatus(linked=False)

    return GarminStatus(
        linked=True,
        linked_at=account.linked_at,
        last_sync_at=account.last_sync_at,
        last_sync_error=account.last_sync_error,
    )


@router.delete("/unlink")
def unlink_garmin_account(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()

    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Garmin account linked")

    session.delete(account)
    session.commit()

    return {"message": "Garmin account unlinked"}


@router.get("/test")
def test_garmin_pull(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Proves a linked session actually works by pulling today's stats live from Garmin."""
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()

    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Garmin account linked")

    try:
        garmin_session = resume_garmin_session(decrypt(account.encrypted_token))
        return get_daily_summary(garmin_session, date.today().isoformat())
    except GarminAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Garmin session is no longer valid - please relink your account",
        )
    except GarminRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Garmin is temporarily rate-limiting requests - wait a while and try again",
        )


@router.post("/sync")
def sync_garmin_data(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """
    Manual trigger for the same sync logic the future scheduled Celery
    task will call - lets us verify it end-to-end before adding scheduling.
    """
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()

    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Garmin account linked")

    try:
        sync_user_garmin_data(session=session, user_id=user.id)
    except GarminAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Garmin session is no longer valid - please relink your account",
        )
    except GarminRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Garmin is temporarily rate-limiting requests - wait a while and try again",
        )

    return {"message": "Sync complete"}
