"""PG integration — /check counts at check time (issue #124).

  f. INCORRECT check increments players.systems_checked and the event checks metric.
  g. SAVEPOINT proof: a Postgres error inside event_service.record does not abort
     the outer transaction; the surrounding caller's write commits successfully.
  b. A capture adds captures=1 and no extra checks (2 checks → checks == 2, not 4).
  c. Found-but-lost and stalemate checks count (Silver, combat stubbed).
  d. A multi-bounty /check counts N for both counters.
  e. NOT_FOUND adds nothing.

Connection: pg_env.PG_ASYNC_URL (CI postgres service / dev stack).
Skipped when the DB is unreachable or not migrated.
"""

from __future__ import annotations

import os
import sys
import types
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _sau = types.ModuleType("sqlalchemy_utils")
    _sau.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sau

from unittest.mock import AsyncMock

import pytest
from persist.models.game_event import GameEvent, GameEventMetric
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from services.combat_models import FightResults, FightStats
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from services import event_service
from tests.pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

# Distinct namespace: high IDs to avoid collisions with prod/dev data.
_GUILD = 999_124_001
_DISCORD_A = 999_124_101  # user for test f
_DISCORD_B = 999_124_102  # user for test g
_DISCORD_C = 999_124_103  # user for test b (Bronze capture)
_DISCORD_D = 999_124_104  # user for test c (Silver loss/stalemate)
_DISCORD_E = 999_124_105  # user for test d (multi-bounty)
_DISCORD_F = 999_124_106  # user for test e (NOT_FOUND)

