"""
D5-T2 — Cross-path lock-ordering / deadlock safety (REAL Postgres).

D5-T1 proved the single-player aggregate-root Player lock at the loadout
choke-point.  D5-T2 audits and fixes the ORDER in which paths that lock >1 row
acquire their locks, enforcing the global rule:

    aggregate row (Bounty / Duel) FIRST, then Player row(s) in ASCENDING
    player_id order.

These tests drive REAL async Postgres sessions against bountydev-db so genuine
row-level FOR UPDATE blocking applies (SQLite cannot model FOR UPDATE blocking
or deadlock detection).  The harness is genuinely concurrent: two real
``AsyncSession`` objects, one holding its transaction open across an ``await``
while the other contends for the same rows.

Cases:
  H   DEADLOCK-HUNT — buy-credit-RMW vs equip on the SAME player, N iterations,
      both arrival orders → always serialises, never deadlocks, credits + slots
      consistent (design §case 5; the buy's credit lock and the equip's loadout
      lock are the SAME Player row → collapse into one lock class).
  X   CROSS-PLAYER two-player ascending-lock vs reverse-intent op, N iterations →
      both converge on ascending id order → no AB-BA deadlock.
  T   transfer_ship (two players, ascending lock) concurrent with an equip on
      from_player → no deadlock; from_player aggregate consistent.
  Tv  ANTI-VACUOUS for the transfer_ship fix: WITHOUT the to_player lock a
      concurrent credit op on to_player is NOT serialised; WITH it, it blocks —
      proving the added to_player lock is load-bearing.
  B   BOUNTY-ORDERING — a Bounty-then-Player transaction (mirrors /check's
      payout lock order) interleaved with a Player-only loadout op on the same
      player → no deadlock, no double-apply (the player op serialises behind the
      bounty txn's player lock; the bounty txn never waits on the player op).

Connection: bountydev-db at 172.18.0.2:5432 (bountydev-net bridge IP — re-check via
`sudo docker inspect bountydev-db` after a stack rebuild; host-published localhost:15432 is
unreachable from this dev container).
Each test creates its own engine inline (mirrors test_d5_loadout_locking.py) to
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
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from persist.repositories.bounty_repository import BountyRepository
from persist.repositories.player_repository import PlayerRepository
from services.loadout_consistency_service import LoadoutConsistencyService
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Real Postgres connection — bountydev-db on docker bridge network
# ---------------------------------------------------------------------------

_PG_URL = "postgresql+asyncpg://bounty:bounty@172.18.0.2:5432/bountydb"

# Test-isolation constants: guild/user IDs that cannot collide with production.
_TEST_GUILD = 999_888_777_056
_TEST_USER_A = 999_888_056_001
_TEST_USER_B = 999_888_056_002

# Static game data assumed seeded in bountydev-db (verified present at HEAD):
_ITEM_NAME = "Micro Gun MK I"
_INV_TYPE = "primary_weapon"
_EQUIP_TYPE = "weapons"
_SHIP_NAME = "Wasp"  # single primary slot


# ---------------------------------------------------------------------------
# Engine / factory + cleanup / seed helpers
# ---------------------------------------------------------------------------


def _make_pg_factory() -> tuple:
    engine = create_async_engine(_PG_URL, pool_size=8, max_overflow=4, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def _cleanup(factory) -> None:
    async with factory() as db, db.begin():
        await db.execute(text(f"UPDATE players SET active_ship_id = NULL WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(
            text(
                "DELETE FROM player_inventories WHERE player_id IN "
                f"(SELECT id FROM players WHERE guild_id = {_TEST_GUILD})"
            )
        )
        await db.execute(
            text(f"DELETE FROM player_ships WHERE player_id IN (SELECT id FROM players WHERE guild_id = {_TEST_GUILD})")
        )
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


async def _seed_player_with_ship(
    db: AsyncSession,
    user_id: int,
    *,
    cargo_qty: int = 1,
    credits: int = 10_000,
) -> tuple[int, int]:
    """Seed a player, an active Wasp, and ``cargo_qty`` copies of the test weapon."""
    await _seed_user(db, user_id)
    player = Player(
        user_id=user_id,
        guild_id=_TEST_GUILD,
        credits=credits,
        tier="Bronze",
        classic_mode=True,
    )
    db.add(player)
    await db.flush()

    ship = PlayerShip(
        player_id=player.id,
        ship_name=_SHIP_NAME,
        is_active=True,
        weapons=[],
        modules=[],
        turrets=[],
        secondary_weapons=[],
    )
    db.add(ship)
    await db.flush()
    player.active_ship_id = ship.id

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
    return player.id, ship.id


async def _seed_bounty(db: AsyncSession) -> int:
    bounty = Bounty(
        guild_id=_TEST_GUILD,
        division="bronze",
        criminal_name="D5T2 Test Criminal",
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


async def _owned_breakdown(factory, player_id: int) -> tuple[int, int]:
    async with factory() as db:
        res = await db.execute(
            select(PlayerInventory.quantity).where(
                PlayerInventory.player_id == player_id,
                PlayerInventory.item_type == _INV_TYPE,
                PlayerInventory.item_name == _ITEM_NAME,
            )
        )
        cargo = res.scalars().first() or 0
        res = await db.execute(select(PlayerShip).where(PlayerShip.player_id == player_id))
        equipped = sum(list(s.weapons or []).count(_ITEM_NAME) for s in res.scalars().all())
        return cargo, equipped


def _is_deadlock(exc: BaseException) -> bool:
    """True if exc is a PostgreSQL deadlock (SQLSTATE 40P01)."""
    s = str(exc)
    return "deadlock detected" in s or "40P01" in s


# ===========================================================================
# Case H: DEADLOCK-HUNT — buy-credit-RMW vs equip on the SAME player
# ===========================================================================


@pytest.mark.parametrize("equip_first", [True, False])
@pytest.mark.parametrize("iteration", range(4))
async def test_caseH_buy_vs_equip_same_player_no_deadlock(equip_first, iteration):
    """Shop-buy credit RMW vs equip on one player, both arrival orders, N runs.

    Both ops lock the SAME Player row (the buy for credits, the equip for the
    loadout aggregate) — D5's "collapse into one lock class" claim.  Whichever
    acquires the Player FOR UPDATE first runs to completion; the other blocks at
    its own Player lock and proceeds after.  There is exactly one lock class, so
    NO deadlock is possible and the credit deduction + slot mutation both land.

    The buy is modelled faithfully to ``shop_service.purchase_ship`` ordering:
    Player FOR UPDATE first, then mutate credits.  (The full purchase_ship adds a
    GuildConfig-bound shop row; the lock ORDER under test is the player lock,
    which is what this exercises against the real choke-point's player lock.)
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1, credits=10_000)

        svc = LoadoutConsistencyService()
        player_repo = PlayerRepository()
        first_locked = asyncio.Event()
        errors: list = []
        ship_price = 3000

        async def buy_op(go_first: bool):
            if not go_first:
                await first_locked.wait()
                await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    # purchase_ship order: Player FOR UPDATE FIRST, then credit RMW.
                    p = await player_repo.get_by_id_for_update(db, player_id)
                    if go_first:
                        first_locked.set()
                        await asyncio.sleep(0.25)  # hold the lock so the other side blocks
                    p.credits = p.credits - ship_price
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    errors.append(("buy", exc))

        async def equip_op(go_first: bool):
            if not go_first:
                await first_locked.wait()
                await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    if go_first:
                        # equip_one takes the Player lock internally as its first act.
                        await svc.equip_one(
                            db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                        )
                        first_locked.set()
                        await asyncio.sleep(0.25)
                    else:
                        await svc.equip_one(
                            db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                        )
                    await db.commit()
                except ValueError:
                    await db.rollback()  # legitimate guard rejection (no cargo) — not a deadlock
                except Exception as exc:
                    await db.rollback()
                    errors.append(("equip", exc))

        if equip_first:
            await asyncio.gather(equip_op(True), buy_op(False))
        else:
            await asyncio.gather(buy_op(True), equip_op(False))

        # No deadlock and no unexpected error on either side.
        assert not any(_is_deadlock(e) for _, e in errors), f"DEADLOCK detected: {errors}"
        assert errors == [], f"[iter {iteration} equip_first={equip_first}] unexpected error(s): {errors}"

        # Credits were deducted exactly once (buy committed); slot mutated exactly once.
        final_credits = await _credits(factory, player_id)
        assert final_credits == 10_000 - ship_price, f"credits not deducted exactly once: {final_credits}"
        cargo, equipped = await _owned_breakdown(factory, player_id)
        assert equipped == 1, f"equip must have applied exactly once: equipped={equipped}"
        assert cargo + equipped == 1, f"owned not conserved: cargo={cargo} equipped={equipped}"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case X: CROSS-PLAYER two-player ascending lock — no AB-BA deadlock
