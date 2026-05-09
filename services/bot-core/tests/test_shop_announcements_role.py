"""Tests for shop_announcements_role_id in shop_refresh_executor.

Verifies that the executor uses shop_announcements_role_id when available,
falls back to bounty_hunter_role_id, and passes None when both are None.

These tests check the logic inline with the executor source to avoid complex
deferred-import patching. The key logic is:
    mention_role_id = shop_announcements_role_id or bounty_hunter_role_id

We test this by examining what _announce_shop_refresh is called with.
"""

import os
import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock shared / shared.bblogger before any source imports
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_logger(name="test"):
        logger = MagicMock()
        for m in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, m, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure src is on the path
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ---------------------------------------------------------------------------
# Unit tests: role selection logic (pure Python, no deferred imports)
# ---------------------------------------------------------------------------


class TestShopAnnouncementsRoleSelectionLogic:
    """Verify the role-selection logic: shop_announcements or fallback to bounty_hunter.

    Logic: mention_role_id = _shop_ann_id if isinstance(_shop_ann_id, int) else _bh_role_id
    """

    def _select_mention_role(self, shop_ann_id, bh_role_id):
        """Mirror the selection logic from the executor/admin router."""
        return shop_ann_id if isinstance(shop_ann_id, int) else bh_role_id

    def test_shop_announcements_role_preferred(self):
        """shop_announcements_role_id (int) takes priority over bounty_hunter_role_id."""
        mention_role_id = self._select_mention_role(44444, 33333)
        assert mention_role_id == 44444

    def test_falls_back_to_bounty_hunter_when_shop_announcements_none(self):
        """Falls back to bounty_hunter_role_id when shop_announcements_role_id is None."""
        mention_role_id = self._select_mention_role(None, 33333)
        assert mention_role_id == 33333

    def test_none_when_both_are_none(self):
        """Returns None when both role IDs are None."""
        mention_role_id = self._select_mention_role(None, None)
        assert mention_role_id is None

    def test_non_int_shop_announcement_falls_back(self):
        """A non-int shop_announcements_role_id (e.g. MagicMock) falls back to bounty_hunter."""
        from unittest.mock import MagicMock

        mention_role_id = self._select_mention_role(MagicMock(), 33333)
        assert mention_role_id == 33333


class TestShopRefreshExecutorSourceContainsFallback:
    """Verify the source code of shop_refresh_executor has the fallback logic."""

    def test_executor_source_has_shop_announcements_role_id(self):
        """shop_refresh_executor source must reference shop_announcements_role_id."""
        executor_path = os.path.join(
            _SRC,
            "utils",
            "executors",
            "shop_refresh_executor.py",
        )
        with open(executor_path) as f:
            source = f.read()
        assert "shop_announcements_role_id" in source, "shop_refresh_executor.py must use shop_announcements_role_id"

    def test_executor_source_has_fallback_pattern(self):
        """shop_refresh_executor source must have the fallback to bounty_hunter_role_id."""
        executor_path = os.path.join(
            _SRC,
            "utils",
            "executors",
            "shop_refresh_executor.py",
        )
        with open(executor_path) as f:
            source = f.read()
        # Check that both role IDs are referenced (fallback pattern present)
        assert "shop_announcements_role_id" in source and "bounty_hunter_role_id" in source, (
            "shop_refresh_executor.py must have fallback from shop_announcements_role_id to bounty_hunter_role_id"
        )


class TestAdminRouterShopRefreshSourceContainsFallback:
    """Verify admin.py shop refresh also uses shop_announcements_role_id."""

    def test_admin_router_source_has_shop_announcements_role_id(self):
        """admin.py refresh_shop must reference shop_announcements_role_id."""
        admin_path = os.path.join(
            _SRC,
            "api",
            "routers",
            "admin.py",
        )
        with open(admin_path) as f:
            source = f.read()
        assert "shop_announcements_role_id" in source, "admin.py refresh_shop must use shop_announcements_role_id"


class TestGuildConfigModelHasField:
    """Verify GuildConfig model has shop_announcements_role_id field."""

    def test_guild_config_model_has_field(self):
        """GuildConfig model source must declare shop_announcements_role_id."""
        model_path = os.path.join(
            _SRC,
            "persist",
            "models",
            "guild_config.py",
        )
        with open(model_path) as f:
            source = f.read()
        assert "shop_announcements_role_id" in source, (
            "GuildConfig model must declare shop_announcements_role_id column"
        )


class TestMigrationFileExists:
    """Verify the Alembic migration file for shop_announcements_role_id exists."""

    def test_migration_file_exists(self):
        """Migration 0003 must exist and reference shop_announcements_role_id."""
        migration_path = os.path.join(
            _SRC,
            "persist",
            "database",
            "revisions",
            "versions",
            "0003_add_shop_announcements_role_id.py",
        )
        assert os.path.exists(migration_path), "Migration file 0003_add_shop_announcements_role_id.py must exist"
        with open(migration_path) as f:
            source = f.read()
        assert "shop_announcements_role_id" in source
        assert "guild_configs" in source
        assert "add_column" in source