_pg_factory = async_sessionmaker(
    create_async_engine(PG_ASYNC_URL, poolclass=NullPool),
    class_=AsyncSession,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Cleanup helper
# ---------------------------------------------------------------------------


async def _cleanup() -> None:
    async with _pg_factory() as db, db.begin():
        # event_results FK → game_events (must precede game_events delete)
        await db.execute(
            text("DELETE FROM event_results WHERE event_id IN (SELECT id FROM game_events WHERE guild_id = :gid)"),
            {"gid": _GUILD},
        )
        # metrics first (FK → events + players)
        await db.execute(
            text("DELETE FROM game_event_metrics WHERE event_id IN (SELECT id FROM game_events WHERE guild_id = :gid)"),
            {"gid": _GUILD},
        )
        await db.execute(text("DELETE FROM game_events WHERE guild_id = :gid"), {"gid": _GUILD})
        await db.execute(
            text("DELETE FROM player_inventories WHERE player_id IN (SELECT id FROM players WHERE guild_id = :gid)"),
            {"gid": _GUILD},
        )
        await db.execute(text("DELETE FROM bounty WHERE guild_id = :gid"), {"gid": _GUILD})
        # Nullify active_ship_id before deleting player_ships (circular FK)
        await db.execute(
            text("UPDATE players SET active_ship_id = NULL WHERE guild_id = :gid"),
            {"gid": _GUILD},
        )
        await db.execute(
            text("DELETE FROM player_ships WHERE player_id IN (SELECT id FROM players WHERE guild_id = :gid)"),
            {"gid": _GUILD},
        )
        await db.execute(text("DELETE FROM players WHERE guild_id = :gid"), {"gid": _GUILD})
        await db.execute(
            text("DELETE FROM users WHERE id = ANY(:ids)"),
            {"ids": [_DISCORD_A, _DISCORD_B, _DISCORD_C, _DISCORD_D, _DISCORD_E, _DISCORD_F]},
        )
        await db.execute(text("DELETE FROM guild_configs WHERE guild_id = :gid"), {"gid": _GUILD})


# ---------------------------------------------------------------------------
# Test f: check_bounty INCORRECT → systems_checked + GameEventMetric persisted
# ---------------------------------------------------------------------------


async def test_incorrect_check_increments_systems_checked_and_event_metric():
    """INCORRECT /check atomically increments player.systems_checked and
    inserts a game_event_metrics checks=1.0 row via the begin_nested SAVEPOINT path.

    Reads back cross-session to confirm both writes persisted on Postgres (issue #124 f).
    """
    await _cleanup()

    # ---- Seed ----
    now = datetime.now(UTC)
    async with _pg_factory() as db, db.begin():
        db.add(User(id=_DISCORD_A, discord_username="bountybot_test_a"))
        await db.flush()
        db.add(
            Player(
                user_id=_DISCORD_A,
                guild_id=_GUILD,
                credits=1000,
                lifetime_credits=1000,
                xp=0,
                xp_surplus=0,
                tier="Bronze",
                systems_checked=0,
                bounty_wins=0,
                classic_mode=False,
            )
        )
        await db.flush()
        # Active "systems_checked" event — record() will upsert checks=1.0
        db.add(
            GameEvent(
                guild_id=_GUILD,
                type_slug="systems_checked",
                state="active",
                duration_days=7,
                started_at=now,
                ends_at=now + timedelta(days=7),
                created_by_user_id=0,
                created_at=now,
                updated_at=now,
            )
        )

    # Read back IDs
    async with _pg_factory() as db:
        result = await db.execute(select(Player).where(Player.user_id == _DISCORD_A))
        player_row = result.scalars().first()
        assert player_row is not None
        player_id = player_row.id

        result2 = await db.execute(select(GameEvent).where(GameEvent.guild_id == _GUILD))
        event_row = result2.scalars().first()
        assert event_row is not None
        event_id = event_row.id

    # Seed a bounty (INCORRECT system: "Alpha" is in route but not the answer "Sol")
    from persist.models.bounty import Bounty

    async with _pg_factory() as db, db.begin():
        db.add(
            Bounty(
                guild_id=_GUILD,
                division="bronze",
                criminal_name="Test Criminal (issue #124)",
                criminal_faction="terran",
                route=["Alpha", "Sol"],
                answer="Sol",
                reward=10000,
                reward_per_sys=1000,
                checked={"Alpha": -1, "Sol": -1},
                status="active",
                issue_time=now,
                end_time=now + timedelta(hours=8),
                tech_level=3,
                criminal_ship={"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []},
            )
        )

    # ---- Act ----
    from services.bounty_service import BountyService

    async with _pg_factory() as db:
        result = await BountyService().check_bounty(db, player_id, "Alpha", _GUILD)

    from services.bounty_service import CheckResult

    assert result.result == CheckResult.INCORRECT, f"Expected INCORRECT, got {result.result}"

    # ---- Assert (cross-session reads) ----
    async with _pg_factory() as db:
        p = await db.get(Player, player_id)
        assert p is not None
        assert p.systems_checked == 1, f"Expected systems_checked=1 (atomic UPDATE), got {p.systems_checked}"

        metric = (
            (
                await db.execute(
                    select(GameEventMetric).where(
                        GameEventMetric.event_id == event_id,
                        GameEventMetric.player_id == player_id,
                        GameEventMetric.metric == "checks",
                    )
                )
            )
            .scalars()
            .first()
        )
        assert metric is not None, "GameEventMetric row for checks must exist"
        assert float(metric.value) == 1.0, f"Expected checks=1.0, got {metric.value}"

    await _cleanup()


# ---------------------------------------------------------------------------
# Test g: SAVEPOINT isolation — Postgres error inside record() is contained
# ---------------------------------------------------------------------------


async def test_record_savepoint_isolates_pg_error():
    """A Postgres error inside event_service.record (begin_nested) must not abort
    the outer transaction.  The caller's surrounding write commits despite the inner
    failure.  Proves that begin_nested (SAVEPOINT) provides isolation on real PG
    (issue #124 g).
    """
    await _cleanup()

    # ---- Seed player and active event ----
    now = datetime.now(UTC)
    async with _pg_factory() as db, db.begin():
        db.add(User(id=_DISCORD_B, discord_username="bountybot_test_b"))
        await db.flush()
        db.add(
            Player(
                user_id=_DISCORD_B,
                guild_id=_GUILD,
                credits=100,
                lifetime_credits=100,
                xp=0,
                xp_surplus=0,
                tier="Bronze",
                systems_checked=0,
                bounty_wins=0,
                classic_mode=False,
            )
        )
        await db.flush()
        db.add(
            GameEvent(
                guild_id=_GUILD,
                type_slug="systems_checked",
                state="active",
                duration_days=7,
                started_at=now,
                ends_at=now + timedelta(days=7),
                created_by_user_id=0,
                created_at=now,
                updated_at=now,
            )
        )

    async with _pg_factory() as db:
        result = await db.execute(select(Player).where(Player.user_id == _DISCORD_B))
        player_row = result.scalars().first()
        assert player_row is not None
        player_id = player_row.id

    # ---- Act: outer transaction with a dirty write + record() forced to fail on PG ----
    # _upsert_metric executes "SELECT 1/0" → PG raises division_by_zero.
    # The begin_nested SAVEPOINT in record() must roll back that error so the
    # outer transaction (with player.credits=200) can still commit.

    async def _division_by_zero(session, *_args, **_kwargs):
        await session.execute(text("SELECT 1/0"))

    player_ns = SimpleNamespace(id=player_id, guild_id=_GUILD, tier="Bronze", user_id=_DISCORD_B)

    with patch("services.event_service._upsert_metric", side_effect=_division_by_zero):
        async with _pg_factory() as db, db.begin():
            p = await db.get(Player, player_id)
            p.credits = 200  # dirty write — must persist despite record() failing

            # record() will call _upsert_metric → division_by_zero → begin_nested rolls
            # back savepoint → except Exception swallows → outer transaction lives
            await event_service.record(db, player_ns, {"checks": 1.0}, context="bounty")
            # If we reach here, the outer transaction is still alive.
            # async with db.begin() auto-commits on clean exit.

    # ---- Assert: dirty write persisted ----
    async with _pg_factory() as db:
        p = await db.get(Player, player_id)
        assert p is not None
        assert p.credits == 200, (
            f"Expected credits=200 (outer write committed), got {p.credits}. "
            "The SAVEPOINT must have isolated the inner PG error."
        )

    await _cleanup()


# ---------------------------------------------------------------------------
# Helpers for tests b / c / d / e
# ---------------------------------------------------------------------------

_NOW_BOUNTY_END = 8  # hours from now for all test bounties

_FAKE_STATS = FightStats(ship_name="TestShip", raw_hp=100, raw_dps=10.0, varied_hp=100, varied_dps=10.0, ttk=10.0)

# FightResults for parametrized test c: player loses or stalemates
_FIGHT_LOSS = FightResults(
    winner_name="Criminal",
    loser_name="TestShip",
    is_stalemate=False,
    ship1_stats=_FAKE_STATS,
    ship2_stats=_FAKE_STATS,
    winner_side=2,
)
_FIGHT_STALEMATE = FightResults(
    winner_name=None,
    loser_name=None,
    is_stalemate=True,
    ship1_stats=_FAKE_STATS,
    ship2_stats=_FAKE_STATS,
    winner_side=None,
)


def _bounty_caps_event(now: datetime) -> GameEvent:
    return GameEvent(
        guild_id=_GUILD,
        type_slug="bounty_caps",
        state="active",
        duration_days=7,
        started_at=now,
        ends_at=now + timedelta(days=7),
        created_by_user_id=None,
        created_at=now,
        updated_at=now,
    )


def _systems_checked_event(now: datetime) -> GameEvent:
    return GameEvent(
        guild_id=_GUILD,
        type_slug="systems_checked",
        state="active",
        duration_days=7,
        started_at=now,
        ends_at=now + timedelta(days=7),
        created_by_user_id=None,
        created_at=now,
        updated_at=now,
    )


async def _read_metric(db_factory, event_id: int, player_id: int, metric: str):
    async with db_factory() as db:
        return (
            (
                await db.execute(
                    select(GameEventMetric).where(
                        GameEventMetric.event_id == event_id,
                        GameEventMetric.player_id == player_id,
                        GameEventMetric.metric == metric,
                    )
                )
            )
            .scalars()
            .first()
        )


# ---------------------------------------------------------------------------
# Test b: Capture adds no extra checks (Bronze, no ship → auto-capture)
# ---------------------------------------------------------------------------


async def test_capture_no_extra_checks():
    """Bronze auto-capture: /check wrong system, then answer.

    Expected: event checks == 2 (NOT 4), captures == 1,
    lifetime systems_checked == 2, bounty_wins == 1  (issue #124 b).
    """
    from persist.models.bounty import Bounty

    await _cleanup()
    now = datetime.now(UTC)

    async with _pg_factory() as db, db.begin():
        db.add(User(id=_DISCORD_C, discord_username="bountybot_test_c"))
        await db.flush()
        # check_cooldown=0 prevents the 3-minute gate between consecutive /checks
        db.add(GuildConfig(guild_id=_GUILD, check_cooldown=0))
        await db.flush()
        db.add(
            Player(
                user_id=_DISCORD_C,
                guild_id=_GUILD,
                credits=1000,
                lifetime_credits=1000,
                xp=0,
                xp_surplus=0,
                tier="Bronze",
                systems_checked=0,
                bounty_wins=0,
                classic_mode=False,
            )
        )
        await db.flush()
        db.add(_bounty_caps_event(now))

    async with _pg_factory() as db:
        player_row = (await db.execute(select(Player).where(Player.user_id == _DISCORD_C))).scalars().first()
        assert player_row is not None
        player_id = player_row.id
        event_row = (await db.execute(select(GameEvent).where(GameEvent.guild_id == _GUILD))).scalars().first()
        assert event_row is not None
        event_id = event_row.id

    async with _pg_factory() as db, db.begin():
        db.add(
            Bounty(
                guild_id=_GUILD,
                division="bronze",
                criminal_name="Criminal B",
                criminal_faction="terran",
                route=["Alpha", "Sol"],
                answer="Sol",
                reward=10000,
                reward_per_sys=1000,
                checked={"Alpha": -1, "Sol": -1},
                status="active",
                issue_time=now,
                end_time=now + timedelta(hours=_NOW_BOUNTY_END),
                tech_level=3,
                criminal_ship={"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []},
            )
        )

    from services.bounty_service import BountyService, CheckResult

    svc = BountyService()
    # First /check — wrong system → INCORRECT, checks metric += 1
    async with _pg_factory() as db:
        r1 = await svc.check_bounty(db, player_id, "Alpha", _GUILD)
    assert r1.outcomes[0].result == CheckResult.INCORRECT

    # Second /check — answer → CORRECT Bronze auto-capture, checks metric += 1, captures += 1
    async with _pg_factory() as db:
        r2 = await svc.check_bounty(db, player_id, "Sol", _GUILD)
    assert r2.outcomes[0].result == CheckResult.CORRECT
    assert r2.outcomes[0].combat_won is True

    # Cross-session read-back
    async with _pg_factory() as db:
        p = await db.get(Player, player_id)
        assert p is not None
        assert p.systems_checked == 2, f"Expected systems_checked=2, got {p.systems_checked}"
        assert p.bounty_wins == 1, f"Expected bounty_wins=1, got {p.bounty_wins}"

    checks = await _read_metric(_pg_factory, event_id, player_id, "checks")
    assert checks is not None, "checks metric row must exist"
    assert float(checks.value) == 2.0, f"Expected checks=2.0, got {checks.value}"

    captures = await _read_metric(_pg_factory, event_id, player_id, "captures")
    assert captures is not None, "captures metric row must exist"
    assert float(captures.value) == 1.0, f"Expected captures=1.0, got {captures.value}"

    await _cleanup()


# ---------------------------------------------------------------------------
# Test c: Found-but-lost and stalemate count (Silver)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fight_result,label",
    [(_FIGHT_LOSS, "loss"), (_FIGHT_STALEMATE, "stalemate")],
)
async def test_found_but_lost_and_stalemate_count(fight_result: FightResults, label: str):
    """Silver player finds the answer but loses (or stalemates) combat.

    Expected: result CORRECT / combat_won False, event checks == 1,
    lifetime systems_checked == 1, captures 0, bounty still active  (issue #124 c).
    """
    from persist.models.bounty import Bounty
    from services.bounty_service import BountyService, CheckResult

    await _cleanup()
    now = datetime.now(UTC)

    async with _pg_factory() as db, db.begin():
        db.add(User(id=_DISCORD_D, discord_username="bountybot_test_d"))
        await db.flush()
        db.add(GuildConfig(guild_id=_GUILD, check_cooldown=0))
        await db.flush()
        player_orm = Player(
            user_id=_DISCORD_D,
            guild_id=_GUILD,
            credits=1000,
            lifetime_credits=1000,
            xp=0,
            xp_surplus=0,
            tier="Silver",
            systems_checked=0,
            bounty_wins=0,
            classic_mode=False,
        )
        db.add(player_orm)
        await db.flush()
        ship_orm = PlayerShip(player_id=player_orm.id, ship_name="TestShip", is_active=True)
        db.add(ship_orm)
        await db.flush()
        player_orm.active_ship_id = ship_orm.id
        db.add(_bounty_caps_event(now))

    async with _pg_factory() as db:
        player_row = (await db.execute(select(Player).where(Player.user_id == _DISCORD_D))).scalars().first()
        assert player_row is not None
        player_id = player_row.id
        event_row = (await db.execute(select(GameEvent).where(GameEvent.guild_id == _GUILD))).scalars().first()
        assert event_row is not None
        event_id = event_row.id

    async with _pg_factory() as db, db.begin():
        db.add(
            Bounty(
                guild_id=_GUILD,
                division="silver",
                criminal_name="Criminal C",
                criminal_faction="terran",
                route=["Alpha", "Sol"],
                answer="Sol",
                reward=20000,
                reward_per_sys=2000,
                checked={"Alpha": -1, "Sol": -1},
                status="active",
                issue_time=now,
                end_time=now + timedelta(hours=_NOW_BOUNTY_END),
                tech_level=5,
                criminal_ship={"ship_name": "Bandit", "ship_armour": 200, "weapons": [], "turrets": []},
            )
        )

    svc = BountyService()
    # 1 mock: patch fight_ships so we control the combat outcome
    svc.combat_service.fight_ships = AsyncMock(return_value=fight_result)

    async with _pg_factory() as db:
        result = await svc.check_bounty(db, player_id, "Sol", _GUILD)

    assert result.outcomes[0].result == CheckResult.CORRECT, f"{label}: expected CORRECT outcome"
    assert result.outcomes[0].combat_won is False, f"{label}: expected combat_won=False"

    async with _pg_factory() as db:
        p = await db.get(Player, player_id)
        assert p is not None
        assert p.systems_checked == 1, f"{label}: expected systems_checked=1, got {p.systems_checked}"
        assert p.bounty_wins == 0, f"{label}: expected bounty_wins=0, got {p.bounty_wins}"

    checks = await _read_metric(_pg_factory, event_id, player_id, "checks")
    assert checks is not None, f"{label}: checks metric row must exist"
    assert float(checks.value) == 1.0, f"{label}: expected checks=1.0, got {checks.value}"

    captures = await _read_metric(_pg_factory, event_id, player_id, "captures")
    assert captures is None, f"{label}: captures metric must NOT exist (no capture)"

    # Bounty still active: criminal escaped, checks were reset
    async with _pg_factory() as db:
        from persist.models.bounty import Bounty as _B

        active = (await db.execute(select(_B).where(_B.guild_id == _GUILD, _B.status == "active"))).scalars().first()
        assert active is not None, f"{label}: bounty must still be active after {label}"

    await _cleanup()


# ---------------------------------------------------------------------------
# Test d: Multi-bounty /check counts N for both counters
# ---------------------------------------------------------------------------


async def test_multi_bounty_check_counts_n():
    """Two active bronze bounties whose routes both contain system X, X not the answer.

    One /check of X → two INCORRECT outcomes → event checks == 2,
    lifetime systems_checked == 2  (exercises atomic +n UPDATE with n=2, issue #124 d).
    """
    from persist.models.bounty import Bounty

    await _cleanup()
    now = datetime.now(UTC)

    async with _pg_factory() as db, db.begin():
        db.add(User(id=_DISCORD_E, discord_username="bountybot_test_e"))
        await db.flush()
        db.add(GuildConfig(guild_id=_GUILD, check_cooldown=0))
        await db.flush()
        db.add(
            Player(
                user_id=_DISCORD_E,
                guild_id=_GUILD,
                credits=1000,
                lifetime_credits=1000,
                xp=0,
                xp_surplus=0,
                tier="Bronze",
                systems_checked=0,
                bounty_wins=0,
                classic_mode=False,
            )
        )
        await db.flush()
        db.add(_bounty_caps_event(now))

    async with _pg_factory() as db:
        player_row = (await db.execute(select(Player).where(Player.user_id == _DISCORD_E))).scalars().first()
        assert player_row is not None
        player_id = player_row.id
        event_row = (await db.execute(select(GameEvent).where(GameEvent.guild_id == _GUILD))).scalars().first()
        assert event_row is not None
        event_id = event_row.id

    async with _pg_factory() as db, db.begin():
        # Both bounties include "Vertex"; neither has "Vertex" as the answer
        db.add(
            Bounty(
                guild_id=_GUILD,
                division="bronze",
                criminal_name="Criminal D1",
                criminal_faction="terran",
                route=["Vertex", "Alpha", "Sol"],
                answer="Sol",
                reward=10000,
                reward_per_sys=1000,
                checked={"Vertex": -1, "Alpha": -1, "Sol": -1},
                status="active",
                issue_time=now,
                end_time=now + timedelta(hours=_NOW_BOUNTY_END),
                tech_level=3,
                criminal_ship={"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []},
            )
        )
        db.add(
            Bounty(
                guild_id=_GUILD,
                division="bronze",
                criminal_name="Criminal D2",
                criminal_faction="terran",
                route=["Vertex", "Beta", "Nova"],
                answer="Nova",
                reward=10000,
                reward_per_sys=1000,
                checked={"Vertex": -1, "Beta": -1, "Nova": -1},
                status="active",
                issue_time=now,
                end_time=now + timedelta(hours=_NOW_BOUNTY_END),
                tech_level=3,
                criminal_ship={"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []},
            )
        )

    from services.bounty_service import BountyService, CheckResult

    svc = BountyService()
    async with _pg_factory() as db:
        result = await svc.check_bounty(db, player_id, "Vertex", _GUILD)

    assert len(result.outcomes) == 2, f"Expected 2 outcomes, got {len(result.outcomes)}"
    assert all(o.result == CheckResult.INCORRECT for o in result.outcomes), "Both must be INCORRECT"

    async with _pg_factory() as db:
        p = await db.get(Player, player_id)
        assert p is not None
        assert p.systems_checked == 2, f"Expected systems_checked=2 (atomic +2), got {p.systems_checked}"

    checks = await _read_metric(_pg_factory, event_id, player_id, "checks")
    assert checks is not None, "checks metric row must exist"
    assert float(checks.value) == 2.0, f"Expected checks=2.0 (two bounties), got {checks.value}"

    await _cleanup()


# ---------------------------------------------------------------------------
# Test e: Not-found adds nothing
# ---------------------------------------------------------------------------


async def test_not_found_adds_nothing():
    """/check a system not in any active bounty route → NOT_FOUND.

    systems_checked unchanged, no checks metric row  (issue #124 e).
    """
    from persist.models.bounty import Bounty

    await _cleanup()
    now = datetime.now(UTC)

    async with _pg_factory() as db, db.begin():
        db.add(User(id=_DISCORD_F, discord_username="bountybot_test_f2"))
        await db.flush()
        db.add(
            Player(
                user_id=_DISCORD_F,
                guild_id=_GUILD,
                credits=1000,
                lifetime_credits=1000,
                xp=0,
                xp_surplus=0,
                tier="Bronze",
                systems_checked=0,
                bounty_wins=0,
                classic_mode=False,
            )
        )
        await db.flush()
        db.add(_systems_checked_event(now))

    async with _pg_factory() as db:
        player_row = (await db.execute(select(Player).where(Player.user_id == _DISCORD_F))).scalars().first()
        assert player_row is not None
        player_id = player_row.id
        event_row = (await db.execute(select(GameEvent).where(GameEvent.guild_id == _GUILD))).scalars().first()
        assert event_row is not None
        event_id = event_row.id

    async with _pg_factory() as db, db.begin():
        db.add(
            Bounty(
                guild_id=_GUILD,
                division="bronze",
                criminal_name="Criminal E",
                criminal_faction="terran",
                route=["Alpha", "Sol"],
                answer="Sol",
                reward=10000,
                reward_per_sys=1000,
                checked={"Alpha": -1, "Sol": -1},
                status="active",
                issue_time=now,
                end_time=now + timedelta(hours=_NOW_BOUNTY_END),
                tech_level=3,
                criminal_ship={"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []},
            )
        )

    from services.bounty_service import BountyService, CheckResult

    svc = BountyService()
    async with _pg_factory() as db:
        result = await svc.check_bounty(db, player_id, "Nowhere", _GUILD)

    assert result.outcomes[0].result == CheckResult.NOT_FOUND

    async with _pg_factory() as db:
        p = await db.get(Player, player_id)
        assert p is not None
        assert p.systems_checked == 0, f"Expected systems_checked=0 (NOT_FOUND), got {p.systems_checked}"

    metric = await _read_metric(_pg_factory, event_id, player_id, "checks")
    assert metric is None, "No checks metric row must exist for NOT_FOUND"

    await _cleanup()
