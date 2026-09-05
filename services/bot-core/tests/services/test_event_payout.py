"""Tests for event_service.start_event() and end_event() — slice 3 (issue #30).

Tier B (SQLite in-memory) + respx for gateway mocks.
Max 2 mocks per test (respx not counted; only unittest.mock.patch counts).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response
from persist.models.admin_audit_log import AdminAuditLog
from persist.models.base import Base
from persist.models.game_event import EventResult, GameEvent, GameEventMetric, GameEventPrize
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.user import User
from services.event_service import announce, end_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Tables we can create in SQLite (no ARRAY columns).
# Player has use_alter=True FK to player_ships; SQLite ignores FK constraints.
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

GUILD_ID = 8_000_000_001

GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_MEMBERS_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/guilds/{GUILD_ID}/members?limit=5000"
_CHANNEL_URL_TMPL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/channels/{{channel_id}}/messages"


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


def _make_event(guild_id: int = GUILD_ID, slug: str = "bounty_caps", duration_days: int = 7) -> GameEvent:
    now = datetime.now(UTC)
    return GameEvent(
        guild_id=guild_id,
        type_slug=slug,
        params={},
        state="scheduled",
        duration_days=duration_days,
        scheduled_start_at=now - timedelta(minutes=1),
        created_by_user_id=9999,
        created_at=now,
        updated_at=now,
    )


def _make_user(discord_id: int, name: str = "TestUser") -> User:
    return User(id=discord_id, discord_username=name, display_name=name)


def _make_player(user_id: int, credits: int = 1000) -> Player:
    now = datetime.now(UTC)
    return Player(
        user_id=user_id,
        guild_id=GUILD_ID,
        credits=credits,
        created_at=now,
        updated_at=now,
    )


def _make_prize(
    event_id: int, rank_from: int | None, rank_to: int | None, qty: int = 100, kind: str = "credits"
) -> GameEventPrize:
    return GameEventPrize(event_id=event_id, rank_from=rank_from, rank_to=rank_to, kind=kind, item_ref=None, qty=qty)


def _member_resp(discord_ids: list[int]) -> Response:
    data = [{"user": {"id": str(did)}} for did in discord_ids]
    return Response(200, json={"data": data})


def _ok_resp() -> Response:
    return Response(200, json={"id": "1"})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_three_way_tie_first_place_all_get_1st(engine_and_factory):
    """3-way tie for 1st: three rank-1 payouts; 2nd/3rd prizes unawarded."""
    _, factory = engine_and_factory

    async with factory() as db:
        # Setup guild config
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=111)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()

        # Three per-rank prizes
        db.add(_make_prize(ev.id, 1, 1, qty=500))  # 1st place: 500 credits
        db.add(_make_prize(ev.id, 2, 2, qty=300))  # 2nd place: 300 credits
        db.add(_make_prize(ev.id, 3, 3, qty=100))  # 3rd place: 100 credits

        # Three users+players with the same score
        for i, did in enumerate([101, 102, 103], 1):
            db.add(_make_user(did, f"Player{i}"))
            await db.flush()
            db.add(_make_player(did, credits=0))
        await db.commit()

    async with factory() as db:
        result = await db.execute(select(Player))
        players = result.scalars().all()
        ev = (await db.execute(select(GameEvent))).scalar_one()

        # Seed metric rows — all same value
        for p in players:
            db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=10))
            db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=10))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=111)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([101, 102, 103]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            await end_event(db, ev, payout=True)

    async with factory() as db:
        results = (await db.execute(select(EventResult))).scalars().all()
        assert len(results) == 3, f"expected 3 result rows, got {len(results)}"
        ranks = [r.rank for r in results]
        assert all(rk == 1 for rk in ranks), f"all should be rank 1, got {ranks}"
        # All should receive the 1st-place prize (500 credits in prize text)
        for r in results:
            assert "500" in r.prize, f"expected 1st-prize (500) in prize text, got: {r.prize!r}"
            assert r.status == "ok"

        players = (await db.execute(select(Player))).scalars().all()
        # Each player should have gained 500 credits (1st-place prize)
        for p in players:
            assert p.credits == 500, f"player {p.id} expected 500 credits, got {p.credits}"

    # Event is ended
    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        assert ev.state == "ended"


async def test_top_n_range_tie_straddling_gives_4_recipients(engine_and_factory):
    """Top-3 slot + tie straddling 3rd → 4 recipients."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=222)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 3, qty=200))  # top-3 range

        # 4 players: A=10, B=8, C=6, D=6 → ranks 1,2,3,3 → 4 get top-3
        for i, (did, _score) in enumerate([(201, 10), (202, 8), (203, 6), (204, 6)], 1):
            db.add(_make_user(did, f"P{i}"))
            await db.flush()
            db.add(_make_player(did, credits=0))
        await db.commit()

    async with factory() as db:
        players = (await db.execute(select(Player))).scalars().all()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        scores = {201: 10, 202: 8, 203: 6, 204: 6}
        for p in players:
            db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=scores[p.user_id]))
            db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=scores[p.user_id]))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=222)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([201, 202, 203, 204]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            await end_event(db, ev, payout=True)

    async with factory() as db:
        results = (await db.execute(select(EventResult))).scalars().all()
        assert len(results) == 4, f"expected 4, got {len(results)}"
        # C and D both rank 3 — all get the top-3 prize
        assert all(r.status == "ok" for r in results)
        assert all("200" in r.prize for r in results)


async def test_participation_only_qualified(engine_and_factory):
    """Participation slot: only qualified players get it (qualified=True only)."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=333)
        db.add(cfg)
        # Use bounty_caps which is always qualified=True (no extra condition)
        ev = _make_event(slug="bounty_caps")
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, None, None, qty=50))  # participation

        # 2 players: one has a contribution, one has 0 (will still be "qualified" since bounty_caps has no min)
        for did in [301, 302]:
            db.add(_make_user(did, f"P{did}"))
            await db.flush()
            db.add(_make_player(did, credits=0))
        await db.commit()

    async with factory() as db:
        players = (await db.execute(select(Player))).scalars().all()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        # Only player 301 has a metric row → only they appear in standings
        for p in players:
            if p.user_id == 301:
                db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=5))
                db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=5))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=333)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([301, 302]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            await end_event(db, ev, payout=True)

    async with factory() as db:
        results = (await db.execute(select(EventResult))).scalars().all()
        # Only player 301 appears in standings (only they have a metric row)
        assert len(results) == 1
        assert results[0].status == "ok"

        p301 = (await db.execute(select(Player).where(Player.user_id == 301))).scalar_one()
        p302 = (await db.execute(select(Player).where(Player.user_id == 302))).scalar_one()
        assert p301.credits == 50
        assert p302.credits == 0  # no metric row → not in standings → no prize


async def test_departed_player_forfeits(engine_and_factory):
    """Player absent from guild members is filtered out; remaining players are re-ranked."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=444)
        db.add(cfg)
        ev = _make_event(slug="bounty_caps")
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=300))  # 1st place only

        for did, _score in [(401, 10), (402, 8), (403, 6)]:
            db.add(_make_user(did, f"P{did}"))
            await db.flush()
            db.add(_make_player(did, credits=0))
        await db.commit()

    async with factory() as db:
        players = (await db.execute(select(Player))).scalars().all()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        scores = {401: 10, 402: 8, 403: 6}
        for p in players:
            db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=scores[p.user_id]))
            db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=scores[p.user_id]))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=444)
    with respx.mock:
        # Player 401 (top scorer) is departed — only 402, 403 are members
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([402, 403]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            await end_event(db, ev, payout=True)

    async with factory() as db:
        results = (await db.execute(select(EventResult))).scalars().all()
        # Only 402 and 403 should have result rows (401 was filtered)
        assert len(results) == 2

        p401 = (await db.execute(select(Player).where(Player.user_id == 401))).scalar_one()
        p402 = (await db.execute(select(Player).where(Player.user_id == 402))).scalar_one()
        p403 = (await db.execute(select(Player).where(Player.user_id == 403))).scalar_one()

        # 401 departed — gets nothing
        assert p401.credits == 0
        # 402 is now rank 1 (highest remaining) — gets 1st prize
        assert p402.credits == 300
        assert p403.credits == 0  # rank 2 — no 2nd-place prize

        r402 = next(r for r in results if r.rank == 1)
        assert r402.status == "ok"


async def test_end_event_idempotent(engine_and_factory):
    """Calling end_event twice on the same event is a no-op the second time."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=555)
        db.add(cfg)
        ev = _make_event(slug="bounty_caps")
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=100))

        db.add(_make_user(501, "P501"))
        await db.flush()
        db.add(_make_player(501, credits=0))
        await db.commit()

    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=5))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=5))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=555)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([501]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            first = await end_event(db, ev, payout=True)
        assert first  # first call returns non-empty

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            second = await end_event(db, ev, payout=True)
        assert second == {}  # idempotent no-op returns empty dict

    async with factory() as db:
        results = (await db.execute(select(EventResult))).scalars().all()
        assert len(results) == 1  # only one set of result rows


async def test_partial_failure_item_ref_missing(engine_and_factory):
    """Item prize with item_ref=None → partial failure; event still ends; other ok slots unaffected."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=666)
        db.add(cfg)
        ev = _make_event(slug="bounty_caps")
        db.add(ev)
        await db.flush()
        # Participation credit prize (ok path) + item prize with null item_ref (fail path)
        db.add(_make_prize(ev.id, None, None, qty=50, kind="credits"))  # participation — ok
        db.add(
            GameEventPrize(  # 1st place item — will fail (item_ref=None)
                event_id=ev.id, rank_from=1, rank_to=1, kind="item", item_ref=None, qty=1
            )
        )

        db.add(_make_user(601, "P601"))
        await db.flush()
        db.add(_make_player(601, credits=0))
        await db.commit()

    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=5))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=5))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=666)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([601]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            await end_event(db, ev, payout=True)

    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        assert ev.state == "ended"  # event ended despite partial failure

        results = (await db.execute(select(EventResult))).scalars().all()
        assert len(results) == 1
        r = results[0]
        # partial failure from the item slot; the credit slot succeeded
        assert r.status.startswith("partial"), f"expected partial, got {r.status!r}"
        assert "credits" in r.prize, f"credit prize text should appear: {r.prize!r}"

        p = (await db.execute(select(Player))).scalar_one()
        assert p.credits == 50  # credit participation prize was awarded despite item slot failing


# ---------------------------------------------------------------------------
# Slice-3 fix-pass tests (issue #30, slice 3 review)
# ---------------------------------------------------------------------------


async def test_announce_failure_is_nonfatal(engine_and_factory):
    """announce() with a 500 response does not raise; end_event state=ended and results written
    regardless (announce is caller-posted after commit; a channel error must not abort results)."""
    from services.event_service import announce

    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=700)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=100))
        db.add(_make_user(701, "A701"))
        await db.flush()
        db.add(_make_player(701, credits=0))
        await db.commit()

    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=3))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=3))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=700)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([701]))
        # end_event does NOT call announce; this mock verifies the 500 is non-fatal when called
        respx.post(channel_url).mock(return_value=Response(500, text="internal error"))

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            result = await end_event(db, ev, payout=True)

        # Caller posts the announcement — a 500 must not raise
        ann = result.get("announcement")
        assert ann is not None
        await announce(*ann)  # non-fatal: logs the error, does not raise

    # DB state is correct: event ended and results written before any announce attempt
    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        assert ev.state == "ended"
        results = (await db.execute(select(EventResult))).scalars().all()
        assert len(results) == 1
    assert ann[0] == GUILD_ID
    assert ann[1] == 700


