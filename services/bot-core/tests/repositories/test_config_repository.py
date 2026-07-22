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
from persist.models.base import Base
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.repositories.config_repository import ConfigRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


# ---------------------------------------------------------------------------
# Real-SQLite fixtures (mock-true-up): back behavioral round-trip tests where
# MagicMock(spec=GuildConfig) would auto-mock un-defaulted attributes and mask
# preservation gaps. GuildShop is included because GuildConfig.shops carries
# cascade="all, delete-orphan" — reset_to_defaults deletes the existing config,
# which requires the guild_shops table to exist even with zero rows.
# ---------------------------------------------------------------------------

_CONFIG_TABLES = [GuildConfig.__table__, GuildShop.__table__]


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_CONFIG_TABLES)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


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
    async def test_reset_to_defaults_preserves_admin_role_id(self, repo, db_session):
        """B.66: reset_to_defaults must preserve admin_role_id from existing config.

        Real-SQLite round trip (mock-true-up): a MagicMock(spec=GuildConfig) auto-mocks
        every un-defaulted attribute, so a preservation bug (field silently dropped)
        would still read back as a truthy sub-mock instead of failing the assertion.
        """
        existing = GuildConfig(guild_id=100, admin_role_id=999_111_222, starting_credits=777)
        await repo.add(db_session, existing)

        result = await repo.reset_to_defaults(db_session, guild_id=100)

        assert result.admin_role_id == 999_111_222
        # Sanity: a non-preserved game setting actually was reset to its default.
        assert result.starting_credits == 0

        refetched = await repo.get_by_guild_id(db_session, 100)
        assert refetched is not None
        assert refetched.admin_role_id == 999_111_222

    @pytest.mark.asyncio
    async def test_reset_to_defaults_preserves_channel_ids(self, repo, db_session):
        """B.66: reset_to_defaults must preserve all channel and role IDs."""
        existing = GuildConfig(
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
        await repo.add(db_session, existing)

        result = await repo.reset_to_defaults(db_session, guild_id=200)

        # All infrastructure fields must be preserved
        assert result.admin_role_id == 1001
        assert result.category_id == 2001
        assert result.shop_channel_id == 3001
        assert result.bronze_bounty_channel_id == 4001
        assert result.silver_bounty_channel_id == 4002
        assert result.gold_bounty_channel_id == 4003
        assert result.platinum_bounty_channel_id == 4004
        assert result.hunting_channel_id == 5001
        assert result.discussion_channel_id == 6001
        assert result.image_channel_id == 7001
        assert result.bounty_hunter_role_id == 8001
        assert result.bronze_role_id == 9001
        assert result.silver_role_id == 9002
        assert result.gold_role_id == 9003
        assert result.platinum_role_id == 9004

        # Verify persisted, not just returned in-memory.
        refetched = await repo.get_by_guild_id(db_session, 200)
        assert refetched.admin_role_id == 1001
        assert refetched.shop_channel_id == 3001
        assert refetched.platinum_role_id == 9004

    @pytest.mark.asyncio
    async def test_reset_to_defaults_no_existing_config(self, repo, mock_db):
        """B.66: reset_to_defaults with no existing config creates a fresh default."""
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result([]),  # get_by_guild_id → none
                _make_scalars_result([]),  # create_or_update lookup → not found
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
    async def test_reset_to_defaults_null_infra_fields_not_preserved(self, repo, db_session):
        """B.66: None infrastructure fields must NOT be written back (no overwrite of new defaults)."""
        existing = GuildConfig(guild_id=400, admin_role_id=None, shop_channel_id=None)
        await repo.add(db_session, existing)

        result = await repo.reset_to_defaults(db_session, guild_id=400)

        # None values were never preserved (nothing to preserve) — the new default
        # config's own None defaults for these fields remain in place.
        assert result.admin_role_id is None
        assert result.shop_channel_id is None

        refetched = await repo.get_by_guild_id(db_session, 400)
        assert refetched.admin_role_id is None
        assert refetched.shop_channel_id is None


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
        # Real GuildConfig instead of MagicMock(spec=GuildConfig): a real attribute
        # assignment + readback actually proves the value round-trips through the
        # object, rather than a spec'd mock which would accept/return anything.
        config = GuildConfig(guild_id=600)
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([config]))

        result = await repo.update_division_temperatures(
            mock_db, guild_id=600, temperatures={"bronze": 2.0, "silver": 1.5, "gold": 3.0}
        )

        assert result is config
        assert result.division_temperatures == {"bronze": 2.0, "silver": 1.5, "gold": 3.0}
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
