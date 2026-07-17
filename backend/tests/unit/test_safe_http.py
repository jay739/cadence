from __future__ import annotations

import pytest

from app.core.safe_http import (
    POLICY_ALLOW_PRIVATE,
    POLICY_PUBLIC_ONLY,
    SafeHttpClient,
    SafeHttpError,
    _ip_is_blocked,
    _resolve_and_check,
)


class TestIpBlocking:
    def test_public_only_blocks_loopback(self):
        assert _ip_is_blocked("127.0.0.1", allow_private=False) is True

    def test_public_only_blocks_private_rfc1918(self):
        assert _ip_is_blocked("10.0.0.5", allow_private=False) is True
        assert _ip_is_blocked("192.168.1.1", allow_private=False) is True
        assert _ip_is_blocked("172.16.0.1", allow_private=False) is True

    def test_public_only_blocks_link_local_metadata(self):
        # 169.254.169.254 is the AWS/GCP/Azure/OCI cloud metadata endpoint.
        assert _ip_is_blocked("169.254.169.254", allow_private=False) is True

    def test_public_only_allows_public_ip(self):
        assert _ip_is_blocked("8.8.8.8", allow_private=False) is False

    def test_allow_private_still_blocks_link_local_metadata(self):
        # Even operator-configured integrations must never be able to hit the metadata IP.
        assert _ip_is_blocked("169.254.169.254", allow_private=True) is True

    def test_allow_private_permits_rfc1918(self):
        assert _ip_is_blocked("10.0.0.5", allow_private=True) is False
        assert _ip_is_blocked("192.168.1.50", allow_private=True) is False

    def test_allow_private_permits_loopback(self):
        assert _ip_is_blocked("127.0.0.1", allow_private=True) is False

    def test_multicast_always_blocked(self):
        assert _ip_is_blocked("224.0.0.1", allow_private=False) is True
        assert _ip_is_blocked("224.0.0.1", allow_private=True) is True

    def test_ipv6_loopback_and_link_local(self):
        assert _ip_is_blocked("::1", allow_private=False) is True
        assert _ip_is_blocked("fe80::1", allow_private=False) is True
        assert _ip_is_blocked("fe80::1", allow_private=True) is True


class TestResolveAndCheck:
    def test_rejects_non_http_scheme(self):
        with pytest.raises(SafeHttpError, match="scheme"):
            _resolve_and_check("file:///etc/passwd", policy=POLICY_PUBLIC_ONLY)

    def test_rejects_ftp_scheme(self):
        with pytest.raises(SafeHttpError, match="scheme"):
            _resolve_and_check("ftp://example.com/", policy=POLICY_PUBLIC_ONLY)

    def test_rejects_url_with_no_hostname(self):
        with pytest.raises(SafeHttpError):
            _resolve_and_check("http:///path", policy=POLICY_PUBLIC_ONLY)

    def test_public_policy_rejects_literal_loopback_url(self):
        with pytest.raises(SafeHttpError, match="Blocked target"):
            _resolve_and_check("http://127.0.0.1/admin", policy=POLICY_PUBLIC_ONLY)

    def test_public_policy_rejects_metadata_ip_url(self):
        with pytest.raises(SafeHttpError, match="Blocked target"):
            _resolve_and_check(
                "http://169.254.169.254/latest/meta-data/", policy=POLICY_PUBLIC_ONLY
            )

    def test_allow_private_permits_literal_private_ip_url(self):
        # Simulates the operator's own Prowlarr/qBittorrent/slskd on the Docker network.
        target = _resolve_and_check(
            "http://10.0.0.50:9696/api/v1/search", policy=POLICY_ALLOW_PRIVATE
        )
        assert target.ip == "10.0.0.50"

    def test_allow_private_still_rejects_metadata_ip(self):
        with pytest.raises(SafeHttpError, match="Blocked target"):
            _resolve_and_check("http://169.254.169.254/", policy=POLICY_ALLOW_PRIVATE)


class TestSafeHttpClientIntegration:
    """These exercise the client's validation path without any real network call, by pointing
    at addresses that must be rejected before a socket is ever opened."""

    @pytest.mark.asyncio
    async def test_get_to_loopback_raises_before_any_request(self):
        client = SafeHttpClient(timeout_seconds=1.0, max_retries=0)
        with pytest.raises(SafeHttpError):
            await client.get(
                "http://127.0.0.1:9999/whatever", policy=POLICY_PUBLIC_ONLY
            )

    @pytest.mark.asyncio
    async def test_get_to_metadata_ip_raises_even_with_allow_private(self):
        client = SafeHttpClient(timeout_seconds=1.0, max_retries=0)
        with pytest.raises(SafeHttpError):
            await client.get("http://169.254.169.254/", policy=POLICY_ALLOW_PRIVATE)

    @pytest.mark.asyncio
    async def test_get_to_unresolvable_host_raises_safe_http_error(self):
        client = SafeHttpClient(timeout_seconds=1.0, max_retries=0)
        with pytest.raises(SafeHttpError):
            await client.get(
                "http://this-host-should-never-resolve.invalid.example.test/",
                policy=POLICY_PUBLIC_ONLY,
            )
