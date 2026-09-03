"""Tests for event_service.record() and standings() — slice 1 (issue #30).

Uses the in-memory SQLite fixture pattern from test_shop_announcements_role.py.
Max 2 mocks per test (bblogger only, via root conftest).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from persist.models.base import Base
from persist.models.game_event import GameEvent, GameEventMetric
from persist.models.guild_config import GuildConfig
from services.event_service import record, standings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Tables needed for these tests — avoids importing discord_message which needs sqlalchemy_utils
_TABLES = [GuildConfig.__table__, GameEvent.__table__, GameEventMetric.__table__]

GUILD_ID = 7_000_000_001
USER_ID_A = 1_000_000_001
USER_ID_B = 1_000_000_002


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine_and_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=_TABLES)
        await engine.dispose()


def _make_player(
    guild_id: int = GUILD_ID, user_id: int = USER_ID_A, tier: str = "Silver", id: int = 1
) -> SimpleNamespace:
    return SimpleNamespace(guild_id=guild_id, user_id=user_id, tier=tier, id=id)


def _make_event(guild_id: int = GUILD_ID, slug: str = "secondary_fired", params: dict | None = None) -> GameEvent:
    now = datetime.now(UTC)
    return GameEvent(
        guild_id=guild_id,
        type_slug=slug,
        params=params or {},
        state="active",
        duration_days=7,
        created_at=now,
        updated_at=now,
    )


async def _seed(session: AsyncSession, stakes: int = 1000, events: list[GameEvent] | None = None) -> None:
    config = GuildConfig(guild_id=GUILD_ID, event_min_duel_stakes=stakes)
    session.add(config)
    for ev in (events or []):
        session.add(ev)
    await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_bounty_contrib_increments_nukes(engine_and_factory):
    """bounty context with secondary_fired:nuke increments the metric row."""
    _, factory = engine_and_factory
    ev = _make_event(slug="secondary_fired", params={"subtype": "nuke"})
    async with factory() as session:
        await _seed(session, events=[ev])
        await session.refresh(ev)
        player = _make_player()
        await record(session, player, {"secondary_fired:nuke": 3.0}, context="bounty")
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        row = (await session.execute(
            select(GameEventMetric).where(GameEventMetric.player_id == player.id)
        )).scalar_one()
        assert float(row.value) == 3.0
        assert row.metric == "secondary_fired:nuke"


async def test_duel_below_stakes_ignored(engine_and_factory):
    """Duel contribution with stakes < event_min_duel_stakes is skipped."""
    _, factory = engine_and_factory
    ev = _make_event(slug="secondary_fired", params={"subtype": "nuke"})
    async with factory() as session:
        await _seed(session, stakes=1000, events=[ev])
        player = _make_player()
        await record(session, player, {"secondary_fired:nuke": 5.0}, context="duel", stakes=500)
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(GameEventMetric))).scalars().all()
        assert len(rows) == 0, "No metric row should be written for a below-stakes duel"


async def test_duel_at_stakes_counts(engine_and_factory):
    """Duel contribution with stakes == event_min_duel_stakes is counted."""
    _, factory = engine_and_factory
    ev = _make_event(slug="secondary_fired", params={"subtype": "nuke"})
    async with factory() as session:
        await _seed(session, stakes=1000, events=[ev])
        player = _make_player()
        await record(session, player, {"secondary_fired:nuke": 2.0}, context="duel", stakes=1000)
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        row = (await session.execute(select(GameEventMetric))).scalar_one()
        assert float(row.value) == 2.0


async def test_max_folding_in_standings(engine_and_factory):
    """standings() returns the max value across two record() calls for a max-agg event."""
    _, factory = engine_and_factory
    ev = _make_event(slug="longest_battle_won", params={})
    async with factory() as session:
        await _seed(session, events=[ev])
        await session.refresh(ev)
        player = _make_player()
        # Two fights: duration_ticks_win 50 then 30 — max should be 50
        # fights metric is also needed
        await record(session, player, {"duration_ticks_win": 50.0, "fights": 1.0}, context="bounty")
        await record(session, player, {"duration_ticks_win": 30.0, "fights": 1.0}, context="bounty")
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        # Reload the event
        ev2 = (await session.execute(select(GameEvent))).scalar_one()
        result = await standings(session, ev2)
        assert len(result) == 1
        pid, val, qual = result[0]
        assert pid == player.id
        assert val == 50.0  # max not sum
        # fights=2 (sum) >= min_fights=10 → not qualified
        assert qual is False


async def test_standings_qualified_at_min_fights(engine_and_factory):
    """avg_accuracy event: qualified=False below 10 fights, True at exactly 10."""
    _, factory = engine_and_factory
    ev_acc = _make_event(slug="avg_accuracy", params={})
    async with factory() as session:
        await _seed(session, events=[ev_acc])
        await session.refresh(ev_acc)
        player_a = _make_player(user_id=USER_ID_A, id=1)
        player_b = _make_player(user_id=USER_ID_B, id=2)
        # Player A: 9 fights — not qualified
        await record(session, player_a, {"hits": 72.0, "shots": 90.0, "fights": 9.0}, context="bounty")
        # Player B: 10 fights — qualified
        await record(session, player_b, {"hits": 80.0, "shots": 100.0, "fights": 10.0}, context="bounty")
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        ev2 = (await session.execute(select(GameEvent))).scalar_one()
        result = await standings(session, ev2)
        by_pid = {pid: (val, qual) for pid, val, qual in result}
        _, qual_a = by_pid[player_a.id]
        _, qual_b = by_pid[player_b.id]
        assert qual_a is False, "9 fights should be unqualified"
        assert qual_b is True, "10 fights should be qualified"


async def test_record_swallows_exception(engine_and_factory):
    """record() does not raise when the session is broken."""
    _, _factory = engine_and_factory

    class _BadSession:
        """Minimal bad session stub — execute always raises."""
        bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        async def execute(self, *a, **kw):
            raise RuntimeError("simulated DB error")

        async def flush(self):
            pass

    player = _make_player()
    # Must not raise — non-fatal by design (same rule as _increment_player_stats)
    await record(_BadSession(), player, {"secondary_fired:nuke": 1.0}, context="bounty")


async def test_sum_upsert_accumulates(engine_and_factory):
    """Two record() calls on a SUM metric accumulate: 3 + 5 = 8."""
    _, factory = engine_and_factory
    ev = _make_event(slug="bounty_caps", params={})
    async with factory() as session:
        await _seed(session, events=[ev])
        await session.refresh(ev)
        player = _make_player()
        await record(session, player, {"captures": 3.0}, context="bounty")
        await record(session, player, {"captures": 5.0}, context="bounty")
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        row = (await session.execute(select(GameEventMetric))).scalar_one()
        assert float(row.value) == 8.0


async def test_duel_no_stakes_ignored(engine_and_factory):
    """record() with context='duel' and stakes=None writes zero metric rows.

    This test FAILS before fix 1 (old code let None through) and passes after.
    """
    _, factory = engine_and_factory
    ev = _make_event(slug="duels_won", params={})
    async with factory() as session:
        await _seed(session, stakes=1000, events=[ev])
        player = _make_player()
        await record(session, player, {"duel_wins": 1.0}, context="duel", stakes=None)
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(GameEventMetric))).scalars().all()
        assert len(rows) == 0, "No metric row when stakes is None"
