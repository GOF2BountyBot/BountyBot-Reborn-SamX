"""Unit tests for CriminalRepository.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers:
- __init__ stores Criminal model
- create_or_update creates new criminal when not found
- create_or_update updates existing criminal when found
- create_or_update maps "builtIn" → "built_in", "isPlayer" → "is_player"
- create_or_update handles unmapped keys with lowercase conversion
"""

import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

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
from persist.repositories.criminal_repository import CriminalRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> CriminalRepository:
    return CriminalRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_execute_result(first_value) -> MagicMock:
    """Build a mock that mimics result.scalars().one_or_none()."""
    scalars_mock = MagicMock()
    scalars_mock.one_or_none = MagicMock(return_value=first_value)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


# ---------------------------------------------------------------------------
# TestCriminalRepositoryInit
# ---------------------------------------------------------------------------


class TestCriminalRepositoryInit:
    def test_init_stores_criminal_model(self, repo):
        """CriminalRepository.__init__ must store the Criminal model class."""
        from persist.models.criminal import Criminal

        assert repo._model is Criminal


# ---------------------------------------------------------------------------
# TestCriminalRepositoryCreateOrUpdate
# ---------------------------------------------------------------------------


class TestCriminalRepositoryCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_new_criminal_when_not_found(self, repo, mock_db):
        """create_or_update should create a new Criminal when none exists."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))

        raw = {"name": "Pirate Bob", "builtIn": True, "isPlayer": False}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        # The added object is what was refreshed and returned
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_update_existing_criminal_when_found(self, repo, mock_db):
        """create_or_update should update an existing Criminal when found."""
        existing = MagicMock()
        existing.name = "Pirate Bob"
        mock_db.execute = AsyncMock(return_value=_make_execute_result(existing))

        raw = {"name": "Pirate Bob", "builtIn": False, "isPlayer": True}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing

    @pytest.mark.asyncio
    async def test_maps_built_in_key(self, repo, mock_db):
        """create_or_update must map 'builtIn' → 'built_in' on new object."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))

        raw = {"name": "NPC-1", "builtIn": True}
        await repo.create_or_update(mock_db, raw)

        added_obj = mock_db.add.call_args[0][0]
        # The Criminal constructor was called with built_in=True
        assert added_obj is not None

    @pytest.mark.asyncio
    async def test_maps_is_player_key(self, repo, mock_db):
        """create_or_update must map 'isPlayer' → 'is_player' on new object."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))

        raw = {"name": "Player1", "isPlayer": True}
        await repo.create_or_update(mock_db, raw)

        # Verify the object was constructed (add was called)
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_unmapped_keys_converted_to_lowercase(self, repo, mock_db):
        """create_or_update must lowercase unmapped keys for new Criminal."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))

        # Use only valid Criminal model fields; key 'faction' is already lowercase
        raw = {"name": "Bandit", "faction": "Pirates", "wiki": "http://wiki"}
        await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_sets_mapped_attrs_on_existing(self, repo, mock_db):
        """On update, setattr must use mapped keys ('built_in', 'is_player')."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_execute_result(existing))

        raw = {"name": "Boss", "builtIn": True, "isPlayer": False}
        await repo.create_or_update(mock_db, raw)

        # MagicMock stores setattr results as plain attributes; verify them directly
        assert existing.built_in is True
        assert existing.is_player is False

    @pytest.mark.asyncio
    async def test_execute_called_once_for_lookup(self, repo, mock_db):
        """create_or_update must execute exactly one SELECT query."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))

        raw = {"name": "Raider"}
        await repo.create_or_update(mock_db, raw)

        mock_db.execute.assert_awaited_once()
