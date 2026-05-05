"""Unit tests for ConfigRepository – mock-based (no real database).

Targets the exception-handling paths that the integration tests do not reach.
"""

import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE any src imports
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest
from persist.models.guild_config import GuildConfig
from persist.repositories.config_repository import ConfigRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> MagicMock:
    """Return a MagicMock with GuildConfig-like attributes."""
    from datetime import UTC, datetime

    defaults = dict(
        id=1,
        guild_id=100,
        admin_role_id=None,
        ship_count_range={"min": 3, "max": 5},
        weapon_count_range={"min": 3, "max": 5},
        module_count_range={"min": 3, "max": 5},
        turret_count_range={"min": 3, "max": 5},
        ship_quantity_range={"min": 1, "max": 1},
        weapon_quantity_range={"min": 2, "max": 4},
        module_quantity_range={"min": 2, "max": 4},
        turret_quantity_range={"min": 2, "max": 4},
        tech_level_probabilities={"same_level": 0.70, "one_lower": 0.20, "two_lower": 0.10},
        sale_price_factor=0.8,
        starting_credits=0,
        xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000},
        division_temperatures={"bronze": 1.0, "silver": 1.0, "gold": 1.0},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    obj = MagicMock(spec=GuildConfig)
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def _make_scalars_result(items) -> MagicMock:
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=items)
    scalars_mock.first = MagicMock(return_value=items[0] if items else None)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


def _make_scalar_one_result(value) -> MagicMock:
    result_mock = MagicMock()
    result_mock.scalar_one = MagicMock(return_value=value)
    return result_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> ConfigRepository:
    return ConfigRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    db.delete = MagicMock()
    db.rollback = AsyncMock()
    db.get = AsyncMock()
    return db


# ===================================================================
# get_by_id – exception path (lines 25-27)
# ===================================================================


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_by_id_exception(self, repo, mock_db):
        mock_db.get = AsyncMock(side_effect=Exception("DB down"))

        with pytest.raises(Exception, match="DB down"):
            await repo.get_by_id(mock_db, 1)


# ===================================================================
# count – exception path (lines 35-40)
# ===================================================================


class TestCount:
    @pytest.mark.asyncio
    async def test_count_success(self, repo, mock_db):
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(5))
        result = await repo.count(mock_db)
        assert result == 5

    @pytest.mark.asyncio
    async def test_count_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("count error"))

        with pytest.raises(Exception, match="count error"):
            await repo.count(mock_db)


# ===================================================================
# list_all – exception path (lines 47-49)
# ===================================================================


class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("list error"))

        with pytest.raises(Exception, match="list error"):
            await repo.list_all(mock_db)


# ===================================================================
# add – exception path (lines 59-62)
# ===================================================================


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_exception_triggers_rollback(self, repo, mock_db):
        config = _make_config()
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.add(mock_db, config)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# create_or_update – inner exception path (lines 82-84)
# ===================================================================


class TestCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_or_update_commit_fail_on_update_triggers_rollback(self, repo, mock_db):
        existing = _make_config(guild_id=500)
        # get_by_guild_id returns the existing config
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([existing]))
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.create_or_update(mock_db, {"guild_id": 500, "starting_credits": 999})

        mock_db.rollback.assert_awaited()


# ===================================================================
# remove – exception path (lines 101-104)
# ===================================================================


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_exception_triggers_rollback(self, repo, mock_db):
        config = _make_config()
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock(side_effect=Exception("delete fail"))

        with pytest.raises(Exception, match="delete fail"):
            await repo.remove(mock_db, config)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# get_by_guild_id – exception path (lines 113-115)
# ===================================================================


class TestGetByGuildId:
    @pytest.mark.asyncio
    async def test_get_by_guild_id_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("query fail"))

        with pytest.raises(Exception, match="query fail"):
            await repo.get_by_guild_id(mock_db, 100)


# ===================================================================
# create_default_config – exception path (lines 149-151)
# ===================================================================


class TestCreateDefaultConfig:
    @pytest.mark.asyncio
    async def test_create_default_config_exception(self, repo, mock_db):
        # Make create_or_update fail by causing execute to fail on guild lookup
        mock_db.execute = AsyncMock(side_effect=Exception("default fail"))

        with pytest.raises(Exception, match="default fail"):
            await repo.create_default_config(mock_db, guild_id=100)


# ===================================================================
# update_shop_config – inner rollback + outer exception (lines 178-180)
# ===================================================================


class TestUpdateShopConfig:
    @pytest.mark.asyncio
    async def test_update_shop_config_commit_fail_triggers_rollback(self, repo, mock_db):
        config = _make_config(guild_id=200)
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([config]))
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.update_shop_config(
                mock_db,
                {
                    "guild_id": 200,
                    "sale_price_factor": 0.5,
                },
            )

        mock_db.rollback.assert_awaited()


