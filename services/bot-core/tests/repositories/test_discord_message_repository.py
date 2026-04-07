"""Unit tests for DiscordMessageRepository.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers all 8 methods:
- create_or_update: create new + update existing
- get_by_composite_key: found + not found
- get_by_type: with guild_id, with channel_id, without filters
- list_by_guild: returns list
- list_by_channel: returns list
- list_by_guild_and_channel: returns list
- list_by_guild_and_type: returns list
- delete_by_composite_key: found + not found
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
from persist.repositories.discord_message_repository import DiscordMessageRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> DiscordMessageRepository:
    return DiscordMessageRepository()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    db.delete = AsyncMock()
    return db


def _make_one_or_none_result(value) -> MagicMock:
    """Build a mock for result.scalars().one_or_none()."""
    scalars_mock = MagicMock()
    scalars_mock.one_or_none = MagicMock(return_value=value)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


def _make_all_result(values: list) -> MagicMock:
    """Build a mock for result.scalars().all()."""
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=values)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


# ---------------------------------------------------------------------------
# TestDiscordMessageRepositoryInit
# ---------------------------------------------------------------------------


class TestDiscordMessageRepositoryInit:
    def test_init_stores_discord_message_model(self, repo):
        """DiscordMessageRepository.__init__ must store DiscordMessage model."""
        from persist.models.discord_message import DiscordMessage

        assert repo._model is DiscordMessage


# ---------------------------------------------------------------------------
# TestCreateOrUpdate
# ---------------------------------------------------------------------------


class TestCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_new_message_when_not_found(self, repo, mock_db):
        """create_or_update should create a new message when none exists."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {
            "guild_id": 111,
            "channel_id": 222,
            "message_id": 333,
            "embed_payload": {"title": "Hello"},
            "message_type": "announcement",
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_update_existing_message_when_found(self, repo, mock_db):
        """create_or_update should update an existing message when found."""
        existing = MagicMock()
        existing.id = 1
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {
            "guild_id": 111,
            "channel_id": 222,
            "message_id": 333,
            "embed_payload": {"title": "Updated"},
            "message_type": "news",
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        assert result is existing
        assert existing.embed_payload == {"title": "Updated"}
        assert existing.message_type == "news"

    @pytest.mark.asyncio
    async def test_create_uses_default_message_type(self, repo, mock_db):
        """create_or_update defaults message_type to 'general' when not provided."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {
            "guild_id": 1,
            "channel_id": 2,
            "message_id": 3,
            "embed_payload": {},
        }
        result = await repo.create_or_update(mock_db, raw)

        added = mock_db.add.call_args[0][0]
        assert added.message_type == "general"
        assert result is not None


# ---------------------------------------------------------------------------
# TestGetByCompositeKey
# ---------------------------------------------------------------------------


class TestGetByCompositeKey:
    @pytest.mark.asyncio
    async def test_returns_message_when_found(self, repo, mock_db):
        """get_by_composite_key should return a message when it exists."""
        msg = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(msg))

        result = await repo.get_by_composite_key(mock_db, 1, 2, 3)

        assert result is msg
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, repo, mock_db):
        """get_by_composite_key should return None when message does not exist."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_composite_key(mock_db, 9, 9, 9)

        assert result is None


# ---------------------------------------------------------------------------
# TestGetByType
# ---------------------------------------------------------------------------


class TestGetByType:
    @pytest.mark.asyncio
    async def test_get_by_type_without_filters(self, repo, mock_db):
        """get_by_type should return all messages of the given type."""
        msgs = [MagicMock(), MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_all_result(msgs))

        result = await repo.get_by_type(mock_db, "announcement")

        assert result == msgs
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_by_type_with_guild_id(self, repo, mock_db):
        """get_by_type with guild_id should filter by guild."""
        msgs = [MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_all_result(msgs))

        result = await repo.get_by_type(mock_db, "news", guild_id=42)

        assert result == msgs
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_by_type_with_channel_id(self, repo, mock_db):
        """get_by_type with channel_id should filter by channel."""
        msgs = [MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_all_result(msgs))

        result = await repo.get_by_type(mock_db, "news", channel_id=99)

        assert result == msgs

    @pytest.mark.asyncio
    async def test_get_by_type_returns_empty_list(self, repo, mock_db):
        """get_by_type should return an empty list when no results."""
        mock_db.execute = AsyncMock(return_value=_make_all_result([]))

        result = await repo.get_by_type(mock_db, "nonexistent")

        assert result == []


# ---------------------------------------------------------------------------
# TestListByGuild
# ---------------------------------------------------------------------------


class TestListByGuild:
    @pytest.mark.asyncio
    async def test_list_by_guild_returns_list(self, repo, mock_db):
        """list_by_guild should return all messages for a guild."""
        msgs = [MagicMock(), MagicMock(), MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_all_result(msgs))

        result = await repo.list_by_guild(mock_db, guild_id=100)

        assert result == msgs
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_by_guild_returns_empty_list(self, repo, mock_db):
        """list_by_guild should return empty list when no messages."""
        mock_db.execute = AsyncMock(return_value=_make_all_result([]))

        result = await repo.list_by_guild(mock_db, guild_id=999)

        assert result == []


# ---------------------------------------------------------------------------
# TestListByChannel
# ---------------------------------------------------------------------------


class TestListByChannel:
    @pytest.mark.asyncio
    async def test_list_by_channel_returns_list(self, repo, mock_db):
        """list_by_channel should return messages for a guild+channel."""
        msgs = [MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_all_result(msgs))

        result = await repo.list_by_channel(mock_db, guild_id=10, channel_id=20)

        assert result == msgs
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_by_channel_returns_empty_list(self, repo, mock_db):
        """list_by_channel should return empty list when none exist."""
        mock_db.execute = AsyncMock(return_value=_make_all_result([]))

        result = await repo.list_by_channel(mock_db, guild_id=1, channel_id=2)

        assert result == []


# ---------------------------------------------------------------------------
# TestListByGuildAndChannel
# ---------------------------------------------------------------------------


class TestListByGuildAndChannel:
    @pytest.mark.asyncio
    async def test_list_by_guild_and_channel_returns_list(self, repo, mock_db):
        """list_by_guild_and_channel should return messages for the pair."""
        msgs = [MagicMock(), MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_all_result(msgs))

        result = await repo.list_by_guild_and_channel(mock_db, guild_id=5, channel_id=6)

        assert result == msgs
        mock_db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestListByGuildAndType
# ---------------------------------------------------------------------------


class TestListByGuildAndType:
    @pytest.mark.asyncio
    async def test_list_by_guild_and_type_returns_list(self, repo, mock_db):
        """list_by_guild_and_type delegates to get_by_type and returns list."""
        msgs = [MagicMock()]
        mock_db.execute = AsyncMock(return_value=_make_all_result(msgs))

        result = await repo.list_by_guild_and_type(mock_db, guild_id=7, message_type="alert")

        assert result == msgs
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_by_guild_and_type_returns_empty_list(self, repo, mock_db):
        """list_by_guild_and_type returns empty list when none match."""
        mock_db.execute = AsyncMock(return_value=_make_all_result([]))

        result = await repo.list_by_guild_and_type(mock_db, guild_id=8, message_type="missing")

        assert result == []


# ---------------------------------------------------------------------------
# TestDeleteByCompositeKey
# ---------------------------------------------------------------------------


class TestDeleteByCompositeKey:
    @pytest.mark.asyncio
    async def test_delete_returns_true_when_found(self, repo, mock_db):
        """delete_by_composite_key returns True when message exists and is deleted."""
        msg = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(msg))

        result = await repo.delete_by_composite_key(mock_db, 1, 2, 3)

        assert result is True
        mock_db.delete.assert_awaited_once_with(msg)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_not_found(self, repo, mock_db):
        """delete_by_composite_key returns False when message does not exist."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.delete_by_composite_key(mock_db, 9, 9, 9)

        assert result is False
        mock_db.delete.assert_not_called()
        mock_db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# TestGetByGuildTypeAndReference
# ---------------------------------------------------------------------------


def _make_first_result(value) -> MagicMock:
    """Build a mock for result.scalars().first()."""
    scalars_mock = MagicMock()
    scalars_mock.first = MagicMock(return_value=value)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


def _make_returning_result(row_ids: list) -> MagicMock:
    """Build a mock for result.fetchall() (used by DELETE RETURNING)."""
    result_mock = MagicMock()
    result_mock.fetchall = MagicMock(return_value=row_ids)
    return result_mock


class TestGetByGuildTypeAndReference:
    @pytest.mark.asyncio
    async def test_returns_message_when_found(self, repo, mock_db):
        """get_by_guild_type_and_reference returns the matching message when found."""
        msg = MagicMock()
        msg.id = "some-uuid"
        mock_db.execute = AsyncMock(return_value=_make_first_result(msg))

        result = await repo.get_by_guild_type_and_reference(mock_db, 111, "bounty_announcement", 42)

        assert result is msg
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, repo, mock_db):
        """get_by_guild_type_and_reference returns None when no record matches."""
        mock_db.execute = AsyncMock(return_value=_make_first_result(None))

        result = await repo.get_by_guild_type_and_reference(mock_db, 111, "bounty_announcement", 999)

        assert result is None
        mock_db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestDeleteByGuildTypeAndReference
# ---------------------------------------------------------------------------


class TestDeleteByGuildTypeAndReference:
    @pytest.mark.asyncio
    async def test_delete_returns_true_when_records_deleted(self, repo, mock_db):
        """delete_by_guild_type_and_reference returns True when at least one record was deleted."""
        mock_db.execute = AsyncMock(return_value=_make_returning_result([("uuid-1",)]))

        result = await repo.delete_by_guild_type_and_reference(mock_db, 111, "bounty_announcement", 42)

        assert result is True
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_no_records_found(self, repo, mock_db):
        """delete_by_guild_type_and_reference returns False when no records match."""
        mock_db.execute = AsyncMock(return_value=_make_returning_result([]))

        result = await repo.delete_by_guild_type_and_reference(mock_db, 111, "bounty_announcement", 999)

        assert result is False
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_rolls_back_and_raises_on_error(self, repo, mock_db):
        """delete_by_guild_type_and_reference rolls back and re-raises on DB error."""
        mock_db.execute = AsyncMock(side_effect=RuntimeError("DB error"))

        with pytest.raises(RuntimeError, match="DB error"):
            await repo.delete_by_guild_type_and_reference(mock_db, 111, "bounty_announcement", 42)

        mock_db.rollback.assert_awaited_once()
