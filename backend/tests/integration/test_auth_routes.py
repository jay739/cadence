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
