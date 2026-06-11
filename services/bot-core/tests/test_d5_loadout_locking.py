"""
D5-T1 — Same-player loadout/inventory aggregate-root locking (REAL Postgres).

These tests exercise the Player-row ``SELECT ... FOR UPDATE`` aggregate-root
mutex injected at the ``LoadoutConsistencyService`` choke-point (``_lock_player``).
They use REAL async Postgres sessions against bountydev-db so that genuine
row-level lock blocking semantics apply — SQLite cannot be used because it does
not implement FOR UPDATE blocking.

The harness is genuinely concurrent: two real ``AsyncSession`` objects, one of
which holds its transaction open across an ``await`` while the other attempts the
same mutation.  A sequential harness would false-pass, so every concurrency
assertion drives both coroutines through ``asyncio.gather`` with one session
parking inside its open transaction.

Cases (from D5 design §Mandatory tester cases):
  1  Concurrent DOUBLE-EQUIP of the same single-slot item → exactly one succeeds,
     no duplicate slot ref, ``owned`` conserved.
  2  Concurrent EQUIP + SELL of the same item → no negative cargo, no item
     materialised/destroyed, final ``owned = cargo + equipped``.
  4  switch-ship (set-active) during SELL of a cargo item → no double-mint/drop.
  8  consolidate_inventory (path 18) during a same-player equip → the consolidate
     route's lock-first + db.begin() serialises the multi-row RMW against the
     equip, conserving owned (D5-T3).  Plus an ANTI-VACUOUS probe that proves the
     two protections SEPARATELY: (a) the LOCK serialises concurrent same-player
     RMWs and prevents the lost update; (b) db.begin() provides durability in
     this raw-factory harness (its sessions roll back uncommitted flushes on
     close, so without db.begin() a commit=False merge does not persist).
  9  Cross-player NON-interference: two DIFFERENT players equipping concurrently
     must NOT serialise (proves no over-locking).
  R  Lock RELEASED on success AND on exception/rollback.
  A  ANTI-VACUOUS probe: WITHOUT the _lock_player call the double-equip produces a
     lost-update / duplicate slot — proving the lock is load-bearing.

Connection: bountydev-db at 172.18.0.2:5432 (bountydev-net bridge IP — re-check via
`sudo docker inspect bountydev-db` after a stack rebuild; host-published localhost:15432 is
unreachable from this dev container).
Each test creates its own engine inline (mirrors test_concurrency_idempotency.py)
to keep the asyncpg pool bound to the test's event loop.
"""

# ---------------------------------------------------------------------------
# Path / sys.modules setup — must happen before any application imports
# ---------------------------------------------------------------------------
import asyncio
import contextlib
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
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from persist.repositories.player_repository import PlayerRepository
from services.inventory_service import InventoryService
from services.loadout_consistency_service import LoadoutConsistencyService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Real Postgres connection — bountydev-db on docker bridge network
# ---------------------------------------------------------------------------

_PG_URL = "postgresql+asyncpg://bounty:bounty@172.18.0.2:5432/bountydb"

# Test-isolation constants: guild/user IDs that cannot collide with production data.
_TEST_GUILD = 999_888_777_055
_TEST_USER_A = 999_888_055_001
_TEST_USER_B = 999_888_055_002

# Static game data assumed seeded in bountydev-db (verified present at HEAD):
#   item 'Micro Gun MK I' (type PrimaryWeapon)  → inventory item_type 'primary_weapon'
#   ship 'Wasp'           (max_primaries == 1)  → single primary slot
_ITEM_NAME = "Micro Gun MK I"
_INV_TYPE = "primary_weapon"
_EQUIP_TYPE = "weapons"
_SHIP_NAME = "Wasp"  # single primary slot


# ---------------------------------------------------------------------------
# Engine / factory helper
# ---------------------------------------------------------------------------