# ===================================================================
# reset_to_defaults – exception path (lines 203-205)
# ===================================================================


class TestResetToDefaults:
    @pytest.mark.asyncio
    async def test_reset_to_defaults_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("reset fail"))

        with pytest.raises(Exception, match="reset fail"):
            await repo.reset_to_defaults(mock_db, guild_id=100)

    @pytest.mark.asyncio
    async def test_reset_to_defaults_preserves_admin_role_id(self, repo, mock_db):
        """B.66: reset_to_defaults must preserve admin_role_id from existing config."""
        existing = _make_config(guild_id=100, admin_role_id=999_111_222)

        # First call (get_by_guild_id for existing) returns the existing config
        # Second call (get_by_guild_id inside create_default_config → create_or_update)
        # returns None so a new config is created
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result([existing]),   # get_by_guild_id → existing
                _make_scalars_result([]),            # create_or_update lookup → not found
            ]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.delete = AsyncMock()
        mock_db.flush = AsyncMock()

        # Track what was passed to setattr on the new config by capturing db.add calls
        created_configs = []

        def capture_add(obj):
            created_configs.append(obj)

        mock_db.add.side_effect = capture_add

        await repo.reset_to_defaults(mock_db, guild_id=100)

        # Verify that admin_role_id was set back on the newly created config
        assert len(created_configs) == 1
        new_config = created_configs[0]
        assert new_config.admin_role_id == 999_111_222

    @pytest.mark.asyncio
    async def test_reset_to_defaults_preserves_channel_ids(self, repo, mock_db):
        """B.66: reset_to_defaults must preserve all channel and role IDs."""
        existing = _make_config(
            guild_id=200,
            admin_role_id=1001,
            category_id=2001,
            shop_channel_id=3001,
            bronze_bounty_channel_id=4001,
            silver_bounty_channel_id=4002,
            gold_bounty_channel_id=4003,
            platinum_bounty_channel_id=4004,
            hunting_channel_id=5001,
            discussion_channel_id=6001,
            image_channel_id=7001,
            bounty_hunter_role_id=8001,
            bronze_role_id=9001,
            silver_role_id=9002,
            gold_role_id=9003,
            platinum_role_id=9004,
        )

        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result([existing]),   # get_by_guild_id → existing
                _make_scalars_result([]),            # create_or_update lookup → not found
            ]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.delete = AsyncMock()
        mock_db.flush = AsyncMock()

        created_configs = []

        def capture_add(obj):
            created_configs.append(obj)

        mock_db.add.side_effect = capture_add

        await repo.reset_to_defaults(mock_db, guild_id=200)

        assert len(created_configs) == 1
        new_config = created_configs[0]

        # All infrastructure fields must be preserved
        assert new_config.admin_role_id == 1001
        assert new_config.category_id == 2001
        assert new_config.shop_channel_id == 3001
        assert new_config.bronze_bounty_channel_id == 4001
        assert new_config.silver_bounty_channel_id == 4002
        assert new_config.gold_bounty_channel_id == 4003
        assert new_config.platinum_bounty_channel_id == 4004
        assert new_config.hunting_channel_id == 5001
        assert new_config.discussion_channel_id == 6001
        assert new_config.image_channel_id == 7001
        assert new_config.bounty_hunter_role_id == 8001
        assert new_config.bronze_role_id == 9001
        assert new_config.silver_role_id == 9002
        assert new_config.gold_role_id == 9003
        assert new_config.platinum_role_id == 9004

    @pytest.mark.asyncio
    async def test_reset_to_defaults_no_existing_config(self, repo, mock_db):
        """B.66: reset_to_defaults with no existing config creates a fresh default."""
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result([]),   # get_by_guild_id → none
                _make_scalars_result([]),   # create_or_update lookup → not found
            ]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.flush = AsyncMock()

        # Should not raise
        await repo.reset_to_defaults(mock_db, guild_id=300)

        # A new config was created (add was called)
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_to_defaults_null_infra_fields_not_preserved(self, repo, mock_db):
        """B.66: None infrastructure fields must NOT be written back (no overwrite of new defaults)."""
        existing = _make_config(guild_id=400, admin_role_id=None, shop_channel_id=None)

        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result([existing]),
                _make_scalars_result([]),
            ]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.delete = AsyncMock()
        mock_db.flush = AsyncMock()

        created_configs = []

        def capture_add(obj):
            created_configs.append(obj)

        mock_db.add.side_effect = capture_add

        await repo.reset_to_defaults(mock_db, guild_id=400)

        assert len(created_configs) == 1
        new_config = created_configs[0]
        # None values should remain as the default (None) not explicitly set
        # The GuildConfig constructor sets admin_role_id=None by default, so this is fine
        assert new_config.admin_role_id is None


