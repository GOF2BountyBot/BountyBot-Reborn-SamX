"""Unit tests for InventoryRepository – mock-based (no real database).

Targets the exception-handling paths (except Exception as e: blocks).
Missed lines: 24-26, 37-39, 49-52, 74-76, 94-97, 114-116, 137-139,
              224-226, 248-250, 265-267, 288-290
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
from persist.models.player_inventory import PlayerInventory
from persist.repositories.inventory_repository import InventoryRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inventory(**overrides) -> MagicMock:
    """Return a MagicMock with PlayerInventory-like attributes."""
    defaults = dict(
        id=1,
        player_id=10,
        item_type="weapon",
        item_name="Laser Cannon",
        quantity=1,
        acquired_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    obj = MagicMock(spec=PlayerInventory)
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


def _make_rowcount_result(count: int) -> MagicMock:
    result_mock = MagicMock()
    result_mock.rowcount = count
    return result_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> InventoryRepository:
    return InventoryRepository()


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
    db.flush = AsyncMock()
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
# list_all – exception path (lines 37-39)
# ===================================================================


class TestListAll:
    @pytest.mark.asyncio
    async def test_list_all_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("list fail"))

        with pytest.raises(Exception, match="list fail"):
            await repo.list_all(mock_db)


# ===================================================================
# add – exception path with rollback (lines 49-52)
# ===================================================================


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_exception_triggers_rollback(self, repo, mock_db):
        item = _make_inventory()
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.add(mock_db, item)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# create_or_update – inner commit exception with rollback (lines 74-76)
# ===================================================================


class TestCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_or_update_commit_fail_triggers_rollback(self, repo, mock_db):
        """When updating an existing item, a commit failure should rollback."""
        existing = _make_inventory(player_id=10, item_type="weapon", item_name="Laser Cannon", quantity=1)
        # get_player_item returns existing item
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([existing]))
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.create_or_update(
                mock_db,
                {
                    "player_id": 10,
                    "item_type": "weapon",
                    "item_name": "Laser Cannon",
                    "quantity": 2,
                },
            )

        mock_db.rollback.assert_awaited()


# ===================================================================
# remove – exception path with rollback (lines 94-97)
# ===================================================================


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_exception_triggers_rollback(self, repo, mock_db):
        item = _make_inventory()
        mock_db.commit = AsyncMock(side_effect=Exception("delete fail"))

        with pytest.raises(Exception, match="delete fail"):
            await repo.remove(mock_db, item)

        mock_db.rollback.assert_awaited_once()


# ===================================================================
# get_player_items – exception path (lines 114-116)
# ===================================================================


class TestGetPlayerItems:
    @pytest.mark.asyncio
    async def test_get_player_items_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("player items fail"))

        with pytest.raises(Exception, match="player items fail"):
            await repo.get_player_items(mock_db, player_id=10)

    @pytest.mark.asyncio
    async def test_get_player_items_with_type_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("player items fail"))

        with pytest.raises(Exception, match="player items fail"):
            await repo.get_player_items(mock_db, player_id=10, item_type="weapon")


# ===================================================================
# get_player_item – exception path (lines 137-139)
# ===================================================================


class TestGetPlayerItem:
    @pytest.mark.asyncio
    async def test_get_player_item_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("item lookup fail"))

        with pytest.raises(Exception, match="item lookup fail"):
            await repo.get_player_item(mock_db, player_id=10, item_type="weapon", item_name="Laser Cannon")


# ===================================================================
# update_quantity – inner commit exception with rollback (lines 224-226)
# ===================================================================


class TestUpdateQuantity:
    @pytest.mark.asyncio
    async def test_update_quantity_commit_fail_triggers_rollback(self, repo, mock_db):
        """When commit fails during update_quantity, rollback should be called."""
        _make_inventory(id=1)
        # execute() succeeds (for the UPDATE statement), but commit() fails
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.commit = AsyncMock(side_effect=Exception("commit fail"))

        with pytest.raises(Exception, match="commit fail"):
            await repo.update_quantity(mock_db, inventory_id=1, new_quantity=5)

        mock_db.rollback.assert_awaited()


# ===================================================================
# get_item_count_by_type – exception path (lines 248-250)
# ===================================================================


class TestGetItemCountByType:
    @pytest.mark.asyncio
    async def test_get_item_count_by_type_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("count fail"))

        with pytest.raises(Exception, match="count fail"):
            await repo.get_item_count_by_type(mock_db, player_id=10, item_type="weapon")


# ===================================================================
# clear_player_inventory – exception path (lines 265-267)
# ===================================================================


class TestClearPlayerInventory:
    @pytest.mark.asyncio
    async def test_clear_player_inventory_exception(self, repo, mock_db):
        mock_db.execute = AsyncMock(side_effect=Exception("clear fail"))

        with pytest.raises(Exception, match="clear fail"):
            await repo.clear_player_inventory(mock_db, player_id=10)


# ===================================================================
# get_inventory_summary – exception path (lines 288-290)
# ===================================================================


class TestGetInventorySummary:
    @pytest.mark.asyncio
    async def test_get_inventory_summary_exception(self, repo, mock_db):
        """get_inventory_summary delegates to get_player_items; propagate its exception."""
        mock_db.execute = AsyncMock(side_effect=Exception("summary fail"))

        with pytest.raises(Exception, match="summary fail"):
            await repo.get_inventory_summary(mock_db, player_id=10)

    @pytest.mark.asyncio
    async def test_get_inventory_summary_concrete_types_counted(self, repo, mock_db):
        """Regression guard (DEF-A42-001 / A.36): concrete item types are counted correctly.

        Post-A.36, player_inventories.item_type stores concrete types only.
        get_inventory_summary() must aggregate using concrete keys, not generic aliases.

        This test was staged by the tester to expose the DEF-A42-001 bug where the
        summary dict used generic alias keys ('weapon', 'turret') and silently returned
        0 for all weapon and turret rows (which store 'primary_weapon', 'turret_weapon').
        """
        # Mock inventory rows with concrete types (as A.36 mandates)
        items = [
            _make_inventory(item_type="primary_weapon", item_name="Micro Gun MK I", quantity=2),
            _make_inventory(item_type="turret_weapon", item_name="Raptor Turret", quantity=1),
            _make_inventory(item_type="module", item_name="Nano Repair Kit", quantity=3),
            _make_inventory(item_type="ship", item_name="Betty", quantity=1),
        ]
        mock_db.execute = AsyncMock(return_value=_make_scalars_result(items))

        summary = await repo.get_inventory_summary(mock_db, player_id=10)

        # Concrete type keys — not generic aliases
        assert summary["primary_weapon"] == 2, "primary_weapon must be counted (not under 'weapon')"
        assert summary["turret_weapon"] == 1, "turret_weapon must be counted (not under 'turret')"
        assert summary["module"] == 3
        assert summary["ship"] == 1
        assert summary["secondary_weapon"] == 0  # not present but key must exist
        assert summary["total_items"] == 7  # 2 + 1 + 3 + 1

        # Verify the old generic alias keys are NOT present in the response
        assert "weapon" not in summary, "'weapon' generic alias must NOT be a summary key (A.36)"
        assert "turret" not in summary, "'turret' generic alias must NOT be a summary key (A.36)"
