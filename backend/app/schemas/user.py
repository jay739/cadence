from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator

from app.models.user import AuthProvider, UserRole


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
    auth_provider: AuthProvider
    avatar_url: str | None = None
    webhook_url: str | None = None
    # Only ever populated for a NAVIDROME-backed account, by the route handler (not derived
    # from the ORM object): where to send that user to manage their own password, since Cadence
    # never stores one for them. Left null for LOCAL accounts and for any response built before
    # the caller has settings in hand (e.g. nothing here reads it).
    navidrome_url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UpdateAvatarIn(BaseModel):
    avatar_url: str | None = Field(default=None, max_length=2048)


class UpdateEmailIn(BaseModel):
    email: EmailStr


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class RequestStatusCounts(BaseModel):
    """Per-status counts of the current user's own requests, plus the total across all of them."""

    pending: int
    approved: int
    downloading: int
    fulfilled: int
    rejected: int
    failed: int
    awaiting_quality: int
    total: int


class WebhookUrlIn(BaseModel):
    """PATCH body for setting or clearing the caller's own webhook URL.

    `webhook_url` is always sent explicitly: null (or an empty/blank string, accepted as a
    convenience) clears it, any other value must be a well-formed http(s) URL with a host. This
    is a fully user-supplied outbound target Cadence will later POST to on the user's behalf --
    format is validated here so a malformed value never reaches storage, but the SSRF-relevant
    check (rejecting private/loopback/link-local/metadata targets) happens at delivery time via
    app.core.safe_http's POLICY_PUBLIC_ONLY guard, not here (see app.services.webhook_delivery).
    """

    webhook_url: str | None = Field(default=None, max_length=2048)

    @field_validator("webhook_url")
    @classmethod
    def _valid_http_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(
                "webhook_url must be a valid http:// or https:// URL with a host"
            )
        return v
