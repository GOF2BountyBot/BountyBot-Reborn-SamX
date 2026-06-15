"""
D5-T2c — Aggregate-root Player lock on the inventory / player-mutation paths
(REAL Postgres).

This module closes the systemic lock gap whose live-confirmed instance was
D-015: ``InventoryService.transfer_item_between_players`` duplicated a player's
LAST copy of an item when two ``POST /inventory/transfer`` calls raced (total
count 2 → 3).  The root cause was the broader D5-T2 pattern — a same-player
read-modify-write executed WITHOUT first acquiring the aggregate-root Player
``FOR UPDATE`` lock — present on several directly-exposed endpoints:

    1. transfer_item_between_players  (POST /inventory/transfer)   — D-015
    2. add_item_to_inventory          (POST /inventory/add, admin) — qty RMW
    3. remove_item_from_inventory     (POST /inventory/remove, admin)
    4. update_player_credits          (PUT  /players/{id}/credits) — lifetime RMW
    5. create_challenge               (POST /duels/challenge)      — hardening

Like the sibling D5-T2 modules, these tests drive REAL async Postgres sessions
against bountydev-db so genuine row-level ``FOR UPDATE`` blocking applies
(SQLite cannot model FOR UPDATE blocking / deadlock detection).  The harness is
genuinely concurrent: two real ``AsyncSession`` objects, one holding its
transaction open across an ``await`` while the other contends for the same row.

HOW THESE TESTS ARE NON-VACUOUS (they fail without the fix):
Each test uses the canonical idiom from test_concurrency_idempotency.py — a
HOLDER session that performs the FIRST same-player mutation under a Player
``FOR UPDATE`` lock and HOLDS it across an await, while a CONTENDER calls the
REAL service method under test.  The service method's own internal lock is the
only thing that can serialise the contender's READ:

  * WITH the fix, the service acquires ``get_by_id_for_update`` FIRST, so the
    contender blocks on the Player row BEFORE its read and reads the holder's
    committed state → correct result.
  * WITHOUT the fix (the pre-fix unlocked ``get_by_id``), the contender reads
    STALE state during the hold (READ COMMITTED), computes a result from the
    stale value, and only its final write blocks on the row — producing the
    classic LOST UPDATE / duplication.  The asserted outcome differs, so the
    test goes red.  (Verified by neutralising the locks during development.)

Connection: resolved from POSTGRES_* env vars, falling back to the dev stack
(bountydev-db on the docker bridge) — see tests/pg_env.py.  CI provisions its own
migrated + seeded postgres service container; without a usable DB the module
skips.  Each test creates its own engine inline (mirrors test_d5t2_lock_ordering)
to keep the asyncpg pool bound to the test's event loop.
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
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.user import User
from persist.repositories.duel_repository import DuelRepository
from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.player_repository import PlayerRepository
from services.duel_service import DuelService
from services.inventory_service import InventoryService
from services.player_service import PlayerService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.pg_env import PG_ASYNC_URL, pg_skip_reason

# ---------------------------------------------------------------------------
# Real Postgres connection — POSTGRES_* env (CI) with bountydev-db fallback.
# ---------------------------------------------------------------------------

_PG_URL = PG_ASYNC_URL

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

# Test-isolation constants: guild/user IDs that cannot collide with production.
_TEST_GUILD = 999_888_777_062
_TEST_USER_A = 999_888_062_001
_TEST_USER_B = 999_888_062_002
_TEST_USER_C = 999_888_062_003

# Static game data assumed seeded in bountydev-db (verified present at HEAD,
# reused from test_d5t2_lock_ordering.py).
_ITEM_NAME = "Micro Gun MK I"
_INV_TYPE = "primary_weapon"


# ---------------------------------------------------------------------------
# Engine / factory + cleanup / seed helpers
# ---------------------------------------------------------------------------


def _make_pg_factory() -> tuple:
    engine = create_async_engine(_PG_URL, pool_size=8, max_overflow=4, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def _cleanup(factory) -> None:
    async with factory() as db, db.begin():
        await db.execute(text(f"DELETE FROM duel_requests WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(
            text(
                "DELETE FROM player_inventories WHERE player_id IN "
                f"(SELECT id FROM players WHERE guild_id = {_TEST_GUILD})"
            )
        )
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


async def _seed_player(
    db: AsyncSession, user_id: int, *, cargo_qty: int = 0, credits: int = 10_000, lifetime: int = 0
) -> int:
    """Seed a player with ``cargo_qty`` copies of the test weapon in cargo."""
    await _seed_user(db, user_id)
    player = Player(
        user_id=user_id,
        guild_id=_TEST_GUILD,
        credits=credits,
        lifetime_credits=lifetime,
        tier="Bronze",
        classic_mode=True,
    )
    db.add(player)
    await db.flush()
    if cargo_qty > 0:
        db.add(
            PlayerInventory(
                player_id=player.id,
                item_type=_INV_TYPE,
                item_name=_ITEM_NAME,
                quantity=cargo_qty,
            )
        )
        await db.flush()
    return player.id


async def _total_item_count(factory, *player_ids: int) -> int:
    """Sum the test weapon's quantity across the given players' cargo rows."""
    async with factory() as db:
        res = await db.execute(
            select(PlayerInventory.quantity).where(
                PlayerInventory.player_id.in_(player_ids),
                PlayerInventory.item_type == _INV_TYPE,
                PlayerInventory.item_name == _ITEM_NAME,
            )
        )
        return sum(res.scalars().all())


async def _cargo_qty(factory, player_id: int) -> int:
    async with factory() as db:
        res = await db.execute(
            select(PlayerInventory.quantity).where(
                PlayerInventory.player_id == player_id,
                PlayerInventory.item_type == _INV_TYPE,
                PlayerInventory.item_name == _ITEM_NAME,
            )
        )
        return res.scalars().first() or 0


async def _player_row(factory, player_id: int) -> tuple[int, int]:
    async with factory() as db:
        res = await db.execute(select(Player.credits, Player.lifetime_credits).where(Player.id == player_id))
        row = res.one()
        return row.credits, row.lifetime_credits


# ===========================================================================
# Case 1 (D-015): transfer of the LAST copy — concurrent transfers serialise,
#                 no duplication.
# ===========================================================================


async def test_case1_transfer_last_copy_no_duplication():
    """Two transfers of a player's LAST copy race → item count CONSERVED at 1.

    Reproduces D-015.  The HOLDER performs the first transfer's effect under the
    source player's FOR UPDATE lock — decrement source cargo 1→0 and mint a copy
    into recipient B — then holds the lock.  The CONTENDER calls the REAL
    ``transfer_item_between_players`` to recipient C.

    WITH the fix the contender's transfer locks the source player FIRST, blocks
    until the holder commits, reads the fresh source cargo (0), and its remove
    fails the "insufficient quantity" guard → the single copy was moved exactly
    once (to B), total count stays 1.  WITHOUT the fix the contender reads the
    stale source cargo (1) during the hold, passes the check, and ALSO mints a
    copy into C → total becomes 2 (the live D-015 duplication).
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            from_pid = await _seed_player(db, _TEST_USER_A, cargo_qty=1)
            to_b = await _seed_player(db, _TEST_USER_B, cargo_qty=0)
            to_c = await _seed_player(db, _TEST_USER_C, cargo_qty=0)

        svc = InventoryService()
        player_repo = PlayerRepository()
        inv_repo = InventoryRepository()
        holder_committed = asyncio.Event()
        contender_outcome: dict = {}

        async def holder():
            async with factory() as db:
                await db.begin()
                # First transfer's effect, under the source player's lock.
                await player_repo.get_by_id_for_update(db, from_pid)
                await inv_repo.remove_item(db, from_pid, _INV_TYPE, _ITEM_NAME, 1, commit=False)
                await inv_repo.add_item(db, to_b, _INV_TYPE, _ITEM_NAME, 1, commit=False)
                await db.flush()
                holder_committed.set()
                await asyncio.sleep(0.25)  # hold the source lock so the contender contends
                await db.commit()

        async def contender():
            await holder_committed.wait()
            await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    await svc.transfer_item_between_players(db, from_pid, to_c, _INV_TYPE, _ITEM_NAME, quantity=1)
                    await db.commit()
                    contender_outcome["v"] = "ok"
                except ValueError as exc:
                    await db.rollback()
                    contender_outcome["v"] = ("rejected", str(exc))
                except Exception as exc:  # pragma: no cover
                    await db.rollback()
                    contender_outcome["v"] = ("error", exc)

        await asyncio.gather(holder(), contender())

        assert contender_outcome["v"][0] == "rejected", (
            f"contender transfer must be rejected (source already emptied): {contender_outcome}"
        )
        # THE D-015 INVARIANT: total item count conserved — NOT duplicated.
        total = await _total_item_count(factory, from_pid, to_b, to_c)
        assert total == 1, f"item duplicated/lost: total={total} (expected 1)"
        assert await _cargo_qty(factory, from_pid) == 0
        assert await _cargo_qty(factory, to_b) == 1, "the one valid transfer landed on B"
        assert await _cargo_qty(factory, to_c) == 0, "the racing transfer to C must NOT have minted a copy"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case 2: add_item_to_inventory — concurrent adds, no lost update.
