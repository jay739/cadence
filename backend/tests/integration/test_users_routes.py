from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.base import get_sessionmaker
from app.models.request import Request, RequestStatus
from tests.integration.conftest import (
    make_admin_headers,
    make_navidrome_headers,
    register_and_login,
)

pytestmark = pytest.mark.asyncio


def _payload(**overrides):
    body = {
        "source": "apple",
        "source_id": "track-abc",
        "title": "Tum Hi Ho",
        "artist": "Arijit Singh",
        "album": "Aashiqui 2",
    }
    body.update(overrides)
    return body


async def _force_status(client_request_id: str, status: RequestStatus) -> None:
    """Set a request's status directly, for statuses ordinary flows don't reach on their own
    (downloading/fulfilled/failed normally come from the fulfillment worker, not a route).
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(Request).where(Request.id == client_request_id)
        )
        row = result.scalar_one()
        row.status = status
        await session.commit()


class TestStats:
    async def test_requires_auth(self, app_client):
        resp = await app_client.get("/api/users/me/stats")
        assert resp.status_code == 401

    async def test_counts_by_status_and_total(self, app_client):
        headers = await register_and_login(
            app_client, "statsuser@example.com", "a-strong-password-1"
        )
        pending = await app_client.post(
            "/api/requests", json=_payload(source_id="s-pending"), headers=headers
        )
        approved = await app_client.post(
            "/api/requests", json=_payload(source_id="s-approved"), headers=headers
        )
        rejected = await app_client.post(
            "/api/requests", json=_payload(source_id="s-rejected"), headers=headers
        )
        fulfilled = await app_client.post(
            "/api/requests", json=_payload(source_id="s-fulfilled"), headers=headers
        )
        failed = await app_client.post(
            "/api/requests", json=_payload(source_id="s-failed"), headers=headers
        )
        downloading = await app_client.post(
            "/api/requests", json=_payload(source_id="s-downloading"), headers=headers
        )
        assert pending.status_code == 201

        admin_headers = await make_admin_headers(app_client)
        await app_client.post(
            f"/api/requests/{approved.json()['id']}/approve",
            json={"note": None},
            headers=admin_headers,
        )
        await app_client.post(
            f"/api/requests/{rejected.json()['id']}/reject",
            json={"note": None},
            headers=admin_headers,
        )
        await _force_status(fulfilled.json()["id"], RequestStatus.FULFILLED)
        await _force_status(failed.json()["id"], RequestStatus.FAILED)
        await _force_status(downloading.json()["id"], RequestStatus.DOWNLOADING)

        resp = await app_client.get("/api/users/me/stats", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {
            "pending": 1,
            "approved": 1,
            "downloading": 1,
            "fulfilled": 1,
            "rejected": 1,
            "failed": 1,
            "total": 6,
        }

    async def test_only_counts_the_caller_s_own_requests(self, app_client):
        headers1 = await register_and_login(
            app_client, "statsmine@example.com", "a-strong-password-1"
        )
        headers2 = await register_and_login(
            app_client, "statsother@example.com", "a-strong-password-1"
        )
        await app_client.post("/api/requests", json=_payload(), headers=headers1)
        await app_client.post(
            "/api/requests", json=_payload(source_id="other"), headers=headers2
        )

        resp = await app_client.get("/api/users/me/stats", headers=headers1)
        assert resp.json()["total"] == 1

    async def test_zero_requests_yields_all_zero_counts(self, app_client):
        headers = await register_and_login(
            app_client, "statsempty@example.com", "a-strong-password-1"
        )
        resp = await app_client.get("/api/users/me/stats", headers=headers)
        assert resp.json() == {
            "pending": 0,
            "approved": 0,
            "downloading": 0,
            "fulfilled": 0,
            "rejected": 0,
            "failed": 0,
            "total": 0,
        }


class TestUpdateAvatar:
    async def test_requires_auth(self, app_client):
        resp = await app_client.patch(
            "/api/users/me/avatar", json={"avatar_url": "https://example.com/a.png"}
        )
        assert resp.status_code == 401

    async def test_sets_avatar_url(self, app_client):
        headers = await register_and_login(
            app_client, "avataruser@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/users/me/avatar",
            json={"avatar_url": "https://example.com/me.png"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["avatar_url"] == "https://example.com/me.png"

    async def test_empty_string_clears_avatar(self, app_client):
        headers = await register_and_login(
            app_client, "avatarclear@example.com", "a-strong-password-1"
        )
        await app_client.patch(
            "/api/users/me/avatar",
            json={"avatar_url": "https://example.com/me.png"},
            headers=headers,
        )
        resp = await app_client.patch(
            "/api/users/me/avatar", json={"avatar_url": ""}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["avatar_url"] is None


class TestResolvedAvatar:
    """`avatar` is the picture to render; `avatar_url` stays the raw stored value.

    Keeping both on the wire is what lets the profile form round-trip "not set" while the rest
    of the UI always has something to draw.
    """

    async def test_a_new_account_gets_a_generated_doodle(self, app_client):
        headers = await register_and_login(
            app_client, "doodle@example.com", "a-strong-password-1"
        )
        body = (await app_client.get("/api/auth/me", headers=headers)).json()

        assert body["avatar_url"] is None
        assert body["avatar_source"] == "generated"
        assert body["avatar"].startswith("data:image/svg+xml;base64,")

    async def test_setting_an_avatar_url_takes_precedence_over_the_doodle(
        self, app_client
    ):
        headers = await register_and_login(
            app_client, "doodle2@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/users/me/avatar",
            json={"avatar_url": "https://example.com/me.png"},
            headers=headers,
        )

        assert resp.json()["avatar"] == "https://example.com/me.png"
        assert resp.json()["avatar_source"] == "url"

    async def test_clearing_the_url_falls_back_to_the_same_doodle_as_before(
        self, app_client
    ):
        # The doodle is seeded on the user id, so it must be identical before and after -- a
        # user who sets and then clears a picture gets their own avatar back, not a new one.
        headers = await register_and_login(
            app_client, "doodle3@example.com", "a-strong-password-1"
        )
        original = (await app_client.get("/api/auth/me", headers=headers)).json()[
            "avatar"
        ]
        await app_client.patch(
            "/api/users/me/avatar",
            json={"avatar_url": "https://example.com/me.png"},
            headers=headers,
        )
        cleared = await app_client.patch(
            "/api/users/me/avatar", json={"avatar_url": ""}, headers=headers
        )

        assert cleared.json()["avatar_source"] == "generated"
        assert cleared.json()["avatar"] == original

    async def test_different_users_get_different_doodles(self, app_client):
        first = await register_and_login(
            app_client, "doodle4@example.com", "a-strong-password-1"
        )
        second = await register_and_login(
            app_client, "doodle5@example.com", "a-strong-password-1"
        )
        first_avatar = (await app_client.get("/api/auth/me", headers=first)).json()[
            "avatar"
        ]
        second_avatar = (await app_client.get("/api/auth/me", headers=second)).json()[
            "avatar"
        ]
        assert first_avatar != second_avatar

    async def test_navidrome_accounts_also_get_a_doodle(self, app_client):
        # Navidrome exposes no per-user picture (see app.services.avatars), so a Navidrome-backed
        # account falls through to the generated doodle exactly like a local one.
        headers = await make_navidrome_headers(app_client)
        body = (await app_client.get("/api/auth/me", headers=headers)).json()

        assert body["auth_provider"] == "navidrome"
        assert body["avatar_source"] == "generated"
        assert body["avatar"].startswith("data:image/svg+xml;base64,")


class TestUpdateEmail:
    async def test_requires_auth(self, app_client):
        resp = await app_client.patch(
            "/api/users/me/email", json={"email": "new@example.com"}
        )
        assert resp.status_code == 401

    async def test_happy_path_changes_email(self, app_client):
        headers = await register_and_login(
            app_client, "emailold@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/users/me/email",
            json={"email": "emailnew@example.com"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "emailnew@example.com"

        # The new address is now the one that logs in.
        login_resp = await app_client.post(
            "/api/auth/login",
            json={"email": "emailnew@example.com", "password": "a-strong-password-1"},
        )
        assert login_resp.status_code == 200

    async def test_rejects_duplicate_email(self, app_client):
        await register_and_login(
            app_client, "emailtaken@example.com", "a-strong-password-1"
        )
        headers = await register_and_login(
            app_client, "emailwants@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/users/me/email",
            json={"email": "emailtaken@example.com"},
            headers=headers,
        )
        assert resp.status_code == 409

    async def test_rejects_invalid_email(self, app_client):
        headers = await register_and_login(
            app_client, "emailinvalid@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/users/me/email", json={"email": "not-an-email"}, headers=headers
        )
        assert resp.status_code == 422

    async def test_setting_own_current_email_is_a_noop_success(self, app_client):
        headers = await register_and_login(
            app_client, "emailsame@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/users/me/email",
            json={"email": "emailsame@example.com"},
            headers=headers,
        )
        assert resp.status_code == 200


class TestChangePassword:
    async def test_requires_auth(self, app_client):
        resp = await app_client.post(
            "/api/users/me/password",
            json={
                "current_password": "a-strong-password-1",
                "new_password": "a-new-password-2",
            },
        )
        assert resp.status_code == 401

    async def test_happy_path_local_account(self, app_client):
        headers = await register_and_login(
            app_client, "pwchange@example.com", "a-strong-password-1"
        )
        resp = await app_client.post(
            "/api/users/me/password",
            json={
                "current_password": "a-strong-password-1",
                "new_password": "a-brand-new-password-2",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

        # The old password no longer works, the new one does.
        old_login = await app_client.post(
            "/api/auth/login",
            json={"email": "pwchange@example.com", "password": "a-strong-password-1"},
        )
        assert old_login.status_code == 401
        new_login = await app_client.post(
            "/api/auth/login",
            json={
                "email": "pwchange@example.com",
                "password": "a-brand-new-password-2",
            },
        )
        assert new_login.status_code == 200

    async def test_old_session_token_is_revoked_after_change(self, app_client):
        headers = await register_and_login(
            app_client, "pwrevoke@example.com", "a-strong-password-1"
        )
        await app_client.post(
            "/api/users/me/password",
            json={
                "current_password": "a-strong-password-1",
                "new_password": "a-brand-new-password-2",
            },
            headers=headers,
        )
        # The Bearer token used to make that very request is now stale (token_version bumped).
        resp = await app_client.get("/api/auth/me", headers=headers)
        assert resp.status_code == 401

    async def test_rejects_wrong_current_password(self, app_client):
        headers = await register_and_login(
            app_client, "pwwrong@example.com", "a-strong-password-1"
        )
        resp = await app_client.post(
            "/api/users/me/password",
            json={
                "current_password": "totally-wrong",
                "new_password": "a-brand-new-password-2",
            },
            headers=headers,
        )
        assert resp.status_code == 401

    async def test_rejects_short_new_password(self, app_client):
        headers = await register_and_login(
            app_client, "pwshort@example.com", "a-strong-password-1"
        )
        resp = await app_client.post(
            "/api/users/me/password",
            json={"current_password": "a-strong-password-1", "new_password": "short"},
            headers=headers,
        )
        assert resp.status_code == 422

    async def test_inapplicable_for_navidrome_accounts(self, app_client):
        headers = await make_navidrome_headers(app_client)
        resp = await app_client.post(
            "/api/users/me/password",
            json={
                "current_password": "whatever",
                "new_password": "a-brand-new-password-2",
            },
            headers=headers,
        )
        assert resp.status_code == 400


class TestMeIncludesProfileFields:
    async def test_me_reports_local_provider_and_no_navidrome_url(self, app_client):
        headers = await register_and_login(
            app_client, "meprofile@example.com", "a-strong-password-1"
        )
        resp = await app_client.get("/api/auth/me", headers=headers)
        assert resp.json()["auth_provider"] == "local"
        assert resp.json()["navidrome_url"] is None
        assert resp.json()["avatar_url"] is None

    async def test_me_reports_navidrome_provider(self, app_client):
        headers = await make_navidrome_headers(app_client)
        resp = await app_client.get("/api/auth/me", headers=headers)
        assert resp.json()["auth_provider"] == "navidrome"

    async def test_me_includes_navidrome_url_when_configured(
        self, tmp_path, monkeypatch
    ):
        """The profile page's "manage your password in Navidrome" link needs the server's
        actual configured base URL, not just the boolean /api/health exposes to everyone.
        """
        from app.core.config import get_settings
        from app.main import create_app
        from tests.integration.conftest import _run_with_lifespan, _set_test_env

        _set_test_env(monkeypatch, tmp_path, demo_mode=False)
        monkeypatch.setenv("NAVIDROME_URL", "http://navidrome.example.internal:4533")
        get_settings.cache_clear()
        app = create_app()
        async for client in _run_with_lifespan(app):
            headers = await make_navidrome_headers(client)
            resp = await client.get("/api/auth/me", headers=headers)
            assert (
                resp.json()["navidrome_url"] == "http://navidrome.example.internal:4533"
            )
        get_settings.cache_clear()
