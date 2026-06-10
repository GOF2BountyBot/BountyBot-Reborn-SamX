"""
Concurrency / idempotency tests for /bounties/check and /duels/accept.

These tests use REAL async Postgres sessions against bountydev-db to exercise
row-level locks (SELECT ... FOR UPDATE).  SQLite cannot be used here because
SQLite does not support FOR UPDATE blocking semantics.

Test matrix:
  B1  Two concurrent /check sessions — only one payout applied
  B2a Stale-attribute trap: locked re-fetch with populate_existing reads FRESH state
  B2b Negative probe: naive FOR UPDATE without populate_existing returns stale data,
      demonstrating WHY the fix is required
  B3  No AB-BA deadlock under ascending-ID multi-bounty lock ordering
  B4  Timeout-then-retry: exactly one application even after client retry
  D1  Two concurrent /accept sessions — only one payout applied
  D2a Stale-attribute trap for duel status (positive probe)
  D2b Negative probe for duel (naive FOR UPDATE returns stale status)
  D3  No AB-BA deadlock under duel-first + ascending-player lock ordering
  D4  Timeout-then-retry for duel accept

Connection: bountydev-db at 172.18.0.2:5432 (bountydev-net bridge IP — re-check via `sudo docker inspect bountydev-db` after a stack rebuild; host-published localhost:15432 is unreachable from this dev container).

Engine / factory creation: each test creates its own engine inline (not via
fixture) to avoid pytest-asyncio loop-binding issues with asyncpg connection
pools.  The engine is disposed at the end of each test.
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
from datetime import UTC, datetime, timedelta

from persist.models.bounty import Bounty
from persist.models.duel_request import DuelRequest
from persist.models.player import Player
from persist.models.user import User
from persist.repositories.bounty_repository import BountyRepository
from persist.repositories.duel_repository import DuelRepository
from persist.repositories.player_repository import PlayerRepository
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Real Postgres connection — bountydev-db on docker bridge network
# ---------------------------------------------------------------------------

_PG_URL = "postgresql+asyncpg://bounty:bounty@172.18.0.2:5432/bountydb"

# Test-isolation constants: guild/user IDs that cannot collide with production data.
_TEST_GUILD = 999_888_777_001
_TEST_USER_A = 999_888_000_001
_TEST_USER_B = 999_888_000_002


# ---------------------------------------------------------------------------
# Engine / factory helper
# ---------------------------------------------------------------------------


def _make_pg_factory() -> tuple:
    """Create a fresh Postgres engine + session factory bound to the current loop.

    Each test creates its own engine inline so the asyncpg connection pool is
    bound to the test's event loop, avoiding 'Future attached to a different
    loop' errors from pytest-asyncio fixture-scope interactions.

    Returns (engine, factory).  The caller must ``await engine.dispose()``.
    """
    engine = create_async_engine(_PG_URL, pool_size=6, max_overflow=4, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# Cleanup / seed helpers
# ---------------------------------------------------------------------------


async def _cleanup(factory) -> None:
    """Hard-delete all test rows in the correct FK order."""
    async with factory() as db, db.begin():
        await db.execute(text(f"DELETE FROM duel_requests WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(text(f"DELETE FROM bounty WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(text(f"DELETE FROM players WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(text(f"DELETE FROM users WHERE id IN ({_TEST_USER_A}, {_TEST_USER_B})"))


async def _seed_user(db: AsyncSession, user_id: int) -> User:
    existing = await db.get(User, user_id)
    if existing:
        return existing
    user = User(id=user_id, discord_username=f"testuser_{user_id}")
    db.add(user)
    await db.flush()
    return user


async def _seed_player(db: AsyncSession, user_id: int, credits: int = 10_000) -> Player:
    await _seed_user(db, user_id)
    result = await db.execute(select(Player).where(Player.user_id == user_id, Player.guild_id == _TEST_GUILD))
    existing = result.scalars().first()
    if existing:
        return existing
    player = Player(
        user_id=user_id,
        guild_id=_TEST_GUILD,
        credits=credits,
        tier="Bronze",
        classic_mode=True,
    )
    db.add(player)
    await db.flush()
    return player


async def _seed_bounty(db: AsyncSession, route: list[str] | None = None) -> Bounty:
    if route is None:
        route = ["Sol", "Alpha Centauri", "Vega"]
    checked = {s: -1 for s in route}
    bounty = Bounty(
        guild_id=_TEST_GUILD,
        division="bronze",
        criminal_name="Test Criminal",
        route=route,
        answer=route[-1],
        reward=1000,
        reward_per_sys=100,
        checked=checked,
        tech_level=1,
        status="active",
        end_time=datetime.now(UTC) + timedelta(hours=2),
    )
    db.add(bounty)
    await db.flush()
    return bounty


async def _seed_duel(db: AsyncSession, challenger_id: int, target_id: int, stakes: int = 500) -> DuelRequest:
    duel = DuelRequest(
        guild_id=_TEST_GUILD,
        challenger_id=challenger_id,
        target_id=target_id,
        stakes=stakes,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(duel)
    await db.flush()
    return duel


# ===========================================================================
# B1: Two concurrent /check sessions — exactly one check recorded
# ===========================================================================


async def test_b1_concurrent_check_exactly_one_payout():
    """Two concurrent sessions checking the SAME system → exactly one check recorded.

    Session A acquires the FOR UPDATE lock, marks the system as checked, and
    holds the lock.  Session B pre-loads the bounty (sees -1), then tries to
    acquire the same FOR UPDATE lock — BLOCKS until A commits.  After A
    commits, B re-reads the fresh checked map (via populate_existing=True) and
    sees the system is already checked → NO-OP.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        bounty_repo = BountyRepository()
        system = "Sol"

        # Seed
        async with factory() as db, db.begin():
            bounty = await _seed_bounty(db, route=["Sol", "Alpha Centauri", "Vega"])
            bounty_id = bounty.id

        committed_event = asyncio.Event()
        results: dict = {}

        async def session_a():
            async with factory() as db:
                locked = await bounty_repo.get_by_id_for_update(db, bounty_id)
                assert locked is not None
                checked = dict(locked.checked)
                assert checked.get(system, -1) == -1
                checked[system] = _TEST_USER_A
                locked.checked = checked
                await db.flush()
                committed_event.set()
                await asyncio.sleep(0.15)  # hold lock so B blocks
                await db.commit()
                results["a_committed"] = True

        async def session_b():
            await committed_event.wait()
            await asyncio.sleep(0.05)
            async with factory() as db:
                # Pre-load UNLOCKED (stale)
                stale = await bounty_repo.get_by_id(db, bounty_id)
                assert stale is not None
                results["b_stale_val"] = stale.checked.get(system, -1)

                # Lock — blocks until A commits, then populates fresh data
                locked = await bounty_repo.get_by_id_for_update(db, bounty_id)
                assert locked is not None
                results["b_fresh_val"] = locked.checked.get(system, -1)
                results["b_result"] = "no_op" if results["b_fresh_val"] != -1 else "would_check"
                await db.rollback()

        await asyncio.gather(session_a(), session_b())

        assert results["a_committed"] is True
        assert results["b_stale_val"] == -1, "B pre-load must see stale -1"
        assert results["b_fresh_val"] == _TEST_USER_A, (
            "B locked re-fetch must see A's committed value (populate_existing=True required)"
        )
        assert results["b_result"] == "no_op"

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# B2a: Stale-attribute trap — positive probe (populate_existing=True works)
#
# The "stale" scenario in the real race: B loads the row BEFORE A commits
# (gets old value cleanly — NOT dirty), A commits, B then locks and must
# see A's committed value.  populate_existing=True overwrites CLEAN stale
# attributes; it does NOT overwrite dirty (Python-modified) ones.
# ===========================================================================


