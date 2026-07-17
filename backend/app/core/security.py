"""Password hashing and session token issuance/verification.

Passwords: argon2 via passlib (modern, memory-hard algorithm as required by the mission).
Sessions: JWT (HS256) signed with SESSION_SECRET, real expiry, and a token_version claim so
bumping User.token_version (done on password change or explicit "log out everywhere")
invalidates all previously issued tokens without needing a server-side session store.

Comparisons involving secrets go through hmac.compare_digest / passlib's constant-time verify
so timing side-channels don't leak information about how close a guess was.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def create_session_token(
    *,
    user_id: str,
    role: str,
    token_version: int,
    secret: str,
    ttl_minutes: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "tv": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


class SessionTokenError(Exception):
    pass


def decode_session_token(token: str, *, secret: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise SessionTokenError("Session expired") from exc
    except jwt.InvalidTokenError as exc:
        raise SessionTokenError("Invalid session token") from exc