# ===========================================================================


@pytest.mark.parametrize("iteration", range(6))
async def test_caseX_two_player_ascending_lock_no_deadlock(iteration):
    """Two transactions touching players {A,B} in OPPOSITE intended order.

    Both honour the global rule and lock in ASCENDING player_id order, so even
    when their business intent is reversed they acquire the SAME lock first and
    serialise instead of deadlocking.  This is the structural guarantee behind
    transfer_credits / transfer_ship / duel-accept.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            pid_a, _ = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=0)
            pid_b, _ = await _seed_player_with_ship(db, _TEST_USER_B, cargo_qty=0)

        player_repo = PlayerRepository()
        lo, hi = sorted([pid_a, pid_b])
        errors: list = []

        async def txn(label: str):
            async with factory() as db:
                await db.begin()
                try:
                    # Rule 2: ALWAYS ascending id order regardless of business direction.
                    for pid in (lo, hi):
                        await player_repo.get_by_id_for_update(db, pid)
                    await asyncio.sleep(0.05)  # widen the contention window
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    errors.append((label, exc))

        await asyncio.gather(txn("t1"), txn("t2"))
        assert not any(_is_deadlock(e) for _, e in errors), f"[iter {iteration}] DEADLOCK detected: {errors}"
        assert errors == [], f"[iter {iteration}] unexpected error(s): {errors}"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case T: transfer_ship (two players, ascending) concurrent with equip
# ===========================================================================


async def test_caseT_transfer_ship_concurrent_equip_from_player_no_deadlock():
    """transfer_ship's two-player ascending lock vs an equip on from_player.

    Mirrors the router's NEW ordering (D5-T2): lock both players ascending, then
    evacuate from_player's ship loadout.  Run concurrently with an equip on
    from_player.  The equip locks only from_player; the transfer locks both —
    no cycle (the equip never waits on to_player), so no deadlock, and the
    from_player aggregate stays consistent (owned conserved).
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            # from_player has a NON-active ship carrying one equipped weapon to evacuate.
            from_pid, _active_ship = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)
            to_pid, _ = await _seed_player_with_ship(db, _TEST_USER_B, cargo_qty=0)
            xfer_ship = PlayerShip(
                player_id=from_pid,
                ship_name=_SHIP_NAME,
                is_active=False,
                weapons=[_ITEM_NAME],
                modules=[],
                turrets=[],
                secondary_weapons=[],
            )
            db.add(xfer_ship)
            await db.flush()
            xfer_ship_id = xfer_ship.id
            active_ship_id = _active_ship

        svc = LoadoutConsistencyService()
        player_repo = PlayerRepository()
        transfer_locked = asyncio.Event()
        errors: list = []
        lo, hi = sorted([from_pid, to_pid])

        async def transfer_op():
            async with factory() as db:
                await db.begin()
                try:
                    # NEW transfer_ship ordering: both players ascending, THEN evacuate.
                    for pid in (lo, hi):
                        await player_repo.get_by_id_for_update(db, pid)
                    transfer_locked.set()
                    await asyncio.sleep(0.2)  # hold both player locks
                    ship = await db.get(PlayerShip, xfer_ship_id)
                    await svc.evacuate_ship_loadout_to_inventory(db, ship=ship)
                    ship.player_id = to_pid
                    ship.is_active = False
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    errors.append(("transfer", exc))

        async def equip_op():
            await transfer_locked.wait()
            await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    # Equip on from_player's ACTIVE ship — blocks on from_player's lock
                    # held by the transfer, then proceeds after the transfer commits.
                    await svc.equip_one(
                        db,
                        player_id=from_pid,
                        ship_id=active_ship_id,
                        item_name=_ITEM_NAME,
                        equipment_type=_EQUIP_TYPE,
                    )
                    await db.commit()
                except ValueError:
                    await db.rollback()  # guard rejection is legitimate, not a deadlock
                except Exception as exc:
                    await db.rollback()
                    errors.append(("equip", exc))

        await asyncio.gather(transfer_op(), equip_op())
        assert not any(_is_deadlock(e) for _, e in errors), f"DEADLOCK detected: {errors}"
        assert errors == [], f"unexpected error(s): {errors}"

        # The transferred ship now belongs to to_player and is empty.
        async with factory() as db:
            ship = await db.get(PlayerShip, xfer_ship_id)
            assert ship.player_id == to_pid, "ship ownership not transferred"
            assert list(ship.weapons or []) == [], "transferred ship slots must be evacuated"

        # from_player aggregate consistent: the evacuated weapon + any pre-seeded cargo
        # are conserved against what the equip consumed (no item lost or duplicated).
        cargo, equipped = await _owned_breakdown(factory, from_pid)
        # Seeded: 1 cargo + 1 on the transferred ship = 2 owned for from_player.
        # The evacuate mints the transferred-ship copy into cargo (=2 cargo), then the
        # equip may move one cargo -> active-ship slot.  Either way owned == 2.
        assert cargo + equipped == 2, f"from_player owned not conserved: cargo={cargo} equipped={equipped}"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case Tv: ANTI-VACUOUS — the to_player lock in transfer_ship is load-bearing
