"""
Auth endpoints: register a new user (sends a verification email), verify
that email via a one-time token, log in to receive a JWT (only allowed
once verified), and a /me endpoint that only works with a valid token.

These belong to the website, which is paused - the Telegram bot identifies
people by chat id and touches none of this. They are kept working, and
gated, rather than deleted, so the website can be resumed without rebuilding
the auth flow from scratch.

BEFORE EXPOSING THIS TO THE INTERNET: /register and /login both need rate
limiting at the reverse proxy. An HTTP endpoint has no equivalent of the
bot's per-conversation attempt counter, so the invite-code check below can
be attempted as fast as the network allows.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.email import EmailNotConfiguredError, send_verification_email
from app.core.security import create_access_token, generate_verification_token, hash_password, verify_password
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import Token, UserLogin, UserPublic, UserRegister
from app.services.signup_control import invite_code_matches, rejection_reason

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

VERIFICATION_TOKEN_LIFETIME = timedelta(hours=24)


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(
    data: UserRegister,
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    # Same two gates the Telegram flow applies, for the same reason: this is
    # the *other* path that creates a User, and it was previously open. It is
    # unreachable today (the API binds to 127.0.0.1 and email is unconfigured)
    # but that is circumstance, not a decision - the day this deployment grows
    # a reverse proxy the door would otherwise stand open with nobody
    # remembering it exists.
    #
    # Checked before the user row is created, not after: a gate that runs
    # afterwards has already written the thing it was meant to prevent.
    refusal = rejection_reason(session)
    if refusal is not None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Registration is closed")

    if settings.is_invite_required and not invite_code_matches(data.invite_code or ""):
        # Logged with the caller's address because, unlike the bot's
        # conversation, an HTTP endpoint has no per-session attempt counter.
        # Real rate limiting belongs at the reverse proxy that would expose
        # this - see the note in the module docstring.
        logger.warning("Rejected registration with bad invite code from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid invite code")

    existing = session.exec(select(User).where(User.email == data.email)).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    token = generate_verification_token()
    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        verification_token=token,
        verification_token_expires_at=datetime.utcnow() + VERIFICATION_TOKEN_LIFETIME,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    try:
        send_verification_email(to=user.email, token=token)
    except EmailNotConfiguredError:
        # Deployments that only run the Telegram bot carry no mail
        # credentials by design. Say so, instead of returning a 500 that
        # looks like a crash - and roll the user back, since an account
        # that can never receive its verification link is unusable.
        session.delete(user)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email sending is not configured on this deployment",
        )

    return user


@router.get("/verify")
def verify_email(token: str, session: Session = Depends(get_session)) -> dict:
    user = session.exec(select(User).where(User.verification_token == token)).first()

    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification token")

    if user.verification_token_expires_at is None or datetime.utcnow() > user.verification_token_expires_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token has expired")

    # single-use: clear the token so this same link can never verify anything again
    user.is_active = True
    user.verification_token = None
    user.verification_token_expires_at = None
    session.add(user)
    session.commit()

    return {"message": "Email verified successfully"}


@router.post("/login", response_model=Token)
def login(data: UserLogin, session: Session = Depends(get_session)) -> Token:
    user = session.exec(select(User).where(User.email == data.email)).first()

    # Same error for "no such user" and "wrong password" on purpose - not
    # revealing which one it was makes it harder to enumerate valid emails.
    if user is None or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not verified yet")

    access_token = create_access_token(user_id=user.id)
    return Token(access_token=access_token)


@router.get("/me", response_model=UserPublic)
def read_current_user(user: User = Depends(get_current_user)) -> User:
    return user
