from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services.users import (
    EmailAlreadyRegisteredError,
    authenticate_user,
    bootstrap_admin,
    create_user,
    get_user_by_email,
    get_user_by_id,
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