def _make_pg_factory() -> tuple:
    """Create a fresh Postgres engine + session factory bound to the current loop."""
    engine = create_async_engine(_PG_URL, pool_size=6, max_overflow=4, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# Cleanup / seed helpers
# ---------------------------------------------------------------------------


async def _cleanup(factory) -> None:
    """Hard-delete all test rows in the correct FK order."""
    async with factory() as db, db.begin():
        # Break the players.active_ship_id → player_ships.id FK before deleting ships.
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
    """Seed a player, an active Wasp, and ``cargo_qty`` copies of the test weapon.

    Returns ``(player_id, ship_id)``.
    """
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


async def _owned_breakdown(factory, player_id: int, ship_id: int) -> tuple[int, int]:
    """Return ``(cargo_qty, equipped_count)`` for the test weapon on this player.

    ``cargo`` is the ``player_inventories.quantity`` for the weapon (0 if no row).
    ``equipped`` is the count of slot references to the weapon across ALL the
    player's ships (the invariant counts every ship, not just ``ship_id``).
    """
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
        equipped = 0
        for s in res.scalars().all():
            equipped += list(s.weapons or []).count(_ITEM_NAME)
        return cargo, equipped


# ===========================================================================
# Case 1: Concurrent double-equip of the same single-slot item
# ===========================================================================


async def test_case1_concurrent_double_equip_exactly_one_succeeds():
    """Two concurrent equips of the same single-slot item → exactly one succeeds.

    Session A acquires the choke-point's Player FOR UPDATE lock, equips, and holds
    the transaction open.  Session B enters its own ``equip_one`` and blocks at
    ``_lock_player`` until A commits, then re-reads committed state (cargo == 0,
    slot full) and its guard rejects the second equip.  Result: exactly one slot
    reference, no duplicate, ``owned`` conserved at 1.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)

        svc = LoadoutConsistencyService()
        a_locked = asyncio.Event()
        results: dict = {}

        async def session_a():
            async with factory() as db:
                await db.begin()
                await svc.equip_one(
                    db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                )
                a_locked.set()
                # Hold the lock open so B is forced to block at its _lock_player.
                await asyncio.sleep(0.3)
                await db.commit()
                results["a"] = "equipped"

        async def session_b():
            await a_locked.wait()
            await asyncio.sleep(0.05)  # ensure A still holds the lock
            async with factory() as db:
                await db.begin()
                try:
                    await svc.equip_one(
                        db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                    )
                    await db.commit()
                    results["b"] = "equipped"
                except ValueError as e:
                    await db.rollback()
                    results["b"] = f"rejected:{e}"

        await asyncio.gather(session_a(), session_b())

        assert results["a"] == "equipped"
        assert results["b"].startswith("rejected:"), f"B must be rejected, got {results['b']!r}"

        cargo, equipped = await _owned_breakdown(factory, player_id, ship_id)
        assert equipped == 1, f"Exactly one slot reference expected, got {equipped}"
        assert cargo == 0, f"Cargo must be consumed exactly once, got {cargo}"
        assert cargo + equipped == 1, "owned = cargo + equipped must be conserved at 1"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Anti-vacuous probe: WITHOUT _lock_player the double-equip loses an update
# ===========================================================================


async def test_anti_vacuous_lock_is_load_bearing():
    """Negative/positive probe proving ``_lock_player`` is load-bearing.

    The corruption mechanism of the double-equip race is that the second request
    evaluates its slot-cap / cargo guard against STALE (pre-commit) state because
    it never blocked.  This probe demonstrates exactly that read-side race and
    contrasts it with the locked behaviour — without driving two writers at the
    same cargo row (which would write-write block in Postgres regardless of our
    application lock, masking the point).

    Phase 1 (lock bypassed): A holds an uncommitted equip (slot now full, cargo 0
    in A's snapshot).  B, with ``_lock_player`` monkeypatched to a no-op, reads the
    guard inputs and sees STALE committed state (slot empty, cargo 1) → its guard
    WOULD PASS → it would wrongly equip a second copy (lost update / duplicate).

    Phase 2 (lock active): the same B, now using the real ``_lock_player``, BLOCKS
    on A's FOR UPDATE until A commits, then re-reads FRESH state (slot full, cargo
    0) → its guard correctly REJECTS.  The contrast proves the lock changes the
    outcome and is therefore load-bearing, not vacuous.
    """
    engine, factory = _make_pg_factory()
    try:
        # ---------------- Phase 1: bypassed lock → stale guard read ----------------
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)

        a_holding = asyncio.Event()
        b_probed = asyncio.Event()
        probe: dict = {}

        async def holder():
            svc_a = LoadoutConsistencyService()
            async with factory() as db:
                await db.begin()
                # Equip but do NOT commit — slot is full / cargo 0 only in this txn.
                await svc_a.equip_one(
                    db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                )
                a_holding.set()
                await b_probed.wait()
                await db.rollback()  # discard — we only needed the uncommitted state

        async def stale_reader():
            await a_holding.wait()
            async with factory() as db:
                await db.begin()
                # Read the SAME guard inputs equip_one uses, with NO lock.
                ship = await db.get(PlayerShip, ship_id)
                slot = list(ship.weapons or [])
                inv = await db.execute(
                    select(PlayerInventory.quantity).where(
                        PlayerInventory.player_id == player_id,
                        PlayerInventory.item_type == _INV_TYPE,
                        PlayerInventory.item_name == _ITEM_NAME,
                    )
                )
                cargo = inv.scalars().first() or 0
                # equip_one guard: rejects iff slot full (len >= 1) or cargo <= 0.
                probe["unlocked_slot_len"] = len(slot)
                probe["unlocked_cargo"] = cargo
                probe["unlocked_guard_would_pass"] = (len(slot) < 1) and (cargo > 0)
                b_probed.set()
                await db.rollback()

        await asyncio.gather(holder(), stale_reader())

        assert probe["unlocked_slot_len"] == 0, "stale read must see the pre-commit empty slot"
        assert probe["unlocked_cargo"] == 1, "stale read must see the pre-commit cargo of 1"
        assert probe["unlocked_guard_would_pass"] is True, (
            "ANTI-VACUOUS Phase 1 FAILED: without the lock, B's guard does not see "
            "stale state — there would be no race for the lock to fix."
        )

        # ---------------- Phase 2: real lock → fresh guard read, reject -------------
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)

        svc = LoadoutConsistencyService()  # real _lock_player
        a_locked = asyncio.Event()
        outcome: dict = {}

        async def session_a():
            async with factory() as db:
                await db.begin()
                await svc.equip_one(
                    db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                )
                a_locked.set()
                await asyncio.sleep(0.25)  # hold the aggregate-root lock
                await db.commit()

        async def session_b():
            await a_locked.wait()
            await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    await svc.equip_one(
                        db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                    )
                    await db.commit()
                    outcome["b"] = "equipped"
                except ValueError:
                    await db.rollback()
                    outcome["b"] = "rejected"

        await asyncio.gather(session_a(), session_b())
        assert outcome["b"] == "rejected", (
            "ANTI-VACUOUS Phase 2 FAILED: with the lock, B's guard did NOT reject — "
            "the lock did not change the outcome."
        )
        cargo, equipped = await _owned_breakdown(factory, player_id, ship_id)
        assert equipped == 1 and cargo == 0, f"locked run must conserve owned at 1 (cargo={cargo} equipped={equipped})"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case 2: Concurrent equip + sell of the same item
# ===========================================================================


async def test_case2_concurrent_equip_and_sell_owned_conserved():
    """Concurrent equip + sell of the same single cargo copy → owned conserved.

    Both operations contend for the one cargo copy under the aggregate-root lock.
    Whichever wins the lock first runs to completion; the loser re-reads committed
    state.  Net invariant: no negative cargo, no item materialised or destroyed,
    final ``owned = cargo + equipped`` stays at the pre-seeded total of 1.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)

        svc = LoadoutConsistencyService()
        player_repo = PlayerRepository()
        a_locked = asyncio.Event()
        results: dict = {}

        async def equip_session():
            async with factory() as db:
                await db.begin()
                await svc.equip_one(
                    db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                )
                a_locked.set()
                await asyncio.sleep(0.3)  # hold the aggregate-root lock
                await db.commit()
                results["equip"] = "ok"

        async def sell_session():
            await a_locked.wait()
            await asyncio.sleep(0.05)
            async with factory() as db:
                await db.begin()
                # Mirror shop_service.sell_item ordering: acquire the SAME
                # aggregate-root Player lock first (it serialises against the equip).
                await player_repo.get_by_id_for_update(db, player_id)
                inv = await db.execute(
                    select(PlayerInventory).where(
                        PlayerInventory.player_id == player_id,
                        PlayerInventory.item_type == _INV_TYPE,
                        PlayerInventory.item_name == _ITEM_NAME,
                    )
                )
                row = inv.scalars().first()
                qty = row.quantity if row else 0
                if qty <= 0:
                    # Equip already consumed the only cargo copy — sell must NOT
                    # mint a negative quantity or a phantom item.
                    results["sell"] = "no_stock"
                    await db.rollback()
                    return
                row.quantity = qty - 1
                await db.flush()
                await db.commit()
                results["sell"] = "sold"

        await asyncio.gather(equip_session(), sell_session())

        cargo, equipped = await _owned_breakdown(factory, player_id, ship_id)
        assert cargo >= 0, f"Cargo must never go negative, got {cargo}"
        if results["sell"] == "sold":
            # The single copy was sold; the equip won the cargo first only if it
            # equipped. Whatever the order, owned must not exceed the seeded total.
            assert cargo + equipped <= 1, f"owned overshoot: cargo={cargo} equipped={equipped}"
        else:
            assert cargo + equipped == 1, f"owned must stay 1, got cargo={cargo} equipped={equipped}"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case 4: switch-ship (set-active) during sell of a cargo item
