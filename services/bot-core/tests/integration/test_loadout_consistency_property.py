"""
Property tests for Package G B.19 — total-ownership conservation.

Verifies the four hard invariants under random action sequences:

I1 — No item duplication across a single player's ships.
I2 — No materialisation from nothing — every JSON slot reference has an
     inventory provenance, and total ownership only changes on explicit
     buy/reward/sell/transfer events.
I3 — Atomicity — covered by router-level transaction wrapping (asserted
     elsewhere; this property test exercises the in-memory state machine).
I4 — Active ship within slot caps.

The action vocabulary is the 6-action set from the design spec:

  ("equip", item_name, ship_id)
  ("unequip", item_name, ship_id)
  ("buy_ship", new_ship_kind)
  ("sell_ship", ship_id)
  ("transfer_ship", ship_id, other_player)
  ("set_active", ship_id)

Each test runs 50 random seeds × 20 sequence lengths.  Failures produce the
action sequence that broke consistency, which itself becomes a regression
seed.

===========================================================================
TWO LEVELS OF COVERAGE IN THIS FILE
===========================================================================

LEVEL 1 — Real-service property SWEEP (``test_invariants_hold_*`` etc.)
    Drives the ACTUAL ``LoadoutConsistencyService`` through many random action
    sequences against a real SQLite-in-memory session — ``player_ships`` and
    ``player_inventories`` are genuine ORM writes and the invariants are re-read
    from those rows after every action.  Each of the six design actions maps to
    the real choke-point method it models (equip_one / unequip_one /
    transfer_loadout_to_new_ship / evacuate_ship_loadout_to_inventory /
    activate_ship), so a drift in the production overflow / reconcile / evacuate /
    merge logic surfaces as a property failure (with a re-playable seed).  Only
    the ARRAY-column static catalogs (item_repo / ship_repo) are mocked, keyed by
    name and returning the real STI discriminator / real slot caps.

LEVEL 2 — Real-service unit property tests (``test_real_service_*``)  [G.2]
    These tests invoke the actual ``LoadoutConsistencyService`` against a
    real SQLite-in-memory session (using the ``db_session`` / ``async_engine``
    fixtures from ``tests/integration/conftest.py``).  They use real ORM
    mutation paths so that a divergence between the simulator and the
    production service would surface as a test failure.

    Because SQLite does not support PostgreSQL ARRAY columns (used by
    ``Ship``, ``Item``, and module/weapon STI tables), these tests mock
    ``item_repo`` and ``ship_repo`` at the repo boundary.  Only
    ``player_ships`` and ``player_inventories`` rows are persisted to the
    real in-memory DB.
"""

import random
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mocks for the real-service tests (Level 2 tests below).
# The bblogger and sqlalchemy_utils mocks are installed by
# tests/integration/conftest.py, but we also need them here for
# LoadoutConsistencyService's module-level logger call.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

# ---------------------------------------------------------------------------
# Import LoadoutConsistencyService for Level 2 real-service tests.
#
# When pytest runs from /proj, Python finds /proj/services/ as a namespace
# package (no __init__.py) before services/bot-core/src/services/ (which has
# a proper __init__.py).  This causes ModuleNotFoundError for sub-modules.
#
# Fix: temporarily ensure src/ is at the front of sys.path and clear any
# cached ``services`` namespace package before importing, then restore.
# ---------------------------------------------------------------------------
import os as _os

_src_dir = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", "..", "src"))

# If the cached 'services' is a namespace (no __file__), remove it so the
# next import finds src/services/ instead.
if "services" in sys.modules:
    _svc_init = getattr(sys.modules["services"], "__file__", None) or ""
    # Only purge when `services` did NOT resolve to *our* src/ tree. The check must
    # use the computed src path — a hardcoded "bot-core/src" substring misfires in
    # deployed layouts where src lives at /app/src, making this purge run on every
    # collection and re-execute already-imported modules (duplicate class objects).
    if _os.path.normpath(_src_dir) not in _os.path.normpath(_svc_init):
        # Cached as namespace package — clear it and all sub-modules.
        for _k in list(sys.modules):
            if _k == "services" or _k.startswith("services."):
                del sys.modules[_k]