async def test_gateway_members_500_skips_filter_all_paid(engine_and_factory):
    """Gateway /members 500 → membership filter skipped; all qualified players are paid."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=800)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=200))
        for did in [801, 802]:
            db.add(_make_user(did, f"P{did}"))
            await db.flush()
            db.add(_make_player(did, credits=0))
        await db.commit()

    async with factory() as db:
        players = (await db.execute(select(Player))).scalars().all()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        scores = {801: 10, 802: 8}
        for p in players:
            db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=scores[p.user_id]))
            db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=scores[p.user_id]))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=800)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=Response(500, text="err"))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            await end_event(db, ev, payout=True)

    # Both players paid (filter skipped); rank 1 gets the prize
    async with factory() as db:
        results = (await db.execute(select(EventResult))).scalars().all()
        assert len(results) == 2
        p801 = (await db.execute(select(Player).where(Player.user_id == 801))).scalar_one()
        assert p801.credits == 200  # rank 1 wins
        p802 = (await db.execute(select(Player).where(Player.user_id == 802))).scalar_one()
        assert p802.credits == 0  # rank 2, no prize


async def test_gateway_members_timeout_skips_filter_all_paid(engine_and_factory):
    """Gateway /members timeout → membership filter skipped; all qualified players paid."""
    import httpx as _httpx

    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=810)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=150))
        db.add(_make_user(811, "P811"))
        await db.flush()
        db.add(_make_player(811, credits=0))
        await db.commit()

    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=5))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=5))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=810)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(side_effect=_httpx.ReadTimeout("timeout", request=None))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            await end_event(db, ev, payout=True)

    async with factory() as db:
        p811 = (await db.execute(select(Player).where(Player.user_id == 811))).scalar_one()
        assert p811.credits == 150


async def test_zero_participant_event_ends_cleanly(engine_and_factory):
    """Event with no metric rows → state=ended, no EventResult rows, no crash."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=900)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=100))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=900)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            result = await end_event(db, ev, payout=True)

    assert result.get("status") == "none"
    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        assert ev.state == "ended"
        results = (await db.execute(select(EventResult))).scalars().all()
        assert results == []