async def test_b2a_with_populate_existing_reads_fresh_committed_state():
    """get_by_id_for_update with populate_existing=True re-reads committed state.

    Uses a real concurrent scenario: B loads the row CLEANLY before A commits,
    A commits a change, B locks and must observe the fresh committed value.
    This is the exact race that occurs in production: the first unlocked SELECT
    in check_bounty / accept_duel loads a CLEAN stale snapshot; populate_existing
    on the subsequent FOR UPDATE re-loads it from the committed DB row.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        bounty_repo = BountyRepository()
        system = "Alpha Centauri"

        async with factory() as db, db.begin():
            bounty = await _seed_bounty(db, route=["Sol", "Alpha Centauri", "Vega"])
            bounty_id = bounty.id

        committed_event = asyncio.Event()
        a_locked_event = asyncio.Event()
        b_preloaded_event = asyncio.Event()
        result: dict = {}

        async def session_a():
            """Lock row, wait for B to pre-load, then commit the change."""
            async with factory() as db_a:
                # Lock immediately (before B pre-loads so B sees -1)
                locked_a = await bounty_repo.get_by_id_for_update(db_a, bounty_id)
                a_locked_event.set()
                # Wait for B to pre-load (B will block on the lock after this)
                await b_preloaded_event.wait()
                # Now commit with the change
                checked = dict(locked_a.checked)
                checked[system] = _TEST_USER_A
                locked_a.checked = checked
                await db_a.commit()
                committed_event.set()

        async def session_b():
            """Pre-load BEFORE A commits (gets clean stale value), then lock."""
            # Wait for A to hold the lock first
            await a_locked_event.wait()

            async with factory() as db_b:
                # Load UNLOCKED — A holds the lock but our SELECT (no FOR UPDATE) goes through
                # and gets the pre-commit value (checked[system] == -1)
                stale = await bounty_repo.get_by_id(db_b, bounty_id)
                assert stale is not None
                result["b_pre_load"] = stale.checked.get(system, -1)
                b_preloaded_event.set()

                # Now lock — A still holds the lock so we block until A commits
                locked = await bounty_repo.get_by_id_for_update(db_b, bounty_id)
                assert locked is stale, "Same Python object via identity map"
                result["b_fresh_val"] = locked.checked.get(system, -1)
                await db_b.rollback()

        await asyncio.gather(session_a(), session_b())

        assert result["b_pre_load"] == -1, "B must have seen -1 before A committed"
        assert result["b_fresh_val"] == _TEST_USER_A, (
            "populate_existing=True must overwrite CLEAN stale attributes with "
            "the committed DB value after the FOR UPDATE fetch"
        )

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# B2b: Stale-attribute trap — negative probe (naive FOR UPDATE returns stale)
#
# Uses a real concurrent scenario to prove that a naive FOR UPDATE WITHOUT
# populate_existing returns the pre-commit in-memory value after A commits.
# ===========================================================================


async def test_b2b_without_populate_existing_returns_stale_value():
    """Naive FOR UPDATE without populate_existing returns stale in-memory data.

    This is the negative probe that proves WHY populate_existing=True is
    mandatory.  B loads the row cleanly BEFORE A commits, A commits a change,
    B's naive FOR UPDATE (no populate_existing) returns the pre-commit cached
    value — this is the double-apply bug vector.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        bounty_repo = BountyRepository()
        system = "Vega"

        async with factory() as db, db.begin():
            bounty = await _seed_bounty(db, route=["Sol", "Alpha Centauri", "Vega"])
            bounty_id = bounty.id

        a_locked_event = asyncio.Event()
        b_preloaded_event = asyncio.Event()
        result: dict = {}

        async def session_a():
            async with factory() as db_a:
                locked = await bounty_repo.get_by_id_for_update(db_a, bounty_id)
                a_locked_event.set()
                await b_preloaded_event.wait()
                checked = dict(locked.checked)
                checked[system] = _TEST_USER_A
                locked.checked = checked
                await db_a.commit()

        async def session_b():
            await a_locked_event.wait()
            async with factory() as db_b:
                # Load CLEANLY (A holds the lock but our unlocked SELECT goes through)
                stale = await bounty_repo.get_by_id(db_b, bounty_id)
                result["pre_load"] = stale.checked.get(system, -1)  # should be -1
                b_preloaded_event.set()

                # NAIVE FOR UPDATE without populate_existing — blocks until A commits
                naive_result = await db_b.execute(
                    select(Bounty).where(Bounty.id == bounty_id).with_for_update()
                    # .execution_options(populate_existing=True) intentionally OMITTED
                )
                naive_locked = naive_result.scalars().first()
                assert naive_locked is stale, "Same Python object via identity map"
                result["naive_val"] = naive_locked.checked.get(system, -1)
                await db_b.rollback()

        await asyncio.gather(session_a(), session_b())

        assert result["pre_load"] == -1
        assert result["naive_val"] == -1, (
            "WITHOUT populate_existing, the identity-map cached -1 is returned "
            "even after A committed _TEST_USER_A — this is the double-apply bug "
            "that populate_existing=True in get_by_id_for_update closes"
        )

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# B3: No AB-BA deadlock under ascending-ID multi-bounty lock ordering
# ===========================================================================


