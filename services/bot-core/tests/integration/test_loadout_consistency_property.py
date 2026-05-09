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

LEVEL 1 — Simulator-based property tests (``test_invariants_hold_*`` etc.)
    Use the deterministic ``PlayerWorld`` in-memory simulator which mirrors
    the ``LoadoutConsistencyService`` contract.  These tests verify the
    CONTRACT (the state-machine semantics) under many action sequences.
    They do NOT exercise the production service code — if the real service
    drifts from the contract, the simulator tests still pass.  The existing
    25 service-level unit tests and router integration tests serve as the
    regression net for the production code.

LEVEL 2 — Real-service property tests (``test_real_service_*``)  [G.2]
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
    if "bot-core/src" not in _svc_init:
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
from services.loadout_consistency_service import (
    LoadoutConsistencyService as _LoadoutConsistencyService,
)

# ---------------------------------------------------------------------------
# Deterministic simulator that mirrors the LoadoutConsistencyService contract.
#
# This is the "ground truth" model — it implements the SAME contract that the
# production service enforces.  If the production service drifts, the property
# test would be the first place to surface it (because the property test
# directly compares ledger conservation against the model's own behaviour).
# ---------------------------------------------------------------------------


class PlayerWorld:
    """In-memory world for a single player.

    Tracks:
      - ships: list of dicts {id, slot caps, kind→list[name]}
      - inventory: dict {name → quantity}
      - active_ship_id
      - ledger: ground-truth count per item name — incremented only by
        buy/issue, decremented only by sell/transfer-out.
    """

    def __init__(self, player_id: int) -> None:
        self.player_id = player_id
        self.ships: list[dict] = []
        self.inventory: dict[str, int] = {}
        self.active_ship_id: int | None = None
        self.ledger: dict[str, int] = {}
        self._next_ship_id = 1

    # ---- introspection ----

    def total_owned(self, name: str) -> int:
        """Sum of slot references plus inventory quantity."""
        slot_count = 0
        for ship in self.ships:
            for kind in ("weapons", "modules", "turrets", "secondary_weapons"):
                slot_count += sum(1 for x in ship[kind] if x == name)
        return slot_count + self.inventory.get(name, 0)

    # ---- mutators (mirror LoadoutConsistencyService) ----

    def issue_item(self, name: str, kind: str, qty: int = 1) -> None:
        """Add to inventory + ledger (an explicit "buy" / "starter" / "reward")."""
        self.inventory[name] = self.inventory.get(name, 0) + qty
        self.ledger[name] = self.ledger.get(name, 0) + qty

    def add_ship(self, kind: str, caps: dict[str, int]) -> int:
        """Create a new ship with empty slots; returns its id."""
        sid = self._next_ship_id
        self._next_ship_id += 1
        self.ships.append(
            {
                "id": sid,
                "kind": kind,
                "caps": dict(caps),
                "weapons": [],
                "modules": [],
                "turrets": [],
                "secondary_weapons": [],
            }
        )
        if self.active_ship_id is None:
            self.active_ship_id = sid
        return sid

    def _ship(self, sid: int) -> dict | None:
        for s in self.ships:
            if s["id"] == sid:
                return s
        return None

    def equip(self, name: str, kind: str, sid: int) -> bool:
        """Return True if the equip succeeded."""
        ship = self._ship(sid)
        if ship is None:
            return False
        if self.inventory.get(name, 0) <= 0:
            return False
        if len(ship[kind]) >= ship["caps"][kind]:
            return False
        # Decrement inventory, append to ship slot.
        self.inventory[name] -= 1
        if self.inventory[name] == 0:
            del self.inventory[name]
        ship[kind].append(name)
        return True

    def unequip(self, name: str, kind: str, sid: int) -> bool:
        ship = self._ship(sid)
        if ship is None:
            return False
        if name not in ship[kind]:
            return False
        ship[kind].remove(name)
        self.inventory[name] = self.inventory.get(name, 0) + 1
        return True

    def buy_ship(self, kind: str, caps: dict[str, int]) -> int:
        """Mirrors purchase_ship: clears old active ship's loadout into the new one
        (fitting subset) + overflow to inventory."""
        new_sid = self.add_ship(kind, caps)
        new_ship = self._ship(new_sid)
        if self.active_ship_id is not None and self.active_ship_id != new_sid:
            old = self._ship(self.active_ship_id)
            if old is not None:
                for k in ("weapons", "modules", "turrets", "secondary_weapons"):
                    src_items = list(old[k])
                    cap = new_ship["caps"][k]
                    fitting = src_items[:cap]
                    overflow = src_items[cap:]
                    new_ship[k] = list(fitting)
                    old[k] = []  # B.19 fix: clear src
                    for over in overflow:
                        self.inventory[over] = self.inventory.get(over, 0) + 1
        # Set new ship as active
        self.active_ship_id = new_sid
        return new_sid

    def sell_ship(self, sid: int) -> bool:
        """Decrements ledger by ship-name (we don't track ship names in ledger
        so we just remove the ship and its loadout into inventory)."""
        if sid == self.active_ship_id:
            return False  # cannot sell active
        ship = self._ship(sid)
        if ship is None:
            return False
        # Evacuate equipped items to inventory
        for k in ("weapons", "modules", "turrets", "secondary_weapons"):
            for name in ship[k]:
                self.inventory[name] = self.inventory.get(name, 0) + 1
            ship[k] = []
        # Remove ship
        self.ships = [s for s in self.ships if s["id"] != sid]
        return True

    def transfer_ship_out(self, sid: int) -> bool:
        """Ship + its remaining loadout leave the player; loadout returns to
        sender's inventory (mirrors transfer_ship router behaviour)."""
        if sid == self.active_ship_id:
            return False
        ship = self._ship(sid)
        if ship is None:
            return False
        for k in ("weapons", "modules", "turrets", "secondary_weapons"):
            for name in ship[k]:
                self.inventory[name] = self.inventory.get(name, 0) + 1
            ship[k] = []
        self.ships = [s for s in self.ships if s["id"] != sid]
        return True

    def set_active(self, sid: int) -> bool:
        """Mirrors reconcile_active_ship_slots + flag flip."""
        ship = self._ship(sid)
        if ship is None:
            return False
        # Reconcile against caps — overflow to inventory.
        for k in ("weapons", "modules", "turrets", "secondary_weapons"):
            cap = ship["caps"][k]
            current = ship[k]
            if len(current) > cap:
                keep = current[:cap]
                overflow = current[cap:]
                ship[k] = keep
                for over in overflow:
                    self.inventory[over] = self.inventory.get(over, 0) + 1
        self.active_ship_id = sid
        return True

    # ---- invariant checks ----

    def assert_invariants(self) -> None:
        # I1 (slot-reference count cannot exceed equipped balance):
        # for each name, total slot references across all ships must equal
        # ledger - inventory.  This is the precise statement of "a single
        # named instance never appears in more than one slot reference"
        # AS WELL AS "every slot reference has an inventory provenance".
        for name, ledger_qty in self.ledger.items():
            slot_count = 0
            for ship in self.ships:
                for k in ("weapons", "modules", "turrets", "secondary_weapons"):
                    slot_count += ship[k].count(name)
            inv_qty = self.inventory.get(name, 0)
            assert slot_count + inv_qty == ledger_qty, (
                f"I1/I2 violated: name='{name}' ledger={ledger_qty} slot_count={slot_count} inv_qty={inv_qty}"
            )

        # I4: each ship's loadout fits its caps (after any reconciliation steps).
        for ship in self.ships:
            for k in ("weapons", "modules", "turrets", "secondary_weapons"):
                assert len(ship[k]) <= ship["caps"][k], (
                    f"I4 violated: ship {ship['id']} has {len(ship[k])} {k} but cap is {ship['caps'][k]}"
                )

        # I2 (no orphan inventory references): every inventory key must be in
        # the ledger.  An inventory entry without a ledger entry would imply
        # something materialised from nothing.
        for name in self.inventory:
            assert name in self.ledger, f"I2 violated: '{name}' in inventory without ledger entry"