# Ensure src/ is at position 0.
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
elif sys.path[0] != _src_dir:
    sys.path.remove(_src_dir)
    sys.path.insert(0, _src_dir)

# Now the import will find src/services/ correctly.
# ---------------------------------------------------------------------------
# Level 1 — REAL-service property sweep (true-up).
#
# Previously this block ran the whole 150-case sweep against an in-memory
# ``PlayerWorld`` simulator that re-implemented the service contract; the real
# ``LoadoutConsistencyService`` was imported but NEVER called, so a drift in the
# production overflow / reconcile / evacuate / merge logic passed silently.  The
# sweep now drives the ACTUAL service against a real SQLite-in-memory session:
# ``player_ships`` and ``player_inventories`` rows are genuine ORM writes, and the
# invariants are re-derived by READING those rows back after every action.
#
# Only the static catalogs are mocked — ``item_repo`` (Item STI) and ``ship_repo``
# (Ship slot caps) live in PostgreSQL ARRAY-column tables SQLite cannot host.  The
# mocks are FAITHFUL: keyed by name, returning the real STI discriminator and the
# real per-kind slot caps (not bare MagicMock chains).
#
# Each of the six design actions maps to the real choke-point method it models:
#   equip         -> equip_one
#   unequip       -> unequip_one
#   buy_ship      -> transfer_loadout_to_new_ship (+ active flip)   [B.95 merge-overflow]
#   sell_ship     -> evacuate_ship_loadout_to_inventory (+ row delete)
#   transfer_ship -> evacuate_ship_loadout_to_inventory (+ row delete)
#   set_active    -> activate_ship (reconcile caps + transfer old active)
#
# Every action runs inside its OWN ``async with db.begin()`` unit of work, so a
# rejected action (real ``ValueError``) rolls the whole action back — invariant I3
# (atomicity) is exercised for real instead of asserted elsewhere.  After every
# action the invariants are checked straight from persisted rows:
#   I1/I2 — for each item name: Σ(slot references across ALL ships) + cargo qty
#           == the constant total issued (no duplication, no materialisation);
#           every cargo row also has an issued provenance.
#   I4    — every ship's per-kind slot count <= that ship's static cap.
# A divergence in the real service surfaces here as a property failure carrying
# the seed + action sequence that broke it (a re-playable regression seed).
# ---------------------------------------------------------------------------
from services.exceptions import InvalidItemTypeError
from services.loadout_consistency_service import (
    LoadoutConsistencyService as _LoadoutConsistencyService,
)
from sqlalchemy import select

# name -> slot kind (the STI discriminator + inventory type derive from the kind)
_ITEM_KIND: dict[str, str] = {
    "Pulse Laser": "weapons",
    "Burst Laser": "weapons",
    "Rail Gun": "weapons",
    "Shield Booster": "modules",
    "Cargo Bay": "modules",
    "Beam Turret": "turrets",
}

# slot kind -> Item.type STI discriminator (what item_repo returns)
_KIND_TO_STI: dict[str, str] = {
    "weapons": "PrimaryWeapon",
    "modules": "Module",  # generic module class — not in MODULE_EQUIP_LIMITS, so unlimited
    "turrets": "TurretWeapon",
}

# slot kind -> concrete inventory item_type (matches equipment_service._INVENTORY_TYPE_MAP)
_INV_TYPE: dict[str, str] = {
    "weapons": "primary_weapon",
    "modules": "module",
    "turrets": "turret_weapon",
}

# ship name -> static per-kind slot caps (what ship_repo returns)
_SHIP_CAPS: dict[str, dict[str, int]] = {
    "Betty": {"weapons": 1, "modules": 3, "turrets": 0, "secondary_weapons": 0},
    "Sidewinder": {"weapons": 2, "modules": 3, "turrets": 1, "secondary_weapons": 0},
    "Hera": {"weapons": 2, "modules": 4, "turrets": 1, "secondary_weapons": 0},
    "Terran": {"weapons": 2, "modules": 5, "turrets": 2, "secondary_weapons": 0},
}
_SHIP_NAMES = list(_SHIP_CAPS)
_SLOT_ATTRS = ("weapons", "modules", "turrets", "secondary_weapons")


