from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class TestRegister:
    async def test_register_creates_user_and_returns_token(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={"email": "newuser@example.com", "password": "a-strong-password-123"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["user"]["email"] == "newuser@example.com"
        assert body["user"]["role"] == "user"
        assert body["access_token"]

    async def test_register_duplicate_email_rejected(self, app_client):
        payload = {"email": "dupe@example.com", "password": "a-strong-password-123"}
        first = await app_client.post("/api/auth/register", json=payload)
        assert first.status_code == 201
        second = await app_client.post("/api/auth/register", json=payload)
        assert second.status_code == 409

    async def test_register_rejects_short_password(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={"email": "short@example.com", "password": "short"},
        )
        assert resp.status_code == 422

    async def test_register_rejects_invalid_email(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={"email": "not-an-email", "password": "a-strong-password-123"},
        )
        assert resp.status_code == 422

    async def test_password_never_appears_in_response(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={
                "email": "secretcheck@example.com",
                "password": "a-strong-password-123",
            },
        )
        assert (
            "password" not in resp.text.lower()
            or "a-strong-password-123" not in resp.text
        )


class TestLogin:
    async def test_login_succeeds_with_correct_credentials(self, app_client):
        await app_client.post(
            "/api/auth/register",
            json={
                "email": "logintest@example.com",
                "password": "a-strong-password-123",
            },
        )
        resp = await app_client.post(
            "/api/auth/login",
            json={
                "email": "logintest@example.com",
                "password": "a-strong-password-123",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    async def test_login_fails_with_wrong_password(self, app_client):
        await app_client.post(
            "/api/auth/register",
            json={"email": "wrongpw@example.com", "password": "a-strong-password-123"},
        )
        resp = await app_client.post(
            "/api/auth/login",
            json={"email": "wrongpw@example.com", "password": "totally-wrong-password"},
        )
        assert resp.status_code == 401

    async def test_login_fails_for_unknown_email(self, app_client):
        resp = await app_client.post(
            "/api/auth/login",
            json={
                "email": "doesnotexist@example.com",
                "password": "whatever-password-123",
            },
        )
        assert resp.status_code == 401

    async def test_login_error_message_does_not_reveal_which_field_was_wrong(
        self, app_client
    ):
        await app_client.post(
            "/api/auth/register",
            json={
                "email": "revealtest@example.com",
                "password": "a-strong-password-123",
            },
        )
        wrong_pw = await app_client.post(
            "/api/auth/login",
            json={"email": "revealtest@example.com", "password": "wrong-one-here"},
        )
        unknown_email = await app_client.post(
            "/api/auth/login",
            json={"email": "neverregistered@example.com", "password": "wrong-one-here"},
        )
        assert wrong_pw.json()["detail"] == unknown_email.json()["detail"]


class TestMe:
    async def test_me_requires_auth(self, app_client):
        resp = await app_client.get("/api/auth/me")
        assert resp.status_code == 401

    async def test_me_returns_current_user(self, app_client):
        register_resp = await app_client.post(
            "/api/auth/register",
            json={"email": "metest@example.com", "password": "a-strong-password-123"},
        )
        token = register_resp.json()["access_token"]
        resp = await app_client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "metest@example.com"

    async def test_me_rejects_garbage_token(self, app_client):
        resp = await app_client.get(
            "/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert resp.status_code == 401


class TestLogout:
    async def test_logout_clears_cookie(self, app_client):
        resp = await app_client.post("/api/auth/logout")
        assert resp.status_code == 204


class TestCookieSecureKnob:
    async def test_cookie_is_secure_by_default(self, demo_app_client):
        resp = await demo_app_client.post(
            "/api/auth/register",
            json={"email": "securecookie@example.com", "password": "a-strong-password-1"},
        )
        assert resp.status_code == 201
        set_cookie = resp.headers["set-cookie"]
        assert "Secure" in set_cookie

    async def test_cookie_secure_can_be_disabled_for_plain_http_deploys(
        self, tmp_path, monkeypatch
    ):
        """COOKIE_SECURE=false exists for LAN/Tailscale plain-HTTP deployments, where a Secure
        cookie is never transmitted by the browser, which silently breaks the two cookie-auth
        flows (Spotify OAuth redirect, streaming search) while Bearer-token flows keep working.
        """
        from app.core.config import get_settings
        from app.main import create_app
        from tests.integration.conftest import _run_with_lifespan, _set_test_env

        _set_test_env(monkeypatch, tmp_path, demo_mode=True)
        monkeypatch.setenv("COOKIE_SECURE", "false")
        get_settings.cache_clear()
        app = create_app()
        async for client in _run_with_lifespan(app):
            resp = await client.post(
                "/api/auth/register",
                json={"email": "plainhttp@example.com", "password": "a-strong-password-1"},
            )
            assert resp.status_code == 201
            set_cookie = resp.headers["set-cookie"]
            assert "cadence_session=" in set_cookie
            assert "Secure" not in set_cookie
            # HttpOnly must survive the knob: only the Secure attribute is configurable.
            assert "HttpOnly" in set_cookie
        get_settings.cache_clear()


class TestAuthRateLimitIsConfigurable:
    """The limit is keyed by client IP, so a shared egress (or the e2e suite, which signs in
    once per test from one address) can exhaust the default through ordinary use."""

    async def test_default_limit_still_rejects_a_burst(self, app_client):
        statuses = []
        for _ in range(14):
            resp = await app_client.post(
                "/api/auth/login",
                json={"identifier": "nobody@example.com", "password": "wrong-password-1"},
            )
            statuses.append(resp.status_code)
        # Unchanged default: the burst is cut off rather than answered indefinitely.
        assert 429 in statuses

    async def test_raising_the_limit_lets_the_same_burst_through(
        self, tmp_path, monkeypatch
    ):
        from app.core.config import get_settings
        from app.main import create_app
        from tests.integration.conftest import _run_with_lifespan, _set_test_env

        _set_test_env(monkeypatch, tmp_path, demo_mode=False)
        monkeypatch.setenv("AUTH_RATE_LIMIT", "500/minute")
        get_settings.cache_clear()
        app = create_app()
        async for client in _run_with_lifespan(app):
            statuses = []
            for _ in range(14):
                resp = await client.post(
                    "/api/auth/login",
                    json={
                        "identifier": "nobody@example.com",
                        "password": "wrong-password-1",
                    },
                )
                statuses.append(resp.status_code)
            # Same burst that trips the default: every one is answered on its own merits
            # (401 for the bad credentials) rather than being rate limited.
            assert 429 not in statuses
        get_settings.cache_clear()
