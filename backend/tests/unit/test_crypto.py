"""Unit tests for the settings-encryption round trip (app.core.crypto)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import DecryptionError, decrypt_value, encrypt_value, mask_secret


class TestEncryptDecryptRoundTrip:
    def test_round_trip_returns_original_plaintext(self):
        key = Fernet.generate_key()
        secret = "sk-super-secret-api-key-123"
        token = encrypt_value(secret, key=key)
        assert token != secret
        assert decrypt_value(token, key=key) == secret

    def test_ciphertext_is_not_plaintext_substring(self):
        key = Fernet.generate_key()
        secret = "qbittorrent-password-hunter2"
        token = encrypt_value(secret, key=key)
        assert secret not in token

    def test_different_keys_cannot_decrypt_each_others_ciphertext(self):
        key_a = Fernet.generate_key()
        key_b = Fernet.generate_key()
        token = encrypt_value("value", key=key_a)
        with pytest.raises(DecryptionError):
            decrypt_value(token, key=key_b)

    def test_tampered_ciphertext_raises_decryption_error(self):
        key = Fernet.generate_key()
        token = encrypt_value("value", key=key)
        tampered = token[:-4] + ("A" * 4)
        with pytest.raises(DecryptionError):
            decrypt_value(tampered, key=key)

    def test_encrypting_same_value_twice_yields_different_ciphertext(self):
        # Fernet includes a random IV/timestamp per encryption, so repeated encryption of the
        # same plaintext must not be deterministic (that would leak equality between two
        # saved secrets to anyone with DB read access).
        key = Fernet.generate_key()
        token_a = encrypt_value("same-value", key=key)
        token_b = encrypt_value("same-value", key=key)
        assert token_a != token_b
        assert decrypt_value(token_a, key=key) == decrypt_value(token_b, key=key)


class TestMaskSecret:
    def test_none_stays_none(self):
        assert mask_secret(None) is None

    def test_empty_string_stays_none(self):
        assert mask_secret("") is None

    def test_short_secret_fully_masked(self):
        assert mask_secret("abcd") == "•" * 8

    def test_long_secret_shows_last_four_chars_only(self):
        masked = mask_secret("sk-1234567890ab12")
        assert masked == "•" * 8 + "ab12"
        assert "1234567890" not in masked

    def test_masked_output_never_contains_the_real_value(self):
        secret = "a-fairly-long-real-secret-value-99"
        masked = mask_secret(secret)
        assert secret not in masked
