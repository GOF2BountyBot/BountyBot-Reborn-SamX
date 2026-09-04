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


# ---------------------------------------------------------------------------
# Slice 2 hook tests (issue #30)
# ---------------------------------------------------------------------------


def _all_registry_keys() -> set[str]:
    """Expand all registry metric keys across all known param variants.

    For parameterised types, enumerate known param values from the spec catalog
    to build the full set of concrete keys the registry can consume.
    # ponytail: hardcoded variant lists; update if new param values are added.
    """
    from services.event_types import EVENT_TYPES, resolve_metrics

    _SUBTYPE_VARIANTS = ["nuke", "rocket", "missile", "cluster-missile", "emp-bomb", "shock-blast",
                         "primary", "turret", "ionizing-missile"]
    _MODULE_VARIANTS = ["cloak", "booster", "emergency_system"]
    _WEAPON_VARIANTS = _SUBTYPE_VARIANTS  # weapon param uses same subtype strings

    keys: set[str] = set()
    for slug, et in EVENT_TYPES.items():
        param_combos: list[dict] = [{}]
        if "{subtype}" in str(et.metrics):
            param_combos = [{"subtype": v} for v in _SUBTYPE_VARIANTS]
        elif "{module}" in str(et.metrics):
            param_combos = [{"module": v} for v in _MODULE_VARIANTS]
        elif "{weapon}" in str(et.metrics):
            param_combos = [{"weapon": v} for v in _WEAPON_VARIANTS]
        for params in param_combos:
            keys.update(resolve_metrics(slug, params).keys())
    return keys


def _make_fight_contrib(
    *,
    is_winner: bool = True,
    is_stalemate: bool = False,
    secondary_subtypes: dict | None = None,
    module_activations: dict | None = None,
    killing_blow_subtype: str | None = None,
) -> dict[str, float]:
    """Build a representative fight-hook contrib dict (mirrors combat_service logic)."""
    contrib: dict[str, float] = {
        "fights": 1.0,
        "shots_fired": 10.0,
        "shots": 10.0,
        "hits": 7.0,
        "total_damage_dealt": 500.0,
        "max_damage_dealt": 500.0,
        "max_damage_taken": 300.0,
        "max_nuke_absorbed": 200.0,
    }
    for sub, cnt in (secondary_subtypes or {"nuke": 2}).items():
        contrib[f"secondary_fired:{sub}"] = float(cnt)
    for mod, cnt in (module_activations or {"cloak": 1}).items():
        contrib[f"module_activations:{mod}"] = float(cnt)
    if is_winner:
        contrib["duration_ticks_win"] = 100.0
        if killing_blow_subtype:
            contrib[f"kills_by_weapon:{killing_blow_subtype}"] = 1.0
    elif not is_stalemate:  # mirrors combat_service: stalemate emits neither duration key
        contrib["duration_ticks_loss"] = 100.0
    return contrib


async def test_fight_contrib_keys_subset_of_registry(engine_and_factory):
    """All keys in the fight-hook contrib dict are known to the registry.

    This is the mismatch guard (brief §7a): if a key the hook emits is not in
    the registry, no event type can ever consume it and we have a silent bug.
    """
    registry_keys = _all_registry_keys()
    contrib = _make_fight_contrib(
        is_winner=True,
        secondary_subtypes={"nuke": 2, "rocket": 1},
        module_activations={"cloak": 1, "booster": 2},
        killing_blow_subtype="nuke",
    )
    unknown = set(contrib.keys()) - registry_keys
    assert not unknown, f"Fight contrib contains keys unknown to the registry: {unknown}"
    loser = _make_fight_contrib(is_winner=False)
    assert not (set(loser) - registry_keys), "loser-path contrib has keys unknown to the registry"
    assert "duration_ticks_loss" in loser
    stalemate = _make_fight_contrib(is_winner=False, is_stalemate=True)
    assert "duration_ticks_loss" not in stalemate and "duration_ticks_win" not in stalemate


async def test_duel_stalemate_records_only_duel_fights(engine_and_factory):
    """Stalemate duel: record() with {duel_fights: 1} only — no win/loss/credits rows."""
    _, factory = engine_and_factory
    ev = _make_event(slug="duels_fought", params={})
    async with factory() as session:
        await _seed(session, stakes=500, events=[ev])
        await session.refresh(ev)
        # Stalemate: BOTH players get only duel_fights=1
        for pid in (1, 2):
            await record(session, _make_player(id=pid), {"duel_fights": 1.0}, context="duel", stakes=1000)
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(GameEventMetric))).scalars().all()
        assert sorted(r.player_id for r in rows) == [1, 2]
        assert all(r.metric == "duel_fights" and float(r.value) == 1.0 for r in rows)