async def test_end_event_payout_false_returns_cancelled_announcement(engine_and_factory):
    """end_event(payout=False) → state=cancelled, no minting, announcement returned in result."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=950)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=500))
        db.add(_make_user(951, "P951"))
        await db.flush()
        p = _make_player(951, credits=1000)
        db.add(p)
        await db.flush()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=10))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=10))
        ev.state = "active"
        await db.commit()

    async with factory() as db, db.begin():
        ev = (await db.execute(select(GameEvent))).scalar_one()
        result = await end_event(db, ev, payout=False, reason="Test cancel")

    assert result["status"] == "cancelled"
    # Announcement tuple should be present (guild_id, channel_id, embed, text)
    ann = result.get("announcement")
    assert ann is not None
    assert ann[0] == GUILD_ID
    assert ann[1] == 950
    assert "Cancelled" in ann[2].get("title", "")

    # No credits minted, no result rows
    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        assert ev.state == "cancelled"
        results = (await db.execute(select(EventResult))).scalars().all()
        assert results == []
        p951 = (await db.execute(select(Player).where(Player.user_id == 951))).scalar_one()
        assert p951.credits == 1000  # unchanged


# ---------------------------------------------------------------------------
# Announcement body / request inspection tests (item 5)
# ---------------------------------------------------------------------------


async def test_end_announcement_embed_structure_and_role_mention(engine_and_factory):
    """end_event POST to channel carries correct embed shape and role mention when configured."""
    _, factory = engine_and_factory
    role_id = 99_999

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=1001, event_announcements_role_id=role_id)
        db.add(cfg)
        ev = _make_event(slug="bounty_caps")
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=500))
        db.add(_make_user(1001, "Winner"))
        await db.flush()
        db.add(_make_player(1001, credits=0))
        await db.commit()

    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=5))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=5))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=1001)
    members_url = _MEMBERS_URL
    with respx.mock:
        members_route = respx.get(members_url).mock(return_value=_member_resp([1001]))
        channel_route = respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            result = await end_event(db, ev, payout=True)

        # announce-after-commit: caller must POST the announcement tuple
        if result.get("announcement"):
            await announce(*result["announcement"])

    # members GET must carry limit param
    assert members_route.called
    assert "limit=5000" in str(members_route.calls[0].request.url)

    # channel POST was called — inspect body
    assert channel_route.called
    import json as _json

    body = _json.loads(channel_route.calls[0].request.content)
    assert body["message_type"] == "default"
    embed = body["content"]
    assert isinstance(embed.get("title"), str)
    assert isinstance(embed.get("description"), str)
    assert isinstance(embed.get("color"), int)
    fields = embed.get("fields", [])
    assert isinstance(fields, list)
    for f in fields:
        assert "name" in f and "value" in f and "inline" in f

    # text_content must mention the role
    tc = body.get("text_content", "") or ""
    assert f"<@&{role_id}>" in tc, f"Expected role mention in text_content, got: {tc!r}"

    # end embed description names the ranked winner
    winner_in_desc = "Winner" in embed["description"]
    winner_in_fields = any("Winner" in f.get("value", "") for f in fields)
    assert winner_in_desc or winner_in_fields


async def test_end_announcement_mentions_only_opted_in_winners(engine_and_factory):
    """Winner @mentions honour the opt-out: an opted-out placed player is named in the embed but never pinged."""
    _, factory = engine_and_factory
    role_id = 99_999

    async with factory() as db:
        db.add(GuildConfig(guild_id=GUILD_ID, discussion_channel_id=1001, event_announcements_role_id=role_id))
        ev = _make_event(slug="bounty_caps")
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=500))
        db.add(_make_prize(ev.id, 2, 2, qty=100))
        db.add(_make_user(1001, "Loud"))
        db.add(_make_user(1002, "Quiet"))
        await db.flush()
        db.add(_make_player(1001, credits=0))
        quiet = _make_player(1002, credits=0)
        quiet.event_notifications_enabled = False
        db.add(quiet)
        await db.commit()

    async with factory() as db:
        players = {p.user_id: p for p in (await db.execute(select(Player))).scalars().all()}
        ev = (await db.execute(select(GameEvent))).scalar_one()
        for uid, caps in ((1001, 5), (1002, 3)):
            db.add(GameEventMetric(event_id=ev.id, player_id=players[uid].id, metric="captures", value=caps))
            db.add(GameEventMetric(event_id=ev.id, player_id=players[uid].id, metric="checks", value=caps))
        ev.state = "active"
        await db.commit()

    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([1001, 1002]))
        respx.post(_CHANNEL_URL_TMPL.format(channel_id=1001)).mock(return_value=_ok_resp())
        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            result = await end_event(db, ev, payout=True)

    _guild_id, _channel_id, embed, text_content = result["announcement"]
    assert f"<@&{role_id}>" in text_content and "<@1001>" in text_content
    assert "<@1002>" not in text_content
    assert "Quiet" in json.dumps(embed)  # still listed as 2nd place, just not pinged


async def test_end_announcement_no_role_mention_when_null(engine_and_factory):
    """end_event: text_content has no role mention when event_announcements_role_id is NULL."""
    _, factory = engine_and_factory

    async with factory() as db:
        # No event_announcements_role_id set
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=1002)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=100))
        db.add(_make_user(1002, "P1002"))
        await db.flush()
        db.add(_make_player(1002, credits=0))
        await db.commit()

    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=5))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=5))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=1002)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([1002]))
        channel_route = respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            result = await end_event(db, ev, payout=True)

        # announce-after-commit: caller must POST the announcement tuple
        if result.get("announcement"):
            await announce(*result["announcement"])

    import json as _json

    body = _json.loads(channel_route.calls[0].request.content)
    tc = body.get("text_content")
    # text_content should be None or contain no <@&...> mention
    assert tc is None or "<@&" not in tc, f"Unexpected role mention in text_content: {tc!r}"


async def test_cancel_announcement_embed_structure(engine_and_factory):
    """end_event(payout=False) POST has correct embed shape: title, description, color, fields."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=1003)
        db.add(cfg)
        ev = _make_event()
        db.add(ev)
        await db.flush()
        ev.state = "active"
        await db.commit()

    async with factory() as db, db.begin():
        ev = (await db.execute(select(GameEvent))).scalar_one()
        result = await end_event(db, ev, payout=False, reason="Admin cancelled")

    ann = result.get("announcement")
    assert ann is not None
    _guild_id, _channel_id, embed, text_content = ann
    assert isinstance(embed.get("title"), str)
    assert isinstance(embed.get("description"), str)
    assert isinstance(embed.get("color"), int)
    assert isinstance(embed.get("fields"), list)
    for f in embed["fields"]:
        assert "name" in f and "value" in f and "inline" in f
    assert text_content is None  # no mentions on cancel


