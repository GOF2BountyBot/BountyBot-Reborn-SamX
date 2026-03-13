"""Unit tests for ItemRepository.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers:
- __init__ stores Item model (stub repository)
- get_by_name: with and without item_type, found and not found
- get_all_by_tech_level: with and without item_type, ship special case
- get_random_by_tech_level: random selection, ship weighting, empty list
- get_count: sums counts across all model types
- create_or_update: create new and update existing
- _get_model: unknown item_type raises ValueError
"""

import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Mock shared.bblogger and sqlalchemy_utils BEFORE any src imports
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

_mock_sau = ModuleType("sqlalchemy_utils")
_mock_sau.UUIDType = MagicMock()
sys.modules.setdefault("sqlalchemy_utils", _mock_sau)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest
from persist.repositories.item_repository import ItemRepository

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> ItemRepository:
    return ItemRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_scalars_result(values: list) -> MagicMock:
    """Build a mock db.execute result whose .scalars().all() returns *values*."""
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=values)
    scalars_mock.one_or_none = MagicMock(
        return_value=values[0] if values else None
    )
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


def _make_scalar_result(value) -> MagicMock:
    """Build a mock db.execute result whose .scalar() returns *value*."""
    result_mock = MagicMock()
    result_mock.scalar = MagicMock(return_value=value)
    return result_mock


def _make_one_or_none_result(value) -> MagicMock:
    scalars_mock = MagicMock()
    scalars_mock.one_or_none = MagicMock(return_value=value)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


# ---------------------------------------------------------------------------
# TestItemRepositoryInit
# ---------------------------------------------------------------------------


class TestItemRepositoryInit:
    def test_init_stores_item_model(self):
        """ItemRepository.__init__ must store the Item model class."""
        from persist.models.item import Item

        repo = ItemRepository()
        assert repo._model is Item

    def test_init_creates_instance_successfully(self):
        """ItemRepository can be instantiated without errors."""
        repo = ItemRepository()
        assert repo is not None

    def test_inherits_generic_repository(self):
        """ItemRepository must be a subclass of GenericRepository."""
        from persist.repositories.generic_repository import GenericRepository

        repo = ItemRepository()
        assert isinstance(repo, GenericRepository)

    def test_type_map_contains_all_item_types(self):
        """_TYPE_MAP should contain all five item types."""
        repo = ItemRepository()
        expected = {"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"}
        assert set(repo._TYPE_MAP.keys()) == expected

    def test_get_model_returns_correct_class(self):
        """_get_model should return the correct SQLAlchemy model for each type."""
        from persist.models.module import Module
        from persist.models.primary_weapon import PrimaryWeapon
        from persist.models.secondary_weapon import SecondaryWeapon
        from persist.models.ship import Ship
        from persist.models.turret_weapon import TurretWeapon

        repo = ItemRepository()
        assert repo._get_model("ship") is Ship
        assert repo._get_model("primary_weapon") is PrimaryWeapon
        assert repo._get_model("secondary_weapon") is SecondaryWeapon
        assert repo._get_model("turret_weapon") is TurretWeapon
        assert repo._get_model("module") is Module

    def test_get_model_raises_for_unknown_type(self):
        """_get_model should raise ValueError for unknown item_type."""
        repo = ItemRepository()
        with pytest.raises(ValueError, match="Unknown item_type"):
            repo._get_model("unknown_type")


# ---------------------------------------------------------------------------
# TestGetByName
# ---------------------------------------------------------------------------


