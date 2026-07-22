"""Tests for DiscordMessageRepository.

Uses SQLite in-memory (aiosqlite) with the REAL DiscordMessage model — the
model's cross-dialect ``UUIDType(binary=False)`` degrades to CHAR(36) on
SQLite, so the table round-trips without PostgreSQL. This lets the filter
queries (get_by_type / list_by_* / get_by_guild_type_and_reference) be
exercised against real WHERE clauses instead of hard-coded mock returns.

Only the DB-error rollback path keeps a mock session (a real SQLite commit
cannot be forced to fail deterministically).
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE any src imports.
# NOTE: sqlalchemy_utils is intentionally NOT mocked here — the real package is
# installed and provides a working UUIDType so the DiscordMessage table can be
# created on SQLite.
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest
from persist.models.base import Base
from persist.models.discord_message import DiscordMessage
from persist.repositories.discord_message_repository import DiscordMessageRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Fixtures — own SQLite engine (mirrors test_combat_log_repository.py)
# ---------------------------------------------------------------------------

_DM_TABLES = [DiscordMessage.__table__]


@pytest.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_DM_TABLES)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def repo() -> DiscordMessageRepository:
    return DiscordMessageRepository()


def _raw(**overrides) -> dict:
    """A minimal valid create_or_update payload (embed_payload is a Text column)."""
    data = dict(
        guild_id=111,
        channel_id=222,
        message_id=333,
        embed_payload='{"title": "Hello"}',
        message_type="announcement",
    )
    data.update(overrides)
    return data


async def _seed(repo, db_session, **overrides) -> DiscordMessage:
    """Persist a DiscordMessage row via the repo and return it."""
    return await repo.create_or_update(db_session, _raw(**overrides))


# ---------------------------------------------------------------------------
# TestDiscordMessageRepositoryInit
# ---------------------------------------------------------------------------


class TestDiscordMessageRepositoryInit:
    def test_init_stores_discord_message_model(self, repo):
        """DiscordMessageRepository.__init__ must store DiscordMessage model."""
        assert repo._model is DiscordMessage


# ---------------------------------------------------------------------------
# TestCreateOrUpdate — real round-trips
# ---------------------------------------------------------------------------


class TestCreateOrUpdate:
    async def test_create_new_message_when_not_found(self, repo, db_session):
        """create_or_update inserts a new row when none exists, and it is retrievable."""
        result = await repo.create_or_update(db_session, _raw(message_type="announcement"))

        assert result.id is not None
        fetched = await repo.get_by_composite_key(db_session, 111, 222, 333)
        assert fetched is not None
        assert fetched.id == result.id
        assert fetched.message_type == "announcement"
        assert fetched.embed_payload == '{"title": "Hello"}'

    async def test_update_existing_message_when_found(self, repo, db_session):
        """create_or_update updates the existing row (same PK) rather than inserting."""
        created = await _seed(repo, db_session, message_type="announcement")

        updated = await repo.create_or_update(
            db_session, _raw(embed_payload='{"title": "Updated"}', message_type="news")
        )

        # Same primary key → an UPDATE, not a second INSERT.
        assert updated.id == created.id
        fetched = await repo.get_by_composite_key(db_session, 111, 222, 333)
        assert fetched.embed_payload == '{"title": "Updated"}'
        assert fetched.message_type == "news"
        # Exactly one row for this composite key.
        all_of_type = await repo.get_by_type(db_session, "news")
        assert len([m for m in all_of_type if m.message_id == 333]) == 1

    async def test_create_uses_default_message_type(self, repo, db_session):
        """create_or_update defaults message_type to 'general' when not provided."""
        raw = {"guild_id": 1, "channel_id": 2, "message_id": 3, "embed_payload": "{}"}
        result = await repo.create_or_update(db_session, raw)

        fetched = await repo.get_by_composite_key(db_session, 1, 2, 3)
        assert result.message_type == "general"
        assert fetched.message_type == "general"

    async def test_create_sets_reference_id_when_provided(self, repo, db_session):
        """create_or_update persists reference_id from raw dict when creating."""
        await _seed(repo, db_session, message_type="bounty_announcement", reference_id=42)

        fetched = await repo.get_by_composite_key(db_session, 111, 222, 333)
        assert fetched.reference_id == 42

    async def test_create_reference_id_none_when_not_provided(self, repo, db_session):
        """create_or_update leaves reference_id NULL when not in raw dict."""
        raw = {"guild_id": 1, "channel_id": 2, "message_id": 3, "embed_payload": "{}"}
        await repo.create_or_update(db_session, raw)

        fetched = await repo.get_by_composite_key(db_session, 1, 2, 3)
        assert fetched.reference_id is None

    async def test_update_sets_reference_id_when_provided(self, repo, db_session):
        """create_or_update updates reference_id on existing row when raw contains it."""
        await _seed(repo, db_session, message_type="bounty_announcement", reference_id=None)

        await repo.create_or_update(db_session, _raw(message_type="bounty_announcement", reference_id=99))

        fetched = await repo.get_by_composite_key(db_session, 111, 222, 333)
        assert fetched.reference_id == 99

    async def test_update_preserves_existing_reference_id_when_not_in_raw(self, repo, db_session):
        """create_or_update preserves existing reference_id when raw omits it."""
        await _seed(repo, db_session, message_type="bounty_announcement", reference_id=77)

        # raw without reference_id
        raw = {"guild_id": 111, "channel_id": 222, "message_id": 333, "embed_payload": '{"t": 1}'}
        await repo.create_or_update(db_session, raw)

        fetched = await repo.get_by_composite_key(db_session, 111, 222, 333)
        assert fetched.reference_id == 77


# ---------------------------------------------------------------------------
# TestGetByCompositeKey
# ---------------------------------------------------------------------------


class TestGetByCompositeKey:
    async def test_returns_message_when_found(self, repo, db_session):
        created = await _seed(repo, db_session)

        result = await repo.get_by_composite_key(db_session, 111, 222, 333)

        assert result is not None
        assert result.id == created.id

    async def test_returns_none_when_not_found(self, repo, db_session):
        await _seed(repo, db_session)  # a different key exists

        result = await repo.get_by_composite_key(db_session, 9, 9, 9)

        assert result is None

    async def test_composite_key_discriminates_on_each_component(self, repo, db_session):
        """A row is only matched when all three of guild/channel/message match."""
        await _seed(repo, db_session, guild_id=111, channel_id=222, message_id=333)

        assert await repo.get_by_composite_key(db_session, 999, 222, 333) is None
        assert await repo.get_by_composite_key(db_session, 111, 999, 333) is None
        assert await repo.get_by_composite_key(db_session, 111, 222, 999) is None
        assert await repo.get_by_composite_key(db_session, 111, 222, 333) is not None


# ---------------------------------------------------------------------------
# TestGetByType — real filtering on message_type / guild_id / channel_id
# ---------------------------------------------------------------------------


class TestGetByType:
    async def test_get_by_type_filters_by_type(self, repo, db_session):
        """Only rows of the requested type are returned; other types excluded."""
        await _seed(repo, db_session, message_id=1, message_type="news")
        await _seed(repo, db_session, message_id=2, message_type="news")
        await _seed(repo, db_session, message_id=3, message_type="announcement")

        result = await repo.get_by_type(db_session, "news")

        assert {m.message_id for m in result} == {1, 2}
        assert all(m.message_type == "news" for m in result)

    async def test_get_by_type_with_guild_id_filters_by_guild(self, repo, db_session):
        """guild_id argument restricts results to that guild only."""
        await _seed(repo, db_session, guild_id=42, message_id=1, message_type="news")
        await _seed(repo, db_session, guild_id=99, message_id=2, message_type="news")

        result = await repo.get_by_type(db_session, "news", guild_id=42)

        assert {m.message_id for m in result} == {1}
        assert all(m.guild_id == 42 for m in result)

    async def test_get_by_type_with_channel_id_filters_by_channel(self, repo, db_session):
        """channel_id argument restricts results to that channel only."""
        await _seed(repo, db_session, channel_id=500, message_id=1, message_type="news")
        await _seed(repo, db_session, channel_id=600, message_id=2, message_type="news")

        result = await repo.get_by_type(db_session, "news", channel_id=500)

        assert {m.message_id for m in result} == {1}

    async def test_get_by_type_orders_newest_first(self, repo, db_session):
        """Results are ordered by created_at descending."""
        old = await _seed(
            repo,
            db_session,
            message_id=1,
            message_type="news",
        )
        new = await _seed(
            repo,
            db_session,
            message_id=2,
            message_type="news",
        )
        # Force a deterministic ordering on created_at.
        old.created_at = datetime.now(UTC) - timedelta(hours=2)
        new.created_at = datetime.now(UTC)
        await db_session.commit()

        result = await repo.get_by_type(db_session, "news")
        ids = [m.message_id for m in result]
        assert ids.index(2) < ids.index(1)

    async def test_get_by_type_returns_empty_list(self, repo, db_session):
        await _seed(repo, db_session, message_type="news")

        result = await repo.get_by_type(db_session, "nonexistent")

        assert result == []


# ---------------------------------------------------------------------------
# TestListByGuild
# ---------------------------------------------------------------------------


class TestListByGuild:
    async def test_list_by_guild_returns_only_that_guild(self, repo, db_session):
        await _seed(repo, db_session, guild_id=100, message_id=1)
        await _seed(repo, db_session, guild_id=100, message_id=2)
        await _seed(repo, db_session, guild_id=200, message_id=3)

        result = await repo.list_by_guild(db_session, guild_id=100)

        assert {m.message_id for m in result} == {1, 2}
        assert all(m.guild_id == 100 for m in result)

    async def test_list_by_guild_returns_empty_list(self, repo, db_session):
        await _seed(repo, db_session, guild_id=100)

        result = await repo.list_by_guild(db_session, guild_id=999)

        assert result == []


# ---------------------------------------------------------------------------
# TestListByChannel
# ---------------------------------------------------------------------------


class TestListByChannel:
    async def test_list_by_channel_filters_guild_and_channel(self, repo, db_session):
        await _seed(repo, db_session, guild_id=10, channel_id=20, message_id=1)
        await _seed(repo, db_session, guild_id=10, channel_id=30, message_id=2)
        await _seed(repo, db_session, guild_id=99, channel_id=20, message_id=3)

        result = await repo.list_by_channel(db_session, guild_id=10, channel_id=20)

        assert {m.message_id for m in result} == {1}

    async def test_list_by_channel_returns_empty_list(self, repo, db_session):
        await _seed(repo, db_session, guild_id=10, channel_id=20)

        result = await repo.list_by_channel(db_session, guild_id=1, channel_id=2)

        assert result == []


# ---------------------------------------------------------------------------
# TestListByGuildAndChannel
# ---------------------------------------------------------------------------


class TestListByGuildAndChannel:
    async def test_list_by_guild_and_channel_filters_pair(self, repo, db_session):
        await _seed(repo, db_session, guild_id=5, channel_id=6, message_id=1)
        await _seed(repo, db_session, guild_id=5, channel_id=6, message_id=2)
        await _seed(repo, db_session, guild_id=5, channel_id=7, message_id=3)

        result = await repo.list_by_guild_and_channel(db_session, guild_id=5, channel_id=6)

        assert {m.message_id for m in result} == {1, 2}


# ---------------------------------------------------------------------------
# TestListByGuildAndType
# ---------------------------------------------------------------------------


class TestListByGuildAndType:
    async def test_list_by_guild_and_type_filters_guild_and_type(self, repo, db_session):
        await _seed(repo, db_session, guild_id=7, message_id=1, message_type="alert")
        await _seed(repo, db_session, guild_id=7, message_id=2, message_type="news")
        await _seed(repo, db_session, guild_id=8, message_id=3, message_type="alert")

        result = await repo.list_by_guild_and_type(db_session, guild_id=7, message_type="alert")

        assert {m.message_id for m in result} == {1}

    async def test_list_by_guild_and_type_returns_empty_list(self, repo, db_session):
        await _seed(repo, db_session, guild_id=7, message_type="alert")

        result = await repo.list_by_guild_and_type(db_session, guild_id=8, message_type="missing")

        assert result == []


# ---------------------------------------------------------------------------
# TestDeleteByCompositeKey
# ---------------------------------------------------------------------------


class TestDeleteByCompositeKey:
    async def test_delete_returns_true_and_removes_row(self, repo, db_session):
        await _seed(repo, db_session, guild_id=1, channel_id=2, message_id=3)

        result = await repo.delete_by_composite_key(db_session, 1, 2, 3)

        assert result is True
        assert await repo.get_by_composite_key(db_session, 1, 2, 3) is None

    async def test_delete_returns_false_when_not_found(self, repo, db_session):
        await _seed(repo, db_session, guild_id=1, channel_id=2, message_id=3)

        result = await repo.delete_by_composite_key(db_session, 9, 9, 9)

        assert result is False
        # The unrelated row is untouched.
        assert await repo.get_by_composite_key(db_session, 1, 2, 3) is not None


# ---------------------------------------------------------------------------
# TestGetByGuildTypeAndReference
# ---------------------------------------------------------------------------


class TestGetByGuildTypeAndReference:
    async def test_returns_message_when_found(self, repo, db_session):
        created = await _seed(repo, db_session, guild_id=111, message_type="bounty_announcement", reference_id=42)

        result = await repo.get_by_guild_type_and_reference(db_session, 111, "bounty_announcement", 42)

        assert result is not None
        assert result.id == created.id

    async def test_returns_none_when_not_found(self, repo, db_session):
        await _seed(repo, db_session, guild_id=111, message_type="bounty_announcement", reference_id=42)

        result = await repo.get_by_guild_type_and_reference(db_session, 111, "bounty_announcement", 999)

        assert result is None

    async def test_discriminates_on_guild_type_and_reference(self, repo, db_session):
        """A wrong guild, type, or reference_id all miss the seeded row."""
        await _seed(repo, db_session, guild_id=111, message_type="bounty_announcement", reference_id=42)

        assert await repo.get_by_guild_type_and_reference(db_session, 222, "bounty_announcement", 42) is None
        assert await repo.get_by_guild_type_and_reference(db_session, 111, "news", 42) is None
        assert await repo.get_by_guild_type_and_reference(db_session, 111, "bounty_announcement", 43) is None


# ---------------------------------------------------------------------------
# TestDeleteByGuildTypeAndReference
# ---------------------------------------------------------------------------


class TestDeleteByGuildTypeAndReference:
    async def test_delete_returns_true_and_removes_matching_rows(self, repo, db_session):
        await _seed(repo, db_session, guild_id=111, message_id=1, message_type="bounty_announcement", reference_id=42)
        # An unrelated row that must survive.
        await _seed(repo, db_session, guild_id=111, message_id=2, message_type="bounty_announcement", reference_id=43)

        result = await repo.delete_by_guild_type_and_reference(db_session, 111, "bounty_announcement", 42)

        assert result is True
        assert await repo.get_by_guild_type_and_reference(db_session, 111, "bounty_announcement", 42) is None
        assert await repo.get_by_guild_type_and_reference(db_session, 111, "bounty_announcement", 43) is not None

    async def test_delete_returns_false_when_no_records_found(self, repo, db_session):
        await _seed(repo, db_session, guild_id=111, message_type="bounty_announcement", reference_id=42)

        result = await repo.delete_by_guild_type_and_reference(db_session, 111, "bounty_announcement", 999)

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_rolls_back_and_raises_on_error(self, repo):
        """On DB error the delete rolls back and re-raises.

        Justified mock: a real SQLite session cannot be made to raise
        deterministically inside execute(), so a mock session forces the error path.
        """
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_db.rollback = AsyncMock()

        with pytest.raises(RuntimeError, match="DB error"):
            await repo.delete_by_guild_type_and_reference(mock_db, 111, "bounty_announcement", 42)

        mock_db.rollback.assert_awaited_once()