# ===========================================================================


async def test_case2_concurrent_add_no_lost_update():
    """Two concurrent +1 adds onto a seed of 1 → quantity sums to 3 (no lost update).

    The add is an RMW (read qty → +delta → write).  The HOLDER does the first +1
    (1→2) under the Player lock and holds; the CONTENDER calls the REAL
    ``add_item_to_inventory(+1)``.  WITH the fix it blocks on the Player lock,
    reads fresh qty=2, writes 3.  WITHOUT the fix it reads stale qty=1 and writes
    2 — a lost update.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            pid = await _seed_player(db, _TEST_USER_A, cargo_qty=1)

        svc = InventoryService()
        player_repo = PlayerRepository()
        inv_repo = InventoryRepository()
        holder_ready = asyncio.Event()

        async def holder():
            async with factory() as db:
                await db.begin()
                await player_repo.get_by_id_for_update(db, pid)
                await inv_repo.add_item(db, pid, _INV_TYPE, _ITEM_NAME, 1, commit=False)  # 1 -> 2
                await db.flush()
                holder_ready.set()
                await asyncio.sleep(0.25)
                await db.commit()

        async def contender():
            await holder_ready.wait()
            await asyncio.sleep(0.03)
            async with factory() as db:
                await svc.add_item_to_inventory(db, pid, _INV_TYPE, _ITEM_NAME, quantity=1)  # must see 2 -> 3

        await asyncio.gather(holder(), contender())
        assert await _cargo_qty(factory, pid) == 3, "both +1 adds must compose on the seed (1+1+1)"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case 3: remove_item_from_inventory — concurrent removes serialise.
# ===========================================================================


async def test_case3_concurrent_remove_no_lost_update():
    """Two concurrent −1 removes from a seed of 2 → quantity lands at 0.

    The HOLDER removes one (2→1) under the Player lock and holds; the CONTENDER
    calls the REAL ``remove_item_from_inventory(1)``.  WITH the fix it blocks,
    reads fresh qty=1, removes → 0.  WITHOUT the fix it reads stale qty=2 and
    writes 1 — losing a removal.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            pid = await _seed_player(db, _TEST_USER_A, cargo_qty=2)

        svc = InventoryService()
        player_repo = PlayerRepository()
        inv_repo = InventoryRepository()
        holder_ready = asyncio.Event()

        async def holder():
            async with factory() as db:
                await db.begin()
                await player_repo.get_by_id_for_update(db, pid)
                await inv_repo.remove_item(db, pid, _INV_TYPE, _ITEM_NAME, 1, commit=False)  # 2 -> 1
                await db.flush()
                holder_ready.set()
                await asyncio.sleep(0.25)
                await db.commit()

        async def contender():
            await holder_ready.wait()
            await asyncio.sleep(0.03)
            async with factory() as db:
                await svc.remove_item_from_inventory(db, pid, _INV_TYPE, _ITEM_NAME, quantity=1)  # must see 1 -> 0

        await asyncio.gather(holder(), contender())
        assert await _cargo_qty(factory, pid) == 0, "both −1 removes must land (2−1−1)"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case 4: update_player_credits — lifetime_credits RMW depends on a fresh read.