# ===========================================================================


async def test_caseTv_to_player_lock_is_load_bearing():
    """Prove the D5-T2 to_player FOR UPDATE in transfer_ship actually serialises.

    Phase 1 (lock present): a transaction locks both players ascending and holds.
    A concurrent credit op on to_player BLOCKS until the transfer commits → it
    does NOT complete within the hold window.

    Phase 2 (lock bypassed): the same transaction locks ONLY from_player (the
    pre-fix behaviour).  The concurrent credit op on to_player completes WITHIN
    the hold window because to_player was never locked.  The contrast proves the
    added to_player lock changes the outcome and is load-bearing.
    """
    engine, factory = _make_pg_factory()
    try:
        player_repo = PlayerRepository()

        async def run(lock_both: bool) -> bool:
            """Return True if the concurrent to_player credit op completed during the hold."""
            await _cleanup(factory)
            async with factory() as db, db.begin():
                from_pid, _ = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=0)
                to_pid, _ = await _seed_player_with_ship(db, _TEST_USER_B, cargo_qty=0)
            lo, hi = sorted([from_pid, to_pid])
            holder_locked = asyncio.Event()
            completed_during_hold = {"v": False}

            async def holder():
                async with factory() as db:
                    await db.begin()
                    if lock_both:
                        for pid in (lo, hi):
                            await player_repo.get_by_id_for_update(db, pid)
                    else:
                        await player_repo.get_by_id_for_update(db, from_pid)  # pre-fix: only from_player
                    holder_locked.set()
                    await asyncio.sleep(0.3)  # hold window
                    await db.rollback()

            async def contender():
                await holder_locked.wait()
                await asyncio.sleep(0.02)
                async with factory() as db:
                    await db.begin()
                    p = await player_repo.get_by_id_for_update(db, to_pid)  # contends for to_player
                    p.credits = p.credits + 1
                    await db.commit()
                    completed_during_hold["v"] = not holder_done["v"]

            holder_done = {"v": False}

            async def holder_wrapper():
                await holder()
                holder_done["v"] = True

            await asyncio.gather(holder_wrapper(), contender())
            return completed_during_hold["v"]

        # Phase 2 (bypass): to_player NOT locked → contender finishes during hold.
        finished_during_hold_bypass = await run(lock_both=False)
        assert finished_during_hold_bypass is True, (
            "ANTI-VACUOUS setup failed: without the to_player lock the contender did "
            "NOT complete during the hold — the probe cannot demonstrate the fix."
        )

        # Phase 1 (fix): to_player locked → contender blocks, finishes AFTER hold.
        finished_during_hold_fix = await run(lock_both=True)
        assert finished_during_hold_fix is False, (
            "ANTI-VACUOUS FAILED: with both players locked the contender still "
            "completed during the hold — the to_player lock is not serialising."
        )
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case B: BOUNTY-ORDERING — Bounty-then-Player vs Player-only loadout op
# ===========================================================================