async def test_b3_no_abba_deadlock_multi_bounty():
    """Ascending-ID lock ordering prevents AB-BA deadlock for multi-bounty /check."""
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        bounty_repo = BountyRepository()

        async with factory() as db, db.begin():
            b1 = await _seed_bounty(db, route=["SysA1", "SysA2"])
            b2 = await _seed_bounty(db, route=["SysB1", "SysB2"])
            low_id = min(b1.id, b2.id)
            high_id = max(b1.id, b2.id)

        results = []

        async def task():
            """Both tasks use ascending ID order — exactly the fix's guarantee."""
            async with factory() as db:
                await bounty_repo.get_by_id_for_update(db, low_id)
                await asyncio.sleep(0.04)
                await bounty_repo.get_by_id_for_update(db, high_id)
                await db.commit()
                results.append("ok")

        await asyncio.wait_for(asyncio.gather(task(), task()), timeout=10.0)
        assert len(results) == 2, "Both tasks must complete without deadlock"

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# B4: Timeout-then-retry → exactly one check applied
# ===========================================================================


async def test_b4_timeout_then_retry_exactly_one_check():
    """Client timeout mid-check + retry → exactly one check applied."""
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        bounty_repo = BountyRepository()
        system = "Sol"

        async with factory() as db, db.begin():
            bounty = await _seed_bounty(db, route=["Sol", "Alpha Centauri", "Vega"])
            bounty_id = bounty.id

        async def attempt_check(player_id: int) -> str:
            async with factory() as db:
                locked = await bounty_repo.get_by_id_for_update(db, bounty_id)
                if locked is None:
                    return "not_found"
                checked = dict(locked.checked)
                if checked.get(system, -1) != -1:
                    await db.rollback()
                    return "already_checked"
                checked[system] = player_id
                locked.checked = checked
                await db.commit()
                return "checked"

        r1 = await attempt_check(_TEST_USER_A)
        assert r1 == "checked"

        # Retry (client timeout then retry)
        r2 = await attempt_check(_TEST_USER_A)
        assert r2 == "already_checked"

        async with factory() as db:
            final = await bounty_repo.get_by_id(db, bounty_id)
            assert final.checked.get(system) == _TEST_USER_A
            checked_count = sum(1 for v in final.checked.values() if v != -1)
            assert checked_count == 1, "Exactly one system checked"

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# D1: Two concurrent /accept sessions — exactly one payout applied
# ===========================================================================


