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
    async def test_update_division_temperatures_creates_if_missing(self, repo, mock_db):
        # First call to get_by_guild_id returns None, subsequent calls for create_default return config
        empty_result = _make_scalars_result([])
        config = _make_config(guild_id=600)
        full_result = _make_scalars_result([config])

        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return empty_result
            return full_result

        mock_db.execute = AsyncMock(side_effect=_side_effect)

        result = await repo.update_division_temperatures(mock_db, guild_id=600, temperatures={"bronze": 2.0})

        assert result is not None

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
