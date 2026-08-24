"""
Auth endpoints: register a new user, log in to receive a JWT, and a
/me endpoint that only works with a valid token - useful both as a
real "who am I" endpoint for a frontend and as a way to verify that
get_current_user actually protects a route.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_session
from app.models.user import User
from app.schemas.user import Token, UserLogin, UserPublic, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(data: UserRegister, session: Session = Depends(get_session)) -> User:
    existing = session.exec(select(User).where(User.email == data.email)).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = User(email=data.email, hashed_password=hash_password(data.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(data: UserLogin, session: Session = Depends(get_session)) -> Token:
    user = session.exec(select(User).where(User.email == data.email)).first()

    # Same error for "no such user" and "wrong password" on purpose - not
    # revealing which one it was makes it harder to enumerate valid emails.
    if user is None or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    access_token = create_access_token(user_id=user.id)
    return Token(access_token=access_token)


@router.get("/me", response_model=UserPublic)
def read_current_user(user: User = Depends(get_current_user)) -> User:
    return user
