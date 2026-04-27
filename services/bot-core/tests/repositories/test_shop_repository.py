"""Unit tests for ShopRepository – mock-based (no real database).

Targets exception-handling paths that integration tests do not reach.
"""

import os
import sys
from datetime import UTC, datetime
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
from persist.models.guild_shop import GuildShop
from persist.repositories.shop_repository import ShopRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shop_item(**overrides) -> MagicMock:
    defaults = dict(
        id=1,
        guild_id=100,
        tier="Bronze",
        tech_level=3,
        item_type="weapon",
        item_name="Laser MK2",
        quantity=5,
        price=1000,
        last_restocked=datetime.now(UTC),
        refresh_interval_hours=12,
    )
    defaults.update(overrides)
    obj = MagicMock(spec=GuildShop)
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
def repo() -> ShopRepository:
    return ShopRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    db.delete = AsyncMock()
    db.rollback = AsyncMock()
    db.get = AsyncMock()
    return db


# ===================================================================
# get_by_id – exception path (lines 24-26)
# ===================================================================


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_by_id_exception(self, repo, mock_db):
        mock_db.get = AsyncMock(side_effect=Exception("DB down"))
        with pytest.raises(Exception, match="DB down"):
            await repo.get_by_id(mock_db, 1)


# ===================================================================
# count – exception path (lines 34-39)
# ===================================================================


class TestCount:
    @pytest.mark.asyncio
    async def test_count_success(self, repo, mock_db):
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_result(10))
        result = await repo.count(mock_db)
        assert result == 10

    @pytest.mark.asyncio
    async def test_count_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("count fail"))
        with pytest.raises(Exception, match="count fail"):
            await repo.count(mock_db)


# ===================================================================
# list_all – exception path (lines 46-48)
# ===================================================================


class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("list fail"))
        with pytest.raises(Exception, match="list fail"):
            await repo.list_all(mock_db)


# ===================================================================
# add – exception path (lines 58-61)
# ===================================================================


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_exception_triggers_rollback(self, repo, mock_db):
        item = _make_shop_item()
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))
        with pytest.raises(Exception, match="commit fail"):
            await repo.add(mock_db, item)
        mock_db.rollback.assert_awaited_once()


# ===================================================================
# create_or_update – exception paths (lines 100-103)
# ===================================================================


class TestCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_or_update_commit_fail_on_existing(self, repo, mock_db):
        existing = _make_shop_item(guild_id=100, tier="Bronze", item_name="Laser")
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([existing]))
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.create_or_update(
                mock_db, {"guild_id": 100, "tier": "Bronze", "item_name": "Laser", "quantity": 10}
            )


# ===================================================================
# get_shop_items – exception path (lines 127-129)
# ===================================================================


class TestGetShopItems:
    @pytest.mark.asyncio
    async def test_get_shop_items_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("query fail"))
        with pytest.raises(Exception, match="query fail"):
            await repo.get_shop_items(mock_db, guild_id=100, tier="Bronze")


# ===================================================================
# get_shop_item_by_name – exception path (lines 150-152)
# ===================================================================


class TestGetShopItemByName:
    @pytest.mark.asyncio
    async def test_get_shop_item_by_name_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("lookup fail"))
        with pytest.raises(Exception, match="lookup fail"):
            await repo.get_shop_item_by_name(mock_db, 100, "Bronze", "Laser")


# ===================================================================
# update_quantity – exception path (lines 188-191)
# ===================================================================


class TestUpdateQuantity:
    @pytest.mark.asyncio
    async def test_update_quantity_exception(self, repo, mock_db):
        # Post Option-B refactor (2026-04-27): repo no longer issues a Core UPDATE.
        # Failure surface is db.commit (the ORM-tracked attribute mutation flushes
        # on commit). db.execute is no longer used by this method.
        from unittest.mock import MagicMock

        item = MagicMock()
        item.id = 1
        item.quantity = 0
        mock_db.get = AsyncMock(return_value=item)
        mock_db.commit = AsyncMock(side_effect=Exception("update fail"))
        with pytest.raises(Exception, match="update fail"):
            await repo.update_quantity(mock_db, shop_item_id=1, new_quantity=5)
        mock_db.rollback.assert_awaited()


# ===================================================================
# clear_shop_tier – exception path (lines 201-204)
# ===================================================================


class TestClearShopTier:
    @pytest.mark.asyncio
    async def test_clear_shop_tier_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("clear fail"))
        with pytest.raises(Exception, match="clear fail"):
            await repo.clear_shop_tier(mock_db, guild_id=100, tier="Bronze")
        mock_db.rollback.assert_awaited()


# ===================================================================
# clear_all_guild_shops – exception path (lines 231-233)
# ===================================================================


class TestClearAllGuildShops:
    @pytest.mark.asyncio
    async def test_clear_all_guild_shops_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("clear all fail"))
        with pytest.raises(Exception, match="clear all fail"):
            await repo.clear_all_guild_shops(mock_db, guild_id=100)
        mock_db.rollback.assert_awaited()


# ===================================================================
# get_guild_shops_summary – exception path (lines 254-256)
# ===================================================================


class TestGetGuildShopsSummary:
    @pytest.mark.asyncio
    async def test_get_guild_shops_summary_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("summary fail"))
        with pytest.raises(Exception, match="summary fail"):
            await repo.get_guild_shops_summary(mock_db, guild_id=100)


# ===================================================================
# get_items_due_for_refresh – exception path (lines 281-294)
# ===================================================================


class TestGetItemsDueForRefresh:
    @pytest.mark.asyncio
    async def test_get_items_due_for_refresh_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("refresh fail"))
        with pytest.raises(Exception, match="refresh fail"):
            await repo.get_items_due_for_refresh(mock_db, guild_id=100)


# ===================================================================
# get_shop_statistics – exception path (lines 324-326)
# ===================================================================


class TestGetShopStatistics:
    @pytest.mark.asyncio
    async def test_get_shop_statistics_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("stats fail"))
        with pytest.raises(Exception, match="stats fail"):
            await repo.get_shop_statistics(mock_db, guild_id=100, tier="Bronze")