async def test_on_tier_change_deletes_division_rows_only(engine_and_factory):
    """on_tier_change deletes only division-scoped active event metrics for the player."""
    from services.event_service import on_tier_change

    _, factory = engine_and_factory
    # Two active events: one division-scoped, one not
    ev_div = _make_event(slug="fights_fought", params={"division": "Silver"})
    ev_plain = _make_event(slug="bounty_caps", params={})
    player = _make_player(id=42)

    async with factory() as session:
        await _seed(session, events=[ev_div, ev_plain])
        await session.refresh(ev_div)
        await session.refresh(ev_plain)
        # Seed one metric row per event for this player
        session.add(GameEventMetric(event_id=ev_div.id, player_id=player.id, metric="fights", value=5))
        session.add(GameEventMetric(event_id=ev_plain.id, player_id=player.id, metric="captures", value=3))
        await session.commit()

    async with factory() as session:
        await on_tier_change(session, player)
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(GameEventMetric))).scalars().all()
        # Only the non-division row should survive
        assert len(rows) == 1
        assert rows[0].metric == "captures"
        assert float(rows[0].value) == 3.0


# ---------------------------------------------------------------------------
# Participation = did the activity (user decision 2026-09-04)
# ---------------------------------------------------------------------------


async def test_winless_dueller_appears_at_zero(engine_and_factory):
    """A player who loses all duels in a duels_won event has value=0 in standings.

    After the activity-metric change, duel_fights is tallied even on losses,
    so the player appears on the board at 0 and qualifies if duel_fights >= min_fights.
    """
    _, factory = engine_and_factory
    ev = _make_event(slug="duels_won", params={})  # default_min_fights=1
    async with factory() as session:
        await _seed(session, stakes=500, events=[ev])
        await session.refresh(ev)
        player = _make_player(id=7)
        # Three qualifying duel losses: each contributes duel_fights=1; duel_wins=0 (skipped)
        for _ in range(3):
            await record(session, player, {"duel_losses": 1.0, "duel_fights": 1.0}, context="duel", stakes=1000)
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        ev2 = (await session.execute(select(GameEvent))).scalar_one()
        result = await standings(session, ev2)
        assert len(result) == 1
        pid, val, qual = result[0]
        assert pid == 7
        assert val == 0.0, "Winless player has value=0 (no duel_wins)"
        assert qual is True, "duel_fights=3 >= default_min_fights=1 → qualified"


async def test_min_fights_per_event_gates_qualification(engine_and_factory):
    """With per-event min_fights=3, a player with 2 fights is unqualified; at 3 they qualify."""
    _, factory = engine_and_factory
    # min_fights=3 stored in event params
    ev = _make_event(slug="duels_won", params={"min_fights": 3})
    async with factory() as session:
        await _seed(session, stakes=500, events=[ev])
        await session.refresh(ev)
        player_a = _make_player(id=8)
        player_b = _make_player(id=9)
        # Player A: 2 fights → unqualified
        for _ in range(2):
            await record(session, player_a, {"duel_losses": 1.0, "duel_fights": 1.0}, context="duel", stakes=1000)
        # Player B: 3 fights → qualified
        for _ in range(3):
            await record(session, player_b, {"duel_losses": 1.0, "duel_fights": 1.0}, context="duel", stakes=1000)
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        ev2 = (await session.execute(select(GameEvent))).scalar_one()
        result = await standings(session, ev2)
        by_pid = {pid: qual for pid, _val, qual in result}
        assert by_pid[8] is False, "2 fights with min_fights=3 → unqualified"
        assert by_pid[9] is True, "3 fights with min_fights=3 → qualified"


# ---------------------------------------------------------------------------
# concurrent end_event guard (item 2)
# ---------------------------------------------------------------------------


async def test_second_end_event_is_noop(engine_and_factory):
    """A second end_event after the first committed returns the idempotent no-op result ({}).

    The SELECT FOR UPDATE guard is a no-op on SQLite, so the true race is only
    covered by the row lock on Postgres; this test verifies the idempotency path.
    """
    from services.event_service import end_event
    from sqlalchemy import select as sa_select

    _, factory = engine_and_factory
    ev = _make_event(slug="duels_won", params={})
    async with factory() as session:
        await _seed(session, events=[ev])
        await session.refresh(ev)
        ev_id = ev.id

    # First end_event — should succeed and return non-empty summary
    async with factory() as session:
        ev1 = (await session.execute(sa_select(GameEvent).where(GameEvent.id == ev_id))).scalar_one()
        result1 = await end_event(session, ev1, payout=False, reason="test", actor_user_id=1)
        await session.commit()

    assert result1 != {}, "First end_event should return a non-empty summary"

    # Second end_event — event is no longer active → idempotent no-op
    async with factory() as session:
        ev2 = (await session.execute(sa_select(GameEvent).where(GameEvent.id == ev_id))).scalar_one()
        result2 = await end_event(session, ev2, payout=False, reason="test", actor_user_id=1)
        await session.commit()

    assert result2 == {}, "Second end_event must return {} (idempotent no-op)"