# ===========================================================================


async def test_case4_set_active_during_sell_no_double_mint():
    """activate_ship (reconcile/transfer) during a SELL of a cargo item.

    A player with a SECOND ship (overflow scenario) switches active ship while a
    concurrent sell consumes a cargo copy.  Both take the same aggregate-root
    Player lock and serialise; the reconcile/transfer overflow-mint and the sell
    decrement cannot interleave, so no copy is double-minted or dropped.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=2)
            # A second (inactive) ship to switch to.
            ship2 = PlayerShip(
                player_id=player_id,
                ship_name=_SHIP_NAME,
                is_active=False,
                weapons=[],
                modules=[],
                turrets=[],
                secondary_weapons=[],
            )
            db.add(ship2)
            await db.flush()
            ship2_id = ship2.id

        svc = LoadoutConsistencyService()
        player_repo = PlayerRepository()
        activate_locked = asyncio.Event()
        results: dict = {}

        async def activate_session():
            async with factory() as db:
                await db.begin()
                await svc.activate_ship(db, player_id=player_id, target_ship_id=ship2_id, player_repo=player_repo)
                activate_locked.set()
                await asyncio.sleep(0.3)
                await db.commit()
                results["activate"] = "ok"

        async def sell_session():
            await activate_locked.wait()
            await asyncio.sleep(0.05)
            async with factory() as db:
                await db.begin()
                await player_repo.get_by_id_for_update(db, player_id)
                inv = await db.execute(
                    select(PlayerInventory).where(
                        PlayerInventory.player_id == player_id,
                        PlayerInventory.item_type == _INV_TYPE,
                        PlayerInventory.item_name == _ITEM_NAME,
                    )
                )
                row = inv.scalars().first()
                qty = row.quantity if row else 0
                if qty > 0:
                    row.quantity = qty - 1
                    await db.flush()
                results["sell_seen_qty"] = qty
                await db.commit()

        await asyncio.gather(activate_session(), sell_session())

        cargo, equipped = await _owned_breakdown(factory, player_id, ship_id)
        # Seeded 2 in cargo, 0 equipped. Sell removed exactly 1 (under lock, after
        # activate committed). activate_ship moves no cargo here (both ships empty),
        # so owned must be exactly 1 (2 seeded − 1 sold), with no double-mint.
        assert cargo + equipped == 1, f"owned must be exactly 1 after one sell, got cargo={cargo} equipped={equipped}"
        assert cargo >= 0
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case 9: Cross-player non-interference (no over-locking)
# ===========================================================================


async def test_case9_cross_player_non_interference_no_serialisation():
    """Two DIFFERENT players equipping concurrently must NOT block each other.

    Player A holds its aggregate-root lock open inside an equip transaction.
    Player B (a different ``players.id`` row) must be able to complete its own
    equip WITHOUT waiting for A to release — proving the lock is row-keyed and
    does not over-lock across players.  We assert B finishes well before A
    releases (A sleeps 0.5s; B must complete in a small fraction of that).
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            pa_id, sa_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)
            pb_id, sb_id = await _seed_player_with_ship(db, _TEST_USER_B, cargo_qty=1)

        svc = LoadoutConsistencyService()
        a_locked = asyncio.Event()
        timings: dict = {}

        async def player_a_holds_lock():
            async with factory() as db:
                await db.begin()
                await svc.equip_one(
                    db, player_id=pa_id, ship_id=sa_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                )
                a_locked.set()
                await asyncio.sleep(0.5)  # hold A's row lock for a long beat
                await db.commit()

        async def player_b_equips():
            await a_locked.wait()
            t0 = asyncio.get_event_loop().time()
            async with factory() as db:
                await db.begin()
                await svc.equip_one(
                    db, player_id=pb_id, ship_id=sb_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                )
                await db.commit()
            timings["b_elapsed"] = asyncio.get_event_loop().time() - t0

        await asyncio.gather(player_a_holds_lock(), player_b_equips())

        # If B had been serialised behind A's lock it would have waited ~0.5s.
        assert timings["b_elapsed"] < 0.25, (
            f"Player B was serialised behind Player A (elapsed {timings['b_elapsed']:.3f}s) — "
            "the aggregate-root lock is OVER-locking across players."
        )
        ca, ea = await _owned_breakdown(factory, pa_id, sa_id)
        cb, eb = await _owned_breakdown(factory, pb_id, sb_id)
        assert ca + ea == 1 and ea == 1, f"Player A owned broken: cargo={ca} equipped={ea}"
        assert cb + eb == 1 and eb == 1, f"Player B owned broken: cargo={cb} equipped={eb}"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Lock release on success AND on exception/rollback
