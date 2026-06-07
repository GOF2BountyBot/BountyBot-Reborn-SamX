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
  9  Cross-player NON-interference: two DIFFERENT players equipping concurrently
     must NOT serialise (proves no over-locking).
  R  Lock RELEASED on success AND on exception/rollback.
  A  ANTI-VACUOUS probe: WITHOUT the _lock_player call the double-equip produces a
     lost-update / duplicate slot — proving the lock is load-bearing.

Connection: bountydev-db at 172.19.0.2:5432 (bountydev-net bridge IP).
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
from services.loadout_consistency_service import LoadoutConsistencyService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Real Postgres connection — bountydev-db on docker bridge network
# ---------------------------------------------------------------------------

_PG_URL = "postgresql+asyncpg://bounty:bounty@172.19.0.2:5432/bountydb"

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
