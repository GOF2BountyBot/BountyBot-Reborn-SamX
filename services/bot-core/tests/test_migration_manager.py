"""
Unit tests for MigrationManager.

All tests are pure unit tests — no live database connection required.
Where behaviour depends on the filesystem or Alembic internals, tests
use lightweight mocking (≤ 2 mocks each, per project conventions).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from src.persist.database.migration_manager import (
    MigrationManager,
    _async_to_sync_url,
    _build_sync_url_from_env,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

SYNC_URL = "postgresql://bounty:bounty@localhost:5432/bountydb"
ASYNC_URL = "postgresql+asyncpg://bounty:bounty@localhost:5432/bountydb"


# ---------------------------------------------------------------------------
# _async_to_sync_url
# ---------------------------------------------------------------------------


class TestAsyncToSyncUrl:
    """Tests for the free-function URL converter."""

    def test_converts_asyncpg_scheme(self) -> None:
        result = _async_to_sync_url(ASYNC_URL)
        assert result == SYNC_URL

    def test_plain_sync_url_is_unchanged(self) -> None:
        result = _async_to_sync_url(SYNC_URL)
        assert result == SYNC_URL

    def test_preserves_credentials_and_path(self) -> None:
        url = "postgresql+asyncpg://user:secret@db-host:9999/mydb"
        expected = "postgresql://user:secret@db-host:9999/mydb"
        assert _async_to_sync_url(url) == expected


# ---------------------------------------------------------------------------
# _build_sync_url_from_env
# ---------------------------------------------------------------------------


class TestBuildSyncUrlFromEnv:
    """Tests for the environment-variable URL builder."""

    def test_uses_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTGRES_HOST", "myhost")
        monkeypatch.setenv("POSTGRES_PORT", "5433")
        monkeypatch.setenv("POSTGRES_DB", "mydb")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")

        url = _build_sync_url_from_env()

        assert url == "postgresql://myuser:mypassword@myhost:5433/mydb"

    def test_falls_back_to_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When env vars are absent the defaults must match the project convention."""
        for key in ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]:
            monkeypatch.delenv(key, raising=False)

        url = _build_sync_url_from_env()

        assert url == "postgresql://bounty:bounty@bounty_db:5432/bountydb"

    def test_url_starts_with_postgresql_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        url = _build_sync_url_from_env()
        assert url.startswith("postgresql://")


# ---------------------------------------------------------------------------
# MigrationManager.__init__
# ---------------------------------------------------------------------------