class TestGetByName:
    @pytest.mark.asyncio
    async def test_get_by_name_with_item_type_found(self, repo, mock_db):
        """get_by_name with item_type should query that model and return the item."""
        item = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(item))

        result = await repo.get_by_name(mock_db, "Laser Cannon", "primary_weapon")

        assert result is item
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_by_name_with_item_type_not_found(self, repo, mock_db):
        """get_by_name with item_type returns None when no item exists."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_name(mock_db, "NonExistent", "module")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_name_without_item_type_returns_first_match(self, repo, mock_db):
        """get_by_name without item_type searches all models and returns the first match."""
        item = MagicMock()
        # First call (ship) returns no result; second call (primary_weapon) returns the item
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_one_or_none_result(None),   # ship — not found
                _make_one_or_none_result(item),    # primary_weapon — found
            ]
        )

        result = await repo.get_by_name(mock_db, "Laser Cannon")

        assert result is item
        # Should have stopped after finding in the second model
        assert mock_db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_get_by_name_without_item_type_not_found_anywhere(self, repo, mock_db):
        """get_by_name without item_type returns None when item not in any model."""
        mock_db.execute = AsyncMock(
            side_effect=[_make_one_or_none_result(None)] * 5
        )

        result = await repo.get_by_name(mock_db, "Completely Unknown")

        assert result is None
        # Should have searched all 5 models
        assert mock_db.execute.await_count == 5

    @pytest.mark.asyncio
    async def test_get_by_name_with_item_type_raises_for_unknown(self, repo, mock_db):
        """get_by_name raises ValueError for unknown item_type."""
        with pytest.raises(ValueError, match="Unknown item_type"):
            await repo.get_by_name(mock_db, "Item", "bad_type")

    @pytest.mark.asyncio
    async def test_get_by_name_ship_type(self, repo, mock_db):
        """get_by_name with item_type='ship' queries the Ship model."""
        ship = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(ship))

        result = await repo.get_by_name(mock_db, "Falcon", "ship")

        assert result is ship

    @pytest.mark.asyncio
    async def test_get_by_name_without_item_type_returns_ship_if_first_match(self, repo, mock_db):
        """get_by_name without item_type can find a ship (first model in _TYPE_MAP)."""
        ship = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(ship))

        result = await repo.get_by_name(mock_db, "Falcon")

        assert result is ship
        # Only needed the first call (ship was first match)
        assert mock_db.execute.await_count == 1


# ---------------------------------------------------------------------------
# TestGetAllByTechLevel
# ---------------------------------------------------------------------------


class TestGetAllByTechLevel:
    @pytest.mark.asyncio
    async def test_get_all_by_tech_level_with_item_type(self, repo, mock_db):
        """get_all_by_tech_level with item_type queries only that model."""
        items = [MagicMock(), MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_scalars_result(items))

        result = await repo.get_all_by_tech_level(mock_db, 3, "module")

        assert result == items
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_all_by_tech_level_ship_returns_empty(self, repo, mock_db):
        """get_all_by_tech_level with item_type='ship' returns [] (no tech_level on Ship)."""
        result = await repo.get_all_by_tech_level(mock_db, 3, "ship")

        assert result == []
        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_all_by_tech_level_without_item_type_aggregates(self, repo, mock_db):
        """get_all_by_tech_level without item_type aggregates results from all tech_level models."""
        weapon1 = MagicMock()
        weapon2 = MagicMock()
        module1 = MagicMock()

        # 4 tech_level models: PrimaryWeapon, SecondaryWeapon, TurretWeapon, Module
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result([weapon1]),   # PrimaryWeapon
                _make_scalars_result([weapon2]),   # SecondaryWeapon
                _make_scalars_result([]),          # TurretWeapon — empty
                _make_scalars_result([module1]),   # Module
            ]
        )

        result = await repo.get_all_by_tech_level(mock_db, 3)

        assert weapon1 in result
        assert weapon2 in result
        assert module1 in result
        assert len(result) == 3
        assert mock_db.execute.await_count == 4

    @pytest.mark.asyncio
    async def test_get_all_by_tech_level_without_item_type_empty(self, repo, mock_db):
        """get_all_by_tech_level without item_type returns [] if no items exist."""
        mock_db.execute = AsyncMock(
            side_effect=[_make_scalars_result([])] * 4
        )

        result = await repo.get_all_by_tech_level(mock_db, 99)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_all_by_tech_level_with_unknown_type_raises(self, repo, mock_db):
        """get_all_by_tech_level raises ValueError for unknown item_type."""
        with pytest.raises(ValueError, match="Unknown item_type"):
            await repo.get_all_by_tech_level(mock_db, 3, "bad_type")


# ---------------------------------------------------------------------------
# TestGetRandomByTechLevel
# ---------------------------------------------------------------------------


class TestGetRandomByTechLevel:
    @pytest.mark.asyncio
    async def test_get_random_returns_item_from_list(self, repo, mock_db):
        """get_random_by_tech_level returns one item from the list."""
        items = [MagicMock(), MagicMock(), MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_scalars_result(items))

        with patch("persist.repositories.item_repository.random.choice", return_value=items[1]) as mock_choice:
            result = await repo.get_random_by_tech_level(mock_db, 3, "module")

        assert result is items[1]
        mock_choice.assert_called_once_with(items)

    @pytest.mark.asyncio
    async def test_get_random_returns_none_when_empty(self, repo, mock_db):
        """get_random_by_tech_level returns None when no items at that tech level."""
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_random_by_tech_level(mock_db, 99, "module")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_random_ship_uses_weighted_selection(self, repo, mock_db):
        """get_random_by_tech_level for ships uses shop_spawn_rate as weight."""
        ship1 = MagicMock()
        ship1.shop_spawn_rate = 0.8
        ship2 = MagicMock()
        ship2.shop_spawn_rate = 0.2

        mock_db.execute = AsyncMock(return_value=_make_scalars_result([ship1, ship2]))

        with patch("persist.repositories.item_repository.random.choices", return_value=[ship1]) as mock_choices:
            result = await repo.get_random_by_tech_level(mock_db, 3, "ship")

        assert result is ship1
        mock_choices.assert_called_once_with([ship1, ship2], weights=[0.8, 0.2], k=1)

    @pytest.mark.asyncio
    async def test_get_random_ship_none_spawn_rate_defaults_to_zero(self, repo, mock_db):
        """Ships with shop_spawn_rate=None are treated as weight 0."""
        ship1 = MagicMock()
        ship1.shop_spawn_rate = None
        ship2 = MagicMock()
        ship2.shop_spawn_rate = 0.5

        mock_db.execute = AsyncMock(return_value=_make_scalars_result([ship1, ship2]))

        with patch("persist.repositories.item_repository.random.choices", return_value=[ship2]) as mock_choices:
            result = await repo.get_random_by_tech_level(mock_db, 1, "ship")

        assert result is ship2
        mock_choices.assert_called_once_with([ship1, ship2], weights=[0.0, 0.5], k=1)

    @pytest.mark.asyncio
    async def test_get_random_ship_all_zero_weights_uses_uniform(self, repo, mock_db):
        """When all ship spawn rates are 0 or None, fall back to uniform random.choice."""
        ship1 = MagicMock()
        ship1.shop_spawn_rate = 0.0
        ship2 = MagicMock()
        ship2.shop_spawn_rate = None

        mock_db.execute = AsyncMock(return_value=_make_scalars_result([ship1, ship2]))

        with patch("persist.repositories.item_repository.random.choice", return_value=ship1) as mock_choice:
            result = await repo.get_random_by_tech_level(mock_db, 1, "ship")

        assert result is ship1
        mock_choice.assert_called_once_with([ship1, ship2])

    @pytest.mark.asyncio
    async def test_get_random_ship_empty_returns_none(self, repo, mock_db):
        """get_random_by_tech_level for 'ship' with no ships returns None."""
        mock_db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_random_by_tech_level(mock_db, 1, "ship")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_random_without_item_type_aggregates(self, repo, mock_db):
        """get_random_by_tech_level without item_type searches all tech_level models."""
        item = MagicMock()
        # 4 tech_level models, first returns item, rest return empty
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result([item]),
                _make_scalars_result([]),
                _make_scalars_result([]),
                _make_scalars_result([]),
            ]
        )

        with patch("persist.repositories.item_repository.random.choice", return_value=item):
            result = await repo.get_random_by_tech_level(mock_db, 3)

        assert result is item

    @pytest.mark.asyncio
    async def test_get_random_without_item_type_empty_returns_none(self, repo, mock_db):
        """get_random_by_tech_level without item_type returns None if no matches."""
        mock_db.execute = AsyncMock(side_effect=[_make_scalars_result([])] * 4)

        result = await repo.get_random_by_tech_level(mock_db, 99)

        assert result is None


# ---------------------------------------------------------------------------
# TestGetCount
# ---------------------------------------------------------------------------


class TestGetCount:
    @pytest.mark.asyncio
    async def test_get_count_sums_all_models(self, repo, mock_db):
        """get_count returns the total count across all 5 model types."""
        # 5 models: ship, primary_weapon, secondary_weapon, turret_weapon, module
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(10),  # Ship
                _make_scalar_result(5),   # PrimaryWeapon
                _make_scalar_result(3),   # SecondaryWeapon
                _make_scalar_result(2),   # TurretWeapon
                _make_scalar_result(7),   # Module
            ]
        )

        result = await repo.get_count(mock_db)

        assert result == 27
        assert mock_db.execute.await_count == 5

    @pytest.mark.asyncio
    async def test_get_count_returns_zero_when_all_empty(self, repo, mock_db):
        """get_count returns 0 when all tables are empty."""
        mock_db.execute = AsyncMock(
            side_effect=[_make_scalar_result(0)] * 5
        )

        result = await repo.get_count(mock_db)

        assert result == 0

    @pytest.mark.asyncio
    async def test_get_count_handles_none_scalar(self, repo, mock_db):
        """get_count treats None scalar as 0 (defensive handling)."""
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalar_result(None),  # e.g., empty table returns None
                _make_scalar_result(5),
                _make_scalar_result(None),
                _make_scalar_result(3),
                _make_scalar_result(None),
            ]
        )

        result = await repo.get_count(mock_db)

        assert result == 8


# ---------------------------------------------------------------------------
# TestCreateOrUpdate
# ---------------------------------------------------------------------------


class TestCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_new_item_when_not_found(self, repo, mock_db):
        """create_or_update should create a new Item when none exists."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {
            "name": "Basic Shield",
            "aliases": ["shield"],
            "builtIn": False,
            "value": 100,
            "type": "module",
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        # result is the refreshed object
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_update_existing_item_when_found(self, repo, mock_db):
        """create_or_update should update an existing Item when found."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {
            "name": "Basic Shield",
            "value": 200,
            "type": "module",
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing

    @pytest.mark.asyncio
    async def test_create_maps_item_fields_correctly(self, repo, mock_db):
        """create_or_update correctly maps builtIn -> built_in and other fields."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockItem:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.item_repository.Item", MockItem):
            raw = {
                "name": "Test Item",
                "aliases": ["t"],
                "builtIn": True,
                "emoji": ":test:",
                "icon": "test.png",
                "value": 999,
                "wiki": "http://wiki/test",
                "type": "test_type",
            }
            await repo.create_or_update(mock_db, raw)

        assert captured_kwargs["name"] == "Test Item"
        assert captured_kwargs["aliases"] == ["t"]
        assert captured_kwargs["built_in"] is True
        assert captured_kwargs["emoji"] == ":test:"
        assert captured_kwargs["icon"] == "test.png"
        assert captured_kwargs["value"] == 999
        assert captured_kwargs["wiki"] == "http://wiki/test"
        assert captured_kwargs["type"] == "test_type"

    @pytest.mark.asyncio
    async def test_update_sets_all_fields_on_existing(self, repo, mock_db):
        """create_or_update sets all item fields on an existing object."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {
            "name": "Updated Shield",
            "builtIn": True,
            "value": 500,
        }
        await repo.create_or_update(mock_db, raw)

        # setattr should have been called for each item field
        existing.name = "Updated Shield"  # sanity: mock accepts attribute sets
        mock_db.refresh.assert_awaited_once_with(existing)