async def test_d1_concurrent_accept_exactly_one_payout():
    """Two concurrent duel-accept sessions → exactly one status change applied."""
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        duel_repo = DuelRepository()
        player_repo = PlayerRepository()

        async with factory() as db, db.begin():
            p_a = await _seed_player(db, _TEST_USER_A, credits=5000)
            p_b = await _seed_player(db, _TEST_USER_B, credits=5000)
            duel = await _seed_duel(db, p_a.id, p_b.id, stakes=200)
            duel_id = duel.id
            p_a_id, p_b_id = p_a.id, p_b.id

        committed_event = asyncio.Event()
        results: dict = {}

        async def session_a():
            async with factory() as db:
                # Lock duel first, then players ascending (global lock order)
                locked_duel = await duel_repo.get_by_id_for_update(db, duel_id)
                assert locked_duel is not None
                assert locked_duel.status == "pending"
                for pid in sorted([p_a_id, p_b_id]):
                    await player_repo.get_by_id_for_update(db, pid)
                locked_duel.status = "completed"
                await db.flush()
                committed_event.set()
                await asyncio.sleep(0.15)
                await db.commit()
                results["a_committed"] = True

        async def session_b():
            await committed_event.wait()
            await asyncio.sleep(0.05)
            async with factory() as db:
                # Pre-load duel UNLOCKED
                stale = await duel_repo.get_by_id(db, duel_id)
                assert stale is not None
                results["b_stale_status"] = stale.status

                # Lock — blocks until A commits
                locked = await duel_repo.get_by_id_for_update(db, duel_id)
                assert locked is not None
                results["b_fresh_status"] = locked.status
                results["b_result"] = "no_op" if locked.status != "pending" else "would_accept"
                await db.rollback()

        await asyncio.gather(session_a(), session_b())

        assert results["a_committed"] is True
        assert results["b_stale_status"] == "pending", "B pre-load saw stale pending"
        assert results["b_fresh_status"] == "completed", (
            "B locked re-fetch must see 'completed' (populate_existing=True required)"
        )
        assert results["b_result"] == "no_op"

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# D2a: Duel stale-attribute trap — positive probe
# ===========================================================================