def _catalog_service():
    """A real ``LoadoutConsistencyService`` with FAITHFUL catalog-keyed repo mocks.

    ``player_ship_repo`` / ``inventory_repo`` / ``player_repo`` are REAL (they hit
    the live SQLite session); only the ARRAY-column static catalogs are mocked, and
    those mocks return the real STI discriminator / real slot caps keyed by name.
    """
    from persist.repositories.inventory_repository import InventoryRepository
    from persist.repositories.player_repository import PlayerRepository
    from persist.repositories.player_ship_repository import PlayerShipRepository

    async def _item_any(_db, name):
        kind = _ITEM_KIND.get(name)
        return None if kind is None else SimpleNamespace(name=name, type=_KIND_TO_STI[kind])

    async def _item_by_name(_db, name, item_type=None):
        kind = _ITEM_KIND.get(name)
        return None if kind is None else SimpleNamespace(name=name, type=_KIND_TO_STI[kind])

    async def _ship_by_name(_db, ship_name):
        caps = _SHIP_CAPS.get(ship_name)
        if caps is None:
            return None
        return SimpleNamespace(
            name=ship_name,
            max_primaries=caps["weapons"],
            max_modules=caps["modules"],
            max_turrets=caps["turrets"],
            max_secondaries=caps["secondary_weapons"],
        )

    item_repo = AsyncMock()
    item_repo.get_by_name_any_type = AsyncMock(side_effect=_item_any)
    item_repo.get_by_name = AsyncMock(side_effect=_item_by_name)
    ship_repo = AsyncMock()
    ship_repo.get_by_name = AsyncMock(side_effect=_ship_by_name)

    return _LoadoutConsistencyService(
        player_ship_repo=PlayerShipRepository(),
        inventory_repo=InventoryRepository(),
        item_repo=item_repo,
        ship_repo=ship_repo,
        player_repo=PlayerRepository(),
    )