# ---------------------------------------------------------------------------
# Test catalog — a small fixed item set keeps action generation deterministic.
# ---------------------------------------------------------------------------

_ITEMS = [
    ("Pulse Laser", "weapons"),
    ("Burst Laser", "weapons"),
    ("Rail Gun", "weapons"),
    ("Shield Booster", "modules"),
    ("Cargo Bay", "modules"),
    ("Beam Turret", "turrets"),
]

_SHIP_KINDS = [
    ("Betty", {"weapons": 1, "modules": 3, "turrets": 0, "secondary_weapons": 0}),
    ("Sidewinder", {"weapons": 2, "modules": 3, "turrets": 1, "secondary_weapons": 0}),
    ("Hera", {"weapons": 2, "modules": 4, "turrets": 1, "secondary_weapons": 0}),
    ("Terran", {"weapons": 2, "modules": 5, "turrets": 2, "secondary_weapons": 0}),
]


def _seed_world(seed: int) -> PlayerWorld:
    """Build a starting world: a Betty with Nirai equipped, Micro Gun in cargo
    (mirrors the new _create_starter_loadout contract)."""
    rng = random.Random(seed)
    w = PlayerWorld(player_id=1)
    # Issue starter items via the issue path (ledger-tracked).
    w.issue_item("Pulse Laser", "weapons", 1)
    w.issue_item("Burst Laser", "weapons", 1)
    w.issue_item("Shield Booster", "modules", 1)
    w.issue_item("Cargo Bay", "modules", 1)
    w.issue_item("Beam Turret", "turrets", 1)
    # Random extras to vary state across seeds.
    for _ in range(rng.randint(0, 3)):
        name, _kind = rng.choice(_ITEMS)
        kind_for_item = next(k for n, k in _ITEMS if n == name)
        w.issue_item(name, kind_for_item, 1)
    # Create the starter ship (Betty) and equip the first weapon.
    sid = w.add_ship(*_SHIP_KINDS[0])
    w.equip("Pulse Laser", "weapons", sid)
    w.equip("Shield Booster", "modules", sid)
    return w


