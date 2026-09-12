"""The Subsonic wire protocol, factored out so Cadence only ever speaks it one way.

`app.services.navidrome_auth` spoke Subsonic first (ping.view/getUser.view for login), and
`app.services.navidrome_library` now speaks it too (search3.view for the already-in-library
check). Rather than let a second, subtly-different implementation grow next to the first, the
three things both need -- the salted-token credential, the protocol constants, and unwrapping
the `subsonic-response` envelope -- live here and are imported by both.

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

# The Subsonic API version Navidrome has supported since well before its 0.50 releases.
# Sent verbatim; bumping it gains nothing for the handful of views Cadence calls.
API_VERSION = "1.16.1"
CLIENT_NAME = "cadence"

# Subsonic error codes Cadence distinguishes. Everything else is "unknown", never surfaced to
# an end user as a statement about their credentials.
ERROR_WRONG_CREDENTIALS = 40
ERROR_NOT_AUTHORIZED = 50


class SubsonicProtocolError(Exception):
    """The server answered, but not in a shape the Subsonic spec describes.

    Distinct from a transport failure (which the SafeHttpClient raises) and from a well-formed
    Subsonic *error* response, which is a normal answer carrying a code.
    """


def auth_params(username: str, password: str) -> dict[str, str]:
    """Build a complete, single-use Subsonic query string for `username`/`password`.

    A fresh salt per call is the point of the scheme: the returned dict is never cached or
    reused, so callers should call this once per request rather than hoisting it.
    """
    salt = secrets.token_hex(8)
    token = hashlib.md5(
        (password + salt).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    return {
        "u": username,
        "t": token,
        "s": salt,
        "v": API_VERSION,
        "c": CLIENT_NAME,
        "f": "json",
    }


def subsonic_body(payload: object) -> dict:
    """Unwrap the `subsonic-response` envelope, or raise SubsonicProtocolError.

    Note that Subsonic signals *application* errors with HTTP 200 and `status: "failed"`, so a
    successful unwrap here says nothing about whether the call succeeded -- check
    `body["status"]` and `error_code(body)`.
    """
    if not isinstance(payload, dict):
        raise SubsonicProtocolError("Navidrome returned a non-object response")
    body = payload.get("subsonic-response")
    if not isinstance(body, dict):
        raise SubsonicProtocolError("Navidrome response missing subsonic-response")
    return body


def is_ok(body: dict) -> bool:
    return body.get("status") == "ok"


def error_code(body: dict) -> int | None:
    """The numeric Subsonic error code, or None if the response carries no usable one."""
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, int) else None


def coerce_bool(value: object) -> bool:
    """Read a Subsonic role flag.

    Navidrome serialises these as real JSON booleans, but other Subsonic servers emit the
    string "true", so accept both rather than trusting one shape.
    """
    return value is True or str(value).lower() == "true"
