from __future__ import annotations

import pytest

from app.models.request import RequestStatus
from app.models.user import User, UserRole
from app.schemas.request import ApproveRequestIn, CreateRequestIn, RejectRequestIn
from app.services.requests import (
    approve_request,
    compute_dedupe_key,
    create_or_get_existing_request,
    reject_request,
)


async def _make_user(db_session) -> User:
    user = User(email="listener@example.com", password_hash="x", role=UserRole.USER)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _body(**overrides) -> CreateRequestIn:
    defaults = dict(
        source="apple",
        source_id="track-1",
        title="Tum Hi Ho",
        artist="Arijit Singh",
        album="Aashiqui 2",
    )
    defaults.update(overrides)
    return CreateRequestIn(**defaults)


class TestDedupeKey:
    def test_deterministic(self):
        assert compute_dedupe_key("apple", "123") == compute_dedupe_key("apple", "123")

    def test_distinguishes_source(self):
        assert compute_dedupe_key("apple", "123") != compute_dedupe_key(
            "spotify", "123"
        )


@pytest.mark.asyncio
class TestCreateOrGetExistingRequest:
    async def test_first_request_creates_row(self, db_session):
        user = await _make_user(db_session)
        request_row, created = await create_or_get_existing_request(
            db_session, user, _body()
        )
        assert created is True
        assert request_row.status == RequestStatus.PENDING

    async def test_second_identical_request_is_idempotent(self, db_session):
        user = await _make_user(db_session)
        first, _ = await create_or_get_existing_request(db_session, user, _body())
        second, created = await create_or_get_existing_request(
            db_session, user, _body()
        )
        assert created is False
        assert second.id == first.id

    async def test_different_track_creates_separate_row(self, db_session):
        user = await _make_user(db_session)
        first, _ = await create_or_get_existing_request(db_session, user, _body())
        second, created = await create_or_get_existing_request(
            db_session, user, _body(source_id="track-2", title="Different Song")
        )
        assert created is True
        assert second.id != first.id

    async def test_rejected_request_can_be_resubmitted(self, db_session):
        user = await _make_user(db_session)
        request_row, _ = await create_or_get_existing_request(db_session, user, _body())
        await reject_request(
            db_session, request_row, RejectRequestIn(note="not needed")
        )

        resubmitted, created = await create_or_get_existing_request(
            db_session, user, _body()
        )
        assert resubmitted.id == request_row.id
        assert resubmitted.status == RequestStatus.PENDING
        assert resubmitted.admin_note is None

    async def test_failed_request_can_be_resubmitted(self, db_session):
        user = await _make_user(db_session)
        request_row, _ = await create_or_get_existing_request(db_session, user, _body())
        request_row.status = RequestStatus.FAILED
        request_row.failure_reason = "no candidates"
        await db_session.commit()

        resubmitted, _ = await create_or_get_existing_request(db_session, user, _body())
        assert resubmitted.status == RequestStatus.PENDING
        assert resubmitted.failure_reason is None

    async def test_pending_request_is_not_reset_on_resubmit(self, db_session):
        user = await _make_user(db_session)
        request_row, _ = await create_or_get_existing_request(db_session, user, _body())
        again, created = await create_or_get_existing_request(db_session, user, _body())
        assert created is False
        assert again.status == RequestStatus.PENDING

    async def test_fulfilled_request_is_not_duplicated_or_reset(self, db_session):
        user = await _make_user(db_session)
        request_row, _ = await create_or_get_existing_request(db_session, user, _body())
        request_row.status = RequestStatus.FULFILLED
        request_row.final_file_path = "Artist/Title.flac"
        await db_session.commit()

        again, created = await create_or_get_existing_request(db_session, user, _body())
        assert created is False
        assert again.status == RequestStatus.FULFILLED
        assert again.final_file_path == "Artist/Title.flac"

    async def test_two_different_users_can_each_request_same_track(self, db_session):
        user1 = await _make_user(db_session)
        user2 = User(email="other@example.com", password_hash="x", role=UserRole.USER)
        db_session.add(user2)
        await db_session.commit()
        await db_session.refresh(user2)

        row1, created1 = await create_or_get_existing_request(
            db_session, user1, _body()
        )
        row2, created2 = await create_or_get_existing_request(
            db_session, user2, _body()
        )
        assert created1 is True
        assert created2 is True
        assert row1.id != row2.id


@pytest.mark.asyncio
class TestApproveReject:
    async def test_approve_sets_status_and_note(self, db_session):
        user = await _make_user(db_session)
        request_row, _ = await create_or_get_existing_request(db_session, user, _body())
        approved = await approve_request(
            db_session, request_row, ApproveRequestIn(note="ok")
        )
        assert approved.status == RequestStatus.APPROVED
        assert approved.admin_note == "ok"

    async def test_reject_sets_status_and_note(self, db_session):
        user = await _make_user(db_session)
        request_row, _ = await create_or_get_existing_request(db_session, user, _body())
        rejected = await reject_request(
            db_session, request_row, RejectRequestIn(note="dup")
        )
        assert rejected.status == RequestStatus.REJECTED
        assert rejected.admin_note == "dup"
