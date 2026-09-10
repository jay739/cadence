"""Login orchestration across the local and Navidrome backends, and user provisioning.

Navidrome itself is stubbed via the injectable authenticator rather than over HTTP; the wire
protocol is covered in tests/unit/test_navidrome_auth.py. What matters here is the ordering
between backends and what ends up in the users table.
"""

from __future__ import annotations

import pytest

from app.db.base import create_all, dispose_engine, get_sessionmaker, init_engine
from app.models.user import AuthProvider, UserRole
from app.services.navidrome_auth import NavidromeProfile
from app.services.users import authenticate, create_user, get_user_by_external_username

pytestmark = pytest.mark.asyncio


class FakeNavidrome:
    """Stands in for a Navidrome server with one known account."""

    def __init__(
        self, *, verdict: bool | None, profile: NavidromeProfile | None = None
    ):
        self.verdict = verdict
        self.profile = profile or NavidromeProfile(is_admin=False, email=None)
        self.configured = True
        self.calls: list[str] = []

    async def verify_credentials(self, username: str, password: str) -> bool | None:
        self.calls.append("verify")
        return self.verdict

    async def fetch_profile(self, username: str, password: str) -> NavidromeProfile:
        self.calls.append("profile")
        return self.profile


@pytest.fixture
async def db(tmp_path):
    init_engine(f"sqlite+aiosqlite:///{tmp_path}/auth.db")
    await create_all()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session
    await dispose_engine()


async def _login(session, identifier, password, nav):
    return await authenticate(
        session,
        identifier=identifier,
        password=password,
        navidrome_url="http://localhost:4533",
        authenticator=nav,
    )


class TestBackendOrdering:
    async def test_local_account_wins_and_navidrome_is_never_consulted(self, db):
        # The operator's local admin must work without depending on Navidrome at all.
        await create_user(db, email="admin@example.com", password="a-strong-password")
        nav = FakeNavidrome(verdict=None)

        user = await _login(db, "admin@example.com", "a-strong-password", nav)

        assert user is not None
        assert nav.calls == []

    async def test_local_admin_still_logs_in_while_navidrome_is_down(self, db):
        await create_user(db, email="admin@example.com", password="a-strong-password")
        nav = FakeNavidrome(verdict=None)  # unreachable

        assert (
            await _login(db, "admin@example.com", "a-strong-password", nav) is not None
        )

    async def test_unreachable_navidrome_is_not_reported_as_bad_password(self, db):
        nav = FakeNavidrome(verdict=None)
        assert await _login(db, "jay", "pw", nav) is None
        # It asked, and refused to guess. The route turns None into "invalid credentials",
        # which is why verify_credentials must never collapse unknown into False upstream.
        assert nav.calls == ["verify"]

    async def test_rejected_credentials_do_not_provision_anything(self, db):
        nav = FakeNavidrome(verdict=False)
        assert await _login(db, "jay", "wrong", nav) is None
        assert await get_user_by_external_username(db, "jay") is None


class TestProvisioning:
    async def test_first_login_creates_a_linked_user(self, db):
        nav = FakeNavidrome(
            verdict=True, profile=NavidromeProfile(is_admin=False, email=None)
        )

        user = await _login(db, "Jay", "pw", nav)

        assert user is not None
        assert user.auth_provider is AuthProvider.NAVIDROME
        assert user.external_username == "jay"
        assert user.role is UserRole.USER
        # No password is stored for an externally-authenticated account, in any form.
        assert user.password_hash is None

    async def test_navidrome_admin_becomes_a_cadence_admin(self, db):
        nav = FakeNavidrome(
            verdict=True, profile=NavidromeProfile(is_admin=True, email=None)
        )
        user = await _login(db, "jay", "pw", nav)
        assert user.role is UserRole.ADMIN

    async def test_second_login_reuses_the_same_row(self, db):
        nav = FakeNavidrome(verdict=True)
        first = await _login(db, "jay", "pw", nav)
        second = await _login(db, "jay", "pw", nav)
        assert first.id == second.id

    async def test_promotion_in_navidrome_is_reflected_on_next_login(self, db):
        user = await _login(db, "jay", "pw", FakeNavidrome(verdict=True))
        assert user.role is UserRole.USER

        promoted = await _login(
            db,
            "jay",
            "pw",
            FakeNavidrome(
                verdict=True, profile=NavidromeProfile(is_admin=True, email=None)
            ),
        )
        assert promoted.id == user.id
        assert promoted.role is UserRole.ADMIN

    async def test_uses_the_email_navidrome_reports(self, db):
        nav = FakeNavidrome(
            verdict=True, profile=NavidromeProfile(is_admin=False, email="jay@real.com")
        )
        user = await _login(db, "jay", "pw", nav)
        assert user.email == "jay@real.com"

    async def test_namespaces_the_email_when_navidrome_has_none(self, db):
        user = await _login(db, "jay", "pw", FakeNavidrome(verdict=True))
        assert user.email == "jay@navidrome.local"

    async def test_never_hijacks_an_existing_local_account_with_the_same_email(
        self, db
    ):
        # Merging these would hand the Navidrome user the local account's request history
        # and role, which is a privilege-escalation path, not a convenience.
        local = await create_user(
            db, email="shared@example.com", password="a-strong-password"
        )
        nav = FakeNavidrome(
            verdict=True,
            profile=NavidromeProfile(is_admin=True, email="shared@example.com"),
        )

        user = await _login(db, "jay", "pw", nav)

        assert user.id != local.id
        assert user.email == "jay@navidrome.local"
        assert local.role is UserRole.USER


class TestLocalPathSafety:
    async def test_navidrome_row_cannot_be_logged_into_as_a_local_account(self, db):
        # password_hash is null on these rows; the local path must take the timing-equalised
        # miss branch rather than raising on a null hash.
        await _login(db, "jay", "pw", FakeNavidrome(verdict=True))
        nav = FakeNavidrome(verdict=False)

        assert await _login(db, "jay@navidrome.local", "anything", nav) is None

    async def test_inactive_navidrome_user_is_refused(self, db):
        user = await _login(db, "jay", "pw", FakeNavidrome(verdict=True))
        user.is_active = False
        await db.commit()

        assert await _login(db, "jay", "pw", FakeNavidrome(verdict=True)) is None