# ===========================================================================


async def test_case4_concurrent_credit_update_lifetime_rmw():
    """lifetime_credits accumulation requires the fresh balance under the lock.

    Seed credits=5000, lifetime=0.  The HOLDER drops credits to 1000 (a decrease,
    so lifetime is unchanged) under the Player lock and holds.  The CONTENDER
    calls the REAL ``update_player_credits(3000, update_lifetime=True)``.

    WITH the fix the contender blocks, reads the fresh balance 1000, sees
    3000 > 1000 (an increase of 2000) and accumulates lifetime 0 → 2000.  WITHOUT
    the fix it reads the STALE balance 5000, decides 3000 < 5000 is NOT an
    increase, and leaves lifetime at 0 — the bug.  The final lifetime value
    distinguishes the two outcomes.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            pid = await _seed_player(db, _TEST_USER_A, credits=5000, lifetime=0)

        svc = PlayerService()
        player_repo = PlayerRepository()
        holder_ready = asyncio.Event()

        async def holder():
            async with factory() as db:
                await db.begin()
                p = await player_repo.get_by_id_for_update(db, pid)
                p.credits = 1000  # decrease — no lifetime change
                await db.flush()
                holder_ready.set()
                await asyncio.sleep(0.25)
                await db.commit()

        async def contender():
            await holder_ready.wait()
            await asyncio.sleep(0.03)
            async with factory() as db:
                await svc.update_player_credits(db, pid, 3000, update_lifetime=True)

        await asyncio.gather(holder(), contender())
        credits, lifetime = await _player_row(factory, pid)
        assert credits == 3000, f"final credits must be the contender's set value: {credits}"
        assert lifetime == 2000, (
            "lifetime_credits must accumulate the +2000 increase computed from the FRESH "
            f"balance (1000), not be skipped from a stale read of 5000: lifetime={lifetime}"
        )
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case 5: create_challenge — locks both players; HARDENING (not a live exploit).
# ===========================================================================


async def test_case5_create_challenge_blocks_on_player_lock():
    """create_challenge contends on the challenger's held Player lock.

    Proves the D5-T2 hardening lock is wired in: a holder takes the challenger's
    Player row FOR UPDATE and holds it; a concurrent REAL create_challenge BLOCKS
    on that row (it now locks both players ascending before reading credits) and
    so does NOT complete during the hold window — then succeeds after release.

    WITHOUT the fix create_challenge reads the players unlocked and INSERTs the
    duel row (which does not touch the locked Player row), completing DURING the
    hold — so ``completed_during_hold`` would be True and this test would fail.

    This is defense-in-depth: the authoritative stake re-validation happens under
    lock at accept_duel, so the create-time race was never a live double-spend.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            challenger = await _seed_player(db, _TEST_USER_A, credits=10_000)
            target = await _seed_player(db, _TEST_USER_B, credits=10_000)

        svc = DuelService()
        duel_repo = DuelRepository()
        player_repo = PlayerRepository()
        holder_locked = asyncio.Event()
        holder_done = {"v": False}
        completed_during_hold = {"v": False}
        created_id = {"v": None}

        async def holder():
            async with factory() as db:
                await db.begin()
                await player_repo.get_by_id_for_update(db, challenger)
                holder_locked.set()
                await asyncio.sleep(0.3)
                await db.rollback()
            holder_done["v"] = True

        async def challenge_op():
            await holder_locked.wait()
            await asyncio.sleep(0.02)
            async with factory() as db:
                duel = await svc.create_challenge(db, challenger, target, 500, _TEST_GUILD)
                created_id["v"] = duel.id
            completed_during_hold["v"] = not holder_done["v"]

        await asyncio.gather(holder(), challenge_op())

        assert completed_during_hold["v"] is False, (
            "create_challenge did NOT block on the held challenger lock — the "
            "D5-T2 FOR UPDATE in create_challenge is not wired in."
        )
        assert created_id["v"] is not None
        async with factory() as db:
            duel = await duel_repo.get_by_id(db, created_id["v"])
            assert duel is not None and duel.status == "pending"
    finally:
        await _cleanup(factory)
        await engine.dispose()
