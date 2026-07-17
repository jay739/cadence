from __future__ import annotations

import time

import jwt
import pytest

from app.core.security import (
    SessionTokenError,
    constant_time_compare,
    create_session_token,
    decode_session_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        h = hash_password("correct horse battery staple")
        assert h != "correct horse battery staple"

    def test_verify_correct_password(self):
        h = hash_password("hunter22isastrongpw")
        assert verify_password("hunter22isastrongpw", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("hunter22isastrongpw")
        assert verify_password("wrongpassword", h) is False

    def test_verify_garbage_hash_does_not_raise(self):
        assert verify_password("anything", "not-a-real-hash") is False

    def test_hash_uses_argon2(self):
        h = hash_password("anotherpassword123")
        assert h.startswith("$argon2")

    def test_same_password_hashes_differently_each_time(self):
        # argon2 salts each hash, so two hashes of the same password must differ.
        h1 = hash_password("samepassword123")
        h2 = hash_password("samepassword123")
        assert h1 != h2


class TestConstantTimeCompare:
    def test_equal_strings(self):
        assert constant_time_compare("abc123", "abc123") is True

    def test_unequal_strings(self):
        assert constant_time_compare("abc123", "abc124") is False

    def test_different_lengths(self):
        assert constant_time_compare("short", "a-much-longer-string") is False


class TestSessionTokens:
    def test_create_and_decode_round_trip(self):
        token = create_session_token(
            user_id="user-1",
            role="user",
            token_version=0,
            secret="test-secret",
            ttl_minutes=5,
        )
        payload = decode_session_token(token, secret="test-secret")
        assert payload["sub"] == "user-1"
        assert payload["role"] == "user"
        assert payload["tv"] == 0

    def test_decode_with_wrong_secret_fails(self):
        token = create_session_token(
            user_id="user-1",
            role="user",
            token_version=0,
            secret="secret-a",
            ttl_minutes=5,
        )
        with pytest.raises(SessionTokenError):
            decode_session_token(token, secret="secret-b")

    def test_expired_token_rejected(self):
        # Craft an already-expired token directly rather than sleeping in the test.
        now = time.time()
        payload = {
            "sub": "user-1",
            "role": "user",
            "tv": 0,
            "iat": now - 120,
            "exp": now - 60,
        }
        token = jwt.encode(payload, "test-secret", algorithm="HS256")
        with pytest.raises(SessionTokenError):
            decode_session_token(token, secret="test-secret")

    def test_tampered_token_rejected(self):
        token = create_session_token(
            user_id="user-1",
            role="user",
            token_version=0,
            secret="test-secret",
            ttl_minutes=5,
        )
        tampered = token[:-4] + "abcd"
        with pytest.raises(SessionTokenError):
            decode_session_token(tampered, secret="test-secret")

    def test_token_version_embedded_for_rotation_check(self):
        token = create_session_token(
            user_id="user-1",
            role="admin",
            token_version=7,
            secret="test-secret",
            ttl_minutes=5,
        )
        payload = decode_session_token(token, secret="test-secret")
        assert payload["tv"] == 7