class _RealWorld:
    """Drive real ``LoadoutConsistencyService`` mutations; read invariants from the DB.

    ``expected`` is the ground-truth total-owned-per-item ledger: it changes ONLY
    on the explicit ``issue`` (starter/buy/reward) path — never on equip / unequip /
    ship buy / sell / transfer / activate, all of which merely RELOCATE ownership.
    The conservation invariant is therefore ``slot_refs + cargo == expected`` for
    every item name, at all times.
    """

    def __init__(self, db, svc, player_id: int) -> None:
        self.db = db
        self.svc = svc
        self.player_id = player_id
        self.expected: dict[str, int] = {}

    # ---- seeding (ledger-changing) ----

    async def issue(self, name: str, qty: int = 1) -> None:
        # Upsert via the REAL inventory repo so a repeat issue of the same item
        # increments the existing row (honouring uq_player_inventories_player_item)
        # instead of colliding on the unique constraint.
        async with self.db.begin():
            await self.svc.inventory_repo.add_item(
                self.db, self.player_id, _INV_TYPE[_ITEM_KIND[name]], name, qty, commit=False
            )
        self.expected[name] = self.expected.get(name, 0) + qty

    async def add_ship(self, ship_name: str, *, active: bool) -> int:
        from persist.models.player_ship import PlayerShip

        async with self.db.begin():
            ship = PlayerShip(
                player_id=self.player_id,
                ship_name=ship_name,
                is_active=active,
                weapons=[],
                modules=[],
                turrets=[],
                secondary_weapons=[],
            )
            self.db.add(ship)
            await self.db.flush()
            return ship.id

    # ---- snapshot for action generation + invariant checks ----

    async def _snapshot(self):
        from persist.models.player_inventory import PlayerInventory
        from persist.models.player_ship import PlayerShip

        async with self.db.begin():
            ships = (
                (await self.db.execute(select(PlayerShip).where(PlayerShip.player_id == self.player_id)))
                .scalars()
                .all()
            )
            invs = (
                (await self.db.execute(select(PlayerInventory).where(PlayerInventory.player_id == self.player_id)))
                .scalars()
                .all()
            )
            ship_state = [
                {
                    "id": s.id,
                    "name": s.ship_name,
                    "active": bool(s.is_active),
                    "slots": {a: list(getattr(s, a) or []) for a in _SLOT_ATTRS},
                }
                for s in ships
            ]
            cargo: dict[str, int] = {}
            for row in invs:
                cargo[row.item_name] = cargo.get(row.item_name, 0) + row.quantity
        return ship_state, cargo

    # ---- action generation from real state ----

    async def gen_action(self, rng) -> tuple:
        ships, cargo = await self._snapshot()
        active_id = next((s["id"] for s in ships if s["active"]), None)
        choice = rng.choices(
            ["equip", "unequip", "buy_ship", "sell_ship", "transfer_ship", "set_active"],
            weights=[3, 3, 1, 1, 1, 2],
        )[0]
        if choice == "equip":
            # Only equip a name NOT already equipped on ANY ship.  Equipping the
            # same NAME onto a second ship creates an I1-violating state that the
            # real service's evacuate anti-dup guard "repairs" destructively (it
            # cannot tell a legit second copy from a phantom dup) — a distinct
            # item-loss concern covered by ``test_evacuate_destroys_legit_...``
            # below, kept out of the conservation sweep so I1/I2 stays well-defined.
            equipped_names = {n for s in ships for a in _SLOT_ATTRS for n in s["slots"][a]}
            owned = [n for n, q in cargo.items() if q > 0 and n not in equipped_names]
            if not owned or not ships:
                return ("noop",)
            name = rng.choice(owned)
            sid = rng.choice([s["id"] for s in ships])
            return ("equip", name, _ITEM_KIND[name], sid)
        if choice == "unequip":
            equipped = [(n, a, s["id"]) for s in ships for a in _SLOT_ATTRS for n in s["slots"][a]]
            if not equipped:
                return ("noop",)
            return ("unequip", *rng.choice(equipped))
        if choice == "buy_ship":
            return ("buy_ship", rng.choice(_SHIP_NAMES))
        if choice in ("sell_ship", "transfer_ship"):
            candidates = [s["id"] for s in ships if s["id"] != active_id]
            if not candidates:
                return ("noop",)
            return (choice, rng.choice(candidates))
        if choice == "set_active":
            if not ships:
                return ("noop",)
            return ("set_active", rng.choice([s["id"] for s in ships]))
        return ("noop",)

    # ---- action application via the REAL service (each its own txn) ----

    async def apply(self, action: tuple) -> None:
        kind = action[0]
        try:
            if kind == "equip":
                _, name, k, sid = action
                async with self.db.begin():
                    await self.svc.equip_one(
                        self.db, player_id=self.player_id, ship_id=sid, item_name=name, equipment_type=k
                    )
            elif kind == "unequip":
                _, name, k, sid = action
                async with self.db.begin():
                    await self.svc.unequip_one(
                        self.db, player_id=self.player_id, ship_id=sid, item_name=name, equipment_type=k
                    )
            elif kind == "buy_ship":
                await self._buy_ship(action[1])
            elif kind in ("sell_ship", "transfer_ship"):
                await self._evacuate_and_remove(action[1])
            elif kind == "set_active":
                async with self.db.begin():
                    await self.svc.activate_ship(
                        self.db, player_id=self.player_id, target_ship_id=action[1], player_repo=self.svc.player_repo
                    )
            # "noop" -> nothing
        except (ValueError, InvalidItemTypeError):
            # A rejected action's ``db.begin()`` already rolled back — mirrors the
            # design contract that an invalid op is a no-op (the old simulator
            # returned False).  Conservation must therefore still hold afterward.
            pass

    async def _buy_ship(self, ship_name: str) -> None:
        from persist.models.player_ship import PlayerShip

        async with self.db.begin():
            active = (
                (
                    await self.db.execute(
                        select(PlayerShip).where(PlayerShip.player_id == self.player_id, PlayerShip.is_active.is_(True))
                    )
                )
                .scalars()
                .first()
            )
            new_ship = PlayerShip(
                player_id=self.player_id,
                ship_name=ship_name,
                is_active=False,
                weapons=[],
                modules=[],
                turrets=[],
                secondary_weapons=[],
            )
            self.db.add(new_ship)
            await self.db.flush()
            await self.svc.transfer_loadout_to_new_ship(
                self.db,
                player_id=self.player_id,
                src_ship=active,
                dst_ship=new_ship,
                slot_limits=_SHIP_CAPS[ship_name],
            )
            if active is not None:
                active.is_active = False
            new_ship.is_active = True

    async def _evacuate_and_remove(self, ship_id: int) -> None:
        from persist.models.player_ship import PlayerShip

        async with self.db.begin():
            ship = await self.db.get(PlayerShip, ship_id)
            if ship is None or ship.is_active:
                return  # cannot sell/transfer the active ship (design contract)
            await self.svc.evacuate_ship_loadout_to_inventory(self.db, ship=ship)
            await self.db.delete(ship)

    # ---- invariants (read straight from persisted rows) ----

    async def assert_invariants(self, ctx: str = "") -> None:
        ships, cargo = await self._snapshot()
        # I1/I2 — conservation: relocation never changes total ownership.
        for name, total in self.expected.items():
            slot_refs = sum(s["slots"][a].count(name) for s in ships for a in _SLOT_ATTRS)
            owned = slot_refs + cargo.get(name, 0)
            assert owned == total, (
                f"I1/I2 [{ctx}] name={name!r}: slot_refs={slot_refs} + cargo={cargo.get(name, 0)} "
                f"= {owned} != issued {total}"
            )
        # I2 — no materialisation: every cargo row has an issued provenance.
        for name in cargo:
            assert name in self.expected, f"I2 [{ctx}] phantom cargo row {name!r} with no provenance"
        # I4 — every ship within its static slot caps.
        for s in ships:
            caps = _SHIP_CAPS[s["name"]]
            for a in _SLOT_ATTRS:
                assert len(s["slots"][a]) <= caps[a], (
                    f"I4 [{ctx}] ship {s['name']!r} {a}={len(s['slots'][a])} > cap {caps[a]}"
                )


