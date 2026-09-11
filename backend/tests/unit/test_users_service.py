from __future__ import annotations

import pytest

from app.core.security import verify_password
from app.models.user import AuthProvider, UserRole
from app.services.users import (
    EmailAlreadyRegisteredError,
    PasswordChangeNotSupportedError,
    WrongPasswordError,
    authenticate_user,
    bootstrap_admin,
    change_password,
    create_user,
    get_user_by_email,
    get_user_by_id,
    update_avatar,
    update_email,
)

pytestmark = pytest.mark.asyncio


class TestCreateUser:
    async def test_create_user_success(self, db_session):
        user = await create_user(
            db_session, email="Test@Example.com", password="a-strong-password"
        )
        assert user.email == "test@example.com"  # normalized to lowercase
        assert user.role == UserRole.USER

    async def test_create_duplicate_email_raises(self, db_session):
        await create_user(
            db_session, email="dup@example.com", password="a-strong-password"
        )
        with pytest.raises(EmailAlreadyRegisteredError):
            await create_user(
                db_session, email="dup@example.com", password="another-password"
            )

    async def test_create_admin_role(self, db_session):
        user = await create_user(
            db_session,
            email="admin@example.com",
            password="a-strong-password",
            role=UserRole.ADMIN,
        )
        assert user.role == UserRole.ADMIN


class TestGetUser:
    async def test_get_by_email_found(self, db_session):
        created = await create_user(
            db_session, email="findme@example.com", password="a-strong-password"
        )
        found = await get_user_by_email(db_session, "findme@example.com")
        assert found is not None
        assert found.id == created.id

    async def test_get_by_email_case_insensitive(self, db_session):
        await create_user(
            db_session,
            email="caseinsensitive@example.com",
            password="a-strong-password",
        )
        found = await get_user_by_email(db_session, "CaseInsensitive@Example.com")
        assert found is not None

    async def test_get_by_email_not_found(self, db_session):
        found = await get_user_by_email(db_session, "doesnotexist@example.com")
        assert found is None

    async def test_get_by_id_found(self, db_session):
        created = await create_user(
            db_session, email="byid@example.com", password="a-strong-password"
        )
        found = await get_user_by_id(db_session, created.id)
        assert found is not None
        assert found.email == "byid@example.com"

    async def test_get_by_id_not_found(self, db_session):
        found = await get_user_by_id(db_session, "nonexistent-id")
        assert found is None


class TestAuthenticateUser:
    async def test_authenticate_success(self, db_session):
        await create_user(
            db_session, email="authok@example.com", password="a-strong-password"
        )
        user = await authenticate_user(
            db_session, email="authok@example.com", password="a-strong-password"
        )
        assert user is not None

    async def test_authenticate_wrong_password(self, db_session):
        await create_user(
            db_session, email="authbad@example.com", password="a-strong-password"
        )
        user = await authenticate_user(
            db_session, email="authbad@example.com", password="wrong"
        )
        assert user is None

    async def test_authenticate_unknown_email(self, db_session):
        user = await authenticate_user(
            db_session, email="unknown@example.com", password="whatever"
        )
        assert user is None

    async def test_authenticate_inactive_user_rejected(self, db_session):
        created = await create_user(
            db_session, email="inactive@example.com", password="a-strong-password"
        )
        created.is_active = False
        await db_session.commit()
        user = await authenticate_user(
            db_session, email="inactive@example.com", password="a-strong-password"
        )
        assert user is None


class TestBootstrapAdmin:
    async def test_bootstrap_admin_creates_when_configured(self, db_session):
        admin = await bootstrap_admin(
            db_session, email="bootstrap@example.com", password="a-strong-password"
        )
        assert admin is not None
        assert admin.role == UserRole.ADMIN

    async def test_bootstrap_admin_noop_when_not_configured(self, db_session):
        admin = await bootstrap_admin(db_session, email=None, password=None)
        assert admin is None

    async def test_bootstrap_admin_idempotent_on_rerun(self, db_session):
        first = await bootstrap_admin(
            db_session, email="idempotent@example.com", password="a-strong-password"
        )
        second = await bootstrap_admin(
            db_session, email="idempotent@example.com", password="a-different-password"
        )
        assert first.id == second.id


class TestUpdateAvatar:
    async def test_sets_avatar_url(self, db_session):
        user = await create_user(
            db_session, email="avatar@example.com", password="a-strong-password"
        )
        updated = await update_avatar(db_session, user, "https://example.com/a.png")
        assert updated.avatar_url == "https://example.com/a.png"

    async def test_empty_string_clears_to_none(self, db_session):
        user = await create_user(
            db_session, email="avatarclear@example.com", password="a-strong-password"
        )
        await update_avatar(db_session, user, "https://example.com/a.png")
        cleared = await update_avatar(db_session, user, "")
        assert cleared.avatar_url is None


class TestUpdateEmail:
    async def test_changes_email(self, db_session):
        user = await create_user(
            db_session, email="oldmail@example.com", password="a-strong-password"
        )
        updated = await update_email(db_session, user, "NewMail@Example.com")
        assert updated.email == "newmail@example.com"

    async def test_rejects_email_already_owned_by_someone_else(self, db_session):
        await create_user(
            db_session, email="taken@example.com", password="a-strong-password"
        )
        user = await create_user(
            db_session, email="wantsit@example.com", password="a-strong-password"
        )
        with pytest.raises(EmailAlreadyRegisteredError):
            await update_email(db_session, user, "taken@example.com")

    async def test_setting_own_current_email_does_not_conflict_with_itself(
        self, db_session
    ):
        user = await create_user(
            db_session, email="same@example.com", password="a-strong-password"
        )
        updated = await update_email(db_session, user, "same@example.com")
        assert updated.email == "same@example.com"


class TestChangePassword:
    async def test_happy_path_rotates_hash_and_bumps_token_version(self, db_session):
        user = await create_user(
            db_session, email="pwchange@example.com", password="old-strong-password"
        )
        before_version = user.token_version
        updated = await change_password(
            db_session,
            user,
            current_password="old-strong-password",
            new_password="new-strong-password",
        )
        assert verify_password("new-strong-password", updated.password_hash)
        assert not verify_password("old-strong-password", updated.password_hash)
        assert updated.token_version == before_version + 1

    async def test_wrong_current_password_raises(self, db_session):
        user = await create_user(
            db_session, email="pwwrong@example.com", password="old-strong-password"
        )
        with pytest.raises(WrongPasswordError):
            await change_password(
                db_session,
                user,
                current_password="not-the-right-one",
                new_password="new-strong-password",
            )

    async def test_navidrome_account_is_refused(self, db_session):
        user = await create_user(
            db_session, email="pwnavidrome@example.com", password="whatever-password"
        )
        user.auth_provider = AuthProvider.NAVIDROME
        user.password_hash = None
        await db_session.commit()

        with pytest.raises(PasswordChangeNotSupportedError):
            await change_password(
                db_session,
                user,
                current_password="whatever-password",
                new_password="new-strong-password",
            )