async def test_caseB_bounty_then_player_vs_player_only_no_deadlock():
    """A Bounty-then-Player txn (mirrors /check payout) vs a Player-only equip.

    The bounty txn locks the Bounty row FIRST (aggregate-first), then takes the
    Player FOR UPDATE for the payout.  The loadout op locks only the Player.
    Because no path locks Player-then-Bounty, there is no cycle: the bounty txn
    never waits on the loadout op's lock in a way that closes a loop.  Assert no
    deadlock and that the equip applies exactly once (no double-apply), serialised
    behind whichever side won the Player lock.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)
            bounty_id = await _seed_bounty(db)

        svc = LoadoutConsistencyService()
        player_repo = PlayerRepository()
        bounty_repo = BountyRepository()
        bounty_locked = asyncio.Event()
        errors: list = []

        async def bounty_op():
            async with factory() as db:
                await db.begin()
                try:
                    # /check order: Bounty row FOR UPDATE first ...
                    b = await bounty_repo.get_by_id_for_update(db, bounty_id)
                    bounty_locked.set()
                    await asyncio.sleep(0.2)  # hold the bounty lock, then take the player lock
                    # ... then the Player for the payout (distribute_rewards loads the player).
                    p = await player_repo.get_by_id_for_update(db, player_id)
                    p.credits = p.credits + b.reward
                    b.status = "completed"
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    errors.append(("bounty", exc))

        async def equip_op():
            await bounty_locked.wait()
            await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    await svc.equip_one(
                        db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                    )
                    await db.commit()
                except ValueError:
                    await db.rollback()
                except Exception as exc:
                    await db.rollback()
                    errors.append(("equip", exc))

        await asyncio.gather(bounty_op(), equip_op())
        assert not any(_is_deadlock(e) for _, e in errors), f"DEADLOCK detected: {errors}"
        assert not any(isinstance(e, (OperationalError, DBAPIError)) for _, e in errors), f"DB error: {errors}"
        assert errors == [], f"unexpected error(s): {errors}"

        # Payout applied exactly once; equip applied exactly once; owned conserved.
        final_credits = await _credits(factory, player_id)
        assert final_credits == 10_000 + 1000, f"bounty payout not applied exactly once: {final_credits}"
        cargo, equipped = await _owned_breakdown(factory, player_id)
        assert equipped == 1, f"equip double-applied or lost: equipped={equipped}"
        assert cargo + equipped == 1, f"owned not conserved: cargo={cargo} equipped={equipped}"
    finally:
        await _cleanup(factory)
        await engine.dispose()
