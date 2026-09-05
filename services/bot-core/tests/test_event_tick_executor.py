"""Tests for event_tick_executor — slice 3 (issue #30).

Tier B: SQLite in-memory + db_manager bridge mock (1 mock per test).

BEHAVIOURS COVERED
------------------
1. Due scheduled event is started (state → active).
2. Due active event is ended/payout (state → ended).
3. Not-yet-due event is untouched.
4. A failing event does not prevent other events from being processed.
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup and stub registration (mirror other executor tests)
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_shared.bblogger = MagicMock()  # type: ignore[attr-defined]
    _mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_shared.bblogger  # type: ignore[arg-type]

if "sqlalchemy_utils" not in sys.modules:
    _mock_sau = types.ModuleType("sqlalchemy_utils")
    _mock_sau.UUIDType = MagicMock()  # type: ignore[attr-defined]
    sys.modules["sqlalchemy_utils"] = _mock_sau

import pytest
import respx
from httpx import Response
from persist.models.admin_audit_log import AdminAuditLog
from persist.models.base import Base
from persist.models.game_event import EventResult, GameEvent, GameEventMetric, GameEventPrize
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from utils.executors.event_tick_executor import execute_event_tick_job

# ---------------------------------------------------------------------------
# SQLite table list
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    GuildConfig.__table__,
    User.__table__,
    Player.__table__,
    GameEvent.__table__,
    GameEventPrize.__table__,
    GameEventMetric.__table__,
    EventResult.__table__,
    AdminAuditLog.__table__,
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GUILD_ID = 9_700_000_001

GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_MEMBERS_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/guilds/{GUILD_ID}/members?limit=5000"
_CHANNEL_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/channels/777/messages"
_OTHER_CHANNEL_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/channels/888/messages"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def sqlite_engine_and_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=_SQLITE_TABLES)
        await engine.dispose()


def _make_fake_db_manager(factory: Any):
    """1 mock — db_manager bridge (Tier B)."""

    @asynccontextmanager
    async def _fake_get_db():
        async with factory() as session:
            yield session

    fake = MagicMock()
    fake.get_session = MagicMock(side_effect=_fake_get_db)
    return fake


def _make_event(
    state: str,
    scheduled_start_at: datetime | None = None,
    ends_at: datetime | None = None,
    guild_id: int = GUILD_ID,
) -> GameEvent:
    now = datetime.now(UTC)
    return GameEvent(
        guild_id=guild_id,
        type_slug="bounty_caps",
        params={},
        state=state,
        duration_days=7,
        scheduled_start_at=scheduled_start_at,
        started_at=now - timedelta(hours=8) if state == "active" else None,
        ends_at=ends_at,
        created_by_user_id=9999,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_due_scheduled_event_is_started(sqlite_engine_and_factory):
    """A scheduled event with scheduled_start_at <= now is started by the tick."""
    _, factory = sqlite_engine_and_factory
    now = datetime.now(UTC)

    async with factory() as db:
        db.add(GuildConfig(guild_id=GUILD_ID, discussion_channel_id=777))
        ev = _make_event("scheduled", scheduled_start_at=now - timedelta(minutes=1))
        db.add(ev)
        await db.commit()

    fake_db = _make_fake_db_manager(factory)

    with patch("persist.database.manager.db_manager", fake_db), respx.mock:
        respx.post(_CHANNEL_URL).mock(return_value=Response(200, json={"id": "1"}))
        result = await execute_event_tick_job("tick-test-1", {})

    assert result["status"] == "success"
    assert result["started"] == 1
    assert result["ended"] == 0
    assert result["errors"] == 0

    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        assert ev.state == "active"
        assert ev.started_at is not None
        assert ev.ends_at is not None


async def test_due_active_event_is_ended(sqlite_engine_and_factory):
    """An active event with ends_at <= now triggers end_event (payout)."""
    _, factory = sqlite_engine_and_factory
    now = datetime.now(UTC)

    async with factory() as db:
        db.add(GuildConfig(guild_id=GUILD_ID, discussion_channel_id=777))
        ev = _make_event("active", ends_at=now - timedelta(minutes=1))
        db.add(ev)
        await db.flush()
        # Participation prize (credits) so payout has something to do
        db.add(GameEventPrize(event_id=ev.id, rank_from=None, rank_to=None, kind="credits", item_ref=None, qty=10))
        # One player with a metric
        u = User(id=8001, discord_username="TickUser", display_name="TickUser")
        db.add(u)
        await db.flush()
        p = Player(user_id=8001, guild_id=GUILD_ID, credits=0, created_at=now, updated_at=now)
        db.add(p)
        await db.flush()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=5))
        await db.commit()

    fake_db = _make_fake_db_manager(factory)

    with patch("persist.database.manager.db_manager", fake_db), respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=Response(200, json={"data": [{"user": {"id": "8001"}}]}))
        respx.post(_CHANNEL_URL).mock(return_value=Response(200, json={"id": "1"}))
        result = await execute_event_tick_job("tick-test-2", {})

    assert result["status"] == "success"
    assert result["started"] == 0
    assert result["ended"] == 1
    assert result["errors"] == 0

    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        assert ev.state == "ended"


async def test_not_due_event_is_untouched(sqlite_engine_and_factory):
    """A scheduled event with scheduled_start_at in the future is not touched."""
    _, factory = sqlite_engine_and_factory
    now = datetime.now(UTC)

    async with factory() as db:
        db.add(GuildConfig(guild_id=GUILD_ID, discussion_channel_id=777))
        future = now + timedelta(hours=2)
        ev = _make_event("scheduled", scheduled_start_at=future)
        db.add(ev)
        await db.commit()

    fake_db = _make_fake_db_manager(factory)

    with patch("persist.database.manager.db_manager", fake_db):
        result = await execute_event_tick_job("tick-test-3", {})

    assert result["started"] == 0
    assert result["ended"] == 0
    assert result["errors"] == 0

    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        assert ev.state == "scheduled"  # untouched


async def test_failing_event_does_not_stop_others(sqlite_engine_and_factory):
    """A failing event (no guild config → ValueError) doesn't block other events."""
    _, factory = sqlite_engine_and_factory
    now = datetime.now(UTC)

    async with factory() as db:
        # Event A: no guild config → start_event raises ValueError
        ev_a = _make_event("scheduled", scheduled_start_at=now - timedelta(minutes=1), guild_id=GUILD_ID)
        db.add(ev_a)

        # Event B: has guild config → starts OK
        other_guild = GUILD_ID + 1
        db.add(GuildConfig(guild_id=other_guild, discussion_channel_id=888))
        ev_b = _make_event("scheduled", scheduled_start_at=now - timedelta(minutes=1), guild_id=other_guild)
        db.add(ev_b)
        await db.commit()

    fake_db = _make_fake_db_manager(factory)

    with patch("persist.database.manager.db_manager", fake_db), respx.mock:
        respx.post(_OTHER_CHANNEL_URL).mock(return_value=Response(200, json={"id": "1"}))
        result = await execute_event_tick_job("tick-test-4", {})

    # ev_a fails (no config), ev_b succeeds
    assert result["errors"] == 1
    assert result["started"] == 1

    async with factory() as db:
        events = (await db.execute(select(GameEvent))).scalars().all()
        states = {ev.guild_id: ev.state for ev in events}
        assert states[GUILD_ID] == "scheduled"  # ev_a: unchanged (rolled back)
        assert states[other_guild] == "active"  # ev_b: started OK


