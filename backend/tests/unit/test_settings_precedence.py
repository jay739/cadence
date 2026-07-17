"""Unit tests for the env-var-vs-DB precedence and live-refresh logic in
app.services.settings_store.

Precedence rule under test: an environment variable, when set, always wins over a DB-stored
value for the same field; the DB value is used only when the env var is unset. Uses the
`db_session` fixture from tests/integration/conftest.py (a fresh in-memory SQLite DB per test).
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.settings_store import (
    FIELD_REGISTRY,
    apply_effective_settings,
    fields_for_integration,
    get_effective_settings,
    get_masked_settings_view,
    seed_db_from_env_on_boot,
    upsert_setting,
)

pytestmark = pytest.mark.asyncio


def _settings(tmp_path) -> Settings:
    return Settings(
        DATA_DIR=str(tmp_path / "data"),
        SESSION_SECRET="x" * 40,
    )


class TestFieldRegistry:
    def test_every_mission_scoped_field_is_registered(self):
        expected = {
            "prowlarr_url",
            "prowlarr_api_key",
            "qbittorrent_url",
            "qbittorrent_username",
            "qbittorrent_password",
            "slskd_url",
            "slskd_api_key",
            "spotify_client_id",
            "spotify_client_secret",
            "music_library_path",
            "library_path_pattern",
        }
        assert set(FIELD_REGISTRY.keys()) == expected

    def test_non_credential_tuning_fields_are_never_registered(self):
        # SESSION_TTL_MINUTES and OUTBOUND_TIMEOUT_SECONDS must stay env-only per the mission's
        # explicit scoping ("Fields that are not integration credentials ... can stay env-only").
        assert "session_ttl_minutes" not in FIELD_REGISTRY
        assert "outbound_timeout_seconds" not in FIELD_REGISTRY
        assert "fulfillment_prowlarr_attempt_budget" not in FIELD_REGISTRY

    def test_secrets_are_flagged_correctly(self):
        secret_fields = {
            f for f, (_i, _e, is_secret) in FIELD_REGISTRY.items() if is_secret
        }
        assert secret_fields == {
            "prowlarr_api_key",
            "qbittorrent_password",
            "slskd_api_key",
            "spotify_client_secret",
        }

    def test_fields_for_integration(self):
        assert set(fields_for_integration("prowlarr")) == {
            "prowlarr_url",
            "prowlarr_api_key",
        }
        assert set(fields_for_integration("library")) == {
            "music_library_path",
            "library_path_pattern",
        }


class TestPrecedenceDbOnly:
    async def test_db_value_used_when_no_env_var_set(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PROWLARR_URL", raising=False)
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        await upsert_setting(
            db_session,
            field_name="prowlarr_url",
            value="http://prowlarr.internal:9696",
            key=key,
            is_secret=False,
        )
        effective = await get_effective_settings(db_session, settings)
        assert effective.get("prowlarr_url") == "http://prowlarr.internal:9696"

    async def test_unset_field_resolves_to_none(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("SLSKD_URL", raising=False)
        settings = _settings(tmp_path)
        effective = await get_effective_settings(db_session, settings)
        assert effective.get("slskd_url") is None


class TestPrecedenceEnvWinsOverDb:
    async def test_env_var_overrides_existing_db_value(
        self, db_session, tmp_path, monkeypatch
    ):
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        await upsert_setting(
            db_session,
            field_name="prowlarr_url",
            value="http://db-stored-value:9696",
            key=key,
            is_secret=False,
        )
        monkeypatch.setenv("PROWLARR_URL", "http://env-var-value:9696")
        effective = await get_effective_settings(db_session, settings)
        assert effective.get("prowlarr_url") == "http://env-var-value:9696"

    async def test_secret_env_var_also_overrides_db(
        self, db_session, tmp_path, monkeypatch
    ):
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        await upsert_setting(
            db_session,
            field_name="prowlarr_api_key",
            value="db-stored-secret",
            key=key,
            is_secret=True,
        )
        monkeypatch.setenv("PROWLARR_API_KEY", "env-var-secret")
        effective = await get_effective_settings(db_session, settings)
        assert effective.get("prowlarr_api_key") == "env-var-secret"


class TestApplyEffectiveSettingsIsAdditive:
    async def test_no_db_no_env_leaves_constructor_value_untouched(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PROWLARR_URL", raising=False)
        monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
        settings = Settings(
            DATA_DIR=str(tmp_path / "data"),
            SESSION_SECRET="x" * 40,
            PROWLARR_URL="http://constructed-in-code:9696",
        )
        result = await apply_effective_settings(db_session, settings)
        assert result.prowlarr_url == "http://constructed-in-code:9696"

    async def test_db_value_overlays_onto_returned_settings(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        await upsert_setting(
            db_session,
            field_name="spotify_client_id",
            value="db-client-id",
            key=key,
            is_secret=False,
        )
        result = await apply_effective_settings(db_session, settings)
        assert result.spotify_client_id == "db-client-id"
        # Original settings object must not be mutated in place (model_copy semantics).
        assert settings.spotify_client_id is None

    async def test_saving_a_new_value_is_reflected_on_the_very_next_call(
        self, db_session, tmp_path, monkeypatch
    ):
        """The core "no restart needed" guarantee: two consecutive calls to
        apply_effective_settings, with an upsert_setting in between, must see the new value
        on the second call without re-creating the app or clearing any cache."""
        monkeypatch.delenv("SLSKD_URL", raising=False)
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()

        first = await apply_effective_settings(db_session, settings)
        assert first.slskd_url is None

        await upsert_setting(
            db_session,
            field_name="slskd_url",
            value="http://slskd.internal:5030",
            key=key,
            is_secret=False,
        )

        second = await apply_effective_settings(db_session, settings)
        assert second.slskd_url == "http://slskd.internal:5030"


class TestSeedDbFromEnvOnBoot:
    async def test_seeds_db_row_when_env_var_set(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("QBITTORRENT_URL", "http://qbt.internal:8080")
        settings = _settings(tmp_path)
        await seed_db_from_env_on_boot(db_session, settings)

        monkeypatch.delenv("QBITTORRENT_URL", raising=False)
        effective = await get_effective_settings(db_session, settings)
        assert effective.get("qbittorrent_url") == "http://qbt.internal:8080"

    async def test_does_not_touch_db_row_when_env_var_unset(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("QBITTORRENT_USERNAME", raising=False)
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        await upsert_setting(
            db_session,
            field_name="qbittorrent_username",
            value="existing-db-value",
            key=key,
            is_secret=False,
        )
        await seed_db_from_env_on_boot(db_session, settings)
        effective = await get_effective_settings(db_session, settings)
        assert effective.get("qbittorrent_username") == "existing-db-value"


class TestMaskedSettingsView:
    async def test_secret_field_is_masked_not_plaintext(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        await upsert_setting(
            db_session,
            field_name="prowlarr_api_key",
            value="super-secret-key-value-abcd",
            key=key,
            is_secret=True,
        )
        view = await get_masked_settings_view(db_session, settings)
        assert view["prowlarr"]["prowlarr_api_key"] != "super-secret-key-value-abcd"
        assert "super-secret-key-value-abcd" not in view["prowlarr"]["prowlarr_api_key"]
        assert view["prowlarr"]["prowlarr_api_key"].endswith("abcd")

    async def test_non_secret_field_shown_in_full(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("PROWLARR_URL", raising=False)
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        await upsert_setting(
            db_session,
            field_name="prowlarr_url",
            value="http://prowlarr.internal:9696",
            key=key,
            is_secret=False,
        )
        view = await get_masked_settings_view(db_session, settings)
        assert view["prowlarr"]["prowlarr_url"] == "http://prowlarr.internal:9696"

    async def test_source_tag_reflects_env_vs_db(
        self, db_session, tmp_path, monkeypatch
    ):
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        await upsert_setting(
            db_session,
            field_name="slskd_url",
            value="http://db-value:5030",
            key=key,
            is_secret=False,
        )
        monkeypatch.delenv("SLSKD_URL", raising=False)
        view = await get_masked_settings_view(db_session, settings)
        assert view["slskd"]["slskd_url__source"] == "db"

        monkeypatch.setenv("SLSKD_URL", "http://env-value:5030")
        view = await get_masked_settings_view(db_session, settings)
        assert view["slskd"]["slskd_url__source"] == "env"

    async def test_unset_field_has_unset_source(
        self, db_session, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        settings = _settings(tmp_path)
        view = await get_masked_settings_view(db_session, settings)
        assert view["spotify"]["spotify_client_id__source"] == "unset"
        assert view["spotify"]["spotify_client_id"] is None
