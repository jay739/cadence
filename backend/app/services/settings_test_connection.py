"""Per-integration "test connection" checks for the admin settings page.

Each check makes exactly one real outbound call via the existing SSRF-hardened
SafeHttpClient (never a second, unguarded HTTP path, per the mission's carried-over
constraint) and reports success/failure without persisting anything. Used both by the "Test
connection" button (against whatever the admin has currently typed, before saving) and
implicitly validated by the same code path the live search/fulfillment integrations use.
"""

from __future__ import annotations

import base64

from app.core.safe_http import (
    POLICY_ALLOW_PRIVATE,
    POLICY_PUBLIC_ONLY,
    SafeHttpClient,
    SafeHttpError,
)


async def test_prowlarr(url: str | None, api_key: str | None) -> tuple[bool, str]:
    if not url or not api_key:
        return False, "Prowlarr URL and API key are both required"
    client = SafeHttpClient(timeout_seconds=5.0, max_retries=0)
    try:
        resp = await client.get(
            f"{url.rstrip('/')}/api/v1/system/status",
            policy=POLICY_ALLOW_PRIVATE,
            headers={"X-Api-Key": api_key},
        )
    except SafeHttpError as exc:
        return False, f"Could not reach Prowlarr: {exc}"
    if resp.status_code == 401:
        return False, "Prowlarr rejected the API key"
    if resp.status_code >= 400:
        return False, f"Prowlarr responded with HTTP {resp.status_code}"
    return True, "Connected to Prowlarr successfully"


async def test_qbittorrent(
    url: str | None, username: str | None, password: str | None
) -> tuple[bool, str]:
    if not url or not username or not password:
        return False, "qBittorrent URL, username, and password are all required"
    client = SafeHttpClient(timeout_seconds=5.0, max_retries=0)
    try:
        resp = await client.post(
            f"{url.rstrip('/')}/api/v2/auth/login",
            policy=POLICY_ALLOW_PRIVATE,
            data={"username": username, "password": password},
        )
    except SafeHttpError as exc:
        return False, f"Could not reach qBittorrent: {exc}"
    if resp.status_code != 200 or resp.text.strip() != "Ok.":
        return False, "qBittorrent rejected the provided credentials"
    return True, "Connected to qBittorrent successfully"


async def test_slskd(url: str | None, api_key: str | None) -> tuple[bool, str]:
    if not url or not api_key:
        return False, "slskd URL and API key are both required"
    client = SafeHttpClient(timeout_seconds=5.0, max_retries=0)
    try:
        resp = await client.get(
            f"{url.rstrip('/')}/api/v0/session",
            policy=POLICY_ALLOW_PRIVATE,
            headers={"X-API-Key": api_key},
        )
    except SafeHttpError as exc:
        return False, f"Could not reach slskd: {exc}"
    if resp.status_code == 401:
        return False, "slskd rejected the API key"
    if resp.status_code >= 400:
        return False, f"slskd responded with HTTP {resp.status_code}"
    return True, "Connected to slskd successfully"


async def test_spotify(
    client_id: str | None, client_secret: str | None
) -> tuple[bool, str]:
    if not client_id or not client_secret:
        return False, "Spotify client ID and client secret are both required"
    client = SafeHttpClient(timeout_seconds=5.0, max_retries=0)
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = await client.post(
            "https://accounts.spotify.com/api/token",
            policy=POLICY_PUBLIC_ONLY,
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except SafeHttpError as exc:
        return False, f"Could not reach Spotify: {exc}"
    if resp.status_code == 400 or resp.status_code == 401:
        return False, "Spotify rejected the client ID/secret"
    if resp.status_code >= 400:
        return False, f"Spotify responded with HTTP {resp.status_code}"
    return True, "Connected to Spotify successfully"