async def _seed_real_world(db, seed: int) -> _RealWorld:
    """Build a starting REAL world: a Player with a Betty active ship (Pulse Laser +
    Shield Booster equipped), Micro-style extras in cargo — mirrors the starter
    loadout contract, but every row is a genuine ORM write."""
    from persist.models.player import Player
    from persist.models.user import User

    svc = _catalog_service()
    async with db.begin():
        user = User(id=900_000 + seed, discord_username=f"prop-{seed}")
        db.add(user)
        await db.flush()
        player = Player(user_id=user.id, guild_id=1, credits=0, tier="Bronze")
        db.add(player)
        await db.flush()
        pid = player.id

    world = _RealWorld(db, svc, pid)
    # Ledger-tracked starter issue.
    await world.issue("Pulse Laser", 1)
    await world.issue("Burst Laser", 1)
    await world.issue("Shield Booster", 1)
    await world.issue("Cargo Bay", 1)
    await world.issue("Beam Turret", 1)
    rng = random.Random(seed)
    for _ in range(rng.randint(0, 3)):
        await world.issue(rng.choice(list(_ITEM_KIND)), 1)

    sid = await world.add_ship("Betty", active=True)
    async with db.begin():
        await svc.player_repo.update_active_ship(db, pid, sid, commit=False)
    # Equip the first weapon + a module via the REAL service.
    await world.apply(("equip", "Pulse Laser", "weapons", sid))
    await world.apply(("equip", "Shield Booster", "modules", sid))
    return world


# ---------------------------------------------------------------------------
# Property tests (Level 1) — now driving the REAL service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", list(range(50)))
@pytest.mark.parametrize("length", [5, 10, 20])
async def test_invariants_hold_under_random_action_sequences(db_session, seed: int, length: int) -> None:
    """50 seeds x 3 lengths = 150 generated cases, each driven through the REAL
    ``LoadoutConsistencyService`` against live SQLite rows.

    Build a starter world, apply ``length`` random actions (each a real service
    call in its own transaction), and re-verify the conservation + cap invariants
    from persisted rows after every step.  Failure prints the seed + sequence so
    the case can be re-played as a regression seed.
    """
    rng = random.Random(seed * 1000 + length)
    world = await _seed_real_world(db_session, seed)
    sequence: list[tuple] = []
    await world.assert_invariants("initial")
    for _ in range(length):
        action = await world.gen_action(rng)
        sequence.append(action)
        await world.apply(action)
        await world.assert_invariants(f"seed={seed} length={length} sequence={sequence}")


