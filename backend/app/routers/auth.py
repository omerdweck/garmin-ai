"""
Auth endpoints: register a new user (sends a verification email), verify
that email via a one-time token, log in to receive a JWT (only allowed
once verified), and a /me endpoint that only works with a valid token.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.email import send_verification_email
from app.core.security import create_access_token, generate_verification_token, hash_password, verify_password
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import Token, UserLogin, UserPublic, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])

VERIFICATION_TOKEN_LIFETIME = timedelta(hours=24)


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(data: UserRegister, session: Session = Depends(get_session)) -> User:
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

    send_verification_email(to=user.email, token=token)

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
