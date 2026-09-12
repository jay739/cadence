"""Credential validation against Navidrome over the Subsonic API.

This is an authentication backend, not a search source: Cadence never browses the library
through it. A library user types the username and password they already use for Navidrome,
and Cadence asks Navidrome whether that pair is valid, then provisions or links a local user
row so the rest of the app (requests, roles, permissions) keeps working unchanged.

The wire protocol itself (salted-token credential, envelope unwrapping, the constants) lives
in app.services.navidrome_subsonic and is shared with the library client, so Cadence has
exactly one Subsonic implementation. See that module for why MD5 appears here at all.

WHY PER-USER NAVIDROME CALLS ONLY HAPPEN AT LOGIN
-------------------------------------------------
Cadence deliberately never stores a Navidrome password (see AuthProvider.NAVIDROME in
app.models.user: password_hash stays null and the credential is replayed only at login). So
the user's own credential exists in this process for exactly the duration of a login request,
and any Subsonic call that must be made *as that user* has to happen inside `authenticate()`
or not at all.

An operator credential does not buy a way around that, which was verified against a live
Navidrome 0.63.2: `getUser.view?username=<someone else>` answers error 50 "User is not
authorized for the given operation" even when the caller holds adminRole, and `getUsers.view`
returns only the calling user. Navidrome's Subsonic surface is self-scoped. There is therefore
no way to backfill or refresh another user's Navidrome details out of band, and nothing here
pretends otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.core.safe_http import POLICY_ALLOW_PRIVATE, SafeHttpClient
from app.services.navidrome_subsonic import (
    ERROR_WRONG_CREDENTIALS,
    SubsonicProtocolError,
    auth_params,
    coerce_bool,
    error_code,
    is_ok,
    subsonic_body,
)

logger = structlog.get_logger(__name__)

# Every boolean role flag getUser.view is specified to return. Captured wholesale rather than
# cherry-picked so the planned "stop using email as identity" work has the full picture of what
# Navidrome actually says about an account without needing another round of protocol probing.
_ROLE_FIELDS = (
    "scrobblingEnabled",
    "adminRole",
    "settingsRole",
    "downloadRole",
    "uploadRole",
    "playlistRole",
    "coverArtRole",
    "commentRole",
    "podcastRole",
    "streamRole",
    "jukeboxRole",
    "shareRole",
    "videoConversionRole",
)


@dataclass(frozen=True)
class NavidromeProfile:
    """Everything Navidrome will tell us about the user who just authenticated.

    `is_admin` and `email` are the two fields the login path acts on today and keep their
    original position so existing callers and tests construct this unchanged. The rest is
    carried for the benefit of the follow-up work that drops email-as-identity: it is populated
    and logged, but nothing in this PR persists or branches on it.

    A note that matters for that follow-up: on the reference deployment neither Navidrome
    account has an email set at all, and Navidrome does not require one. `email` being None is
    the normal case, not an edge case, which is exactly why
    `app.services.users._provider_namespaced_email` has to invent a synthetic
    `<username>@navidrome.local` address today.
    """

    is_admin: bool
    email: str | None
    # Navidrome's own spelling of the username, which is the canonical identity the follow-up
    # PR should key on. May differ in case from what the user typed at the login form.
    username: str | None = None
    # Raw role flags from getUser.view, e.g. {"downloadRole": True, "shareRole": False, ...}.
    roles: dict[str, bool] = field(default_factory=dict)
    # Music folder ids this user may see. Navidrome returns these as `folder: [1]`.
    folder_ids: tuple[int, ...] = ()

    @property
    def has_email(self) -> bool:
        """Whether Navidrome supplied a real address, as opposed to Cadence inventing one."""
        return bool(self.email)


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
        return auth_params(username, password)

    @staticmethod
    def _subsonic_body(payload: object) -> dict:
        # Translated to this module's own exception type so the error handling below (and every
        # existing caller and test) keeps catching exactly what it always caught.
        try:
            return subsonic_body(payload)
        except SubsonicProtocolError as exc:
            raise NavidromeAuthError(str(exc)) from exc

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

        if is_ok(body):
            return True

        # Subsonic error 40 is specifically "wrong username or password". Anything else (server
        # misconfiguration, unsupported version, trial expired) is not the user's fault and
        # must not be reported to them as a bad password.
        code = error_code(body)
        if code == ERROR_WRONG_CREDENTIALS:
            return False
        logger.warning("navidrome_auth_error", code=code)
        return None

    async def fetch_profile(self, username: str, password: str) -> NavidromeProfile:
        """Read everything Navidrome holds for this user, as this user.

        Best-effort by design: a failure here yields an empty profile, never an auth failure.
        A Navidrome deployment that does not answer getUser should still let its users log in
        as ordinary requesters rather than locking them out.

        This is the only moment Cadence can make this call for this user -- see the module
        docstring -- so it captures the whole record rather than just the two fields the login
        path currently consumes.
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

        email = user_obj.get("email")
        reported_username = user_obj.get("username")
        profile = NavidromeProfile(
            is_admin=coerce_bool(user_obj.get("adminRole")),
            email=email if isinstance(email, str) and "@" in email else None,
            username=(
                reported_username
                if isinstance(reported_username, str) and reported_username
                else None
            ),
            roles={
                key: coerce_bool(user_obj[key])
                for key in _ROLE_FIELDS
                if key in user_obj
            },
            folder_ids=_folder_ids(user_obj.get("folder")),
        )
        # Logged rather than persisted: this PR deliberately does not touch the User model or
        # the auth flow, so the structured log is where an operator (and the follow-up PR's
        # author) can see what Navidrome actually reports for a real account.
        logger.info(
            "navidrome_profile_fetched",
            username=profile.username or username,
            is_admin=profile.is_admin,
            has_email=profile.has_email,
            roles=profile.roles,
            folder_ids=list(profile.folder_ids),
        )
        return profile


def _folder_ids(value: object) -> tuple[int, ...]:
    """Navidrome returns `folder` as a list of ints; tolerate a bare int or a missing key."""
    if isinstance(value, int) and not isinstance(value, bool):
        return (value,)
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, int) and not isinstance(v, bool))
