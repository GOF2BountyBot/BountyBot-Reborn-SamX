"""Tests for CriminalRepository.

Criminal has an ARRAY column (`aliases`), so a full SQLite round-trip is not
feasible. Instead, create_or_update's key-remapping is verified with a
kwarg-capturing fake patched over the real Criminal constructor (the pattern
the ship/module/system tests use): we assert the ACTUAL remapped kwargs
(`built_in`, `is_player`, lowercased keys) rather than merely that `add` was
called. The update branch uses a real-attribute stand-in so the setattr'd
values are directly assertable.
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
from persist.repositories.criminal_repository import CriminalRepository

# ---------------------------------------------------------------------------
# Fixtures / helpers
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
    db.rollback = AsyncMock()
    return db


def _make_execute_result(first_value) -> MagicMock:
    """Build a mock that mimics result.scalars().one_or_none()."""
    scalars_mock = MagicMock()
    scalars_mock.one_or_none = MagicMock(return_value=first_value)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


def _capturing_criminal():
    """Return (captured_kwargs, FakeCriminal) — FakeCriminal records its ctor kwargs
    and exposes them as real attributes so the returned object is assertable."""
    captured: dict = {}

    class FakeCriminal:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            object.__setattr__(self, "id", None)
            for k, v in kwargs.items():
                object.__setattr__(self, k, v)

    return captured, FakeCriminal


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
    async def test_create_new_criminal_maps_keys(self, repo, mock_db):
        """create_or_update constructs a new Criminal with remapped kwargs when none exists."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))
        captured, FakeCriminal = _capturing_criminal()

        raw = {"name": "Pirate Bob", "builtIn": True, "isPlayer": False}
        with patch("persist.repositories.criminal_repository.Criminal", FakeCriminal):
            result = await repo.create_or_update(mock_db, raw)

        # Real remapped kwargs — the mapping is genuinely exercised.
        assert captured["name"] == "Pirate Bob"
        assert captured["built_in"] is True
        assert captured["is_player"] is False
        assert "builtIn" not in captured
        assert "isPlayer" not in captured
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_update_existing_criminal_sets_mapped_attrs(self, repo, mock_db):
        """create_or_update updates an existing Criminal's remapped attrs in place."""
        existing = SimpleNamespace(id=1, name="Pirate Bob", built_in=True, is_player=False)
        mock_db.execute = AsyncMock(return_value=_make_execute_result(existing))

        raw = {"name": "Pirate Bob", "builtIn": False, "isPlayer": True}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing
        # Mapped attrs were applied to the existing object.
        assert existing.built_in is False
        assert existing.is_player is True

    @pytest.mark.asyncio
    async def test_maps_built_in_key(self, repo, mock_db):
        """'builtIn' → 'built_in' on the constructed object."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))
        captured, FakeCriminal = _capturing_criminal()

        with patch("persist.repositories.criminal_repository.Criminal", FakeCriminal):
            await repo.create_or_update(mock_db, {"name": "NPC-1", "builtIn": True})

        assert captured["built_in"] is True
        assert "builtIn" not in captured

    @pytest.mark.asyncio
    async def test_maps_is_player_key(self, repo, mock_db):
        """'isPlayer' → 'is_player' on the constructed object."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))
        captured, FakeCriminal = _capturing_criminal()

        with patch("persist.repositories.criminal_repository.Criminal", FakeCriminal):
            await repo.create_or_update(mock_db, {"name": "Player1", "isPlayer": True})

        assert captured["is_player"] is True
        assert "isPlayer" not in captured

    @pytest.mark.asyncio
    async def test_unmapped_keys_converted_to_lowercase(self, repo, mock_db):
        """Unmapped keys are lowercased for the new Criminal constructor."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))
        captured, FakeCriminal = _capturing_criminal()

        # 'Faction' and 'Wiki' are unmapped keys with uppercase — must be lowercased.
        raw = {"name": "Bandit", "Faction": "Pirates", "Wiki": "http://wiki"}
        with patch("persist.repositories.criminal_repository.Criminal", FakeCriminal):
            await repo.create_or_update(mock_db, raw)

        assert captured["faction"] == "Pirates"
        assert captured["wiki"] == "http://wiki"
        assert "Faction" not in captured
        assert "Wiki" not in captured

    @pytest.mark.asyncio
    async def test_update_sets_mapped_attrs_on_existing(self, repo, mock_db):
        """On update, setattr must use mapped keys ('built_in', 'is_player')."""
        existing = SimpleNamespace(id=1, name="Boss")
        mock_db.execute = AsyncMock(return_value=_make_execute_result(existing))

        raw = {"name": "Boss", "builtIn": True, "isPlayer": False}
        await repo.create_or_update(mock_db, raw)

        assert existing.built_in is True
        assert existing.is_player is False

    @pytest.mark.asyncio
    async def test_execute_called_once_for_lookup(self, repo, mock_db):
        """create_or_update must execute exactly one SELECT lookup query."""
        mock_db.execute = AsyncMock(return_value=_make_execute_result(None))
        _, FakeCriminal = _capturing_criminal()

        with patch("persist.repositories.criminal_repository.Criminal", FakeCriminal):
            await repo.create_or_update(mock_db, {"name": "Raider"})

        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_value_error_when_name_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'name' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for criminal"):
            await repo.create_or_update(mock_db, {"faction": "Pirates"})

        mock_db.execute.assert_not_awaited()
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_dict(self, repo, mock_db):
        """create_or_update must raise ValueError for an empty dict."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for criminal"):
            await repo.create_or_update(mock_db, {})
