"""Unit tests for Subsonic credential validation against Navidrome.

The three-way return of verify_credentials is the point of most of these: True accepted,
False rejected, None unknown. Collapsing unknown into rejected would tell every user their
password is wrong during a Navidrome outage, so each failure mode is pinned explicitly.
"""

from __future__ import annotations

import re

import pytest

from app.services.navidrome_auth import NavidromeAuthenticator

pytestmark = pytest.mark.asyncio

PING = re.compile(r"http://localhost:4533/rest/ping\.view.*")
GETUSER = re.compile(r"http://localhost:4533/rest/getUser\.view.*")


def _auth() -> NavidromeAuthenticator:
    return NavidromeAuthenticator("http://localhost:4533")


class TestVerifyCredentials:
    async def test_accepts_valid_credentials(self, httpx_mock):
        httpx_mock.add_response(url=PING, json={"subsonic-response": {"status": "ok"}})
        assert await _auth().verify_credentials("jay", "pw") is True

    async def test_rejects_only_on_subsonic_error_40(self, httpx_mock):
        httpx_mock.add_response(
            url=PING,
            json={
                "subsonic-response": {
                    "status": "failed",
                    "error": {"code": 40, "message": "Wrong username or password"},
                }
            },
        )
        assert await _auth().verify_credentials("jay", "wrong") is False

    async def test_other_subsonic_errors_are_unknown_not_rejection(self, httpx_mock):
        # Code 30 is "server version incompatible". That is the operator's problem, and
        # reporting it as a bad password would send users chasing their own credentials.
        httpx_mock.add_response(
            url=PING,
            json={"subsonic-response": {"status": "failed", "error": {"code": 30}}},
        )
        assert await _auth().verify_credentials("jay", "pw") is None

    async def test_http_error_is_unknown(self, httpx_mock):
        httpx_mock.add_response(url=PING, status_code=502)
        assert await _auth().verify_credentials("jay", "pw") is None

    async def test_malformed_body_is_unknown(self, httpx_mock):
        httpx_mock.add_response(url=PING, json={"not-subsonic": True})
        assert await _auth().verify_credentials("jay", "pw") is None

    async def test_unconfigured_authenticator_is_unknown(self):
        assert (
            await NavidromeAuthenticator(None).verify_credentials("jay", "pw") is None
        )

    async def test_salt_and_token_are_sent_and_differ_per_call(self, httpx_mock):
        # The salt must be per-request, otherwise the md5 token is a replayable constant.
        httpx_mock.add_response(url=PING, json={"subsonic-response": {"status": "ok"}})
        httpx_mock.add_response(url=PING, json={"subsonic-response": {"status": "ok"}})
        auth = _auth()
        await auth.verify_credentials("jay", "pw")
        await auth.verify_credentials("jay", "pw")

        first, second = httpx_mock.get_requests()[0], httpx_mock.get_requests()[1]
        assert first.url.params["s"] != second.url.params["s"]
        assert first.url.params["t"] != second.url.params["t"]
        # The password itself must never appear in the query string.
        assert "pw" not in str(first.url)


class TestFetchProfile:
    async def test_reads_boolean_admin_role(self, httpx_mock):
        httpx_mock.add_response(
            url=GETUSER,
            json={
                "subsonic-response": {
                    "user": {"adminRole": True, "email": "Jay@Example.com"}
                }
            },
        )
        profile = await _auth().fetch_profile("jay", "pw")
        assert profile.is_admin is True
        assert profile.email == "Jay@Example.com"

    async def test_reads_string_admin_role(self, httpx_mock):
        # Navidrome emits a real boolean, other Subsonic servers emit "true".
        httpx_mock.add_response(
            url=GETUSER, json={"subsonic-response": {"user": {"adminRole": "true"}}}
        )
        assert (await _auth().fetch_profile("jay", "pw")).is_admin is True

    async def test_non_admin_user(self, httpx_mock):
        httpx_mock.add_response(
            url=GETUSER, json={"subsonic-response": {"user": {"adminRole": False}}}
        )
        assert (await _auth().fetch_profile("jay", "pw")).is_admin is False

    async def test_blank_email_is_dropped(self, httpx_mock):
        httpx_mock.add_response(
            url=GETUSER,
            json={"subsonic-response": {"user": {"adminRole": False, "email": ""}}},
        )
        assert (await _auth().fetch_profile("jay", "pw")).email is None

    async def test_failure_yields_empty_profile_rather_than_raising(self, httpx_mock):
        # A server that does not answer getUser must still let its users log in as
        # ordinary requesters, not 500 the login route.
        httpx_mock.add_response(url=GETUSER, status_code=500)
        profile = await _auth().fetch_profile("jay", "pw")
        assert profile.is_admin is False
        assert profile.email is None
