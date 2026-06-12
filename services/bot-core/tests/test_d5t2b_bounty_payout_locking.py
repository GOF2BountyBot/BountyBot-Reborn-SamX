"""
D5-T2b — Bounty-payout credit lost-update closure (REAL Postgres).

The D5-T2 audit found that ``bounty_service.distribute_rewards`` loaded each
rewarded player via an **unlocked** ``get_by_id`` and then did
``player.credits += reward.credits_earned``.  With ``expire_on_commit=False`` and
no row lock, two concurrent credit ops on the SAME player (a payout vs a shop
buy / transfer, or two payouts touching the same player) LOSE an update.

D5-T2b makes the payout RMW lost-update-safe by acquiring each rewarded player's
row ``FOR UPDATE`` via ``player_repo.get_by_id_for_update`` (which carries
``populate_existing=True`` from D5-T1) BEFORE reading/mutating that player's
credits, locking players in ASCENDING ``player_id`` order so a multi-checker
payout never AB-BA-deadlocks against another multi-player credit op.

These tests drive REAL async Postgres sessions against bountydev-db so genuine
row-level FOR UPDATE blocking applies (SQLite ignores FOR UPDATE and cannot
model lost updates or deadlock detection).  They call the REAL
``BountyService.distribute_rewards`` — not a hand-rolled simulation — so the
test exercises the production code path.

Cases:
  L   LOST-UPDATE CLOSED — a real ``distribute_rewards`` payout concurrent with
      a credit op (transfer-style RMW) on the SAME player, over N iterations →
      final credits == base + payout + other_increment (BOTH apply, no clobber).
  Lv  ANTI-VACUOUS — neuter the payout's player lock (route get_by_id_for_update
      to the UNLOCKED get_by_id) → the concurrent op's increment is LOST
      (final == base + only one increment).  Restore the lock → both apply.
      Proves the FOR UPDATE is load-bearing, not decorative.
  D   MULTI-PLAYER NO DEADLOCK — a real multi-checker payout (winner + N
      consolation checkers) concurrent with a transfer_credits between two of
      those same players in opposite intent → both lock ascending → no 40P01.
  R   REFRESH-UNDER-LOCK — a concurrent credit commit lands BETWEEN check_bounty's
      unlocked pre-load of the winner and distribute_rewards taking the lock.
      With populate_existing the payout reads the fresh committed balance and the
      concurrent increment survives; the mutation (drop populate_existing / use
      the stale pre-loaded object) loses it.

Connection: resolved from POSTGRES_* env vars, falling back to the dev stack
(bountydev-db on the docker bridge) — see tests/pg_env.py. CI provisions its own
migrated + seeded postgres service container; without a usable DB the module skips.
Each test creates its own engine inline (mirrors test_d5t2_lock_ordering.py) to
keep the asyncpg pool bound to the test's event loop.
"""

# ---------------------------------------------------------------------------
# Path / sys.modules setup — must happen before any application imports
# ---------------------------------------------------------------------------
import asyncio
import os
import sys

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

import types
from unittest.mock import MagicMock

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

# ---------------------------------------------------------------------------
import pytest
from persist.models.bounty import Bounty
from persist.models.player import Player
from persist.models.user import User
from persist.repositories.player_repository import PlayerRepository
from services.bounty_service import BountyService, RewardInfo
from services.player_service import PlayerService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.pg_env import PG_ASYNC_URL, pg_skip_reason

# ---------------------------------------------------------------------------
# Real Postgres connection — resolved from POSTGRES_* env vars (CI service
# container) with the bountydev-db docker-bridge dev stack as the fallback.
# ---------------------------------------------------------------------------

_PG_URL = PG_ASYNC_URL

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

# Test-isolation constants: guild/user IDs that cannot collide with production.
_TEST_GUILD = 999_888_777_062
_TEST_USER_A = 999_888_062_001
_TEST_USER_B = 999_888_062_002
_TEST_USER_C = 999_888_062_003


# ---------------------------------------------------------------------------
# Engine / factory + cleanup / seed helpers
# ---------------------------------------------------------------------------