async def test_starter_world_satisfies_invariants(db_session) -> None:
    """Sanity: the seeded REAL starter world is invariant-correct out of the box."""
    world = await _seed_real_world(db_session, seed=0)
    await world.assert_invariants("starter")


async def test_phantom_dup_is_repaired_by_repair_player_logic(db_session) -> None:
    """The empirical B.19 corrupt state (3 real ships all referencing one module
    with NO cargo provenance) is fixed by the REAL ``repair_player``: the reference
    is kept on the active ship, dropped from the others, and NO inventory row is
    minted (I2).  Previously this test re-implemented the dedup loop inline and
    asserted its own result; it now drives the production method end to end.
    """
    from persist.models.player import Player
    from persist.models.player_inventory import PlayerInventory
    from persist.models.player_ship import PlayerShip
    from persist.models.user import User

    svc = _catalog_service()
    phantom = "Phantom Module"

    async with db_session.begin():
        user = User(id=910_001, discord_username="phantom")
        db_session.add(user)
        await db_session.flush()
        player = Player(user_id=user.id, guild_id=1, credits=0, tier="Bronze")
        db_session.add(player)
        await db_session.flush()
        pid = player.id
        for name, active in (("Betty", True), ("Hera", False), ("Terran", False)):
            db_session.add(
                PlayerShip(
                    player_id=pid,
                    ship_name=name,
                    is_active=active,
                    weapons=[],
                    modules=[phantom],
                    turrets=[],
                    secondary_weapons=[],
                )
            )

    async with db_session.begin():
        result = await svc.repair_player(db_session, pid)

    assert result["duplicates_removed"] == 2  # dropped from the two non-active ships
    assert result["ships_modified"] == 2

    async with db_session.begin():
        ships = (await db_session.execute(select(PlayerShip).where(PlayerShip.player_id == pid))).scalars().all()
        invs = (
            (await db_session.execute(select(PlayerInventory).where(PlayerInventory.player_id == pid))).scalars().all()
        )
    # Exactly one surviving slot reference — on the active ship.
    total_refs = sum(s.modules.count(phantom) for s in ships)
    assert total_refs == 1
    assert next(s for s in ships if s.is_active).modules == [phantom]
    # repair_player must NOT materialise inventory (I2).
    assert invs == []


@pytest.mark.xfail(
    reason=(
        "Latent item-loss bug (logged in FOLLOWUPS.md, R-bc-integration): a player who "
        "legitimately owns 2 copies of one item NAME and equips one on each of two ships "
        "reaches a state equip_one never prevents; evacuating/selling one ship then fires "
        "evacuate's anti-dup guard, which cannot tell a legit second copy from a phantom "
        "dup and DESTROYS the other ship's copy (mints only one back). Total ownership "
        "drops from 2 to 1. The DESIRED behaviour asserted here (both copies conserved) "
        "fails against the shipped service."
    ),
    strict=True,
)
async def test_evacuate_destroys_legit_second_copy_of_same_name(db_session) -> None:
    """Characterises the anti-dup-vs-legit-duplicate item-loss edge (see xfail reason).

    Owns 2 'Pulse Laser' (both equipped, one per ship), then evacuates the
    non-active ship.  Conservation (owned == 2) SHOULD hold but does not — the
    guard destroys the winning ship's copy.  Marked strict xfail so the day the
    src is fixed (equip prevents the state, or the guard keys on provenance) this
    turns green and flags the fix.
    """
    from persist.models.player import Player
    from persist.models.user import User

    svc = _catalog_service()
    async with db_session.begin():
        user = User(id=920_001, discord_username="dup-legit")
        db_session.add(user)
        await db_session.flush()
        player = Player(user_id=user.id, guild_id=1, credits=0, tier="Bronze")
        db_session.add(player)
        await db_session.flush()
        pid = player.id
    world = _RealWorld(db_session, svc, pid)
    world.player_id = pid
    world.expected["Pulse Laser"] = 2  # two legitimately-owned copies

    # Two Betty ships; issue 2 Pulse Lasers to cargo, equip one on each ship.
    async with db_session.begin():
        await svc.inventory_repo.add_item(db_session, pid, "primary_weapon", "Pulse Laser", 2, commit=False)
    active = await world.add_ship("Betty", active=True)
    other = await world.add_ship("Sidewinder", active=False)
    await world.apply(("equip", "Pulse Laser", "weapons", active))
    await world.apply(("equip", "Pulse Laser", "weapons", other))

    # Sanity: both copies equipped, cargo empty (arrange succeeded).
    ships, cargo = await world._snapshot()
    assert sum(s["slots"]["weapons"].count("Pulse Laser") for s in ships) == 2
    assert cargo.get("Pulse Laser", 0) == 0

    # Evacuate the NON-active ship — the destructive anti-dup guard fires here.
    await world._evacuate_and_remove(other)

    # DESIRED (xfail): both copies survive — one back in cargo, one still equipped.
    await world.assert_invariants("legit-dup evacuate")


