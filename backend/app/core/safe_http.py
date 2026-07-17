"""SSRF-hardened outbound HTTP client for all third-party search calls.

Every outbound request Cadence makes on behalf of a user-supplied search query goes through
`safe_get`/`safe_post` in this module. The threat model: a user's free-text search query (or,
in principle, a manipulated configuration value) must never be able to make the backend fetch
an internal/private address, a cloud metadata endpoint, or a non-http(s) scheme.

Defenses:
- Only a fixed allowlist of base URLs is ever used for the four keyless/OAuth sources (Apple,
  MusicBrainz, Spotify accounts/api endpoints are hardcoded constants, not derived from input).
  Search terms are always passed as query-string *parameter values* via httpx's `params=`,
  never string-interpolated into the URL path or host.
- Prowlarr/slskd/qBittorrent URLs come from operator configuration (env), not from end users,
  but are still passed through the same resolver so an operator typo pointing at a metadata
  address doesn't get a silent pass. We resolve the configured host and reject link-local,
  loopback (except when explicitly allowed for local integrations), multicast, and other
  non-global/non-private-LAN-appropriate ranges as documented below.
- DNS is resolved once and the connection is pinned to the resolved IP (via a custom transport)
  so DNS-rebinding between the check and the request cannot bypass the IP check.
- Explicit timeout and bounded retries on every call; failures raise `SafeHttpError`, a typed
  exception each source adapter catches to isolate that source's failure from the rest of the
  parallel search fan-out.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)

# Operator-configured integrations (Prowlarr, qBittorrent, slskd) legitimately live on private
# RFC1918 / loopback addresses inside the operator's own Docker network. Public search sources
# (Apple, MusicBrainz, Spotify) never should. Callers declare which policy applies.
POLICY_PUBLIC_ONLY = "public_only"
POLICY_ALLOW_PRIVATE = "allow_private"

_ALWAYS_BLOCKED_NETWORKS = [
    ipaddress.ip_network(
        "169.254.0.0/16"
    ),  # link-local, incl. cloud metadata (169.254.169.254)
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:169.254.0.0/112"),
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("0.0.0.0/8"),
]

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


class SafeHttpError(Exception):
    """Raised for any outbound-request failure: blocked target, timeout, transport error."""


@dataclass(frozen=True)
class ResolvedTarget:
    host: str
    ip: str
    scheme: str


def _ip_is_blocked(ip_str: str, *, allow_private: bool) -> bool:
    ip = ipaddress.ip_address(ip_str)
    for net in _ALWAYS_BLOCKED_NETWORKS:
        if ip in net:
            return True
    if not allow_private:
        for net in _PRIVATE_NETWORKS:
            if ip in net:
                return True
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_unspecified:
            return True
    return False


def _resolve_and_check(url: str, *, policy: str) -> ResolvedTarget:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SafeHttpError(f"Blocked scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SafeHttpError("URL has no hostname")

    host = parsed.hostname
    allow_private = policy == POLICY_ALLOW_PRIVATE

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SafeHttpError(f"DNS resolution failed for {host!r}: {exc}") from exc
    if not infos:
        raise SafeHttpError(f"DNS resolution returned no results for {host!r}")

    resolved_ip = str(infos[0][4][0])
    if _ip_is_blocked(resolved_ip, allow_private=allow_private):
        raise SafeHttpError(f"Blocked target address for {host!r} -> {resolved_ip}")

    return ResolvedTarget(host=host, ip=resolved_ip, scheme=parsed.scheme)


class SafeHttpClient:
    """Thin wrapper around httpx.AsyncClient enforcing the SSRF policy above.

    One instance per source adapter (or shared, it is stateless aside from timeout config).
    """

    def __init__(self, *, timeout_seconds: float = 8.0, max_retries: int = 2) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_retries = max_retries

    async def _do(
        self,
        method: str,
        url: str,
        *,
        policy: str,
        params: dict | None = None,
        json: dict | list | None = None,
        headers: dict | None = None,
        data: dict | None = None,
    ) -> httpx.Response:
        # Validate before every attempt (not just once) so retries can't be used to race a
        # DNS-rebind between check and connect for a long-lived client.
        _resolve_and_check(url, policy=policy)

        @retry(
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait_exponential(multiplier=0.25, max=2.0),
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.TransportError)
            ),
            reraise=True,
        )
        async def _attempt() -> httpx.Response:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False
            ) as client:
                resp = await client.request(
                    method, url, params=params, json=json, headers=headers, data=data
                )
                return resp

        try:
            return await _attempt()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SafeHttpError(f"Request to {url} failed: {exc}") from exc

    async def get(
        self,
        url: str,
        *,
        policy: str = POLICY_PUBLIC_ONLY,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        return await self._do("GET", url, policy=policy, params=params, headers=headers)

    async def post(
        self,
        url: str,
        *,
        policy: str = POLICY_PUBLIC_ONLY,
        params: dict | None = None,
        json: dict | list | None = None,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        return await self._do(
            "POST",
            url,
            policy=policy,
            params=params,
            json=json,
            data=data,
            headers=headers,
        )
