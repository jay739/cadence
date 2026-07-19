"""Unit tests for the path-based Cache-Control policy in app.core.middleware."""

from __future__ import annotations

from app.core.middleware import _cache_control_for


class TestCacheControlPolicy:
    def test_api_paths_are_never_cached(self):
        assert _cache_control_for("/api/health") == "no-store"
        assert _cache_control_for("/api/search") == "no-store"
        assert _cache_control_for("/api/search/stream") == "no-store"
        assert _cache_control_for("/api/requests/mine") == "no-store"

    def test_hashed_assets_are_cached_immutably_for_a_year(self):
        assert (
            _cache_control_for("/assets/index-vpnVrgcp.js")
            == "public, max-age=31536000, immutable"
        )
        assert (
            _cache_control_for("/assets/index-Chk9Woja.css")
            == "public, max-age=31536000, immutable"
        )

    def test_index_html_and_unhashed_static_files_are_revalidated_not_cached(self):
        # These keep the same filename across a redeploy but can change content, so they must
        # never be treated like the content-hashed /assets/* files.
        assert _cache_control_for("/") == "no-cache"
        assert _cache_control_for("/index.html") == "no-cache"
        assert _cache_control_for("/favicon.svg") == "no-cache"
        assert _cache_control_for("/search") == "no-cache"
