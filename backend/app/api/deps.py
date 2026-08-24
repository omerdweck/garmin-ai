"""
Shared FastAPI dependencies. get_current_user is what "protects" an
endpoint: any route that adds `user: User = Depends(get_current_user)`
will only run if the request carries a valid Authorization: Bearer
<token> header - otherwise FastAPI returns 401 before the route's own
code ever executes.

Using HTTPBearer (not OAuth2PasswordBearer) on purpose: HTTPBearer just
reads a raw bearer token from the header. OAuth2PasswordBearer implies a
specific OAuth2 login flow (form-encoded credentials at a tokenUrl),
which doesn't match our plain-JSON /auth/login endpoint.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.user import User

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user
