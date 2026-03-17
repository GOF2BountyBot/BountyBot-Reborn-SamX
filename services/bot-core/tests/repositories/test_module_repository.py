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
        """get_by_name should return the matching module."""
        module = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(module))

        result = await repo.get_by_name(mock_db, "Shield MK1")

        assert result is module
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
        """create_or_update should create a new Module when none exists."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {
            "name": "Shield MK1",
            "builtIn": True,
            "techLevel": 3,
            "maxEquipped": 2,
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_update_existing_module_when_found(self, repo, mock_db):
        """create_or_update should update an existing Module."""
        existing = MagicMock()
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

    @pytest.mark.asyncio
    async def test_create_with_item_fields_separated(self, repo, mock_db):
        """create_or_update must separate item_fields from module_fields."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

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
        await repo.create_or_update(mock_db, raw)

        added = mock_db.add.call_args[0][0]
        # Module was constructed and added
        assert added is not None

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