def _generate_action(rng: random.Random, world: PlayerWorld) -> tuple:
    """Pick one of the 6 actions, biased toward feasible ones."""
    choice = rng.choices(
        ["equip", "unequip", "buy_ship", "sell_ship", "transfer_ship", "set_active"],
        weights=[3, 3, 1, 1, 1, 2],
    )[0]
    if choice == "equip":
        # Try to pick an item we own and an existing ship with available slot.
        if not world.inventory:
            return ("noop",)
        name = rng.choice(list(world.inventory.keys()))
        kind = next(k for n, k in _ITEMS if n == name)
        if not world.ships:
            return ("noop",)
        ship = rng.choice(world.ships)
        return ("equip", name, kind, ship["id"])
    if choice == "unequip":
        # Pick any equipped item from any ship.
        candidates: list[tuple[str, str, int]] = []
        for s in world.ships:
            for k in ("weapons", "modules", "turrets", "secondary_weapons"):
                for n in s[k]:
                    candidates.append((n, k, s["id"]))
        if not candidates:
            return ("noop",)
        return ("unequip", *rng.choice(candidates))
    if choice == "buy_ship":
        kind, caps = rng.choice(_SHIP_KINDS)
        return ("buy_ship", kind, caps)
    if choice == "sell_ship":
        candidates = [s["id"] for s in world.ships if s["id"] != world.active_ship_id]
        if not candidates:
            return ("noop",)
        return ("sell_ship", rng.choice(candidates))
    if choice == "transfer_ship":
        candidates = [s["id"] for s in world.ships if s["id"] != world.active_ship_id]
        if not candidates:
            return ("noop",)
        return ("transfer_ship", rng.choice(candidates))
    if choice == "set_active":
        if not world.ships:
            return ("noop",)
        return ("set_active", rng.choice([s["id"] for s in world.ships]))
    return ("noop",)