# ---------------------------------------------------------------------------
# Level 2: Real-service property tests [G.2]
#
# These tests invoke the actual LoadoutConsistencyService against a real
# SQLite-in-memory session (from the ``db_session`` fixture in conftest.py).
# They use real ORM mutations on ``player_ships`` and ``player_inventories``
# rows.  item_repo and ship_repo are mocked at the repo boundary (ARRAY
# columns not supported by SQLite).
# ---------------------------------------------------------------------------


def _make_mock_item(name: str, sti_type: str = "PrimaryWeapon") -> SimpleNamespace:
    return SimpleNamespace(name=name, type=sti_type)


@pytest.fixture
def real_svc():
    """Real LoadoutConsistencyService with item_repo and ship_repo mocked.

    player_ship_repo and inventory_repo are NOT mocked — they use real
    repository instances that delegate to the SQLite-in-memory session.
    """
    from persist.repositories.inventory_repository import InventoryRepository
    from persist.repositories.player_ship_repository import PlayerShipRepository

    # Use the already-imported class from module level (avoids namespace clash).
    LoadoutConsistencyService = _LoadoutConsistencyService

    mock_item_repo = AsyncMock()
    mock_item_repo.get_by_name_any_type = AsyncMock(return_value=_make_mock_item("Pulse Laser", "PrimaryWeapon"))
    mock_item_repo.get_by_name = AsyncMock(return_value=_make_mock_item("Pulse Laser", "PrimaryWeapon"))

    mock_ship_repo = AsyncMock()
    mock_ship_repo.get_by_name = AsyncMock(
        return_value=SimpleNamespace(
            name="Betty",
            max_primaries=2,
            max_modules=3,
            max_turrets=1,
            max_secondaries=0,
        )
    )

    return LoadoutConsistencyService(
        player_ship_repo=PlayerShipRepository(),
        inventory_repo=InventoryRepository(),
        item_repo=mock_item_repo,
        ship_repo=mock_ship_repo,
    )


@pytest.mark.asyncio
async def test_real_service_evacuate_clears_slots_and_mints_inventory(db_session, real_svc):
    """G.2 Level 2: Real service evacuates ship and mints inventory rows.

    Inserts a real PlayerShip row with a weapon equipped, then calls
    evacuate_ship_loadout_to_inventory.  Asserts that:
    - The ship's weapons slot is empty in the DB.
    - An inventory row was created for the evacuated weapon.
    """
    from persist.models.player_ship import PlayerShip

    # Insert a real ship row via ORM.
    ship = PlayerShip()
    ship.player_id = 42
    ship.ship_name = "Betty"
    ship.is_active = True
    ship.weapons = ["Pulse Laser"]
    ship.modules = []
    ship.turrets = []
    ship.secondary_weapons = []
    db_session.add(ship)
    await db_session.flush()

    # Real service call — no mocks on the repo layer.
    result = await real_svc.evacuate_ship_loadout_to_inventory(db_session, ship=ship)

    # Contract: items_returned contains the evacuated weapon.
    assert "Pulse Laser" in result["items_returned"]
    # Contract: ship's slot is cleared.
    assert ship.weapons == []

    # Contract: an inventory row was inserted (check via the inventory repo).
    from persist.repositories.inventory_repository import InventoryRepository

    inv_repo = InventoryRepository()
    inv_item = await inv_repo.get_player_item(db_session, 42, "primary_weapon", "Pulse Laser")
    assert inv_item is not None, "Inventory row must be created for evacuated weapon"
    assert inv_item.quantity == 1