# ===================================================================
# update_admin_role – inner rollback + outer (lines 219-221, 226-228)
# ===================================================================


class TestUpdateAdminRole:
    @pytest.mark.asyncio
    async def test_update_admin_role_commit_fail_triggers_rollback(self, repo, mock_db):
        config = _make_config(guild_id=300)
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([config]))
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.update_admin_role(mock_db, guild_id=300, role_id=555)

        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_admin_role_outer_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("lookup fail"))

        with pytest.raises(Exception, match="lookup fail"):
            await repo.update_admin_role(mock_db, guild_id=300, role_id=555)


# ===================================================================
# update_starting_credits – inner rollback + outer (lines 244-246)
# ===================================================================


class TestUpdateStartingCredits:
    @pytest.mark.asyncio
    async def test_update_starting_credits_commit_fail_triggers_rollback(self, repo, mock_db):
        config = _make_config(guild_id=400)
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([config]))
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.update_starting_credits(mock_db, guild_id=400, new_credits=500)

        mock_db.rollback.assert_awaited()


# ===================================================================
# update_xp_thresholds – inner rollback + outer (lines 260, 276-278)
# ===================================================================


class TestUpdateXpThresholds:
    @pytest.mark.asyncio
    async def test_update_xp_thresholds_commit_fail_triggers_rollback(self, repo, mock_db):
        config = _make_config(guild_id=500)
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([config]))
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        thresholds = {"Silver": 500, "Gold": 2000, "Platinum": 10000}

        with pytest.raises(Exception, match="commit fail"):
            await repo.update_xp_thresholds(mock_db, guild_id=500, thresholds=thresholds)

        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_xp_thresholds_outer_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("xp fail"))

        with pytest.raises(Exception, match="xp fail"):
            await repo.update_xp_thresholds(mock_db, guild_id=500, thresholds={})


# ===================================================================
# get_config_summary – exception path (lines 320-322)
# ===================================================================


class TestGetConfigSummary:
    @pytest.mark.asyncio
    async def test_get_config_summary_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("summary fail"))

        with pytest.raises(Exception, match="summary fail"):
            await repo.get_config_summary(mock_db, guild_id=100)


# ===================================================================
# get_all_guild_configs – exception path (lines 339-341)
# ===================================================================


class TestGetAllGuildConfigs:
    @pytest.mark.asyncio
    async def test_get_all_guild_configs_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("all configs fail"))

        with pytest.raises(Exception, match="all configs fail"):
            await repo.get_all_guild_configs(mock_db)


# ===================================================================
# update_division_temperatures – inner rollback + outer (lines 362-384)
# ===================================================================


class TestUpdateDivisionTemperatures:
    @pytest.mark.asyncio
    async def test_update_division_temperatures_success(self, repo, mock_db):
        config = _make_config(guild_id=600)
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([config]))

        result = await repo.update_division_temperatures(
            mock_db, guild_id=600, temperatures={"bronze": 2.0, "silver": 1.5, "gold": 3.0}
        )

        assert result is config
        mock_db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_division_temperatures_returns_none_if_missing(self, repo, mock_db):
        """update_division_temperatures MUST NOT silently auto-create a config row.

        Guild-not-configured guard policy: only /admin_setup creates config
        rows. update_division_temperatures is called from the temperature_decay
        executor which iterates all configured guilds; if the config somehow
        disappeared mid-iteration, the method logs and returns None so the
        executor can keep processing other guilds rather than crash.
        """
        empty_result = _make_scalars_result([])
        mock_db.execute = AsyncMock(return_value=empty_result)

        result = await repo.update_division_temperatures(mock_db, guild_id=600, temperatures={"bronze": 2.0})

        assert result is None
        # Commit must not happen — nothing was changed
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_division_temperatures_commit_fail_triggers_rollback(self, repo, mock_db):
        config = _make_config(guild_id=600)
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([config]))
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.update_division_temperatures(mock_db, guild_id=600, temperatures={"bronze": 2.0})

        mock_db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_division_temperatures_outer_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("temp fail"))

        with pytest.raises(Exception, match="temp fail"):
            await repo.update_division_temperatures(mock_db, guild_id=600, temperatures={})


# ===================================================================
# delete_guild_config – exception path (lines 396-398)
# ===================================================================


class TestDeleteGuildConfig:
    @pytest.mark.asyncio
    async def test_delete_guild_config_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("delete fail"))

        with pytest.raises(Exception, match="delete fail"):
            await repo.delete_guild_config(mock_db, guild_id=100)
