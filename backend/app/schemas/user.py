"""
Pydantic schemas for the User resource. These are plain Pydantic models
(no table=True) - they exist only to define exactly what an API client
may send in and what we send back out. Kept separate from the User
SQLModel so we never accidentally leak internal fields (hashed_password)
or accept fields we don't want (id, is_admin) directly from a client.
"""

from pydantic import BaseModel, EmailStr


class UserRegister(BaseModel):
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserPublic(BaseModel):
    id: int
    email: str
    is_active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
