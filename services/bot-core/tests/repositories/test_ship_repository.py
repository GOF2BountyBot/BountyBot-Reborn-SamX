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
from types import ModuleType, SimpleNamespace
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
from persist.repositories.ship_repository import ShipRepository, _ship_column_names

# ---------------------------------------------------------------------------
# Order-independence guard: _ship_column_names() lazily caches column names
# on the function object the first time it is called.  Tests that patch
# 'persist.repositories.ship_repository.Ship' with a MockShip MUST not run
# when the cache is None (it would call MockShip.__table__ which raises).
#
# This autouse fixture pre-warms the cache from the real Ship model BEFORE
# each test, then clears it AFTER each test so that each test always starts
# with a populated cache.  Pre-warming is safe here because the real Ship
# model is importable in tests (sqlalchemy_utils is mocked at module top).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _warm_and_reset_ship_column_cache():
    """Pre-populate _ship_column_names._cache from the real Ship, then restore after.

    _ship_column_names() lazy-caches column names on the function object. Tests
    that patch 'persist.repositories.ship_repository.Ship' with a MockShip would
    crash (MockShip.__table__ AttributeError) if the cache is empty when the
    patch is active.  This fixture ensures the cache is always pre-warmed from the
    REAL Ship before each test, and clears it after so subsequent tests start fresh
    and the fixture re-warms correctly.  Order-independence guaranteed.
    """
    from persist.models.ship import Ship  # real model — has __table__

    # Save whatever the cache state is before this test
    _saved_cache = getattr(_ship_column_names, "_cache", None)

    # Pre-warm from the real Ship so MockShip patches never hit the
    # None-cache → Ship.__table__ code path while Ship is patched
    _ship_column_names._cache = {col.name for col in Ship.__table__.columns}  # type: ignore[attr-defined]

    yield

    # Restore original state (typically None → cleared for next test warm-up)
    _ship_column_names._cache = _saved_cache  # type: ignore[attr-defined]


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
        """create_or_update should create a new Ship when none exists, mapping
        camelCase JSON keys onto the real Ship constructor kwargs."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {"name": "Falcon", "builtIn": True}
        with patch("persist.repositories.ship_repository.Ship", MockShip):
            result = await repo.create_or_update(mock_db, raw)

        assert captured_kwargs["name"] == "Falcon"
        assert captured_kwargs["built_in"] is True
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]
        assert result.name == "Falcon"
        assert result.built_in is True

    @pytest.mark.asyncio
    async def test_update_existing_ship_when_found(self, repo, mock_db):
        """create_or_update should update an existing Ship's mapped attrs in place."""
        existing = SimpleNamespace(id=1, name="Falcon", built_in=True)
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Falcon", "builtIn": False}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing
        assert existing.name == "Falcon"
        assert existing.built_in is False

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
    async def test_unknown_keys_routed_to_extra_atts_on_new_ship(self, repo, mock_db):
        """PR-2 L1: unknown JSON keys must land in ``extra_atts`` (not as kwargs).

        Previously the loader did ``setattr(obj, lower(key), value)`` for every
        JSON key — which crashed on insert when the key did not map to a
        column. Combat-rewrite seed enrichment (PR-3) introduces new wiki-sourced
        keys like ``mechanics_text``, ``wiki_status``, ``dlc``, etc.; these
        must be tolerated automatically without per-field code changes.
        """
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.ship_repository.Ship", MockShip):
            await repo.create_or_update(
                mock_db,
                {"name": "Raven", "speed": 100, "mechanics_text": "lore"},
            )

        # Unknown keys (not Ship columns) must NOT be passed as top-level kwargs:
        assert "speed" not in captured_kwargs
        assert "mechanics_text" not in captured_kwargs
        # They must be in extra_atts under their ORIGINAL JSON key name:
        assert "extra_atts" in captured_kwargs
        assert captured_kwargs["extra_atts"]["speed"] == 100
        assert captured_kwargs["extra_atts"]["mechanics_text"] == "lore"

    @pytest.mark.asyncio
    async def test_explicit_extra_atts_in_json_is_honored(self, repo, mock_db):
        """PR-2 L1: if seed JSON carries an explicit ``extra_atts`` blob, its
        contents win on conflict; otherwise it merges with discovered unknowns.
        """
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)

        raw = {
            "name": "Wraith",
            "speed": 999,  # unknown key → discovered extras
            "extra_atts": {"wiki_status": "missing", "speed": 42},  # explicit wins on 'speed'
        }
        with patch("persist.repositories.ship_repository.Ship", MockShip):
            await repo.create_or_update(mock_db, raw)

        ea = captured_kwargs["extra_atts"]
        assert ea["wiki_status"] == "missing"
        # Explicit wins:
        assert ea["speed"] == 42

    @pytest.mark.asyncio
    async def test_extra_atts_omitted_when_only_known_keys_present(self, repo, mock_db):
        """When every JSON key is a Ship column, no ``extra_atts`` kwarg should
        be emitted (avoid spurious empty-dict writes)."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockShip:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)

        with patch("persist.repositories.ship_repository.Ship", MockShip):
            await repo.create_or_update(mock_db, {"name": "Eagle", "builtIn": True})

        assert "extra_atts" not in captured_kwargs
        assert captured_kwargs.get("built_in") is True

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

    @pytest.mark.asyncio
    async def test_raises_value_error_when_name_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'name' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for ship"):
            await repo.create_or_update(mock_db, {"ship_name": "Betty", "player_id": 1})

        mock_db.execute.assert_not_awaited()
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_dict(self, repo, mock_db):
        """create_or_update must raise ValueError for an empty dict."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for ship"):
            await repo.create_or_update(mock_db, {})