async def test_start_announcement_embed_structure_and_role_mention(engine_and_factory):
    """start_event returns announcement tuple with correct embed shape and role mention."""
    from services.event_service import start_event

    _, factory = engine_and_factory
    role_id = 77_777

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=1004, event_announcements_role_id=role_id)
        db.add(cfg)
        ev = _make_event(slug="bounty_caps")
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=200))
        await db.commit()

    async with factory() as db:
        ev = (await db.execute(select(GameEvent))).scalar_one()
        ann = await start_event(db, ev)
        await db.commit()

    assert ann is not None
    _guild_id, _channel_id, embed, text_content = ann
    assert isinstance(embed.get("title"), str)
    assert isinstance(embed.get("description"), str)
    assert isinstance(embed.get("color"), int)
    fields = embed.get("fields", [])
    assert isinstance(fields, list)
    for f in fields:
        assert "name" in f and "value" in f and "inline" in f

    assert text_content == f"<@&{role_id}>", f"Expected role mention, got: {text_content!r}"


async def test_end_announcement_embed_shows_prize_and_participation(engine_and_factory):
    """end_event embed description includes prize text per ranked player;
    embed fields include a Participation summary when a participation slot exists."""
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=2001)
        db.add(cfg)
        ev = _make_event(slug="bounty_caps")
        db.add(ev)
        await db.flush()
        db.add(_make_prize(ev.id, 1, 1, qty=500))  # 1st place: 500 credits
        db.add(_make_prize(ev.id, None, None, qty=50))  # participation: 50 credits
        db.add(_make_user(2001, "TopPlayer"))
        await db.flush()
        db.add(_make_player(2001, credits=0))
        await db.commit()

    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="captures", value=10))
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="checks", value=10))
        ev.state = "active"
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=2001)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([2001]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            result = await end_event(db, ev, payout=True)

        if result.get("announcement"):
            await announce(*result["announcement"])

    ann = result["announcement"]
    assert ann is not None
    _gid, _cid, embed, _text = ann

    # Per-player prize text in description
    desc = embed["description"]
    assert "TopPlayer" in desc
    assert "500" in desc, f"expected 1st-prize credits in description: {desc!r}"

    # Participation summary field
    fields = {f["name"]: f["value"] for f in embed.get("fields", [])}
    assert "Participation" in fields, f"expected Participation field, got fields: {list(fields)}"
    assert "50" in fields["Participation"]
    assert "1 recipients" in fields["Participation"] or "recipients" in fields["Participation"]


async def test_lossless_event_participation_paid(engine_and_factory):
    """Lossless event scenario (spec §3, user 2026-09-04):
    duels_won, min_fights=3, participation 3000 credits, stakes floor 1000.
    A player who loses 3 qualifying duels (0 wins, 3 duel_fights) is qualified
    and receives the participation prize.
    """
    _, factory = engine_and_factory

    async with factory() as db:
        cfg = GuildConfig(guild_id=GUILD_ID, discussion_channel_id=5001, event_min_duel_stakes=1000)
        db.add(cfg)
        ev = _make_event(slug="duels_won")
        ev.params = {"min_fights": 3}
        db.add(ev)
        await db.flush()
        # Participation prize only (no rank prizes)
        db.add(_make_prize(ev.id, None, None, qty=3000))
        db.add(_make_user(5001, "Loser"))
        await db.flush()
        db.add(_make_player(5001, credits=0))
        await db.commit()

    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        ev = (await db.execute(select(GameEvent))).scalar_one()
        ev.state = "active"
        # Three losses: duel_fights=3, no duel_wins row
        db.add(GameEventMetric(event_id=ev.id, player_id=p.id, metric="duel_fights", value=3))
        await db.commit()

    channel_url = _CHANNEL_URL_TMPL.format(channel_id=5001)
    with respx.mock:
        respx.get(_MEMBERS_URL).mock(return_value=_member_resp([5001]))
        respx.post(channel_url).mock(return_value=_ok_resp())

        async with factory() as db, db.begin():
            ev = (await db.execute(select(GameEvent))).scalar_one()
            result = await end_event(db, ev, payout=True)

        if result.get("announcement"):
            await announce(*result["announcement"])

    assert result.get("ranked_players") == 1, "Winless player should be ranked (qualified=True)"
    # Verify credit payout: player had 0 credits, should now have 3000
    async with factory() as db:
        p = (await db.execute(select(Player))).scalar_one()
        assert p.credits == 3000, f"Expected 3000 credits (participation prize), got {p.credits}"