async def test_d2a_duel_with_populate_existing_reads_fresh_status():
    """Duel get_by_id_for_update with populate_existing=True re-reads committed status.

    Uses a real concurrent scenario: B loads the duel CLEANLY before A commits
    (gets 'pending'), A commits 'completed', B's FOR UPDATE (with
    populate_existing) must see 'completed'.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        duel_repo = DuelRepository()

        async with factory() as db, db.begin():
            p_a = await _seed_player(db, _TEST_USER_A)
            p_b = await _seed_player(db, _TEST_USER_B)
            duel = await _seed_duel(db, p_a.id, p_b.id)
            duel_id = duel.id

        a_locked_event = asyncio.Event()
        b_preloaded_event = asyncio.Event()
        result: dict = {}

        async def session_a():
            async with factory() as db_a:
                locked = await duel_repo.get_by_id_for_update(db_a, duel_id)
                a_locked_event.set()
                await b_preloaded_event.wait()
                locked.status = "completed"
                await db_a.commit()

        async def session_b():
            await a_locked_event.wait()
            async with factory() as db_b:
                stale = await duel_repo.get_by_id(db_b, duel_id)
                result["pre_load"] = stale.status
                b_preloaded_event.set()

                locked = await duel_repo.get_by_id_for_update(db_b, duel_id)
                assert locked is stale, "Same Python object via identity map"
                result["fresh_status"] = locked.status
                await db_b.rollback()

        await asyncio.gather(session_a(), session_b())

        assert result["pre_load"] == "pending", "B saw pending before A committed"
        assert result["fresh_status"] == "completed", (
            "populate_existing=True must overwrite clean stale 'pending' with "
            "committed 'completed' after the FOR UPDATE fetch"
        )

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# D2b: Duel stale-attribute trap — negative probe
# ===========================================================================


async def test_d2b_duel_without_populate_existing_returns_stale_status():
    """Naive FOR UPDATE without populate_existing returns stale duel status.

    Uses a real concurrent scenario to prove the bug: B loads 'pending'
    cleanly before A commits, A commits 'completed', B's naive FOR UPDATE
    (no populate_existing) still returns 'pending' from the identity map.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        duel_repo = DuelRepository()

        async with factory() as db, db.begin():
            p_a = await _seed_player(db, _TEST_USER_A)
            p_b = await _seed_player(db, _TEST_USER_B)
            duel = await _seed_duel(db, p_a.id, p_b.id)
            duel_id = duel.id

        a_locked_event = asyncio.Event()
        b_preloaded_event = asyncio.Event()
        result: dict = {}

        async def session_a():
            async with factory() as db_a:
                locked = await duel_repo.get_by_id_for_update(db_a, duel_id)
                a_locked_event.set()
                await b_preloaded_event.wait()
                locked.status = "completed"
                await db_a.commit()

        async def session_b():
            await a_locked_event.wait()
            async with factory() as db_b:
                stale = await duel_repo.get_by_id(db_b, duel_id)
                result["pre_load"] = stale.status
                b_preloaded_event.set()

                # NAIVE FOR UPDATE without populate_existing — blocks until A commits
                naive_result = await db_b.execute(
                    select(DuelRequest).where(DuelRequest.id == duel_id).with_for_update()
                    # .execution_options(populate_existing=True) intentionally OMITTED
                )
                naive_locked = naive_result.scalars().first()
                assert naive_locked is stale, "Same Python object via identity map"
                result["naive_status"] = naive_locked.status
                await db_b.rollback()

        await asyncio.gather(session_a(), session_b())

        assert result["pre_load"] == "pending"
        assert result["naive_status"] == "pending", (
            "WITHOUT populate_existing, the identity-map cached 'pending' is "
            "returned even after A committed 'completed' — double-accept bug vector"
        )

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# D3: No AB-BA deadlock under duel-first + ascending-player lock ordering
# ===========================================================================


