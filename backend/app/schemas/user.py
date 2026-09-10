from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, EmailStr, Field

from app.models.user import UserRole


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)


class LoginIn(BaseModel):
    """One login form for both backends.

    The identifier is a local account's email OR a Navidrome username, so it cannot be an
    EmailStr: Navidrome usernames are not addresses. `email` stays accepted as an alias so
    existing clients keep working unchanged.
    """

    identifier: str = Field(
        min_length=1,
        max_length=320,
        validation_alias=AliasChoices("identifier", "email"),
    )
    password: str = Field(min_length=1, max_length=256)

    model_config = {"populate_by_name": True}


class UserOut(BaseModel):
    id: str
    email: str
    role: UserRole
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
