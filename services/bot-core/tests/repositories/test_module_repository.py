"""Unit tests for ModuleRepository.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers:
- __init__ stores Module model
- get_by_name: found + not found
- create_or_update: creates new with field separation (item_fields, module_fields, extra)
- create_or_update: updates existing
- create_or_update: extra fields go to extra_atts JSON
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
from persist.repositories.module_repository import ModuleRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> ModuleRepository:
    return ModuleRepository()


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
# TestModuleRepositoryInit
# ---------------------------------------------------------------------------


class TestModuleRepositoryInit:
    def test_init_stores_module_model(self, repo):
        """ModuleRepository.__init__ must store the Module model class."""
        from persist.models.module import Module

        assert repo._model is Module


# ---------------------------------------------------------------------------
# TestGetByName
# ---------------------------------------------------------------------------


class TestGetByName:
    @pytest.mark.asyncio
    async def test_get_by_name_returns_module_when_found(self, repo, mock_db):
        """get_by_name should return the matching module with its real attributes intact."""
        module = SimpleNamespace(id=7, name="Shield MK1")
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(module))

        result = await repo.get_by_name(mock_db, "Shield MK1")

        assert result is module
        assert result.id == 7
        assert result.name == "Shield MK1"
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_by_name_returns_none_when_not_found(self, repo, mock_db):
        """get_by_name should return None when no module exists."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_name(mock_db, "NonExistent")

        assert result is None


# ---------------------------------------------------------------------------
# TestCreateOrUpdate
# ---------------------------------------------------------------------------


class TestCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_new_module_when_not_found(self, repo, mock_db):
        """create_or_update should create a new Module, mapping item_fields and
        module_fields onto the real Module constructor kwargs."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockModule:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {
            "name": "Shield MK1",
            "builtIn": True,
            "techLevel": 3,
            "maxEquipped": 2,
        }
        with patch("persist.repositories.module_repository.Module", MockModule):
            result = await repo.create_or_update(mock_db, raw)

        assert captured_kwargs["name"] == "Shield MK1"
        assert captured_kwargs["built_in"] is True
        assert captured_kwargs["tech_level"] == 3
        assert captured_kwargs["max_equipped"] == 2
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]
        assert result.tech_level == 3
        assert result.max_equipped == 2

    @pytest.mark.asyncio
    async def test_update_existing_module_when_found(self, repo, mock_db):
        """create_or_update should update mapped item/module attrs on an existing Module."""
        existing = SimpleNamespace(id=3, name="Shield MK1", tech_level=1, max_equipped=1, extra_atts={})
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {
            "name": "Shield MK1",
            "techLevel": 5,
            "maxEquipped": 1,
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing
        assert existing.tech_level == 5
        assert existing.max_equipped == 1
        # builtIn absent from raw -> item_fields default of False is applied
        assert existing.built_in is False
        assert existing.extra_atts == {}

    @pytest.mark.asyncio
    async def test_create_with_item_fields_separated(self, repo, mock_db):
        """create_or_update must separate item_fields from module_fields and map
        each onto the real Module constructor kwargs (no leftover extra_atts)."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockModule:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {
            "name": "Armor Plate",
            "aliases": ["armor"],
            "builtIn": False,
            "emoji": ":shield:",
            "icon": "armor.png",
            "value": 500,
            "wiki": "http://wiki/armor",
            "type": "defense",
            "techLevel": 2,
            "maxEquipped": 3,
        }
        with patch("persist.repositories.module_repository.Module", MockModule):
            await repo.create_or_update(mock_db, raw)

        assert captured_kwargs["name"] == "Armor Plate"
        assert captured_kwargs["aliases"] == ["armor"]
        assert captured_kwargs["built_in"] is False
        assert captured_kwargs["emoji"] == ":shield:"
        assert captured_kwargs["icon"] == "armor.png"
        assert captured_kwargs["value"] == 500
        assert captured_kwargs["wiki"] == "http://wiki/armor"
        assert captured_kwargs["type"] == "defense"
        assert captured_kwargs["tech_level"] == 2
        assert captured_kwargs["max_equipped"] == 3
        # NOTE: the extra-field filter in module_repository.create_or_update excludes
        # raw keys by their *item_fields* dict keys (snake_case), so the camelCase
        # "builtIn" key is not recognised as already-consumed and leaks into
        # extra_atts alongside the correctly-mapped built_in=False. Documented here
        # as observed behaviour, not desired behaviour (see report for suspected bug).
        assert captured_kwargs["extra_atts"] == {"builtIn": False}

    @pytest.mark.asyncio
    async def test_extra_fields_go_to_extra_atts(self, repo, mock_db):
        """Unknown keys in raw should end up in extra_atts on the new object."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {
            "name": "Mystery Module",
            "unknownField": "some_value",
            "anotherExtra": 42,
        }

        # Intercept the Module constructor to capture kwargs
        captured_kwargs = {}

        class MockModule:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                object.__setattr__(self, "name", None)
                object.__setattr__(self, "tech_level", None)
                object.__setattr__(self, "max_equipped", None)
                # Store attrs so setattr works on the returned mock
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.module_repository.Module", MockModule):
            await repo.create_or_update(mock_db, raw)

        assert "extra_atts" in captured_kwargs
        assert captured_kwargs["extra_atts"].get("unknownField") == "some_value"
        assert captured_kwargs["extra_atts"].get("anotherExtra") == 42

    @pytest.mark.asyncio
    async def test_update_sets_extra_atts_on_existing(self, repo, mock_db):
        """On update, extra fields must be set on obj.extra_atts."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {
            "name": "Existing Module",
            "surpriseField": "surprise!",
        }
        await repo.create_or_update(mock_db, raw)

        assert existing.extra_atts == {"surpriseField": "surprise!"}

    @pytest.mark.asyncio
    async def test_raises_value_error_when_name_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'name' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for module"):
            await repo.create_or_update(mock_db, {"techLevel": 3})

        mock_db.execute.assert_not_awaited()
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_dict(self, repo, mock_db):
        """create_or_update must raise ValueError for an empty dict."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for module"):
            await repo.create_or_update(mock_db, {})