class TestMigrationManagerInit:
    """Tests for constructor validation."""

    def test_accepts_sync_url(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        assert mgr._sync_url == SYNC_URL

    def test_rejects_asyncpg_url(self) -> None:
        with pytest.raises(ValueError, match="synchronous URL"):
            MigrationManager(ASYNC_URL)

    def test_error_message_mentions_from_async_url(self) -> None:
        with pytest.raises(ValueError, match="from_async_url"):
            MigrationManager(ASYNC_URL)


# ---------------------------------------------------------------------------
# MigrationManager.from_env
# ---------------------------------------------------------------------------


class TestFromEnv:
    def test_builds_manager_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTGRES_HOST", "envhost")
        monkeypatch.setenv("POSTGRES_PORT", "5432")
        monkeypatch.setenv("POSTGRES_DB", "envdb")
        monkeypatch.setenv("POSTGRES_USER", "envuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "envpw")

        mgr = MigrationManager.from_env()

        assert mgr._sync_url == "postgresql://envuser:envpw@envhost:5432/envdb"

    def test_returns_migration_manager_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        mgr = MigrationManager.from_env()
        assert isinstance(mgr, MigrationManager)


# ---------------------------------------------------------------------------
# MigrationManager.from_async_url
# ---------------------------------------------------------------------------


class TestFromAsyncUrl:
    def test_converts_asyncpg_to_sync(self) -> None:
        mgr = MigrationManager.from_async_url(ASYNC_URL)
        assert mgr._sync_url == SYNC_URL

    def test_accepts_already_sync_url(self) -> None:
        mgr = MigrationManager.from_async_url(SYNC_URL)
        assert mgr._sync_url == SYNC_URL


# ---------------------------------------------------------------------------
# MigrationManager._get_alembic_config
# ---------------------------------------------------------------------------


class TestGetAlembicConfig:
    def test_config_has_correct_url(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        cfg = mgr._get_alembic_config()
        assert cfg.get_main_option("sqlalchemy.url") == SYNC_URL

    def test_config_points_to_existing_ini(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        cfg = mgr._get_alembic_config()
        assert cfg.config_file_name is not None
        assert os.path.exists(cfg.config_file_name)

    def test_config_url_matches_manager_url(self) -> None:
        custom_url = "postgresql://admin:secret@db:9999/prod"
        mgr = MigrationManager(custom_url)
        cfg = mgr._get_alembic_config()
        assert cfg.get_main_option("sqlalchemy.url") == custom_url


# ---------------------------------------------------------------------------
# MigrationManager.ensure_current
# ---------------------------------------------------------------------------


class TestEnsureCurrent:
    def test_calls_alembic_upgrade_head(self) -> None:
        """ensure_current() must delegate to alembic.command.upgrade with 'head'."""
        mgr = MigrationManager(SYNC_URL)
        with patch("src.persist.database.migration_manager.command") as mock_cmd:
            mgr.ensure_current()
            mock_cmd.upgrade.assert_called_once()
            _cfg, target = mock_cmd.upgrade.call_args[0]
            assert target == "head"


# ---------------------------------------------------------------------------
# MigrationManager.auto_generate
# ---------------------------------------------------------------------------


class TestAutoGenerate:
    def test_calls_alembic_revision_with_autogenerate(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        with patch("src.persist.database.migration_manager.command") as mock_cmd:
            mgr.auto_generate("add reputation column")
            mock_cmd.revision.assert_called_once()
            kwargs = mock_cmd.revision.call_args[1]
            assert kwargs["message"] == "add reputation column"
            assert kwargs["autogenerate"] is True

    def test_passes_message_to_alembic(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        with patch("src.persist.database.migration_manager.command") as mock_cmd:
            mgr.auto_generate("my migration")
            kwargs = mock_cmd.revision.call_args[1]
            assert kwargs["message"] == "my migration"


# ---------------------------------------------------------------------------
# MigrationManager.downgrade
# ---------------------------------------------------------------------------


class TestDowngrade:
    def test_default_target_is_minus_one(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        with patch("src.persist.database.migration_manager.command") as mock_cmd:
            mgr.downgrade()
            _cfg, target = mock_cmd.downgrade.call_args[0]
            assert target == "-1"

    def test_custom_target_passed_through(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        with patch("src.persist.database.migration_manager.command") as mock_cmd:
            mgr.downgrade("base")
            _cfg, target = mock_cmd.downgrade.call_args[0]
            assert target == "base"


# ---------------------------------------------------------------------------
# MigrationManager.history
# ---------------------------------------------------------------------------


class TestHistory:
    def test_returns_list_of_strings(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        mock_cmd = MagicMock()

        def fake_history(cfg):
            cfg.stdout.write("rev1 -> rev2 (head), initial schema\n")
            cfg.stdout.write("base -> rev1, empty schema\n")

        mock_cmd.history.side_effect = fake_history

        with patch("src.persist.database.migration_manager.command", mock_cmd):
            result = mgr.history()

        assert isinstance(result, list)
        assert len(result) == 2
        assert "rev2" in result[0]

    def test_empty_history_returns_empty_list(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        mock_cmd = MagicMock()
        mock_cmd.history.side_effect = lambda cfg: None  # writes nothing

        with patch("src.persist.database.migration_manager.command", mock_cmd):
            result = mgr.history()

        assert result == []


# ---------------------------------------------------------------------------
# MigrationManager.get_head_revision
# ---------------------------------------------------------------------------


class TestGetHeadRevision:
    def test_returns_first_head(self) -> None:
        """Returns the first element of ScriptDirectory.get_heads()."""
        mgr = MigrationManager(SYNC_URL)
        mock_script = MagicMock()
        mock_script.get_heads.return_value = ["abc123"]

        with patch.object(mgr, "_get_script_directory", return_value=mock_script):
            result = mgr.get_head_revision()

        assert result == "abc123"

    def test_returns_none_when_no_scripts(self) -> None:
        mgr = MigrationManager(SYNC_URL)
        mock_script = MagicMock()
        mock_script.get_heads.return_value = []

        with patch.object(mgr, "_get_script_directory", return_value=mock_script):
            result = mgr.get_head_revision()

        assert result is None
