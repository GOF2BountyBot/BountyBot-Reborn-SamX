"""Unit tests for SystemRepository.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers:
- __init__ stores System model
- create_or_update: creates new with lowercase mapping
- create_or_update: updates existing
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
from persist.repositories.system_repository import SystemRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> SystemRepository:
    return SystemRepository()


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
# TestSystemRepositoryInit
# ---------------------------------------------------------------------------


class TestSystemRepositoryInit:
    def test_init_stores_system_model(self, repo):
        """SystemRepository.__init__ must store the System model class."""
        from persist.models.system import System

        assert repo._model is System


# ---------------------------------------------------------------------------
# TestSystemRepositoryCreateOrUpdate
# ---------------------------------------------------------------------------


class TestSystemRepositoryCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_new_system_when_not_found(self, repo, mock_db):
        """create_or_update should create a new System when none exists."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {"name": "Sol", "faction": "Neutral"}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_update_existing_system_when_found(self, repo, mock_db):
        """create_or_update should update an existing System."""
        existing = MagicMock()
        existing.name = "Sol"
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Sol", "faction": "Alliance"}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing

    @pytest.mark.asyncio
    async def test_create_with_lowercase_key_mapping(self, repo, mock_db):
        """create_or_update must lowercase all keys for new System."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockSystem:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.system_repository.System", MockSystem):
            await repo.create_or_update(mock_db, {"name": "Alpha Centauri", "starType": "G"})

        # "starType" should be lowercased to "startype"
        assert "startype" in captured_kwargs
        assert captured_kwargs["startype"] == "G"
        assert "name" in captured_kwargs

    @pytest.mark.asyncio
    async def test_update_applies_lowercase_attrs(self, repo, mock_db):
        """On update, setattr must use lowercased key names."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Vega", "starClass": "A"}
        await repo.create_or_update(mock_db, raw)

        # MagicMock stores setattr results as plain attributes; verify them directly
        assert existing.starclass == "A"
        assert existing.name == "Vega"

    @pytest.mark.asyncio
    async def test_execute_called_once_for_lookup(self, repo, mock_db):
        """create_or_update must execute exactly one SELECT query."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        await repo.create_or_update(mock_db, {"name": "Rigel"})

        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_not_called_on_update(self, repo, mock_db):
        """db.add must NOT be called when updating an existing System."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        await repo.create_or_update(mock_db, {"name": "Sirius"})

        mock_db.add.assert_not_called()