# ---------------------------------------------------------------------------
# render_rules unit tests (ux-followups.md item 3, all 21 types)
# ---------------------------------------------------------------------------


def test_render_rules_duels_won_approved_wording():
    """duels_won stakes=1000 / min_fights=3 / no division == user-approved sentence + Prizes line."""
    from services.event_types import EVENT_TYPES, render_rules

    et = EVENT_TYPES["duels_won"]
    result = render_rules(et, min_stakes=1000, min_fights=3, division=None, params={})
    assert result.startswith("Win the most duels."), f"wrong opening: {result!r}"
    assert "1,000 credits" in result, f"stake amount missing: {result!r}"
    assert "Stalemates count as fights but not wins." in result, f"stalemates clause missing: {result!r}"
    assert "Losing still counts as taking part." in result, f"losing clause missing: {result!r}"
    assert "Prizes require at least 3 battles." in result, f"prizes line missing: {result!r}"


def test_render_rules_secondary_fired_nuke():
    """secondary_fired subtype=nuke mentions nukes and both duel stakes and bounty fights."""
    from services.event_types import EVENT_TYPES, render_rules

    et = EVENT_TYPES["secondary_fired"]
    result = render_rules(et, min_stakes=1000, min_fights=1, division=None, params={"subtype": "nuke"})
    assert "nuke" in result, f"subtype nuke missing: {result!r}"
    assert "1,000 credits" in result, f"duel stakes missing: {result!r}"
    assert "bounty fights always count" in result, f"bounty context missing: {result!r}"
    assert "Prizes require" not in result, "min_fights=1 should append nothing"


def test_render_rules_bounty_caps_checks_no_stakes():
    """bounty_caps mentions /check and has no duel-stakes text (new wording)."""
    from services.event_types import EVENT_TYPES, render_rules

    et = EVENT_TYPES["bounty_caps"]
    result = render_rules(et, min_stakes=1000, min_fights=1, division=None, params={})
    assert "/check" in result, f"'/check' missing: {result!r}"
    assert "criminal" in result, f"'criminal' missing from new wording: {result!r}"
    assert "credits" not in result, f"stakes should not appear for bounty type: {result!r}"


def test_render_rules_bounty_caps_min_fights_says_checks():
    """bounty_caps with min_fights=5 appends 'checks' not 'battles'."""
    from services.event_types import EVENT_TYPES, render_rules

    et = EVENT_TYPES["bounty_caps"]
    result = render_rules(et, min_stakes=1000, min_fights=5, division=None, params={})
    assert "Prizes require at least 5 checks." in result, f"checks line missing: {result!r}"
    assert "battles" not in result, f"'battles' (plural) should not appear for bounty type: {result!r}"


def test_longest_battle_won_fmt_ticks_to_seconds():
    """longest_battle_won.fmt converts ticks to seconds rounded to 1 decimal."""
    from services.event_types import EVENT_TYPES

    et = EVENT_TYPES["longest_battle_won"]
    # 1234 ticks * 10 ms/tick = 12340 ms = 12.34 s → formatted as "12.3s"
    assert et.fmt(1234) == "12.3s", f"unexpected: {et.fmt(1234)!r}"
    # sanity: zero ticks
    assert et.fmt(0) == "0.0s"


def test_render_rules_min_fights_1_appends_nothing():
    """min_fights=1 never appends the 'Prizes require' line."""
    from services.event_types import EVENT_TYPES, render_rules

    et = EVENT_TYPES["duels_won"]
    result = render_rules(et, min_stakes=500, min_fights=1, division=None, params={})
    assert "Prizes require" not in result, f"should not appear for min_fights=1: {result!r}"


def test_render_rules_division_appends_line():
    """division='Bronze' appends 'Bronze division only.'"""
    from services.event_types import EVENT_TYPES, render_rules

    et = EVENT_TYPES["duels_won"]
    result = render_rules(et, min_stakes=1000, min_fights=1, division="Bronze", params={"division": "Bronze"})
    assert "Bronze division only." in result, f"division line missing: {result!r}"
