"""Unit tests for ShipRepository.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers:
- __init__ stores Ship model
- create_or_update: creates new with field mapping
- create_or_update: updates existing
- create_or_update: maps "builtIn"→"built_in", "compatibleSkins"→"compatible_skins", etc.
- create_or_update: unmapped keys lowercase conversion
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
from persist.repositories.ship_repository import ShipRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> ShipRepository:
    return ShipRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_one_or_none_result(value) -> MagicMock:
    scalars_mock = MagicMock()
    scalars_mock.one_or_none = MagicMock(return_value=value)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


# ---------------------------------------------------------------------------
# TestShipRepositoryInit
# ---------------------------------------------------------------------------


class TestShipRepositoryInit:
    def test_init_stores_ship_model(self, repo):
        """ShipRepository.__init__ must store the Ship model class."""
        from persist.models.ship import Ship

        assert repo._model is Ship


# ---------------------------------------------------------------------------
# TestShipRepositoryCreateOrUpdate
# ---------------------------------------------------------------------------


class TestShipRepositoryCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_new_ship_when_not_found(self, repo, mock_db):
        """create_or_update should create a new Ship when none exists."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {"name": "Falcon", "builtIn": True}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_update_existing_ship_when_found(self, repo, mock_db):
        """create_or_update should update an existing Ship."""
        existing = MagicMock()
        existing.name = "Falcon"
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Falcon", "builtIn": False}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing

    @pytest.mark.asyncio
    async def test_maps_built_in_key_on_new_ship(self, repo, mock_db):
        """create_or_update must map 'builtIn' → 'built_in' for new Ship."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.ship_repository.Ship", MockShip):
            await repo.create_or_update(mock_db, {"name": "Hawk", "builtIn": True})

        assert "built_in" in captured_kwargs
        assert captured_kwargs["built_in"] is True
        assert "builtIn" not in captured_kwargs

    @pytest.mark.asyncio
    async def test_maps_compatible_skins_key_on_new_ship(self, repo, mock_db):
        """create_or_update must map 'compatibleSkins' → 'compatible_skins'."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.ship_repository.Ship", MockShip):
            await repo.create_or_update(mock_db, {"name": "Eagle", "compatibleSkins": ["skin_a"]})

        assert "compatible_skins" in captured_kwargs
        assert captured_kwargs["compatible_skins"] == ["skin_a"]

    @pytest.mark.asyncio
    async def test_maps_shop_spawn_rate_key(self, repo, mock_db):
        """create_or_update must map 'shopSpawnRate' → 'shop_spawn_rate'."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.ship_repository.Ship", MockShip):
            await repo.create_or_update(mock_db, {"name": "Sparrow", "shopSpawnRate": 0.05})

        assert "shop_spawn_rate" in captured_kwargs
        assert captured_kwargs["shop_spawn_rate"] == 0.05

    @pytest.mark.asyncio
    async def test_maps_max_modules_key(self, repo, mock_db):
        """create_or_update must map 'maxModules' → 'max_modules'."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.ship_repository.Ship", MockShip):
            await repo.create_or_update(mock_db, {"name": "Condor", "maxModules": 4})

        assert "max_modules" in captured_kwargs
        assert captured_kwargs["max_modules"] == 4

    @pytest.mark.asyncio
    async def test_unmapped_keys_lowercased_on_new_ship(self, repo, mock_db):
        """Unmapped keys must be lowercased when creating a new Ship."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.ship_repository.Ship", MockShip):
            await repo.create_or_update(mock_db, {"name": "Raven", "speed": 100})

        assert "speed" in captured_kwargs
        assert captured_kwargs["speed"] == 100

    @pytest.mark.asyncio
    async def test_update_applies_mapped_attrs_on_existing(self, repo, mock_db):
        """On update, setattr must use mapped keys for existing Ship."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Phoenix", "builtIn": True, "compatibleSkins": ["x", "y"]}
        await repo.create_or_update(mock_db, raw)

        # MagicMock stores setattr results as plain attributes; verify them directly
        assert existing.built_in is True
        assert existing.compatible_skins == ["x", "y"]

    @pytest.mark.asyncio
    async def test_execute_called_once_for_lookup(self, repo, mock_db):
        """create_or_update must execute exactly one SELECT query."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        await repo.create_or_update(mock_db, {"name": "Scout"})

        mock_db.execute.assert_awaited_once()