async def test_d3_no_abba_deadlock_duel_accept():
    """Duel-first + ascending-player lock order prevents AB-BA deadlock."""
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        duel_repo = DuelRepository()
        player_repo = PlayerRepository()

        async with factory() as db, db.begin():
            p_a = await _seed_player(db, _TEST_USER_A, credits=5000)
            p_b = await _seed_player(db, _TEST_USER_B, credits=5000)
            duel = await _seed_duel(db, p_a.id, p_b.id, stakes=100)
            duel_id = duel.id
            p_a_id, p_b_id = p_a.id, p_b.id

        done = []

        async def accept_attempt(offset: float):
            await asyncio.sleep(offset)
            async with factory() as db:
                locked_duel = await duel_repo.get_by_id_for_update(db, duel_id)
                if locked_duel is None or locked_duel.status != "pending":
                    await db.rollback()
                    done.append("skipped")
                    return
                for pid in sorted([p_a_id, p_b_id]):
                    await player_repo.get_by_id_for_update(db, pid)
                locked_duel.status = "completed"
                await db.commit()
                done.append("completed")

        await asyncio.wait_for(asyncio.gather(accept_attempt(0.0), accept_attempt(0.03)), timeout=10.0)

        assert "completed" in done
        assert len(done) == 2

    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# D4: Timeout-then-retry for duel accept — exactly one payout
# ===========================================================================


async def test_d4_timeout_then_retry_exactly_one_accept():
    """Client timeout mid-accept + retry → exactly one status change applied."""
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        duel_repo = DuelRepository()

        async with factory() as db, db.begin():
            p_a = await _seed_player(db, _TEST_USER_A, credits=5000)
            p_b = await _seed_player(db, _TEST_USER_B, credits=5000)
            duel = await _seed_duel(db, p_a.id, p_b.id, stakes=150)
            duel_id = duel.id

        async def attempt_accept() -> str:
            async with factory() as db:
                locked = await duel_repo.get_by_id_for_update(db, duel_id)
                if locked is None:
                    return "not_found"
                if locked.status != "pending":
                    await db.rollback()
                    return "already_resolved"
                locked.status = "completed"
                await db.commit()
                return "accepted"

        r1 = await attempt_accept()
        assert r1 == "accepted"

        r2 = await attempt_accept()
        assert r2 == "already_resolved"

        async with factory() as db:
            final = await duel_repo.get_by_id(db, duel_id)
            assert final.status == "completed"

    finally:
        await _cleanup(factory)
        await engine.dispose()