def _make_pg_factory() -> tuple:
    engine = create_async_engine(_PG_URL, pool_size=8, max_overflow=4, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def _cleanup(factory) -> None:
    async with factory() as db, db.begin():
        await db.execute(text(f"DELETE FROM bounty WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(text(f"DELETE FROM players WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(text(f"DELETE FROM users WHERE id IN ({_TEST_USER_A}, {_TEST_USER_B}, {_TEST_USER_C})"))


async def _seed_user(db: AsyncSession, user_id: int) -> User:
    existing = await db.get(User, user_id)
    if existing:
        return existing
    user = User(id=user_id, discord_username=f"testuser_{user_id}")
    db.add(user)
    await db.flush()
    return user


async def _seed_player(db: AsyncSession, user_id: int, *, credits: int = 10_000, classic_mode: bool = True) -> int:
    await _seed_user(db, user_id)
    player = Player(
        user_id=user_id,
        guild_id=_TEST_GUILD,
        credits=credits,
        lifetime_credits=credits,
        xp=0,
        xp_surplus=0,
        tier="Bronze",
        classic_mode=classic_mode,
    )
    db.add(player)
    await db.flush()
    return player.id


async def _seed_bounty(db: AsyncSession) -> int:
    bounty = Bounty(
        guild_id=_TEST_GUILD,
        division="bronze",
        criminal_name="D5T2b Test Criminal",
        route=["Sol", "Alpha"],
        answer="Alpha",
        reward=1000,
        reward_per_sys=100,
        checked={"Sol": -1, "Alpha": -1},
        tech_level=1,
        status="active",
    )
    db.add(bounty)
    await db.flush()
    return bounty.id


async def _credits(factory, player_id: int) -> int:
    async with factory() as db:
        res = await db.execute(select(Player.credits).where(Player.id == player_id))
        return res.scalars().first()


def _is_deadlock(exc: BaseException) -> bool:
    """True if exc is a PostgreSQL deadlock (SQLSTATE 40P01)."""
    s = str(exc)
    return "deadlock detected" in s or "40P01" in s


# ===========================================================================
# Case L: LOST-UPDATE CLOSED — payout vs concurrent credit op on SAME player
# ===========================================================================


@pytest.mark.parametrize("payout_first", [True, False])
@pytest.mark.parametrize("iteration", range(4))
async def test_caseL_payout_vs_credit_op_same_player_no_lost_update(payout_first, iteration):
    """Real distribute_rewards payout vs a credit RMW on the SAME player.

    Both ops lock the SAME Player row FOR UPDATE — the payout via the new D5-T2b
    lock, the other op (modelled on transfer/shop credit RMW) via
    get_by_id_for_update.  Whichever acquires the lock first runs to completion;
    the other blocks then proceeds on the FRESH committed balance.  Both
    increments must land — final == base + payout + other.
    """
    engine, factory = _make_pg_factory()
    payout_amount = 2_000
    other_amount = 777
    base = 10_000
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            pid = await _seed_player(db, _TEST_USER_A, credits=base)
            bid = await _seed_bounty(db)

        svc = BountyService()
        player_repo = PlayerRepository()
        first_locked = asyncio.Event()
        errors: list = []

        async def payout_op(go_first: bool):
            if not go_first:
                await first_locked.wait()
                await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    bounty = await db.get(Bounty, bid)
                    rewards = [RewardInfo(player_id=pid, credits_earned=payout_amount, xp_earned=0, is_winner=True)]
                    if go_first:
                        # Lock the player first (distribute_rewards does), signal, then hold.
                        await player_repo.get_by_id_for_update(db, pid)
                        first_locked.set()
                        await asyncio.sleep(0.25)
                    # distribute_rewards re-locks the same player row (intra-txn no-op
                    # when go_first pre-locked) and applies the credit increment.
                    await svc.distribute_rewards(db, bounty, rewards)
                    # distribute_rewards commits internally.
                except Exception as exc:  # pragma: no cover - failure path
                    await db.rollback()
                    errors.append(("payout", exc))

        async def credit_op(go_first: bool):
            if not go_first:
                await first_locked.wait()
                await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    p = await player_repo.get_by_id_for_update(db, pid)
                    if go_first:
                        first_locked.set()
                        await asyncio.sleep(0.25)
                    p.credits = p.credits + other_amount
                    p.lifetime_credits = p.lifetime_credits + other_amount
                    await db.commit()
                except Exception as exc:  # pragma: no cover - failure path
                    await db.rollback()
                    errors.append(("credit", exc))

        if payout_first:
            await asyncio.gather(payout_op(True), credit_op(False))
        else:
            await asyncio.gather(credit_op(True), payout_op(False))

        assert not any(_is_deadlock(e) for _, e in errors), f"DEADLOCK: {errors}"
        assert errors == [], f"[iter {iteration} payout_first={payout_first}] unexpected error(s): {errors}"

        final = await _credits(factory, pid)
        assert final == base + payout_amount + other_amount, (
            f"LOST UPDATE: expected {base + payout_amount + other_amount}, got {final} "
            f"(base={base} payout={payout_amount} other={other_amount})"
        )
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case Lv: ANTI-VACUOUS — the payout's player FOR UPDATE is load-bearing
# ===========================================================================


async def test_caseLv_payout_lock_is_load_bearing(monkeypatch):
    """Prove the D5-T2b player lock in distribute_rewards actually serialises.

    Phase NEUTERED: monkeypatch get_by_id_for_update -> unlocked get_by_id (the
    pre-fix behaviour).  A concurrent credit op commits during the payout's hold
    window; the payout reads the STALE pre-hold balance and clobbers the
    concurrent increment → final == base + payout ONLY (the +other is LOST).

    Phase FIXED: real lock in place.  The payout blocks behind the concurrent
    op's lock (or vice-versa) and reads the fresh balance → both apply.

    The contrast proves the FOR UPDATE is the thing that closes the gap.
    """
    payout_amount = 2_000
    other_amount = 777
    base = 10_000

    async def run(neuter_lock: bool) -> int:
        engine, factory = _make_pg_factory()
        try:
            await _cleanup(factory)
            async with factory() as db, db.begin():
                pid = await _seed_player(db, _TEST_USER_A, credits=base)
                bid = await _seed_bounty(db)

            svc = BountyService()
            real_player_repo = PlayerRepository()
            payout_loaded = asyncio.Event()
            other_committed = asyncio.Event()

            # When neutered, distribute_rewards' get_by_id_for_update becomes an
            # UNLOCKED get_by_id AND signals + waits so the concurrent op commits
            # in between the (now lock-less) read and the mutation — reproducing
            # the lost update deterministically.
            if neuter_lock:

                async def neutered(db_, obj_id):
                    p = await real_player_repo.get_by_id(db_, obj_id)
                    payout_loaded.set()
                    await other_committed.wait()
                    return p

                svc.player_repo.get_by_id_for_update = neutered  # type: ignore[assignment]

            async def payout_op():
                async with factory() as db:
                    await db.begin()
                    bounty = await db.get(Bounty, bid)
                    rewards = [RewardInfo(player_id=pid, credits_earned=payout_amount, xp_earned=0, is_winner=True)]
                    if not neuter_lock:
                        # Real-lock phase: hold the row so the other op blocks.
                        await real_player_repo.get_by_id_for_update(db, pid)
                        payout_loaded.set()
                        await asyncio.sleep(0.25)
                    await svc.distribute_rewards(db, bounty, rewards)

            async def other_op():
                await payout_loaded.wait()
                if not neuter_lock:
                    await asyncio.sleep(0.03)
                async with factory() as db:
                    await db.begin()
                    p = await real_player_repo.get_by_id_for_update(db, pid)
                    p.credits = p.credits + other_amount
                    p.lifetime_credits = p.lifetime_credits + other_amount
                    await db.commit()
                    other_committed.set()

            await asyncio.gather(payout_op(), other_op())
            return await _credits(factory, pid)
        finally:
            await _cleanup(factory)
            await engine.dispose()

    # NEUTERED: lost update — the concurrent increment is clobbered.
    neutered_final = await run(neuter_lock=True)
    assert neutered_final == base + payout_amount, (
        "ANTI-VACUOUS setup failed: without the player lock the concurrent increment "
        f"should be LOST (expected {base + payout_amount}), but got {neutered_final} — "
        "the probe cannot demonstrate the fix."
    )

    # FIXED: both increments survive.
    fixed_final = await run(neuter_lock=False)
    assert fixed_final == base + payout_amount + other_amount, (
        "ANTI-VACUOUS FAILED: with the FOR UPDATE lock the concurrent increment was "
        f"still lost (expected {base + payout_amount + other_amount}, got {fixed_final})."
    )


# ===========================================================================
# Case D: MULTI-PLAYER NO DEADLOCK — multi-checker payout vs transfer_credits
# ===========================================================================


@pytest.mark.parametrize("iteration", range(4))
async def test_caseD_multi_checker_payout_vs_transfer_no_deadlock(iteration):
    """A real multi-checker payout (winner + 2 consolation) concurrent with a
    transfer_credits between two of those same players in OPPOSITE intent.

    distribute_rewards locks players ascending; transfer_credits locks players
    ascending.  Their shared player rows are acquired in the same order → no
    AB-BA cycle → no 40P01.  Totals are conserved.
    """
    engine, factory = _make_pg_factory()
    base = 10_000
    transfer_amount = 500
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            pid_a = await _seed_player(db, _TEST_USER_A, credits=base, classic_mode=False)
            pid_b = await _seed_player(db, _TEST_USER_B, credits=base, classic_mode=False)
            pid_c = await _seed_player(db, _TEST_USER_C, credits=base, classic_mode=False)
            bid = await _seed_bounty(db)

        svc = BountyService()
        player_svc = PlayerService()
        player_repo = PlayerRepository()
        payout_started = asyncio.Event()
        errors: list = []

        # Winner + two consolation checkers. credits_earned per player.
        reward_a, reward_b, reward_c = 4_000, 1_000, 1_000

        async def payout_op():
            async with factory() as db:
                await db.begin()
                try:
                    # Pre-lock the lowest-id player so the payout's ascending lock
                    # walk overlaps the transfer's window, widening contention.
                    lo = min(pid_a, pid_b, pid_c)
                    await player_repo.get_by_id_for_update(db, lo)
                    payout_started.set()
                    await asyncio.sleep(0.15)
                    bounty = await db.get(Bounty, bid)
                    rewards = [
                        RewardInfo(player_id=pid_a, credits_earned=reward_a, xp_earned=0, is_winner=True),
                        RewardInfo(player_id=pid_b, credits_earned=reward_b, xp_earned=0, is_winner=False),
                        RewardInfo(player_id=pid_c, credits_earned=reward_c, xp_earned=0, is_winner=False),
                    ]
                    await svc.distribute_rewards(db, bounty, rewards)
                except Exception as exc:
                    await db.rollback()
                    errors.append(("payout", exc))

        async def transfer_op():
            await payout_started.wait()
            await asyncio.sleep(0.02)
            async with factory() as db:
                await db.begin()
                try:
                    # transfer_credits locks both players ascending internally;
                    # the txn is caller-owned, so we commit here.
                    await player_svc.transfer_credits(db, pid_c, pid_b, transfer_amount)
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    errors.append(("transfer", exc))

        await asyncio.gather(payout_op(), transfer_op())

        assert not any(_is_deadlock(e) for _, e in errors), f"[iter {iteration}] DEADLOCK: {errors}"
        assert errors == [], f"[iter {iteration}] unexpected error(s): {errors}"

        # Conservation: payout adds reward_* to each; transfer moves transfer_amount
        # from C to B (net zero across B+C).
        ca = await _credits(factory, pid_a)
        cb = await _credits(factory, pid_b)
        cc = await _credits(factory, pid_c)
        assert ca == base + reward_a, f"A: expected {base + reward_a}, got {ca}"
        assert cb == base + reward_b + transfer_amount, f"B: expected {base + reward_b + transfer_amount}, got {cb}"
        assert cc == base + reward_c - transfer_amount, f"C: expected {base + reward_c - transfer_amount}, got {cc}"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case R: REFRESH-UNDER-LOCK — payout reads FRESH committed credits
# ===========================================================================


async def test_caseR_payout_reads_fresh_credits_under_lock(monkeypatch):
    """A concurrent credit commit lands BETWEEN check_bounty's unlocked pre-load
    of the winner and distribute_rewards taking the FOR UPDATE lock.

    The production flow pre-loads the player UNLOCKED in check_bounty
    (player_repo.get_by_id at the top), then later distribute_rewards locks it.
    We reproduce that: pre-load the SAME player object unlocked into session A,
    let a concurrent session commit +other to that player, THEN run
    distribute_rewards in session A.

    With populate_existing=True (D5-T1) the FOR UPDATE re-fetch overwrites the
    stale pre-loaded object with the freshly-committed balance, so the payout
    increments fresh state → +other survives.  The mutation (drop
    populate_existing → the locked re-fetch returns the cached stale object) loses
    the concurrent +other.
    """
    payout_amount = 2_000
    other_amount = 777
    base = 10_000

    async def run(populate_existing: bool) -> int:
        engine, factory = _make_pg_factory()
        try:
            await _cleanup(factory)
            async with factory() as db, db.begin():
                pid = await _seed_player(db, _TEST_USER_A, credits=base)
                bid = await _seed_bounty(db)

            svc = BountyService()

            if not populate_existing:
                # Mutation: locked re-fetch WITHOUT populate_existing returns the
                # session's cached (stale) object — modelling the pre-D5-T1 bug.
                real_repo = PlayerRepository()

                async def stale_for_update(db_, obj_id):
                    # FOR UPDATE acquires the lock but populate_existing is omitted,
                    # so an already-identity-mapped row is returned from cache stale.
                    from sqlalchemy import select as _select

                    await db_.execute(_select(Player).where(Player.id == obj_id).with_for_update())
                    return await real_repo.get_by_id(db_, obj_id)

                svc.player_repo.get_by_id_for_update = stale_for_update  # type: ignore[assignment]

            async with factory() as session_a:
                await session_a.begin()
                # 1) Pre-load the winner UNLOCKED into session A's identity map
                #    (this is exactly what check_bounty does at its top).
                preloaded = await svc.player_repo.get_by_id(session_a, pid)
                assert preloaded.credits == base

                # 2) A concurrent session commits +other to the SAME player.
                async with factory() as session_x, session_x.begin():
                    px = await PlayerRepository().get_by_id_for_update(session_x, pid)
                    px.credits = px.credits + other_amount
                    px.lifetime_credits = px.lifetime_credits + other_amount

                # 3) Now run the real payout in session A.  distribute_rewards
                #    locks the player (FOR UPDATE) and applies +payout.
                bounty = await session_a.get(Bounty, bid)
                rewards = [RewardInfo(player_id=pid, credits_earned=payout_amount, xp_earned=0, is_winner=True)]
                await svc.distribute_rewards(session_a, bounty, rewards)

            return await _credits(factory, pid)
        finally:
            await _cleanup(factory)
            await engine.dispose()

    # WITH populate_existing (production): the concurrent +other is reflected.
    fresh_final = await run(populate_existing=True)
    assert fresh_final == base + other_amount + payout_amount, (
        "REFRESH-UNDER-LOCK FAILED: payout did not read the freshly-committed "
        f"balance (expected {base + other_amount + payout_amount}, got {fresh_final})."
    )

    # WITHOUT populate_existing (mutation): the concurrent +other is LOST.
    stale_final = await run(populate_existing=False)
    assert stale_final == base + payout_amount, (
        "ANTI-VACUOUS setup failed: without populate_existing the stale pre-loaded "
        f"object should clobber +other (expected {base + payout_amount}), got {stale_final}."
    )