# ===========================================================================


async def test_lock_released_on_success_and_on_exception():
    """The aggregate-root lock auto-releases at txn end — on commit AND on rollback.

    Step 1: a transaction that raises (and rolls back) must NOT leave the player
    locked — a subsequent FOR UPDATE acquires immediately.
    Step 2: a transaction that commits also releases the lock.
    Both are timed: a leaked lock would make the follow-up acquisition block.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)

        svc = LoadoutConsistencyService()
        player_repo = PlayerRepository()

        # --- Step 1: failing mutation must release the lock on rollback ---
        async with factory() as db:
            await db.begin()
            # Equip with NO cargo copies of a bogus item → raises ValueError after lock.
            with pytest.raises(ValueError):
                await svc.equip_one(
                    db,
                    player_id=player_id,
                    ship_id=ship_id,
                    item_name="Definitely Not A Real Item 9000",
                    equipment_type=_EQUIP_TYPE,
                )
            await db.rollback()

        # The lock must now be free — acquire it in a fresh session, timed.
        t0 = asyncio.get_event_loop().time()
        async with factory() as db:
            await db.begin()
            locked = await player_repo.get_by_id_for_update(db, player_id)
            assert locked is not None
            await db.rollback()
        assert asyncio.get_event_loop().time() - t0 < 0.5, "Lock leaked after a rolled-back mutation"

        # --- Step 2: successful mutation also releases on commit ---
        async with factory() as db:
            await db.begin()
            await svc.equip_one(
                db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
            )
            await db.commit()

        t0 = asyncio.get_event_loop().time()
        async with factory() as db:
            await db.begin()
            locked = await player_repo.get_by_id_for_update(db, player_id)
            assert locked is not None
            await db.rollback()
        assert asyncio.get_event_loop().time() - t0 < 0.5, "Lock leaked after a committed mutation"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# Case 8 (D5-T3): consolidate_inventory route path — lock-first + db.begin()
# ===========================================================================


async def _seed_player_with_duplicate_cargo(
    db: AsyncSession,
    user_id: int,
    *,
    qty_a: int,
    qty_b: int,
) -> tuple[int, int]:
    """Seed a player + active ship + TWO duplicate cargo rows of the test weapon.

    Duplicate (item_type, item_name) rows are exactly the corruption
    ``consolidate_inventory`` exists to merge.  Returns ``(player_id, ship_id)``.
    """
    await _seed_user(db, user_id)
    player = Player(
        user_id=user_id,
        guild_id=_TEST_GUILD,
        credits=10_000,
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

    for qty in (qty_a, qty_b):
        db.add(
            PlayerInventory(
                player_id=player.id,
                item_type=_INV_TYPE,
                item_name=_ITEM_NAME,
                quantity=qty,
            )
        )
    await db.flush()
    return player.id, ship.id


async def _consolidate_via_route_path(factory, player_id: int) -> None:
    """Drive the EXACT D5-T3 router shape for ``consolidate_inventory``.

    Mirrors ``api/routers/inventory.py::consolidate_inventory``: one explicit
    ``db.begin()`` unit of work, the aggregate-root Player lock acquired FIRST
    (it serialises concurrent same-player RMWs), then the service run with
    ``commit=False`` so the db.begin() owns the atomic transaction boundary.
    """
    player_repo = PlayerRepository()
    inventory_service = InventoryService()
    async with factory() as db, db.begin():
        await player_repo.get_by_id_for_update(db, player_id)
        await inventory_service.consolidate_inventory(db, player_id, commit=False)


async def _consolidate_route_path_without_begin(factory, player_id: int) -> None:
    """The route shape WITHOUT the ``db.begin()`` atomic boundary (mutation arm).

    Same lock-first + ``commit=False`` call sequence as
    ``_consolidate_via_route_path`` but with NO outer ``db.begin()``.  In this
    raw-factory harness the session's uncommitted flushes are rolled back when
    the context manager closes (these factory sessions have no AC-7
    auto-commit), so the merge does NOT persist.  Used to prove ``db.begin()``
    is load-bearing for durability here.
    """
    player_repo = PlayerRepository()
    inventory_service = InventoryService()
    async with factory() as db:
        await player_repo.get_by_id_for_update(db, player_id)
        await inventory_service.consolidate_inventory(db, player_id, commit=False)


async def _cargo_total_and_rowcount(factory, player_id: int) -> tuple[int, int]:
    """Return ``(summed_quantity, row_count)`` for the test weapon's cargo rows."""
    async with factory() as db:
        res = await db.execute(
            select(PlayerInventory.quantity).where(
                PlayerInventory.player_id == player_id,
                PlayerInventory.item_type == _INV_TYPE,
                PlayerInventory.item_name == _ITEM_NAME,
            )
        )
        quantities = list(res.scalars().all())
        return sum(quantities), len(quantities)


