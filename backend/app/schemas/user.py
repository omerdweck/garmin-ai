"""
Pydantic schemas for the User resource. These are plain Pydantic models
(no table=True) - they exist only to define exactly what an API client
may send in and what we send back out. Kept separate from the User
SQLModel so we never accidentally leak internal fields (hashed_password)
or accept fields we don't want (id, is_admin) directly from a client.
"""

from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, EmailStr


def _normalize_email(value: str) -> str:
    """
    Lower-cases the whole address so one person can't end up as two users.

    EmailStr already lower-cases the domain, but leaves the local part
    alone - so "Omer.Dweck@Gmail.com" arrives as "Omer.Dweck@gmail.com"
    and compares unequal to "omer.dweck@gmail.com". That matters because
    phone keyboards auto-capitalise the first letter, so the same person
    typing their address on a phone and on a laptop would register twice
    and then fail to log in with the "wrong" casing. The unique index
    wouldn't catch it either - to Postgres they're different strings.

    Technically RFC 5321 allows the local part to be case-sensitive, but
    no mail provider in practice treats it that way, and matching real
    user behaviour beats matching the spec here.
    """
    return value.strip().lower()


# Applied at the schema boundary rather than in each endpoint, so no future
# route can forget to normalise before querying by email.
NormalizedEmail = Annotated[EmailStr, AfterValidator(_normalize_email)]


class UserRegister(BaseModel):
    email: NormalizedEmail
    password: str
    # Optional in the schema, enforced in the endpoint. Declaring it required
    # here would make a missing code a 422 validation error listing the field
    # name, which tells an anonymous caller that an invite system exists and
    # what to send. The endpoint answers a missing and a wrong code
    # identically instead.
    invite_code: Optional[str] = None


class UserLogin(BaseModel):
    email: NormalizedEmail
    password: str


class UserPublic(BaseModel):
    id: int
    email: str
    is_active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
