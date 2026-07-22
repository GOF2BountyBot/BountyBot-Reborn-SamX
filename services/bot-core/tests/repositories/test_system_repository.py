"""Unit tests for SystemRepository.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers:
- __init__ stores System model
- create_or_update: creates new with lowercase mapping
- create_or_update: updates existing
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
        """create_or_update should create a new System, lowercasing JSON keys onto
        the real System constructor kwargs."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockSystem:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {"name": "Sol", "faction": "Neutral"}
        with patch("persist.repositories.system_repository.System", MockSystem):
            result = await repo.create_or_update(mock_db, raw)

        assert captured_kwargs["name"] == "Sol"
        assert captured_kwargs["faction"] == "Neutral"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]
        assert result.name == "Sol"
        assert result.faction == "Neutral"

    @pytest.mark.asyncio
    async def test_update_existing_system_when_found(self, repo, mock_db):
        """create_or_update should update an existing System's lowercased attrs in place."""
        existing = SimpleNamespace(id=1, name="Sol", faction="Neutral")
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Sol", "faction": "Alliance"}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing
        assert existing.name == "Sol"
        assert existing.faction == "Alliance"

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
    async def test_raises_value_error_when_name_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'name' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for system"):
            await repo.create_or_update(mock_db, {"faction": "Neutral"})

        mock_db.execute.assert_not_awaited()
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_dict(self, repo, mock_db):
        """create_or_update must raise ValueError for an empty dict."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for system"):
            await repo.create_or_update(mock_db, {})