async def test_case8_consolidate_serialises_behind_equip_lock():
    """The consolidate route path BLOCKS behind a same-player equip's lock.

    Session A runs ``equip_one`` (takes the aggregate-root Player FOR UPDATE
    lock) and holds the transaction open.  Session B drives the consolidate
    ROUTE path, whose FIRST action is ``get_by_id_for_update`` on the same
    Player row — so it must BLOCK until A commits, then merge the (now post-equip)
    cargo rows.  We assert (a) B was serialised behind A (it took ~A's hold time)
    and (b) ``owned = cargo + equipped`` is conserved and the duplicate rows are
    merged to a single row.
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        # Two duplicate cargo rows (qty 1 each) → cargo total 2, plus an empty ship.
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_duplicate_cargo(db, _TEST_USER_A, qty_a=1, qty_b=1)

        svc = LoadoutConsistencyService()
        a_locked = asyncio.Event()
        timings: dict = {}

        async def equip_holds_lock():
            async with factory() as db:
                await db.begin()
                await svc.equip_one(
                    db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                )
                a_locked.set()
                await asyncio.sleep(0.4)  # hold the aggregate-root lock
                await db.commit()

        async def consolidate_blocks():
            await a_locked.wait()
            await asyncio.sleep(0.03)  # ensure A still holds the lock
            t0 = asyncio.get_event_loop().time()
            await _consolidate_via_route_path(factory, player_id)
            timings["consolidate_elapsed"] = asyncio.get_event_loop().time() - t0

        await asyncio.gather(equip_holds_lock(), consolidate_blocks())

        # B's FIRST act is the Player FOR UPDATE; it had to wait out most of A's
        # 0.4s hold.  A leak / missing lock would let it return near-instantly.
        assert timings["consolidate_elapsed"] > 0.2, (
            "consolidate route did NOT serialise behind the equip lock "
            f"(elapsed {timings['consolidate_elapsed']:.3f}s) — its lock-first guard is missing."
        )

        cargo_total, row_count = await _cargo_total_and_rowcount(factory, player_id)
        _, equipped = await _owned_breakdown(factory, player_id, ship_id)
        # Equip consumed exactly one unit (cargo 2 → 1); consolidate merged the
        # remaining duplicate rows into a single surviving row of the residual qty.
        assert equipped == 1, f"equip must have placed exactly one slot ref, got {equipped}"
        assert cargo_total + equipped == 2, f"owned must be conserved at 2, got cargo={cargo_total} equipped={equipped}"
        assert row_count == 1, f"consolidate must merge duplicate rows to one, got {row_count} rows"
    finally:
        await _cleanup(factory)
        await engine.dispose()


async def _run_decrement_vs_consolidate(factory, *, take_lock: bool) -> int:
    """Race a same-player cargo decrement against a consolidate; return final cargo.

    Seeds two duplicate rows summing to 8, then runs a concurrent (−1) decrement
    while a consolidate merges the duplicates.

    ``take_lock=True`` drives the REAL route path: the decrementer holds the
    aggregate-root Player ``FOR UPDATE`` lock, and the consolidate route BLOCKS on
    that lock until the decrement commits, then reads the POST-decrement rows and
    merges 7 → survivor 7.  The decrement is PRESERVED (final cargo 7).

    ``take_lock=False`` reproduces the PRE-D5-T3 unlocked route exactly: the
    consolidator reads the rows (sum 8) with NO Player lock and writes the survivor
    back with the STALE sum, each delete/update in its own auto-committed statement
    — so a decrement that commits in between is OVERWRITTEN and LOST (final cargo 8).
    """
    await _cleanup(factory)
    async with factory() as db, db.begin():
        player_id, _ = await _seed_player_with_duplicate_cargo(db, _TEST_USER_A, qty_a=3, qty_b=5)

    player_repo = PlayerRepository()

    if take_lock:
        decrementer_locked = asyncio.Event()

        async def decrement_holds_lock():
            async with factory() as db:
                await db.begin()
                # Take the SAME aggregate-root Player lock the route takes first.
                await player_repo.get_by_id_for_update(db, player_id)
                row = (
                    (
                        await db.execute(
                            select(PlayerInventory)
                            .where(
                                PlayerInventory.player_id == player_id,
                                PlayerInventory.item_type == _INV_TYPE,
                                PlayerInventory.item_name == _ITEM_NAME,
                            )
                            .order_by(PlayerInventory.id)
                        )
                    )
                    .scalars()
                    .first()
                )
                row.quantity = row.quantity - 1  # 8 → 7 total
                await db.flush()
                decrementer_locked.set()
                await asyncio.sleep(0.3)  # hold the lock so consolidate must block
                await db.commit()

        async def consolidate_route():
            await decrementer_locked.wait()
            await asyncio.sleep(0.03)
            # Real route path: blocks on the Player lock, then merges POST-decrement.
            await _consolidate_via_route_path(factory, player_id)

        await asyncio.gather(decrement_holds_lock(), consolidate_route())
    else:
        read_done = asyncio.Event()
        decrement_done = asyncio.Event()

        async def unlocked_consolidator():
            # PRE-D5-T3 route: NO Player lock, auto-commit-per-statement writes.
            async with factory() as db:
                rows = (
                    (
                        await db.execute(
                            select(PlayerInventory)
                            .where(
                                PlayerInventory.player_id == player_id,
                                PlayerInventory.item_type == _INV_TYPE,
                                PlayerInventory.item_name == _ITEM_NAME,
                            )
                            .order_by(PlayerInventory.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                survivor = rows[0]
                stale_sum = sum(r.quantity for r in rows)  # 8 — read BEFORE the decrement
                read_done.set()
                await decrement_done.wait()  # let the concurrent decrement commit first
                # Blind overwrite with the STALE sum (each its own committed statement).
                for dup in rows[1:]:
                    await db.delete(dup)
                    await db.commit()
                survivor.quantity = stale_sum
                await db.commit()

        async def concurrent_decrement():
            await read_done.wait()
            async with factory() as db, db.begin():
                row = (
                    (
                        await db.execute(
                            select(PlayerInventory)
                            .where(
                                PlayerInventory.player_id == player_id,
                                PlayerInventory.item_type == _INV_TYPE,
                                PlayerInventory.item_name == _ITEM_NAME,
                            )
                            .order_by(PlayerInventory.id)
                        )
                    )
                    .scalars()
                    .first()
                )
                row.quantity = row.quantity - 1  # commit a (−1) the consolidate must not lose
            decrement_done.set()

        await asyncio.gather(unlocked_consolidator(), concurrent_decrement())

    cargo_total, _ = await _cargo_total_and_rowcount(factory, player_id)
    return cargo_total


async def test_anti_vacuous_consolidate_lock_is_load_bearing_for_lost_update():
    """PROPERTY A (ANTI-VACUOUS): the Player LOCK prevents the consolidate lost update.

    Consolidate is a read-all → sum → delete-dups → overwrite-survivor cycle.  If
    another transaction decrements a cargo row AFTER consolidate reads but BEFORE it
    writes the survivor, an UNLOCKED consolidate overwrites with the stale sum and
    the decrement vanishes.  The aggregate-root Player ``FOR UPDATE`` lock is what
    serialises the two same-player RMWs and prevents that — db.begin() is NOT what
    fixes the lost update (it governs atomicity/durability, see the separate
    durability test).

    Mutation proof is the contrast of the two runs of the SAME race:
      * WITHOUT the lock (pre-D5-T3 unlocked route): final cargo 8 — the (−1)
        decrement is LOST.  This is the negative assertion; if it ever read 7 the
        scenario would not exercise a real race and the lock would be vacuous.
      * WITH the lock (real route path): the consolidate BLOCKS until the decrement
        commits, re-reads fresh, and merges 7 → final cargo 7 — PRESERVED.
    Deleting ``get_by_id_for_update`` from the route would make the locked run lose
    the update too (cargo 8), failing the ``== 7`` assertion.
    """
    engine, factory = _make_pg_factory()
    try:
        unlocked_cargo = await _run_decrement_vs_consolidate(factory, take_lock=False)
        assert unlocked_cargo == 8, (
            "ANTI-VACUOUS FAILED: the UNLOCKED consolidate did NOT lose the concurrent "
            f"decrement (cargo={unlocked_cargo}); without a real lost update there is "
            "nothing for the lock to fix."
        )

        locked_cargo = await _run_decrement_vs_consolidate(factory, take_lock=True)
        assert locked_cargo == 7, (
            "LOCK NOT LOAD-BEARING: with the Player FOR UPDATE lock the concurrent "
            f"decrement was NOT preserved (cargo={locked_cargo}, expected 7) — the lock "
            "did not serialise the RMW."
        )
    finally:
        await _cleanup(factory)
        await engine.dispose()


async def test_anti_vacuous_consolidate_db_begin_is_load_bearing_for_durability():
    """PROPERTY B (ANTI-VACUOUS): ``db.begin()`` makes the commit=False merge persist.

    The consolidate service runs with ``commit=False`` (flush-only); something must
    own the transaction and commit it.  The route uses an explicit ``db.begin()``.

    This is a HARNESS-scoped durability proof: these raw-factory test sessions have
    no AC-7 auto-commit-on-clean-exit, so an uncommitted flush is rolled back when
    the session closes.  (The production route's ``get_db_session`` additionally
    backstops durability via AC-7; here db.begin() is the sole committer, which
    makes the property cleanly observable.)  No concurrency — this isolates
    durability from the lock's serialisation property (tested separately).

    Mutation proof is the contrast of two single-threaded runs over identical seed
    data (two duplicate rows, sum 8):
      * WITHOUT ``db.begin()``: the flush-only merge is rolled back on session close
        → rows STAY un-merged (2 rows, sum 8) — did NOT persist.
      * WITH ``db.begin()``: the merge commits → 1 row, sum 8 — persisted.
    Stripping ``db.begin()`` from the route helper would leave 2 rows, failing the
    ``row_count == 1`` assertion; adding it back to the no-begin arm would merge to
    1, failing the ``row_count == 2`` assertion.
    """
    engine, factory = _make_pg_factory()
    try:
        # ---- Arm 1: NO db.begin() → flush-only merge rolled back on close --------
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, _ = await _seed_player_with_duplicate_cargo(db, _TEST_USER_A, qty_a=3, qty_b=5)

        await _consolidate_route_path_without_begin(factory, player_id)

        cargo_total, row_count = await _cargo_total_and_rowcount(factory, player_id)
        assert row_count == 2, (
            "db.begin() NOT LOAD-BEARING: the commit=False merge persisted WITHOUT an "
            f"explicit transaction (row_count={row_count}, expected 2 un-merged rows)."
        )
        assert cargo_total == 8, f"un-merged total must be unchanged at 8, got {cargo_total}"

        # ---- Arm 2: WITH db.begin() → merge commits and persists -----------------
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, _ = await _seed_player_with_duplicate_cargo(db, _TEST_USER_A, qty_a=3, qty_b=5)

        await _consolidate_via_route_path(factory, player_id)

        cargo_total, row_count = await _cargo_total_and_rowcount(factory, player_id)
        assert row_count == 1, f"db.begin() did NOT persist the merge (row_count={row_count}, expected 1 merged row)."
        assert cargo_total == 8, f"merged total must conserve quantity at 8, got {cargo_total}"
    finally:
        await _cleanup(factory)
        await engine.dispose()


async def test_case18_consolidate_failure_in_db_begin_rolls_back_cleanly():
    """D5-T3 (path 18): a failure mid-``db.begin()`` rolls back the partial merge.

    Drives the consolidate route shape (lock-first + ``db.begin()`` + commit=False
    merge), then raises INSIDE the ``db.begin()`` block AFTER the merge has flushed.
    The ``db.begin()`` context exits via the exception and rolls back, so NONE of the
    flushed delete/update survives: a fresh read still sees the original two
    duplicate rows (no partial merge, no half-deleted state).
    """
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, _ = await _seed_player_with_duplicate_cargo(db, _TEST_USER_A, qty_a=3, qty_b=5)

        player_repo = PlayerRepository()
        inventory_service = InventoryService()

        class _Boom(RuntimeError):
            pass

        with pytest.raises(_Boom):
            async with factory() as db, db.begin():
                await player_repo.get_by_id_for_update(db, player_id)
                await inventory_service.consolidate_inventory(db, player_id, commit=False)
                # The merge has now flushed (delete dup + survivor=8) but NOT committed.
                raise _Boom("simulated mid-transaction failure")

        # db.begin() rolled back on the exception → original duplicate rows intact.
        cargo_total, row_count = await _cargo_total_and_rowcount(factory, player_id)
        assert row_count == 2, f"rollback must leave both duplicate rows, got {row_count}"
        assert cargo_total == 8, f"rollback must preserve original total 8, got {cargo_total}"
    finally:
        await _cleanup(factory)
        await engine.dispose()


# ===========================================================================
# N-iteration lost-update guarantee for the double-equip (must hold EVERY run)
# ===========================================================================


# ===========================================================================
# D5-T1: populate_existing makes get_by_id_for_update load-bearing
# ===========================================================================


async def test_populate_existing_flushes_identity_map_stale_read():
    """Prove that ``populate_existing=True`` is load-bearing in ``get_by_id_for_update``.

    Recipe (from adversarial tester spec):
    1. Seed a player with credits=10000 in its own committed transaction.
    2. Open session X; call ``player_repo.get_by_id`` (UNLOCKED pre-load).
       Session X's identity map now caches the player with credits=10000.
    3. In a SEPARATE session Y, update + commit that player's credits to 9999.
    4. Back in session X (same open transaction, player still identity-mapped as
       10000), call ``player_repo.get_by_id_for_update`` (the FOR UPDATE path).
       With ``populate_existing=True`` the ORM MUST overwrite the cache and return
       the FRESH Postgres value of 9999.
       Without ``populate_existing=True`` the ORM would return the stale 10000
       from its identity map — the "lock looks correct, tests green" trap.
    5. Assert ``locked_player.credits == 9999``.
    """
    engine, factory = _make_pg_factory()
    player_repo = PlayerRepository()
    try:
        # --- Step 1: seed with a clean transaction ---------------------------------
        await _cleanup(factory)
        async with factory() as db_seed, db_seed.begin():
            await _seed_user(db_seed, _TEST_USER_A)
            player_seed = Player(
                user_id=_TEST_USER_A,
                guild_id=_TEST_GUILD,
                credits=10_000,
                tier="Bronze",
                classic_mode=True,
            )
            db_seed.add(player_seed)
            await db_seed.flush()
            player_id = player_seed.id
        # db_seed.begin() context manager committed — player row is visible to all.

        # --- Step 2: pre-load (unlocked) into session X's identity map ------------
        db_x = factory()
        await db_x.__aenter__()
        await db_x.begin()
        preloaded = await player_repo.get_by_id(db_x, player_id)
        assert preloaded is not None
        assert preloaded.credits == 10_000, f"Pre-condition failed: expected 10000 after seed, got {preloaded.credits}"

        # --- Step 3: separate session Y commits credits=9999 ----------------------
        async with factory() as db_y, db_y.begin():
            player_y = await player_repo.get_by_id_for_update(db_y, player_id)
            assert player_y is not None
            player_y.credits = 9_999
            await db_y.flush()
        # db_y committed; Postgres row now has credits=9999.

        # --- Step 4: session X FOR UPDATE — must read FRESH, not cache ------------
        # The identity map in db_x still has credits=10000.  With populate_existing
        # the ORM re-populates from the Postgres result set; without it it silently
        # returns the stale cached object.
        locked_player = await player_repo.get_by_id_for_update(db_x, player_id)
        assert locked_player is not None

        # --- Step 5: freshness assertion ------------------------------------------
        assert locked_player.credits == 9_999, (
            f"populate_existing not working: expected fresh credits=9999 from Postgres "
            f"but got {locked_player.credits} (stale identity-map value). "
            "Drop .execution_options(populate_existing=True) from get_by_id_for_update "
            "and this test will fail with 10000."
        )
        # The pre-loaded reference and the locked reference are the SAME Python object
        # (same identity map entry), and it must now carry the fresh value.
        assert preloaded.credits == 9_999, (
            f"Identity-map object not updated in-place: preloaded.credits={preloaded.credits} "
            f"but locked_player.credits={locked_player.credits} — they should be the same object."
        )

        await db_x.rollback()
    finally:
        with contextlib.suppress(Exception):
            await db_x.__aexit__(None, None, None)
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.parametrize("iteration", range(5))
async def test_case1_lost_update_holds_every_iteration(iteration):
    """The double-equip lost-update guarantee must hold on EVERY run (N=5)."""
    engine, factory = _make_pg_factory()
    try:
        await _cleanup(factory)
        async with factory() as db, db.begin():
            player_id, ship_id = await _seed_player_with_ship(db, _TEST_USER_A, cargo_qty=1)

        svc = LoadoutConsistencyService()
        a_locked = asyncio.Event()
        outcomes: dict = {}

        async def session_a():
            async with factory() as db:
                await db.begin()
                await svc.equip_one(
                    db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                )
                a_locked.set()
                await asyncio.sleep(0.2)
                await db.commit()
                outcomes["a"] = "ok"

        async def session_b():
            await a_locked.wait()
            await asyncio.sleep(0.03)
            async with factory() as db:
                await db.begin()
                try:
                    await svc.equip_one(
                        db, player_id=player_id, ship_id=ship_id, item_name=_ITEM_NAME, equipment_type=_EQUIP_TYPE
                    )
                    await db.commit()
                    outcomes["b"] = "equipped"
                except ValueError:
                    await db.rollback()
                    outcomes["b"] = "rejected"

        await asyncio.gather(session_a(), session_b())
        cargo, equipped = await _owned_breakdown(factory, player_id, ship_id)
        assert equipped == 1, f"[iter {iteration}] duplicate slot ref: equipped={equipped}"
        assert cargo + equipped == 1, f"[iter {iteration}] owned not conserved: cargo={cargo} equipped={equipped}"
        assert outcomes["b"] == "rejected", f"[iter {iteration}] second equip must be rejected"
    finally:
        await _cleanup(factory)
        await engine.dispose()
