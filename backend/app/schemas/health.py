from __future__ import annotations

from pydantic import BaseModel


class IntegrationStatus(BaseModel):
    configured: bool
    reachable: bool | None = None


class HealthOut(BaseModel):
    """The public, unauthenticated health payload.

    Deliberately minimal. This endpoint is reachable by anyone who can reach the port, and is
    what the container healthcheck and any uptime monitor call, so it carries only liveness
    plus the two facts the UI genuinely needs before it knows who is asking:

    - `navidrome_login_available`, read by the login page pre-authentication to decide whether
      to offer the "sign in with Navidrome" tab at all.
    - `spotify_search_available`, so the Spotify tab can explain itself to an ordinary user
      instead of offering a Connect button that dead-ends.

    Which indexer, download client or fallback exists, and whether any of them currently
    answer, is operator infrastructure detail: that lives on the admin-only route below. It
    never leaked URLs, keys or paths, but "prowlarr is configured and reachable, qbittorrent
    is not" still maps out someone's stack for them.
    """

    version: str
    mode: str
    database_ok: bool
    navidrome_login_available: bool
    spotify_search_available: bool


class IntegrationsHealthOut(BaseModel):
    """Per-integration configured/reachable detail. Admin only.

    Still never contains URLs, keys, or filesystem paths. Carries `mode` so the admin setup
    banner can suppress itself in demo mode from this one response, rather than having to
    fetch the public health route as well just to learn that.
    """

    mode: str
    integrations: dict[str, IntegrationStatus]