async def test_announcement_posted_after_commit(sqlite_engine_and_factory):
    """Announcement is sent only after the DB commit, never before.

    Strategy: wrap the session's commit to log 'commit', patch services.event_service.announce
    to log 'announce'; assert 'commit' precedes 'announce' in the call log.
    """
    _, factory = sqlite_engine_and_factory
    now = datetime.now(UTC)

    async with factory() as db:
        db.add(GuildConfig(guild_id=GUILD_ID, discussion_channel_id=777))
        ev = _make_event("scheduled", scheduled_start_at=now - timedelta(minutes=1))
        db.add(ev)
        await db.commit()

    call_log: list[str] = []

    @asynccontextmanager
    async def _instrumented_session():
        async with factory() as session:
            original_commit = session.commit

            async def _traced_commit():
                call_log.append("commit")
                return await original_commit()

            session.commit = _traced_commit  # type: ignore[method-assign]
            yield session

    fake_db = MagicMock()
    fake_db.get_session = MagicMock(side_effect=_instrumented_session)

    async def _mock_announce(*args, **kwargs):
        call_log.append("announce")

    with (
        patch("persist.database.manager.db_manager", fake_db),
        patch("services.event_service.announce", side_effect=_mock_announce),
    ):
        result = await execute_event_tick_job("tick-order-test", {})

    assert result["started"] == 1
    assert result["errors"] == 0
    # Commit must appear before announce in the log
    assert "commit" in call_log
    assert "announce" in call_log
    commit_idx = call_log.index("commit")
    announce_idx = call_log.index("announce")
    assert commit_idx < announce_idx, f"commit must precede announce; got log={call_log}"