def _apply(action: tuple, world: PlayerWorld) -> None:
    if action[0] == "noop":
        return
    if action[0] == "equip":
        _, name, kind, sid = action
        world.equip(name, kind, sid)
    elif action[0] == "unequip":
        _, name, kind, sid = action
        world.unequip(name, kind, sid)
    elif action[0] == "buy_ship":
        _, kind, caps = action
        world.buy_ship(kind, caps)
    elif action[0] == "sell_ship":
        _, sid = action
        world.sell_ship(sid)
    elif action[0] == "transfer_ship":
        _, sid = action
        world.transfer_ship_out(sid)
    elif action[0] == "set_active":
        _, sid = action
        world.set_active(sid)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", list(range(50)))
@pytest.mark.parametrize("length", [5, 10, 20])
def test_invariants_hold_under_random_action_sequences(seed: int, length: int) -> None:
    """50 seeds × 3 lengths = 150 generated cases.

    For each, build a starter world, apply ``length`` random actions, and
    verify all four invariants hold.  Failure prints the seed + sequence so
    the case can be re-played as a regression seed.
    """
    rng = random.Random(seed * 1000 + length)
    world = _seed_world(seed)
    sequence: list[tuple] = []
    try:
        world.assert_invariants()  # initial state should already be sound
        for _ in range(length):
            action = _generate_action(rng, world)
            sequence.append(action)
            _apply(action, world)
            world.assert_invariants()
    except AssertionError as exc:  # pragma: no cover — fires only on regression
        raise AssertionError(f"seed={seed} length={length} sequence={sequence}: {exc}") from exc


def test_starter_world_satisfies_invariants() -> None:
    """Sanity: the seeded starter world is invariant-correct out of the box."""
    world = _seed_world(seed=0)
    world.assert_invariants()


def test_phantom_dup_is_repaired_by_repair_player_logic() -> None:
    """Simulate the empirical B.19 corrupt state and assert the deduplication
    rules of ``LoadoutConsistencyService.repair_player`` would fix it.

    Note: this is a logic-level check on the repair contract.  The migration
    test (separate file) runs the same logic against a real DB.
    """
    world = PlayerWorld(player_id=1)
    # Manually craft a corrupt state: 3 ships all reference Phantom in modules
    # (no inventory provenance — the bug condition).  Skip the issue_item path
    # so the ledger stays at 0 — the repair rules state phantoms are kept on
    # one ship and dropped from the others.
    world.add_ship("Betty", _SHIP_KINDS[0][1])
    world.add_ship("Hera", _SHIP_KINDS[2][1])
    world.add_ship("Terran", _SHIP_KINDS[3][1])
    for s in world.ships:
        s["modules"] = ["Phantom"]

    # Apply the repair logic: keep on the active ship (id=1), drop from others.
    seen: set[tuple[str, str]] = set()
    for s in sorted(world.ships, key=lambda x: (x["id"] != world.active_ship_id, x["id"])):
        for k in ("weapons", "modules", "turrets", "secondary_weapons"):
            cleaned: list[str] = []
            for name in s[k]:
                key = (name, k)
                if key not in seen:
                    seen.add(key)
                    cleaned.append(name)
            s[k] = cleaned

    # Post-repair: only one ship has Phantom; total slot references count is 1.
    slot_count = sum(s["modules"].count("Phantom") for s in world.ships)
    assert slot_count == 1


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