@pytest.mark.asyncio
async def test_real_service_repair_player_deduplicates_across_ships(db_session, real_svc):
    """G.2 Level 2: Real service repair_player deduplicates corrupt slot state.

    Inserts two real PlayerShip rows both referencing "Pulse Laser" in their
    weapons slot (the empirical B.19 duplicate state).  Calls repair_player
    and asserts:
    - Only one ship retains the reference.
    - The other ship's weapons slot is cleared.
    - No inventory rows are minted (repair must not materialise from nothing).
    """
    from persist.models.player_ship import PlayerShip
    from persist.repositories.inventory_repository import InventoryRepository

    player_id = 101

    active = PlayerShip()
    active.player_id = player_id
    active.ship_name = "Betty"
    active.is_active = True
    active.weapons = ["Pulse Laser"]
    active.modules = []
    active.turrets = []
    active.secondary_weapons = []
    db_session.add(active)

    loser = PlayerShip()
    loser.player_id = player_id
    loser.ship_name = "Hera"
    loser.is_active = False
    loser.weapons = ["Pulse Laser"]
    loser.modules = []
    loser.turrets = []
    loser.secondary_weapons = []
    db_session.add(loser)
    await db_session.flush()

    result = await real_svc.repair_player(db_session, player_id)

    assert result["duplicates_removed"] == 1
    assert result["ships_modified"] == 1
    # Active ship wins.
    assert active.weapons == ["Pulse Laser"]
    # Loser's slot is cleared.
    assert loser.weapons == []

    # No inventory rows must have been minted by repair_player.
    inv_repo = InventoryRepository()
    all_inv = await inv_repo.get_player_items(db_session, player_id)
    assert len(all_inv) == 0, "repair_player must NOT materialise inventory rows (I2 invariant)"


@pytest.mark.asyncio
async def test_real_service_evacuate_anti_duplication_guard_single_mint(db_session, real_svc):
    """G.2 Level 2: Real service anti-duplication guard mints exactly once.

    Pre-seeds two ships with the same phantom weapon.  Calls evacuate on
    ship_a.  Asserts exactly one inventory row (quantity=1) is created — the
    guard removes the duplicate from ship_b before minting, so the total
    inventory count stays at 1 (not 2).

    This is the G.1 exploit closure verified against a real DB session.
    """
    from persist.models.player_ship import PlayerShip
    from persist.repositories.inventory_repository import InventoryRepository

    player_id = 202
    phantom = "M6 A4"

    real_svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_make_mock_item(phantom, "PrimaryWeapon"))

    ship_a = PlayerShip()
    ship_a.player_id = player_id
    ship_a.ship_name = "Betty"
    ship_a.is_active = False
    ship_a.weapons = [phantom]
    ship_a.modules = []
    ship_a.turrets = []
    ship_a.secondary_weapons = []
    db_session.add(ship_a)

    ship_b = PlayerShip()
    ship_b.player_id = player_id
    ship_b.ship_name = "Hera"
    ship_b.is_active = True
    ship_b.weapons = [phantom]
    ship_b.modules = []
    ship_b.turrets = []
    ship_b.secondary_weapons = []
    db_session.add(ship_b)
    await db_session.flush()

    result = await real_svc.evacuate_ship_loadout_to_inventory(db_session, ship=ship_a)

    # The evacuated item is listed in items_returned.
    assert phantom in result["items_returned"]
    # Counter reflects the duplicate removal.
    assert result["duplicates_dropped"] == 1
    # ship_b's weapons slot is cleared by the anti-duplication guard.
    assert ship_b.weapons == []
    # ship_a's weapons slot is cleared after evacuation.
    assert ship_a.weapons == []

    # Exactly one inventory row — quantity 1 (not 2).
    inv_repo = InventoryRepository()
    inv_item = await inv_repo.get_player_item(db_session, player_id, "primary_weapon", phantom)
    assert inv_item is not None, "Inventory row must be created for the evacuated phantom item"
    assert inv_item.quantity == 1, f"Expected quantity=1, got {inv_item.quantity} (phantom-dup exploit if 2)"
