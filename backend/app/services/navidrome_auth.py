"""Credential validation against Navidrome over the Subsonic API.

This is an authentication backend, not a search source: Cadence never browses the library
through it. A library user types the username and password they already use for Navidrome,
and Cadence asks Navidrome whether that pair is valid, then provisions or links a local user
row so the rest of the app (requests, roles, permissions) keeps working unchanged.

Subsonic's auth scheme is salted-token: the client generates a random salt, sends
`t = md5(password + salt)` alongside `s = salt`, and the server recomputes it. MD5 is fixed by
the protocol, not a choice made here, and it is never used to store anything: the digest is a
per-request proof against a per-request salt and Cadence keeps no hash of a Navidrome password
at all. Marked usedforsecurity=False so the intent is explicit and it keeps working on
FIPS-restricted builds.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

import structlog

from app.core.safe_http import POLICY_ALLOW_PRIVATE, SafeHttpClient

logger = structlog.get_logger(__name__)

# The Subsonic API version Navidrome has supported since well before its 0.50 releases.
# Sent verbatim; bumping it gains nothing for a ping/getUser-only client.
_API_VERSION = "1.16.1"
_CLIENT_NAME = "cadence"


@dataclass(frozen=True)
class NavidromeProfile:
    is_admin: bool
    email: str | None


class NavidromeAuthError(Exception):
    """Navidrome could not be reached or answered in a shape we do not understand.

    Deliberately distinct from "credentials rejected", which is a normal negative answer and
    returns None. This is raised only when the outcome is genuinely unknown, so the caller can
    fall back to local accounts instead of telling a user their password is wrong.
    """


class NavidromeAuthenticator:
    def __init__(
        self, base_url: str | None, http: SafeHttpClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._http = http or SafeHttpClient()

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    def _auth_params(self, username: str, password: str) -> dict:
        salt = secrets.token_hex(8)
        token = hashlib.md5(
            (password + salt).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        return {
            "u": username,
            "t": token,
            "s": salt,
            "v": _API_VERSION,
            "c": _CLIENT_NAME,
            "f": "json",
        }

    @staticmethod
    def _subsonic_body(payload: object) -> dict:
        if not isinstance(payload, dict):
            raise NavidromeAuthError("Navidrome returned a non-object response")
        body = payload.get("subsonic-response")
        if not isinstance(body, dict):
            raise NavidromeAuthError("Navidrome response missing subsonic-response")
        return body

    async def verify_credentials(self, username: str, password: str) -> bool | None:
        """True if Navidrome accepts the pair, False if it rejects it, None if unreachable.

        The three-way return matters. Collapsing "server is down" into False would tell a user
        their password is wrong during an outage and, worse, would silently strip access from
        everyone at once.
        """
        if not self.configured:
            return None
        try:
            resp = await self._http.get(
                f"{self._base_url}/rest/ping.view",
                policy=POLICY_ALLOW_PRIVATE,
                params=self._auth_params(username, password),
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - any transport failure is "unknown", not "no"
            logger.warning("navidrome_unreachable", error=str(exc))
            return None

        if resp.status_code != 200:
            logger.warning("navidrome_ping_http_error", status=resp.status_code)
            return None

        try:
            body = self._subsonic_body(resp.json())
        except (NavidromeAuthError, ValueError) as exc:
            logger.warning("navidrome_bad_response", error=str(exc))
            return None

        if body.get("status") == "ok":
            return True

        # Subsonic error 40 is specifically "wrong username or password". Anything else (server
        # misconfiguration, unsupported version, trial expired) is not the user's fault and
        # must not be reported to them as a bad password.
        error_code = (body.get("error") or {}).get("code")
        if error_code == 40:
            return False
        logger.warning("navidrome_auth_error", code=error_code)
        return None

    async def fetch_profile(self, username: str, password: str) -> NavidromeProfile:
        """Read the admin flag and email Navidrome holds for this user.

        Best-effort by design: a failure here yields an empty profile, never an auth failure.
        A Navidrome deployment that does not answer getUser should still let its users log in
        as ordinary requesters rather than locking them out.
        """
        empty = NavidromeProfile(is_admin=False, email=None)
        if not self.configured:
            return empty
        params = self._auth_params(username, password)
        params["username"] = username
        try:
            resp = await self._http.get(
                f"{self._base_url}/rest/getUser.view",
                policy=POLICY_ALLOW_PRIVATE,
                params=params,
            )
            body = self._subsonic_body(resp.json())
        except Exception as exc:  # noqa: BLE001 - profile detail is never worth a 500
            logger.warning("navidrome_getuser_failed", error=str(exc))
            return empty

        user_obj = body.get("user")
        if not isinstance(user_obj, dict):
            return empty
        # Navidrome serialises the role flags as real JSON booleans, but other Subsonic
        # servers emit the string "true", so accept both rather than trusting one shape.
        admin_role = user_obj.get("adminRole")
        email = user_obj.get("email")
        return NavidromeProfile(
            is_admin=admin_role is True or str(admin_role).lower() == "true",
            email=email if isinstance(email, str) and "@" in email else None,
        )
