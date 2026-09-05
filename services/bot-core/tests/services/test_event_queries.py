"""Tests for event_service query helpers: medals(), final_standings(), live_standings().

SQLite in-memory — no gateway mocks needed (pure DB queries).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persist.models.admin_audit_log import AdminAuditLog
from persist.models.base import Base
from persist.models.game_event import EventResult, GameEvent, GameEventMetric, GameEventPrize
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.user import User
from services.event_service import final_standings, live_standings, medals
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TABLES = [
    GuildConfig.__table__,
    User.__table__,
    Player.__table__,
    GameEvent.__table__,
    GameEventPrize.__table__,
    GameEventMetric.__table__,
    EventResult.__table__,
    AdminAuditLog.__table__,
]

GUILD_ID = 9_100_000


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=_TABLES)
        await engine.dispose()


def _make_event(guild_id: int = GUILD_ID, slug: str = "bounty_caps") -> GameEvent:
    now = datetime.now(UTC)
    return GameEvent(
        guild_id=guild_id,
        type_slug=slug,
        params={},
        state="active",
        duration_days=7,
        created_by_user_id=1,
        created_at=now,
        updated_at=now,
    )


def _make_user(discord_id: int, name: str = "User") -> User:
    return User(id=discord_id, discord_username=name, display_name=name)


def _make_player(user_id: int, guild_id: int = GUILD_ID) -> Player:
    now = datetime.now(UTC)
    return Player(user_id=user_id, guild_id=guild_id, credits=0, created_at=now, updated_at=now)


def _make_result(event_id: int, player_id: int, rank: int, type_slug: str = "bounty_caps") -> EventResult:
    return EventResult(
        event_id=event_id,
        guild_id=GUILD_ID,
        type_slug=type_slug,
        player_id=player_id,
        rank=rank,
        value=float(10 - rank),
        qualified=True,
        prize=f"rank{rank}",
        status="ok",
        awarded_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# medals() — Olympic ordering + type_slug filter
# ---------------------------------------------------------------------------


async def test_medals_olympic_ordering(db_factory):
    """gold desc, silver desc, bronze desc, events desc."""
    async with db_factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=1)
        db.add(cfg)

        ev1_a = _make_event()  # first event (gold for p1)
        ev1_b = _make_event()  # second event (gold for p2, silver for p1)
        db.add(ev1_a)
        db.add(ev1_b)
        await db.flush()

        for did in [101, 102]:
            db.add(_make_user(did, f"U{did}"))
        await db.flush()
        p1 = _make_player(101)
        p2 = _make_player(102)
        db.add(p1)
        db.add(p2)
        await db.flush()

        # p1: 1 gold, 1 silver; p2: 1 gold
        db.add(_make_result(ev1_a.id, p1.id, 1))
        db.add(_make_result(ev1_b.id, p2.id, 1))
        db.add(_make_result(ev1_b.id, p1.id, 2))
        await db.commit()

    async with db_factory() as db:
        rows = await medals(db, GUILD_ID)

    # p1 wins on silver tiebreak (same gold count? No: p1 has 1 gold too; p2 has 1 gold.
    # p1: gold=1, silver=1, events=2; p2: gold=1, silver=0, events=1
    # Olympic: gold ties → silver desc → p1 wins
    assert len(rows) == 2
    assert rows[0]["player_id"] == p1.id
    assert rows[0]["gold"] == 1
    assert rows[0]["silver"] == 1
    assert rows[1]["player_id"] == p2.id


async def test_medals_type_slug_filter(db_factory):
    """type_slug filter limits medal rows to that event type."""
    async with db_factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=2)
        db.add(cfg)

        ev_a = _make_event(slug="bounty_caps")
        ev_b = _make_event(slug="duel_wins")
        db.add(ev_a)
        db.add(ev_b)
        await db.flush()

        db.add(_make_user(201, "A"))
        db.add(_make_user(202, "B"))
        await db.flush()
        pa = _make_player(201)
        pb = _make_player(202)
        db.add(pa)
        db.add(pb)
        await db.flush()

        # pa has gold in bounty_caps; pb has gold in duel_wins
        db.add(_make_result(ev_a.id, pa.id, 1, type_slug="bounty_caps"))
        db.add(_make_result(ev_b.id, pb.id, 1, type_slug="duel_wins"))
        await db.commit()

    async with db_factory() as db:
        rows = await medals(db, GUILD_ID, type_slug="bounty_caps")

    assert len(rows) == 1
    assert rows[0]["player_id"] == pa.id
    assert rows[0]["gold"] == 1


# ---------------------------------------------------------------------------
# final_standings() — reads from event_results
# ---------------------------------------------------------------------------


async def test_final_standings_reads_event_results(db_factory):
    """final_standings returns rows from event_results, sorted by rank."""
    async with db_factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=3)
        db.add(cfg)
        ev = _make_event()
        ev.state = "ended"
        db.add(ev)
        await db.flush()

        for did, _rk in [(301, 1), (302, 2), (303, 3)]:
            db.add(_make_user(did, f"U{did}"))
        await db.flush()
        pids = {}
        for did in [301, 302, 303]:
            p = _make_player(did)
            db.add(p)
            await db.flush()
            pids[did] = p.id

        for did, rk in [(301, 1), (302, 2), (303, 3)]:
            db.add(_make_result(ev.id, pids[did], rk))
        await db.commit()

    async with db_factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        rows = await final_standings(db, ev)

    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert rows[0]["qualified"] is True
    assert rows[0]["value"] == 9.0  # 10 - rank


# ---------------------------------------------------------------------------
# live_standings() — ranks qualified only; unqualified shown at rank=None
# ---------------------------------------------------------------------------


async def test_live_standings_qualified_only_ranked(db_factory):
    """live_standings: qualified players get numeric rank; unqualified get rank=None.

    Uses longest_battle_won (min_fights=10) so p403 is unqualified (no fights metric).
    """
    async with db_factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=4)
        db.add(cfg)
        # longest_battle_won requires min_fights=10 to qualify
        ev = _make_event(slug="longest_battle_won")
        db.add(ev)
        await db.flush()

        for did in [401, 402, 403]:
            db.add(_make_user(did, f"U{did}"))
        await db.flush()
        players = {}
        for did in [401, 402, 403]:
            p = _make_player(did)
            db.add(p)
            await db.flush()
            players[did] = p.id

        # p401: duration=300, fights=10 → qualified, rank=1
        # p402: duration=100, fights=10 → qualified, rank=2
        # p403: duration=500, fights=0  → unqualified (no fights row), rank=None
        db.add(GameEventMetric(event_id=ev.id, player_id=players[401], metric="duration_ticks_win", value=300))
        db.add(GameEventMetric(event_id=ev.id, player_id=players[401], metric="fights", value=10))
        db.add(GameEventMetric(event_id=ev.id, player_id=players[402], metric="duration_ticks_win", value=100))
        db.add(GameEventMetric(event_id=ev.id, player_id=players[402], metric="fights", value=10))
        db.add(GameEventMetric(event_id=ev.id, player_id=players[403], metric="duration_ticks_win", value=500))
        # p403 has no fights row → fails min_fights=10
        await db.commit()

    async with db_factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        rows = await live_standings(db, ev)

    by_user = {r["user_id"]: r for r in rows}
    # p403 unqualified → rank is None
    assert by_user[403]["qualified"] is False
    assert by_user[403]["rank"] is None
    # p401 and p402 are qualified and ranked
    assert by_user[401]["qualified"] is True
    assert by_user[401]["rank"] is not None
    assert by_user[402]["qualified"] is True
    assert by_user[402]["rank"] is not None
    # p401 has longer battle → ranks higher
    assert by_user[401]["rank"] < by_user[402]["rank"]


async def test_live_standings_longest_battle_value_display(db_factory):
    """live_standings for longest_battle_won includes value_display formatted as '<N.N>s'."""
    async with db_factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=5)
        db.add(cfg)
        ev = _make_event(slug="longest_battle_won")
        db.add(ev)
        await db.flush()

        db.add(_make_user(501, "U501"))
        await db.flush()
        p = _make_player(501)
        db.add(p)
        await db.flush()

        # 1234 ticks * 10 ms/tick = 12.34 s → "12.3s"
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="duration_ticks_win", value=1234))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="fights", value=10))
        await db.commit()

    async with db_factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        rows = await live_standings(db, ev)

    assert rows, "expected at least one row"
    row = rows[0]
    assert "value_display" in row, "value_display key missing from standings dict"
    assert row["value_display"] == "12.3s", f"expected '12.3s', got {row['value_display']!r}"
