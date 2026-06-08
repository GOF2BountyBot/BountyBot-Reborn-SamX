"""
Unit tests for BountyService — S5 rewrite.

Scope: KEEP-UNIT tests only.
- Pure logic, at most 2 mocks per test.
- Every test asserts on at least one real computed value.
- No ORM mutation paths (distribute_rewards, _award_combat_bonus) — those live
  in tests/integration/test_bounty_service_integration.py.

Classification summary (S5):
  KEEP-UNIT:     138 tests (this file)
  MOVE-INTEGRATION: 13 tests (moved to integration)
  DELETE-TAUTOLOGICAL: 0
  DELETE-DUPLICATE: 0
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard: ensure shared.bblogger is mocked before importing service code.
# conftest.py already does this at collection time, but we defend against
# running this file in isolation.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from services.bounty_service import BountyService, CheckResult, _extract_secondary_combat_fields, get_secondary_subtype

# ---------------------------------------------------------------------------
# Module-level autouse fixture: patch LoadoutBuilder.from_player
# ---------------------------------------------------------------------------
# All check_bounty tests need LoadoutBuilder.from_player to be mocked because
# it internally creates new repository instances that make real DB calls.
# The default mock returns an "Unarmed" loadout. Tests that need a specific
# ship name override this with their own `with patch(...)` context manager.


@pytest.fixture(autouse=True)
def _mock_loadout_builder_from_player():
    """Auto-mock LoadoutBuilder.from_player for all tests in this file.

    Since LoadoutBuilder is imported inside the function body in bounty_service.py,
    we patch it at its definition location: services.loadout_builder.LoadoutBuilder.
    """
    from services.combat_models import ShipLoadout

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Unarmed", base_armour=100)),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers / factory functions
# ---------------------------------------------------------------------------


def _make_criminal(
    name: str = "Test Criminal",
    faction: str = "terran",
    is_player: bool = False,
) -> SimpleNamespace:
    """Return a Criminal-like SimpleNamespace."""
    return SimpleNamespace(name=name, faction=faction, is_player=is_player)


def _make_bounty(criminal_name: str = "Test Criminal") -> SimpleNamespace:
    """Return a Bounty-like SimpleNamespace with criminal_name."""
    return SimpleNamespace(criminal_name=criminal_name)


def _make_ship(
    name: str = "Betty",
    value: int = 16038,
    max_primaries: int = 1,
    max_modules: int = 3,
    max_turrets: int = 0,
    max_secondaries: int = 0,
) -> SimpleNamespace:
    """Return a Ship-like SimpleNamespace."""
    return SimpleNamespace(
        name=name,
        value=value,
        max_primaries=max_primaries,
        max_modules=max_modules,
        max_turrets=max_turrets,
        max_secondaries=max_secondaries,
    )


def _make_weapon(
    name: str = "Micro Gun MK I",
    value: int = 2577,
    dps: float = 9.09,
    tech_level: int = 1,
) -> SimpleNamespace:
    """Return a PrimaryWeapon-like SimpleNamespace."""
    return SimpleNamespace(name=name, value=value, dps=dps, tech_level=tech_level)


def _make_module(
    name: str = "E2 Exoclad",
    value: int = 1070,
    tech_level: int = 1,
    type: str = "ArmourModule",
    extra_atts: dict | None = None,
) -> SimpleNamespace:
    """Return a Module-like SimpleNamespace."""
    return SimpleNamespace(name=name, value=value, tech_level=tech_level, type=type, extra_atts=extra_atts)


def _make_secondary(
    name: str = "Nuke",
    value: int = 10000,
    dps: float = 0.0,
    tech_level: int = 1,
    damage: int = 800,
    subtype: str = "nuke",
    loading_speed_ms: int = 3000,
    range_m: float = 2000.0,
    burst_count: int = 0,
    emp_damage: int = 0,
    magnitude_m: float = 500.0,
    steerable: bool = True,
) -> SimpleNamespace:
    """Return a SecondaryWeapon-like SimpleNamespace for CI-17 tests.

    The extra_atts dict uses the DB nesting pattern: combat-relevant fields
    live in the inner extra_atts dict.
    """
    inner = {
        "subtype": subtype,
        "loading_speed_ms": loading_speed_ms,
        "range_m": range_m,
        "burst_count": burst_count,
        "emp_damage": emp_damage,
        "magnitude_m": magnitude_m,
        "steerable": steerable,
    }
    return SimpleNamespace(
        name=name,
        value=value,
        dps=dps,
        tech_level=tech_level,
        damage=damage,
        emoji=None,
        extra_atts={"extra_atts": inner},
    )


def _setup_mock_db_query(mock_db, return_value):
    """Configure mock_db.execute() to return a value via .scalars().all()."""
    scalars = MagicMock()
    scalars.all.return_value = return_value
    result = MagicMock()
    result.scalars.return_value = scalars
    mock_db.execute = AsyncMock(return_value=result)
    return mock_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> BountyService:
    """Return a BountyService with all repositories replaced by MagicMocks.

    B.49: config_repo is also replaced so that check_bounty / spawn_bounty can
    call get_by_guild_id without going to the real DB.  The default return value
    is None which causes resolve_constant to fall back to global GameConstants.

    X3-bounty: bounty_repo.get_by_id_for_update is configured as an AsyncMock
    with a side_effect that looks up the bounty by ID from whatever
    get_active_by_guild_and_division.return_value is set to at call time.
    This means tests that set get_active_by_guild_and_division.return_value = [bounty]
    automatically get the correct bounty returned by get_by_id_for_update without
    needing an extra line in every test.  Tests that need a specific override can
    overwrite get_by_id_for_update.side_effect or return_value directly.
    """
    svc = BountyService()
    svc.bounty_repo = MagicMock()
    svc.criminal_repo = MagicMock()
    svc.item_repo = MagicMock()
    svc.player_repo = MagicMock()
    svc.config_repo = MagicMock()
    svc.config_repo.get_by_guild_id = AsyncMock(return_value=None)

    # X3-bounty: auto-route get_by_id_for_update to the correct bounty from the
    # active-bounties list configured via get_active_by_guild_and_division.return_value.
    # The side_effect runs at call-time, so it reads whatever return_value the test set.
    async def _for_update_side_effect(_db, bounty_id):
        rv = svc.bounty_repo.get_active_by_guild_and_division.return_value
        if rv is None:
            return None
        # rv may be a coroutine return value (from AsyncMock) or a plain list
        active = rv if isinstance(rv, list) else []
        for b in active:
            if getattr(b, "id", None) == bounty_id:
                return b
        return None

    svc.bounty_repo.get_by_id_for_update = AsyncMock(side_effect=_for_update_side_effect)
    # P6-T1: _build_payout_breakdown now calls player_repo.get_by_ids (batched).
    # Default to empty list; tests that need payout breakdown content can override.
    svc.player_repo.get_by_ids = AsyncMock(return_value=[])

    return svc


@pytest.fixture
def mock_db() -> AsyncMock:
    """Return a mock async database session with commit/refresh pre-configured."""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ===========================================================================
# Tests: select_criminal
# ===========================================================================


@pytest.mark.asyncio
async def test_select_criminal_success(service, mock_db):
    """A criminal is returned when criminals are available and no active bounties."""
    criminals = [
        _make_criminal("Alice", "terran"),
        _make_criminal("Bob", "nivelian"),
    ]
    service.criminal_repo.list_all = AsyncMock(return_value=criminals)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    result = await service.select_criminal(mock_db, guild_id=1, division="bronze")

    assert result is not None
    assert result.name in {"Alice", "Bob"}


@pytest.mark.asyncio
async def test_select_criminal_excludes_active(service, mock_db):
    """A criminal already active in the division is excluded from selection."""
    criminals = [
        _make_criminal("Alice", "terran"),
        _make_criminal("Bob", "terran"),
    ]
    active_bounties = [_make_bounty("Alice")]

    service.criminal_repo.list_all = AsyncMock(return_value=criminals)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=active_bounties)

    # Run enough times to confirm Alice is never selected
    for _ in range(20):
        result = await service.select_criminal(mock_db, guild_id=1, division="bronze")
        assert result is not None
        assert result.name == "Bob", f"Expected Bob, got {result.name}"


@pytest.mark.asyncio
async def test_select_criminal_falls_back_to_reuse_when_pool_exhausted(service, mock_db):
    """A5: when every non-player criminal is already active in this division,
    fall back to the full pool (allowing same-division reuse) instead of None.

    Use case: large/active guild with bounty_max_per_tier > criminal pool size.
    """
    criminals = [_make_criminal("Alice", "terran")]
    active_bounties = [_make_bounty("Alice")]

    service.criminal_repo.list_all = AsyncMock(return_value=criminals)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=active_bounties)

    result = await service.select_criminal(mock_db, guild_id=1, division="bronze")

    # Pool exhausted → fall back to reuse, must return Alice (the only NPC).
    assert result is not None
    assert result.name == "Alice"


@pytest.mark.asyncio
async def test_select_criminal_returns_none_when_no_non_player_criminals(service, mock_db):
    """Returns None only when the criminal table has no non-player criminals
    at all (seed-data/config error). This is the ONLY case that yields None
    post-A5.
    """
    # Only player criminals seeded — no NPCs at all.
    criminals = [_make_criminal("Hero", "terran", is_player=True)]

    service.criminal_repo.list_all = AsyncMock(return_value=criminals)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    result = await service.select_criminal(mock_db, guild_id=1, division="bronze")

    assert result is None


@pytest.mark.asyncio
async def test_select_criminal_returns_none_when_criminal_table_empty(service, mock_db):
    """Returns None when criminal table is completely empty (seed failure)."""
    service.criminal_repo.list_all = AsyncMock(return_value=[])
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    result = await service.select_criminal(mock_db, guild_id=1, division="bronze")

    assert result is None


@pytest.mark.asyncio
async def test_select_criminal_reuse_fallback_uses_full_pool(service, mock_db):
    """A5: when pool is exhausted, fallback considers ALL non-player criminals,
    not just the ones in active bounties. Picks should be distributed across
    the full pool (we observe at least one of each criminal across many trials).
    """
    criminals = [
        _make_criminal("Alice", "terran"),
        _make_criminal("Bob", "terran"),
        _make_criminal("Charlie", "nivelian"),
    ]
    # All three are active → pool exhausted, reuse permitted.
    active_bounties = [_make_bounty("Alice"), _make_bounty("Bob"), _make_bounty("Charlie")]

    service.criminal_repo.list_all = AsyncMock(return_value=criminals)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=active_bounties)

    seen_names: set[str] = set()
    for _ in range(200):
        result = await service.select_criminal(mock_db, guild_id=1, division="bronze")
        assert result is not None
        seen_names.add(result.name)

    # Probability of NOT seeing a given criminal across 200 trials with
    # faction-uniform sampling is vanishingly small. Should see all 3 names.
    assert seen_names == {"Alice", "Bob", "Charlie"}


@pytest.mark.asyncio
async def test_select_criminal_filters_player_criminals(service, mock_db):
    """Criminals with is_player=True are excluded regardless of active bounties."""
    criminals = [
        _make_criminal("PlayerOne", "terran", is_player=True),
        _make_criminal("NPCOne", "nivelian", is_player=False),
    ]
    service.criminal_repo.list_all = AsyncMock(return_value=criminals)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    for _ in range(20):
        result = await service.select_criminal(mock_db, guild_id=1, division="bronze")
        assert result is not None
        assert result.name == "NPCOne"
        assert result.is_player is False


# ===========================================================================
# Tests: find_item_tl
# ===========================================================================


@pytest.mark.asyncio
async def test_find_item_tl_exact_match(service, mock_db):
    """Items exist at the center tech level — returns center immediately."""
    weapon = _make_weapon()
    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[weapon])

    result = await service.find_item_tl(
        mock_db,
        center=3,
        min_tl=1,
        max_tl=10,
        upper_bound=1,
        item_type="primary_weapon",
    )

    assert result == 3


@pytest.mark.asyncio
async def test_find_item_tl_downward_search(service, mock_db):
    """Items only exist at a lower TL than center — found via downward search."""

    async def mock_get_all(db, tl, item_type=None):
        # Only TL 2 has items
        return [_make_weapon(tech_level=2)] if tl == 2 else []

    service.item_repo.get_all_by_tech_level = mock_get_all

    result = await service.find_item_tl(
        mock_db,
        center=5,
        min_tl=1,
        max_tl=10,
        upper_bound=1,
        item_type="primary_weapon",
    )

    assert result == 2


@pytest.mark.asyncio
async def test_find_item_tl_upward_search(service, mock_db):
    """Items only exist at center+1 — found via upward search within upper_bound."""

    async def mock_get_all(db, tl, item_type=None):
        # Only TL 4 has items; center will be 3
        return [_make_weapon(tech_level=4)] if tl == 4 else []

    service.item_repo.get_all_by_tech_level = mock_get_all

    result = await service.find_item_tl(
        mock_db,
        center=3,
        min_tl=1,
        max_tl=10,
        upper_bound=2,  # allows searching up to TL 5
        item_type="primary_weapon",
    )

    assert result == 4


@pytest.mark.asyncio
async def test_find_item_tl_no_items(service, mock_db):
    """No items at any TL in the search range — returns -1."""
    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[])

    result = await service.find_item_tl(
        mock_db,
        center=5,
        min_tl=1,
        max_tl=10,
        upper_bound=1,
        item_type="primary_weapon",
    )

    assert result == -1


# ===========================================================================
# Tests: generate_loadout
# ===========================================================================


@pytest.mark.asyncio
async def test_generate_loadout_tl0_beginner(service, mock_db):
    """Tech level 0 returns the fixed beginner loadout (Betty, no equipment)."""
    result = await service.generate_loadout(mock_db, tech_level=0)

    assert result["ship_name"] == "Betty"
    assert result["ship_value"] == 0
    assert result["weapons"] == []
    assert result["modules"] == []
    assert result["turrets"] == []
    assert result["total_value"] == 0


@pytest.mark.asyncio
async def test_generate_loadout_returns_valid_dict(service, mock_db):
    """Generated loadout contains all expected keys."""
    ship = _make_ship("Groza", value=251600, max_primaries=3, max_modules=8)
    weapon = _make_weapon()
    module = _make_module()

    # Return weapons-only list for primary_weapon queries, modules-only for module queries
    async def _get_all_by_tl(db, tl, item_type=None):
        if item_type == "primary_weapon":
            return [weapon]
        if item_type == "module":
            return [module]
        return []

    service.item_repo.get_all_by_tech_level = _get_all_by_tl

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=module)),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    expected_keys = {
        "ship_name",
        "ship_emoji",
        "ship_value",
        "ship_armour",
        "armor_hp",
        "shield_hp",
        "total_hp",
        "ship_max_primaries",
        "ship_max_modules",
        "ship_max_turrets",
        "ship_max_secondaries",
        "weapons",
        "modules",
        "turrets",
        "secondaries",
        "total_value",
    }
    assert expected_keys == set(result.keys())


@pytest.mark.asyncio
async def test_generate_loadout_equips_weapons(service, mock_db):
    """Weapons are equipped up to ship.max_primaries."""
    ship = _make_ship("Groza", value=251600, max_primaries=3, max_modules=2)
    weapon = _make_weapon(dps=9.09)

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
    ):
        _setup_mock_db_query(mock_db, [ship])
        # Weapons and modules both come from get_all_by_tech_level
        service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[weapon])

        result = await service.generate_loadout(mock_db, tech_level=2)

    assert len(result["weapons"]) == 3  # max_primaries = 3
    assert all("name" in w and "value" in w and "dps" in w for w in result["weapons"])


@pytest.mark.asyncio
async def test_generate_loadout_armour_at_tl_gt_1(service, mock_db):
    """Armour module is guaranteed to be the first module at TL > 1."""
    ship = _make_ship("Groza", value=251600, max_primaries=0, max_modules=3)
    armour_mod = _make_module("E2 Exoclad Armour", value=1070, tech_level=1)

    with patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)):
        _setup_mock_db_query(mock_db, [ship])

        # generic modules list (for filling remaining slots) — use a different type so armour slot isn't blocked
        generic_mod = _make_module("Generic Module", value=500, tech_level=1, type="CabinModule")
        service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[generic_mod])

        with patch.object(
            service,
            "_find_typed_module",
            new=AsyncMock(side_effect=lambda db, kw, tl: armour_mod if kw == "armour" else None),
        ):
            result = await service.generate_loadout(mock_db, tech_level=2)

    assert len(result["modules"]) > 0
    # First module should be the armour one
    assert "armour" in result["modules"][0]["name"].lower() or result["modules"][0]["name"] == armour_mod.name


@pytest.mark.asyncio
async def test_generate_loadout_shield_at_tl_gt_3(service, mock_db):
    """Shield module is guaranteed at TL > 3."""
    ship = _make_ship("Ghost", value=6000000, max_primaries=0, max_modules=5)
    armour_mod = _make_module("E2 Exoclad Armour", value=1070, tech_level=1)
    shield_mod = _make_module("Beamshield II Shield", value=39331, tech_level=4)

    async def fake_find_typed_module(db, kw, tl):
        if kw == "armour":
            return armour_mod
        if kw == "shield":
            return shield_mod
        return None

    with patch.object(service, "find_item_tl", new=AsyncMock(return_value=3)):
        _setup_mock_db_query(mock_db, [ship])

        generic_mod = _make_module("Generic", value=500, tech_level=3, type="CabinModule")
        service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[generic_mod])

        with patch.object(service, "_find_typed_module", new=AsyncMock(side_effect=fake_find_typed_module)):
            result = await service.generate_loadout(mock_db, tech_level=4)

    module_names = [m["name"] for m in result["modules"]]
    assert armour_mod.name in module_names, f"Armour mod missing: {module_names}"
    assert shield_mod.name in module_names, f"Shield mod missing: {module_names}"


@pytest.mark.asyncio
async def test_generate_loadout_calculates_total_value(service, mock_db):
    """total_value equals ship.value + sum(weapon values) + sum(module values)."""
    ship = _make_ship("Betty", value=16038, max_primaries=1, max_modules=2)
    weapon = _make_weapon("Micro Gun", value=2577, dps=9.09)
    module1 = _make_module("E2 Exoclad", value=1070, tech_level=1, type="CabinModule")
    armour_mod = _make_module("Armour Plate", value=500, tech_level=1, type="ArmourModule")

    async def _get_all_by_tl(db, tl, item_type=None):
        if item_type == "primary_weapon":
            return [weapon]
        if item_type == "module":
            return [module1]
        return []

    with patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)):
        _setup_mock_db_query(mock_db, [ship])
        service.item_repo.get_all_by_tech_level = _get_all_by_tl

        with patch.object(
            service,
            "_find_typed_module",
            new=AsyncMock(side_effect=lambda db, kw, tl: armour_mod if kw == "armour" else None),
        ):
            result = await service.generate_loadout(mock_db, tech_level=2)

    # total_value must equal ship value + all weapon values + all module values
    expected_weapon_sum = sum(w["value"] for w in result["weapons"])
    expected_module_sum = sum(m["value"] for m in result["modules"])
    expected_total = ship.value + expected_weapon_sum + expected_module_sum
    assert result["total_value"] == expected_total


@pytest.mark.asyncio
async def test_generate_loadout_module_dict_includes_type(service, mock_db):
    """Each module in the loadout dict must include a 'type' key."""
    ship = _make_ship("Betty", value=16038, max_primaries=0, max_modules=2)
    armour_mod = _make_module("E2 Exoclad", value=1070, tech_level=1, type="ArmourModule")
    cabin_mod = _make_module("Large Cabin", value=5000, tech_level=1, type="CabinModule")

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[cabin_mod])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(
            service,
            "_find_typed_module",
            new=AsyncMock(side_effect=lambda db, kw, tl: armour_mod if kw == "armour" else None),
        ),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    assert len(result["modules"]) > 0
    for mod_dict in result["modules"]:
        assert "type" in mod_dict, f"Module dict missing 'type' key: {mod_dict}"
        assert "extra_atts" in mod_dict, f"Module dict missing 'extra_atts' key: {mod_dict}"


@pytest.mark.asyncio
async def test_generate_loadout_no_duplicate_types_when_limit_1(service, mock_db):
    """Only one module of each type with limit=1 may appear (type-class uniqueness)."""
    # Ship with 4 module slots; armour guaranteed at slot 1
    ship = _make_ship("Groza", value=251600, max_primaries=0, max_modules=4)
    armour_mod = _make_module("D'iol", value=51449, tech_level=7, type="ArmourModule")
    # Pool contains two different ArmourModules — only one should appear
    armour_mod_2 = _make_module("E2 Exoclad", value=1070, tech_level=1, type="ArmourModule")
    cabin_mod = _make_module("Large Cabin", value=5000, tech_level=1, type="CabinModule")

    # Generic pool: two armour variants + one cabin (unlimited)
    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[armour_mod_2, cabin_mod])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(
            service,
            "_find_typed_module",
            new=AsyncMock(side_effect=lambda db, kw, tl: armour_mod if kw == "armour" else None),
        ),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    module_types = [m["type"] for m in result["modules"]]
    armour_count = module_types.count("ArmourModule")
    assert armour_count == 1, f"Expected exactly 1 ArmourModule, got {armour_count}: {module_types}"


@pytest.mark.asyncio
async def test_generate_loadout_unlimited_type_allows_multiple(service, mock_db):
    """Modules with limit=-1 (CabinModule) can fill all remaining slots."""
    # Ship with 3 slots; no guaranteed slots (TL=1, tech_level not > 1 means no armour)
    ship = _make_ship("Betty", value=16038, max_primaries=0, max_modules=3)
    cabin_mod = _make_module("Large Cabin", value=5000, tech_level=1, type="CabinModule")

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[cabin_mod])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=1)

    module_types = [m["type"] for m in result["modules"]]
    cabin_count = module_types.count("CabinModule")
    assert cabin_count == 3, f"Expected 3 CabinModules (unlimited), got {cabin_count}: {module_types}"


@pytest.mark.asyncio
async def test_generate_loadout_limit_0_type_not_equipped(service, mock_db):
    """Modules with limit=0 (e.g. JumpDriveModule) must never be equipped."""
    ship = _make_ship("Betty", value=16038, max_primaries=0, max_modules=3)
    jump_drive = _make_module("Jump Drive", value=99999, tech_level=1, type="JumpDriveModule")
    cabin_mod = _make_module("Large Cabin", value=5000, tech_level=1, type="CabinModule")

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[jump_drive, cabin_mod])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=1)

    module_types = [m["type"] for m in result["modules"]]
    assert "JumpDriveModule" not in module_types, f"JumpDriveModule should never be equipped: {module_types}"


@pytest.mark.asyncio
async def test_generate_loadout_type_tracking_counts_guaranteed_slots(service, mock_db):
    """Armour/shield guaranteed slots count toward type tracking before generic fill."""
    # Ship with 2 slots: slot 1 = armour guaranteed, slot 2 = generic fill
    ship = _make_ship("Betty", value=16038, max_primaries=0, max_modules=2)
    armour_mod = _make_module("D'iol", value=51449, tech_level=7, type="ArmourModule")
    # Generic pool: only another ArmourModule — should be blocked by type limit
    armour_mod_2 = _make_module("E2 Exoclad", value=1070, tech_level=1, type="ArmourModule")

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[armour_mod_2])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(
            service,
            "_find_typed_module",
            new=AsyncMock(side_effect=lambda db, kw, tl: armour_mod if kw == "armour" else None),
        ),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    # Only the guaranteed armour should be equipped; generic fill blocked by limit=1
    module_types = [m["type"] for m in result["modules"]]
    assert module_types.count("ArmourModule") == 1, (
        f"Expected exactly 1 ArmourModule (guaranteed slot already used), got: {module_types}"
    )
    # Only 1 module total (second slot couldn't be filled due to type limit)
    assert len(result["modules"]) == 1, f"Expected 1 module total, got {len(result['modules'])}"


# ===========================================================================
# Tests: spawn_bounty
# ===========================================================================

# ---------------------------------------------------------------------------
# Helpers for spawn_bounty tests
# ---------------------------------------------------------------------------

SAMPLE_ROUTE = ["Alpha", "Beta", "Gamma", "Delta"]
SAMPLE_LOADOUT = {
    "ship_name": "Groza",
    "ship_value": 250000,
    "ship_armour": 200,
    "armor_hp": 200,
    "shield_hp": 0,
    "total_hp": 200,
    "ship_max_primaries": 2,
    "ship_max_modules": 4,
    "ship_max_turrets": 0,
    "weapons": [{"name": "Laser", "value": 5000, "dps": 20.0}],
    "modules": [],
    "turrets": [],
    "total_value": 255000,
}


def _make_created_bounty(**kwargs) -> SimpleNamespace:
    """Return a Bounty-like SimpleNamespace with an id set."""
    defaults = dict(
        id=42,
        guild_id=1,
        division="bronze",
        criminal_name="Test Criminal",
        criminal_faction="terran",
        route=SAMPLE_ROUTE,
        answer="Beta",
        reward=400,
        reward_per_sys=100,
        checked={s: -1 for s in SAMPLE_ROUTE},
        tech_level=3,
        criminal_ship=SAMPLE_LOADOUT,
        status="active",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.fixture
def spawn_service_minimal(service) -> BountyService:
    """Return a BountyService with just repos (no graph/pathfinding) for early-exit tests."""
    return service


@pytest.fixture
def spawn_service(spawn_service_minimal) -> BountyService:
    """Return a BountyService with graph/pathfinding mocks pre-attached."""
    spawn_service_minimal.graph_service = MagicMock()
    spawn_service_minimal.graph_service.load_graph = AsyncMock()
    spawn_service_minimal.graph_service.get_systems_with_jump_gates = MagicMock(
        return_value=["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
    )
    spawn_service_minimal.pathfinding_service = MagicMock()
    spawn_service_minimal.pathfinding_service.make_route = MagicMock(return_value=SAMPLE_ROUTE)
    return spawn_service_minimal


@pytest.mark.asyncio
async def test_spawn_bounty_success(spawn_service, mock_db):
    """Spawning a bounty with valid inputs returns a fully-populated Bounty."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    created = _make_created_bounty(criminal_name="Viper")
    spawn_service.bounty_repo.create = AsyncMock(return_value=created)

    with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert result is not None
    assert result.criminal_name == "Viper"
    assert result.route == SAMPLE_ROUTE
    assert result.status == "active"
    spawn_service.bounty_repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_bounty_no_criminal_available(spawn_service_minimal, mock_db):
    """spawn_bounty returns None when no criminal is available."""
    spawn_service_minimal.criminal_repo.list_all = AsyncMock(return_value=[])
    spawn_service_minimal.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    result = await spawn_service_minimal.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert result is None
    spawn_service_minimal.bounty_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_bounty_route_generation_fails(spawn_service, mock_db):
    """spawn_bounty returns None when pathfinding fails all 3 attempts."""
    from services.pathfinding_service import PathfindingError

    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
    # All attempts fail
    spawn_service.pathfinding_service.make_route = MagicMock(return_value=PathfindingError.NO_ROUTE_FOUND)

    result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert result is None
    assert spawn_service.pathfinding_service.make_route.call_count == 3


@pytest.mark.asyncio
async def test_spawn_bounty_route_retries(spawn_service, mock_db):
    """spawn_bounty retries route generation; succeeds on second attempt."""
    from services.pathfinding_service import PathfindingError

    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    # First call fails, second succeeds
    spawn_service.pathfinding_service.make_route = MagicMock(
        side_effect=[PathfindingError.NO_ROUTE_FOUND, SAMPLE_ROUTE]
    )
    created = _make_created_bounty()
    spawn_service.bounty_repo.create = AsyncMock(return_value=created)

    with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert result is not None
    assert spawn_service.pathfinding_service.make_route.call_count == 2


@pytest.mark.asyncio
async def test_spawn_bounty_not_enough_systems(spawn_service, mock_db):
    """spawn_bounty returns None when fewer than 2 jump gate systems exist."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
    # Only one system with jump gate
    spawn_service.graph_service.get_systems_with_jump_gates = MagicMock(return_value=["Alpha"])

    result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert result is None
    spawn_service.pathfinding_service.make_route.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_bounty_answer_in_route(spawn_service, mock_db):
    """The answer field must be one of the route systems."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    # Capture the Bounty passed to create()
    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert result is not None
    assert len(captured_bounties) == 1
    b = captured_bounties[0]
    assert b.answer in b.route


@pytest.mark.asyncio
async def test_spawn_bounty_checked_dict_matches_route(spawn_service, mock_db):
    """The checked dict must contain all route systems mapped to -1."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
        await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert len(captured_bounties) == 1
    b = captured_bounties[0]
    assert set(b.checked.keys()) == set(b.route)
    assert all(v == -1 for v in b.checked.values())


@pytest.mark.asyncio
async def test_spawn_bounty_reward_calculation(spawn_service, mock_db):
    """New formula: reward_per_sys = floor(consolation_pool / route_len).

    Under the winner-reserve model:
    - total_reward is seeded from the legacy rps formula.
    - winner_reserve = int(total_reward * BOUNTY_WINNER_RESERVE_FACTOR)
    - consolation_pool = total_reward - winner_reserve
    - reward_per_sys = consolation_pool // len(route)
    The stored reward_per_sys is therefore <= reward / len(route).
    """
    from services.game_constants import GameConstants

    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
        await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert len(captured_bounties) == 1
    b = captured_bounties[0]

    # Verify the new formula: rps comes from consolation pool / route len
    expected_winner_reserve = int(b.reward * GameConstants.BOUNTY_WINNER_RESERVE_FACTOR)
    expected_consolation_pool = b.reward - expected_winner_reserve
    expected_rps = expected_consolation_pool // len(b.route)
    assert b.reward_per_sys == expected_rps, (
        f"reward_per_sys={b.reward_per_sys!r} != expected {expected_rps!r} "
        f"(reward={b.reward}, route_len={len(b.route)}, factor={GameConstants.BOUNTY_WINNER_RESERVE_FACTOR})"
    )


@pytest.mark.asyncio
async def test_spawn_bounty_end_time_calculation(spawn_service, mock_db):
    """end_time must be issue_time + timedelta(minutes=480) when no expiry_minutes given."""
    from datetime import timedelta

    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
        await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

    assert len(captured_bounties) == 1
    b = captured_bounties[0]
    # Default expiry is 480 minutes (8 hours)
    expected_end = b.issue_time + timedelta(minutes=480)
    assert b.end_time == expected_end


@pytest.mark.asyncio
async def test_spawn_bounty_uses_provided_tech_level(spawn_service, mock_db):
    """When tech_level is explicitly provided, it is used directly (no random pick)."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    with (
        patch("services.bounty_service.pick_random_item_tl") as mock_pick_tl,
        patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)),
    ):
        await spawn_service.spawn_bounty(mock_db, guild_id=1, division="gold", tech_level=7)

    # pick_random_item_tl should NOT be called when tech_level is provided
    mock_pick_tl.assert_not_called()
    assert len(captured_bounties) == 1
    assert captured_bounties[0].tech_level == 7


@pytest.mark.asyncio
async def test_spawn_bounty_auto_selects_tech_level(spawn_service, mock_db):
    """When tech_level is None, pick_random_item_tl is called with division center."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    created = _make_created_bounty()
    spawn_service.bounty_repo.create = AsyncMock(return_value=created)

    with (
        patch("services.bounty_service.pick_random_item_tl", return_value=5) as mock_pick_tl,
        patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)),
    ):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="silver", tech_level=None)

    # silver division maps to center_tl=3
    mock_pick_tl.assert_called_once_with(3)
    assert result is not None


@pytest.mark.asyncio
async def test_spawn_bounty_bronze_center_tl_is_1(spawn_service, mock_db):
    """Bronze division uses center_tl=1, producing mostly TL-1 criminals."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
    created = _make_created_bounty()
    spawn_service.bounty_repo.create = AsyncMock(return_value=created)

    with (
        patch("services.bounty_service.pick_random_item_tl", return_value=1) as mock_pick_tl,
        patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)),
    ):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=None)

    # Bronze center TL is now 1 (not 2)
    mock_pick_tl.assert_called_once_with(1)
    assert result is not None


@pytest.mark.asyncio
async def test_spawn_bounty_division_tl_map_center_values(spawn_service, mock_db):
    """division_tl_map center values: silver=3, gold=6, platinum=8."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
    created = _make_created_bounty()
    spawn_service.bounty_repo.create = AsyncMock(return_value=created)

    for division, expected_center in [("silver", 3), ("gold", 6), ("platinum", 8)]:
        with (
            patch("services.bounty_service.pick_random_item_tl", return_value=expected_center) as mock_pick_tl,
            patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)),
        ):
            result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division=division, tech_level=None)

        mock_pick_tl.assert_called_once_with(expected_center), (f"Expected center_tl={expected_center} for {division}")
        assert result is not None


@pytest.mark.asyncio
async def test_spawn_bounty_bronze_tl_capped_at_2(spawn_service, mock_db):
    """Bronze tech level is capped at 2 regardless of random picker output."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    # Simulate the random picker returning 4 (above bronze cap of 2)
    with (
        patch("services.bounty_service.pick_random_item_tl", return_value=4),
        patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)),
    ):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=None)

    assert result is not None
    assert len(captured_bounties) == 1
    assert captured_bounties[0].tech_level <= 2, (
        f"Bronze tech_level must be <= 2, got {captured_bounties[0].tech_level}"
    )


@pytest.mark.asyncio
async def test_spawn_bounty_silver_tl_capped_at_4(spawn_service, mock_db):
    """Silver tech level is capped at 4 regardless of random picker output."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    # Simulate the random picker returning 7 (above silver cap of 4)
    with (
        patch("services.bounty_service.pick_random_item_tl", return_value=7),
        patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)),
    ):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="silver", tech_level=None)

    assert result is not None
    assert len(captured_bounties) == 1
    assert captured_bounties[0].tech_level <= 4, (
        f"Silver tech_level must be <= 4, got {captured_bounties[0].tech_level}"
    )


@pytest.mark.asyncio
async def test_spawn_bounty_gold_tl_capped_at_7(spawn_service, mock_db):
    """Gold tech level is capped at 7 regardless of random picker output."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    # Simulate the random picker returning 10 (above gold cap of 7)
    with (
        patch("services.bounty_service.pick_random_item_tl", return_value=10),
        patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)),
    ):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="gold", tech_level=None)

    assert result is not None
    assert len(captured_bounties) == 1
    assert captured_bounties[0].tech_level <= 7, f"Gold tech_level must be <= 7, got {captured_bounties[0].tech_level}"


@pytest.mark.asyncio
async def test_spawn_bounty_tl_cap_does_not_affect_explicit_tech_level(spawn_service, mock_db):
    """An explicitly-provided tech_level bypasses the division TL cap."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    captured_bounties = []

    async def capture_create(db, bounty):
        captured_bounties.append(bounty)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    # Explicitly pass tech_level=5 for bronze (above the cap); should NOT be clamped
    with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
        result = await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=5)

    assert result is not None
    assert len(captured_bounties) == 1
    assert captured_bounties[0].tech_level == 5, (
        f"Explicit tech_level should not be clamped; expected 5, got {captured_bounties[0].tech_level}"
    )


@pytest.mark.asyncio
async def test_spawn_bounty_bronze_tl_within_valid_range(spawn_service, mock_db):
    """Bronze criminal TL is always in the range [1, 2] over many draws."""
    criminal = _make_criminal("Viper", "terran")
    spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
    spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

    observed_tls = []

    async def capture_create(db, bounty):
        observed_tls.append(bounty.tech_level)
        return SimpleNamespace(id=99, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

    spawn_service.bounty_repo.create = capture_create

    # Run 30 spawns with real pick_random_item_tl (no mock) to verify statistical cap
    for _ in range(30):
        observed_tls.clear()
        with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
            await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=None)

    # All observed tech levels must respect the cap
    for tl in observed_tls:
        assert tl <= 2, f"Bronze tech_level {tl} exceeds cap of 2"
        assert tl >= 1, f"Bronze tech_level {tl} is below MIN_TECH_LEVEL"


# ===========================================================================
# Tests: check_bounty
# ===========================================================================


@pytest.fixture
def check_bounty_setup(service, mock_db):
    """Pre-configure common mocks for core check_bounty tests.

    Returns (service, mock_db) with player_repo, bounty_repo.get_active_by_guild_and_division,
    and bounty_repo.update pre-configured as AsyncMocks. Tests set .return_value on each.

    LoadoutBuilder.from_player is already mocked globally by the _mock_loadout_builder_from_player
    autouse fixture, so tests here don't need real DB calls inside the loadout builder.

    X3-bounty: get_by_id_for_update is now called inside _process_single_bounty_check to acquire
    a row-level lock before reading the checked map.  The service() fixture configures a smart
    side_effect on get_by_id_for_update that auto-routes by ID from the active bounties list.
    Tests only need to set get_active_by_guild_and_division.return_value = [bounty] and the
    lock lookup automatically finds the correct bounty.
    """
    service.player_repo.get_by_id = AsyncMock()
    # P6-T1: _build_payout_breakdown now calls get_by_ids (batched) instead of get_by_id.
    # Default to empty list; tests that assert on payout content can override.
    service.player_repo.get_by_ids = AsyncMock(return_value=[])
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    return service, mock_db


def _make_player(
    player_id: int = 1,
    tier: str = "Bronze",
    classic_mode: bool = False,
    bounty_cooldown_end=None,
    active_ship=None,
) -> SimpleNamespace:
    """Return a Player-like SimpleNamespace.

    T10: guild_id and user_id added so fight_ships callsites can extract them.
    """
    return SimpleNamespace(
        id=player_id,
        user_id=player_id * 1000,  # T10: Discord user_id for combat_log
        guild_id=9999,  # T10: guild_id for combat_log
        tier=tier,
        classic_mode=classic_mode,
        bounty_cooldown_end=bounty_cooldown_end,
        active_ship=active_ship,
    )


def _make_active_bounty(
    bounty_id: int = 10,
    route: list | None = None,
    answer: str = "Sol",
    criminal_name: str = "Zara",
    checked: dict | None = None,
) -> SimpleNamespace:
    """Return a Bounty-like SimpleNamespace for check_bounty tests."""
    if route is None:
        route = ["Alpha", "Beta", "Gamma", "Sol", "Omega"]
    if checked is None:
        checked = {s: -1 for s in route}
    return SimpleNamespace(
        id=bounty_id,
        route=route,
        answer=answer,
        criminal_name=criminal_name,
        checked=checked,
        status="active",  # X3-bounty: _process_single_bounty_check now checks status under lock
    )


@pytest.mark.asyncio
async def test_check_bounty_player_not_found(service, mock_db):
    """Returns NOT_FOUND when player_id does not exist."""
    service.player_repo.get_by_id = AsyncMock(return_value=None)

    result = await service.check_bounty(mock_db, player_id=99, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.NOT_FOUND
    assert "Player not found" in result.message


@pytest.mark.asyncio
async def test_check_bounty_on_cooldown(service, mock_db):
    """Returns ON_COOLDOWN when player cooldown has not expired."""
    from datetime import UTC, datetime, timedelta

    future = datetime.now(UTC) + timedelta(seconds=120)
    player = _make_player(bounty_cooldown_end=future)
    service.player_repo.get_by_id = AsyncMock(return_value=player)

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.ON_COOLDOWN
    assert "cooldown" in result.message.lower()


@pytest.mark.asyncio
async def test_check_bounty_cooldown_expired(check_bounty_setup):
    """Proceeds normally when player cooldown has expired."""
    from datetime import UTC, datetime, timedelta

    service, mock_db = check_bounty_setup
    past = datetime.now(UTC) - timedelta(seconds=10)
    player = _make_player(bounty_cooldown_end=past)
    bounty = _make_active_bounty()
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty: lock before read
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="Beta", guild_id=1)

    # Cooldown expired → should proceed (not ON_COOLDOWN)
    assert result.result != CheckResult.ON_COOLDOWN


@pytest.mark.asyncio
async def test_check_bounty_not_found(check_bounty_setup):
    """Returns NOT_FOUND when system is not in any active bounty route."""
    service, mock_db = check_bounty_setup
    player = _make_player()
    bounty = _make_active_bounty()
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty: lock before read

    result = await service.check_bounty(mock_db, player_id=1, system_name="Nonexistent", guild_id=1)

    assert result.result == CheckResult.NOT_FOUND
    assert "not in any active bounty route" in result.message


@pytest.mark.asyncio
async def test_check_bounty_already_checked(check_bounty_setup):
    """Returns ALREADY_CHECKED when system was already checked by another player."""
    service, mock_db = check_bounty_setup
    player = _make_player(player_id=1)
    # Beta already checked by player 42
    bounty = _make_active_bounty(checked={"Alpha": -1, "Beta": 42, "Gamma": -1, "Sol": -1, "Omega": -1})
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty: lock before read

    result = await service.check_bounty(mock_db, player_id=1, system_name="Beta", guild_id=1)

    assert result.result == CheckResult.ALREADY_CHECKED
    assert result.bounty_id == bounty.id
    assert "already checked" in result.message


@pytest.mark.asyncio
async def test_check_bounty_incorrect(check_bounty_setup):
    """Returns INCORRECT when system is in route but not the answer."""
    service, mock_db = check_bounty_setup
    player = _make_player()
    bounty = _make_active_bounty(route=["Alpha", "Beta", "Gamma", "Sol", "Omega"], answer="Sol")
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty: lock before read
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="Alpha", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    assert result.bounty_id == bounty.id
    assert "No sign of" in result.message


@pytest.mark.asyncio
async def test_check_bounty_correct(check_bounty_setup):
    """Returns CORRECT when system matches the bounty answer (classic mode → auto-win bronze path)."""
    from services.bounty_service import RewardInfo

    service, mock_db = check_bounty_setup
    player = _make_player(classic_mode=True)  # auto-win, no combat
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {}
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty: lock before read
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1000, xp_earned=50, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.bounty_id == bounty.id
    # Bronze / classic mode: "captured" message
    assert "captured" in result.message.lower() or "cr" in result.message
    assert result.combat_won is True


@pytest.mark.asyncio
async def test_check_bounty_applies_cooldown(check_bounty_setup):
    """After a valid check, player.bounty_cooldown_end is set to a future time."""
    from datetime import UTC, datetime

    service, mock_db = check_bounty_setup
    player = _make_player()
    bounty = _make_active_bounty()
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty: lock before read
    service.bounty_repo.update.return_value = bounty

    before = datetime.now(UTC)
    await service.check_bounty(mock_db, player_id=1, system_name="Alpha", guild_id=1)

    assert player.bounty_cooldown_end is not None
    assert player.bounty_cooldown_end > before


@pytest.mark.asyncio
async def test_check_bounty_updates_checked_dict(check_bounty_setup):
    """After a valid check, bounty.checked is updated with the player's ID."""
    service, mock_db = check_bounty_setup
    player = _make_player(player_id=7)
    bounty = _make_active_bounty()
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty: lock before read
    service.bounty_repo.update.return_value = bounty

    await service.check_bounty(mock_db, player_id=7, system_name="Alpha", guild_id=1)

    assert bounty.checked["Alpha"] == 7


@pytest.mark.asyncio
async def test_check_bounty_proximity_hint(check_bounty_setup):
    """Returns proximity_hint=True when player checks within CLOSE_BOUNTY_THRESHOLD."""
    service, mock_db = check_bounty_setup
    player = _make_player()
    # route: Alpha(0) Beta(1) Gamma(2) Sol(3) Omega(4)
    # answer = Sol (idx 3); check Gamma (idx 2) → distance = 3 - 2 = 1 → hint
    bounty = _make_active_bounty(
        route=["Alpha", "Beta", "Gamma", "Sol", "Omega"],
        answer="Sol",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty: lock before read
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="Gamma", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    assert result.proximity_hint is True
    assert result.distance_to_answer == 1


@pytest.mark.asyncio
async def test_check_bounty_no_proximity_hint_far(service, mock_db):
    """Returns proximity_hint=False when player checks far from the answer."""
    player = _make_player()
    # route: Alpha(0) Beta(1) Gamma(2) Sol(3) Omega(4)
    # answer = Sol (idx 3); check Alpha (idx 0) → distance = 3 - 0 = 3 → NOT < 4, actually 3 < 4 is True
    # Use a route where distance >= threshold
    # route: A(0) B(1) C(2) D(3) E(4) F(5) Sol(6)
    # check A (idx 0): distance = 6 - 0 = 6 → NOT < 4 → no hint
    bounty = _make_active_bounty(
        route=["A", "B", "C", "D", "E", "F", "Sol"],
        answer="Sol",
    )
    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.get_by_id_for_update = AsyncMock(return_value=bounty)  # X3-bounty
    service.bounty_repo.update = AsyncMock(return_value=bounty)

    result = await service.check_bounty(mock_db, player_id=1, system_name="A", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    assert result.proximity_hint is False
    assert result.distance_to_answer == 6


@pytest.mark.asyncio
async def test_check_bounty_classic_mode_uses_bronze(service, mock_db):
    """Classic mode players always use the bronze division."""
    player = _make_player(tier="Gold", classic_mode=True)
    bounty = _make_active_bounty()
    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])
    service.bounty_repo.get_by_id_for_update = AsyncMock(return_value=bounty)  # X3-bounty
    service.bounty_repo.update = AsyncMock(return_value=bounty)

    await service.check_bounty(mock_db, player_id=1, system_name="Alpha", guild_id=1)

    # Should query bronze division regardless of player tier
    service.bounty_repo.get_active_by_guild_and_division.assert_called_once_with(mock_db, 1, "bronze")


@pytest.mark.asyncio
async def test_check_bounty_recently_spotted_when_1_stop_behind(service, mock_db):
    """Returns recently_spotted=True when checked system is 1 stop behind the answer."""
    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.get_by_id_for_update = AsyncMock()
    service.bounty_repo.update = AsyncMock()

    player = _make_player()
    # route: Alpha(0) Beta(1) Gamma(2) Sol(3)
    # answer = Sol (idx 3); check Gamma (idx 2) → distance = 3 - 2 = 1 → recently_spotted
    bounty = _make_active_bounty(
        route=["Alpha", "Beta", "Gamma", "Sol"],
        answer="Sol",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="Gamma", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    assert result.recently_spotted is True
    assert "recently spotted" in result.message.lower()


@pytest.mark.asyncio
async def test_check_bounty_recently_spotted_when_2_stops_behind(service, mock_db):
    """Returns recently_spotted=True when checked system is 2 stops behind the answer."""
    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.get_by_id_for_update = AsyncMock()
    service.bounty_repo.update = AsyncMock()

    player = _make_player()
    # route: A(0) B(1) C(2) Sol(3)
    # answer = Sol (idx 3); check B (idx 1) → distance = 3 - 1 = 2 → recently_spotted
    bounty = _make_active_bounty(
        route=["A", "B", "C", "Sol"],
        answer="Sol",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="B", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    assert result.recently_spotted is True


@pytest.mark.asyncio
async def test_check_bounty_not_recently_spotted_when_3_or_more_stops_behind(service, mock_db):
    """Returns recently_spotted=False when checked system is 3+ stops behind the answer."""
    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.get_by_id_for_update = AsyncMock()
    service.bounty_repo.update = AsyncMock()

    player = _make_player()
    # route: A(0) B(1) C(2) D(3) Sol(4)
    # answer = Sol (idx 4); check A (idx 0) → distance = 4 - 0 = 4 → NOT recently_spotted
    bounty = _make_active_bounty(
        route=["A", "B", "C", "D", "Sol"],
        answer="Sol",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="A", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    assert result.recently_spotted is False


@pytest.mark.asyncio
async def test_check_bounty_not_recently_spotted_when_ahead_of_answer(service, mock_db):
    """Returns recently_spotted=False when checked system is AHEAD of the answer on the route."""
    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.get_by_id_for_update = AsyncMock()
    service.bounty_repo.update = AsyncMock()

    player = _make_player()
    # route: A(0) Sol(1) B(2) C(3)
    # answer = Sol (idx 1); check B (idx 2) → distance = 1 - 2 = -1 → NOT recently_spotted
    bounty = _make_active_bounty(
        route=["A", "Sol", "B", "C"],
        answer="Sol",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="B", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    assert result.recently_spotted is False


@pytest.mark.asyncio
async def test_check_bounty_on_cooldown_includes_cooldown_until(service, mock_db):
    """Returns cooldown_until (Unix timestamp) when player is on cooldown."""
    from datetime import UTC, datetime, timedelta

    future = datetime.now(UTC) + timedelta(seconds=120)
    player = _make_player(bounty_cooldown_end=future)
    service.player_repo.get_by_id = AsyncMock(return_value=player)

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.ON_COOLDOWN
    assert result.cooldown_until is not None
    assert result.cooldown_until == int(future.timestamp())


@pytest.mark.asyncio
async def test_check_bounty_not_on_cooldown_cooldown_until_is_none(service, mock_db):
    """Returns cooldown_until=None when player is not on cooldown."""
    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.get_by_id_for_update = AsyncMock()
    service.bounty_repo.update = AsyncMock()

    player = _make_player(bounty_cooldown_end=None)
    bounty = _make_active_bounty()
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.get_by_id_for_update.return_value = bounty  # X3-bounty
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="Alpha", guild_id=1)

    assert result.result != CheckResult.ON_COOLDOWN
    assert result.cooldown_until is None


# ===========================================================================
# Tests: check_bounty — combat integration
# ===========================================================================


@pytest.fixture
def combat_integration_setup(service, mock_db):
    """Pre-configure common mocks for check_bounty combat integration tests.

    Returns (service, mock_db) with player_repo, bounty_repo methods, and
    combat_service pre-configured. Tests set .return_value on each.

    LoadoutBuilder.from_player is already mocked globally by the _mock_loadout_builder_from_player
    autouse fixture. Individual tests override this patch when they need a specific ship name.

    X3-bounty: get_by_id_for_update is now called inside _process_single_bounty_check.
    The service() fixture's smart side_effect auto-routes by bounty ID from whatever
    get_active_by_guild_and_division.return_value is set to.
    """
    service.player_repo.get_by_id = AsyncMock()
    # P6-T1: _build_payout_breakdown now calls get_by_ids (batched) instead of get_by_id.
    # Default to empty list so check_bounty tests that don't assert on payout content still pass.
    service.player_repo.get_by_ids = AsyncMock(return_value=[])
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()
    # T10: fight_ships is async — default stalemate (caller can override)
    _fs = SimpleNamespace(ship_name="Ship", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    service.combat_service.fight_ships = AsyncMock(
        return_value=SimpleNamespace(
            winner_name=None,
            loser_name=None,
            is_stalemate=True,
            ship1_stats=_fs,
            ship2_stats=_fs,
            combat_log_id=None,
            winner_side=None,
        )
    )
    return service, mock_db


@pytest.mark.asyncio
async def test_check_bounty_correct_classic_mode_auto_win(combat_integration_setup):
    """Classic mode players auto-win without combat; rewards are distributed (bronze path)."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    player = _make_player(classic_mode=True)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {}
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=500, xp_earned=25, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    # Bronze / classic mode: "captured" message with credits
    assert "captured" in result.message.lower() or "cr" in result.message
    service.calc_rewards.assert_called_once()
    service.distribute_rewards.assert_called_once()


@pytest.mark.asyncio
async def test_check_bounty_correct_no_ship_auto_win(combat_integration_setup):
    """Player with no active ship auto-wins without combat (bronze path, no ship)."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    player = _make_player(active_ship=None, classic_mode=False)  # tier="Bronze", no ship
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {}
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=500, xp_earned=25, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    # Bronze path: "captured" message — no combat bonus when no ship
    assert "captured" in result.message.lower() or "cr" in result.message
    assert result.bonus_won is False
    service.calc_rewards.assert_called_once()
    service.distribute_rewards.assert_called_once()


@pytest.mark.asyncio
async def test_check_bounty_correct_player_wins_combat(combat_integration_setup):
    """Bronze player with ship wins combat → rewards distributed + bonus_won=True."""
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=100)
    player = _make_player(active_ship=active_ship, classic_mode=False)  # tier="Bronze"
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Raider", "ship_armour": 80, "weapons": [], "turrets": []}

    _fight_stats1 = SimpleNamespace(ship_name="Betty", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    _fight_stats2 = SimpleNamespace(ship_name="Raider", raw_hp=80, raw_dps=0.0, varied_hp=80, varied_dps=0.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Betty",
        loser_name="Raider",
        is_stalemate=False,
        ship1_stats=_fight_stats1,
        ship2_stats=_fight_stats2,
        combat_log_id=None,
        winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=800, xp_earned=40, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Betty", base_armour=100)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    # Bronze path: captured + bonus
    assert "captured" in result.message.lower() or "cr" in result.message
    assert result.bonus_won is True
    service.calc_rewards.assert_called_once()
    service.distribute_rewards.assert_called_once()


@pytest.mark.asyncio
async def test_check_bounty_correct_bronze_player_loses_combat_still_captured(combat_integration_setup):
    """Bronze player who loses the optional combat still gets the base reward (auto-capture).

    On bronze, losing combat means bonus_won=False but capture still succeeds.
    The bounty is NOT reset (that only happens for Silver+).
    """
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=50)
    player = _make_player(active_ship=active_ship, classic_mode=False)  # tier="Bronze"
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Dreadnought",
        "ship_armour": 500,
        "weapons": [{"name": "Cannon", "dps": 99}],
        "turrets": [],
    }

    _fight_stats1 = SimpleNamespace(ship_name="Betty", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    _fight_stats2 = SimpleNamespace(
        ship_name="Dreadnought", raw_hp=500, raw_dps=99.0, varied_hp=499, varied_dps=99.0, ttk=None
    )
    mock_fight = SimpleNamespace(
        winner_name="Dreadnought",
        loser_name="Betty",
        is_stalemate=False,
        ship1_stats=_fight_stats1,
        ship2_stats=_fight_stats2,
        combat_log_id=None,
        winner_side=2,  # P2-T8b: criminal is side-2; criminal wins here
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=800, xp_earned=40, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True  # Bronze: always captured = win
    assert result.bonus_won is False  # But lost the optional bonus combat
    assert "captured" in result.message.lower() or "cr" in result.message
    service.calc_rewards.assert_called_once()
    service.distribute_rewards.assert_called_once()
    # No escape or reset for bronze
    service._award_combat_bonus.assert_not_called()


@pytest.mark.asyncio
async def test_check_bounty_correct_silver_player_loses_combat(combat_integration_setup):
    """Silver player loses combat → bounty checks are reset (not escaped), combat_won=False."""
    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=50)
    player = _make_player(active_ship=active_ship, classic_mode=False, tier="Silver")
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Dreadnought",
        "ship_armour": 500,
        "weapons": [{"name": "Cannon", "dps": 99}],
        "turrets": [],
    }

    _fight_stats1 = SimpleNamespace(ship_name="Betty", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    _fight_stats2 = SimpleNamespace(
        ship_name="Dreadnought", raw_hp=500, raw_dps=99.0, varied_hp=499, varied_dps=99.0, ttk=None
    )
    mock_fight = SimpleNamespace(
        winner_name="Dreadnought",
        loser_name="Betty",
        is_stalemate=False,
        ship1_stats=_fight_stats1,
        ship2_stats=_fight_stats2,
        combat_log_id=None,
        winner_side=2,  # P2-T8b: criminal is side-2; criminal wins here
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service._reset_bounty_checks = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is False
    assert "defeated" in result.message.lower() or "escaped" in result.message.lower()
    service._reset_bounty_checks.assert_called_once_with(mock_db, bounty)


@pytest.mark.asyncio
async def test_check_bounty_correct_stalemate_counts_as_win(combat_integration_setup):
    """Stalemate result counts as player win (legacy behavior) — bronze bonus path."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=100)
    player = _make_player(active_ship=active_ship, classic_mode=False)  # tier="Bronze"
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Raider", "ship_armour": 100, "weapons": [], "turrets": []}

    _fight_stats1 = SimpleNamespace(ship_name="Betty", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    _fight_stats2 = SimpleNamespace(
        ship_name="Raider", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None
    )
    mock_fight = SimpleNamespace(
        winner_name=None,
        loser_name=None,
        is_stalemate=True,
        ship1_stats=_fight_stats1,
        ship2_stats=_fight_stats2,
        combat_log_id=None,
        winner_side=None,  # P2-T8b: stalemate has no winner side
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=600, xp_earned=30, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    # Bronze: stalemate → bonus_won=True (stalemate counts as win for bonus)
    assert result.bonus_won is True
    assert "captured" in result.message.lower() or "cr" in result.message


@pytest.mark.asyncio
async def test_check_bounty_correct_no_criminal_ship_data(combat_integration_setup):
    """Graceful handling when criminal_ship is None (empty loadout used) — bronze path."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=100)
    player = _make_player(active_ship=active_ship, classic_mode=False)  # tier="Bronze"
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = None  # no ship data

    # With both sides having 0 DPS, result is a stalemate → player wins bonus
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=500, xp_earned=25, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    # Should not raise — graceful handling of None criminal_ship
    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    # Bronze path: always captured (combat_won = True for capture)
    assert result.combat_won is True


@pytest.mark.asyncio
async def test_check_bounty_correct_combat_with_full_criminal_loadout(combat_integration_setup):
    """Criminal with weapons and turrets builds a correct loadout for combat (bronze bonus path)."""
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Falcon", armour=200)
    player = _make_player(active_ship=active_ship, classic_mode=False)  # tier="Bronze"
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Bandit",
        "ship_armour": 150,
        "weapons": [{"name": "Blaster MK I", "dps": 15.0}, {"name": "Laser MK I", "dps": 10.0}],
        "turrets": [{"name": "Turret MK I", "dps": 5.0}],
    }

    # Capture loadouts passed to fight_ships
    captured_loadouts = {}
    _fight_stats1 = SimpleNamespace(
        ship_name="Falcon", raw_hp=200, raw_dps=0.0, varied_hp=200, varied_dps=0.0, ttk=None
    )
    _fight_stats2 = SimpleNamespace(
        ship_name="Bandit", raw_hp=150, raw_dps=30.0, varied_hp=148, varied_dps=30.0, ttk=None
    )

    async def capture_fight(p_loadout, c_loadout, **kwargs):
        captured_loadouts["player"] = p_loadout
        captured_loadouts["criminal"] = c_loadout
        return SimpleNamespace(
            winner_name="Falcon",
            loser_name="Bandit",
            is_stalemate=False,
            ship1_stats=_fight_stats1,
            ship2_stats=_fight_stats2,
            combat_log_id=None,
            winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
        )

    service.combat_service.fight_ships = capture_fight

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1200, xp_earned=60, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    player_loadout_mock = ShipLoadout(ship_name="Falcon", base_armour=200)
    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=player_loadout_mock),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    assert result.bonus_won is True  # Falcon won against Bandit
    # Verify criminal loadout was built correctly from JSONB data
    criminal_lo = captured_loadouts["criminal"]
    assert criminal_lo.ship_name == "Bandit"
    assert criminal_lo.base_armour == 150
    assert len(criminal_lo.weapons) == 2
    assert len(criminal_lo.turrets) == 1
    assert criminal_lo.weapons[0].dps == 15.0
    assert criminal_lo.turrets[0].name == "Turret MK I"


# ===========================================================================
# Helpers for reward tests
# ===========================================================================


def _make_reward_bounty(
    reward: int = 10000,
    reward_per_sys: int = 1000,
    route: list | None = None,
    answer: str = "Sol",
    checked: dict | None = None,
) -> SimpleNamespace:
    """Return a Bounty-like SimpleNamespace for reward tests."""
    if route is None:
        route = ["Alpha", "Beta", "Gamma", "Sol", "Omega"]
    if checked is None:
        checked = {s: -1 for s in route}
    return SimpleNamespace(
        reward=reward,
        reward_per_sys=reward_per_sys,
        route=route,
        answer=answer,
        checked=checked,
    )


# ===========================================================================
# Tests: calc_rewards
# ===========================================================================


@pytest.mark.asyncio
async def test_calc_rewards_single_winner_only(service, mock_db):
    """One player checked the answer system; no non-winners → winner gets full pool.

    Under the winner-reserve model with no consolation deductions:
    winner_reserve = int(10000 * 0.25) = 2500
    consolation_pool = 7500 (no deductions)
    winner credits = 2500 + 7500 = 10000
    """
    bounty = _make_reward_bounty(
        reward=10000,
        reward_per_sys=1000,
        route=["Alpha", "Beta", "Sol"],
        answer="Sol",
        checked={"Alpha": -1, "Beta": -1, "Sol": 42},
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    assert len(rewards) == 1
    winner = rewards[0]
    assert winner.player_id == 42
    assert winner.is_winner is True
    assert winner.credits_earned == 10000  # reserve + full consolation (no deductions)
    assert winner.systems_checked_count == 1


@pytest.mark.asyncio
async def test_calc_rewards_multi_contributor(service, mock_db):
    """3 players checked systems; non-winners get rps*count (XP=0); winner gets reserve + remaining.

    reward=5000, rps=1000
    winner_reserve = int(5000 * 0.25) = 1250
    consolation_pool = 3750
    p1: 1 check → 1000 credits, XP=0, consolation_pool→2750
    p2: 2 checks → 2000 credits, XP=0, consolation_pool→750
    p3 (winner): 1250 + 750 = 2000, xp=int(2000*0.1)=200
    """
    bounty = _make_reward_bounty(
        reward=5000,
        reward_per_sys=1000,
        route=["Alpha", "Beta", "Gamma", "Sol", "Omega"],
        answer="Sol",
        checked={"Alpha": 1, "Beta": 2, "Gamma": 2, "Sol": 3, "Omega": -1},
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    assert len(rewards) == 3

    by_id = {r.player_id: r for r in rewards}

    assert by_id[1].credits_earned == 1000
    assert by_id[1].is_winner is False
    assert by_id[1].xp_earned == 0  # Failed checkers earn no XP
    assert by_id[1].systems_checked_count == 1

    assert by_id[2].credits_earned == 2000
    assert by_id[2].is_winner is False
    assert by_id[2].xp_earned == 0  # Failed checkers earn no XP
    assert by_id[2].systems_checked_count == 2

    assert by_id[3].credits_earned == 2000  # winner_reserve(1250) + remaining_consolation(750)
    assert by_id[3].is_winner is True
    assert by_id[3].xp_earned == int(2000 * 0.1)  # XP on full winner payout
    assert by_id[3].systems_checked_count == 1


@pytest.mark.asyncio
async def test_calc_rewards_no_contributors(service, mock_db):
    """No systems checked → empty reward list."""
    bounty = _make_reward_bounty(
        reward=5000,
        reward_per_sys=1000,
        route=["Alpha", "Beta", "Sol"],
        answer="Sol",
        checked={"Alpha": -1, "Beta": -1, "Sol": -1},
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    assert rewards == []


@pytest.mark.asyncio
async def test_calc_rewards_failed_checkers_xp_is_zero(service, mock_db):
    """Non-winning (failed) checkers receive credits but XP = 0.

    AC: Failed checkers: XP = 0 (no XP for missed checks).
    reward=10000, rps=1000
    winner_reserve = int(10000 * 0.25) = 2500
    consolation_pool = 7500
    p1 (non-winner): credits=1000, xp=0
    p2 (winner): 2500 + 6500 = 9000, xp=900
    """
    bounty = _make_reward_bounty(
        reward=10000,
        reward_per_sys=1000,
        route=["Alpha", "Sol"],
        answer="Sol",
        checked={"Alpha": 1, "Sol": 2},
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    by_id = {r.player_id: r for r in rewards}

    # Failed checker: gets credits but XP = 0
    assert by_id[1].credits_earned == 1000
    assert by_id[1].xp_earned == 0, "Failed checkers must earn XP=0"
    assert by_id[1].is_winner is False

    # Winner: gets reserve + remaining consolation, XP on full payout
    assert by_id[2].credits_earned == 9000  # 2500 + (7500 - 1000) = 2500 + 6500
    assert by_id[2].xp_earned == int(9000 * 0.1)
    assert by_id[2].is_winner is True


@pytest.mark.asyncio
async def test_calc_rewards_winner_xp_on_full_payout(service, mock_db):
    """Winner XP is computed on the full winner credits (reserve + remaining consolation).

    AC: Winner: xp_earned = int(total_winner_credits * BOUNTY_REWARD_TO_XP_GAIN_MULT)
    reward=8000, rps=500
    winner_reserve = int(8000 * 0.25) = 2000
    consolation_pool = 6000
    no non-winners, so winner gets 2000 + 6000 = 8000
    xp = int(8000 * 0.1) = 800
    """
    bounty = _make_reward_bounty(
        reward=8000,
        reward_per_sys=500,
        route=["A", "B", "Sol"],
        answer="Sol",
        checked={"A": -1, "B": -1, "Sol": 99},
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    assert len(rewards) == 1
    winner = rewards[0]
    assert winner.player_id == 99
    assert winner.is_winner is True
    assert winner.credits_earned == 8000
    assert winner.xp_earned == int(8000 * 0.1), "Winner XP must be on full winner payout"


@pytest.mark.asyncio
async def test_calc_rewards_xp_calculation(service, mock_db):
    """XP: failed checkers get 0; winner gets int(credits * BOUNTY_REWARD_TO_XP_GAIN_MULT).

    Supersedes the old test where all contributors got XP.
    reward=10000, rps=1000
    winner_reserve = 2500, consolation_pool = 7500
    p1 (checker): 1000 credits, xp=0
    p2 (winner): 2500 + (7500-1000) = 9000 credits, xp=900
    """
    bounty = _make_reward_bounty(
        reward=10000,
        reward_per_sys=1000,
        route=["Alpha", "Sol"],
        answer="Sol",
        checked={"Alpha": 1, "Sol": 2},
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    by_id = {r.player_id: r for r in rewards}

    # Failed checker: credits but XP = 0
    assert by_id[1].credits_earned == 1000
    assert by_id[1].xp_earned == 0

    # Winner: reserve + remaining consolation, XP on full amount
    assert by_id[2].credits_earned == 9000
    assert by_id[2].xp_earned == int(9000 * 0.1)


@pytest.mark.asyncio
async def test_calc_rewards_winner_gets_reserve_plus_remaining_consolation(service, mock_db):
    """Winner receives winner_reserve + remaining consolation (not the full original pool).

    reward=3000, rps=500
    winner_reserve = int(3000 * 0.25) = 750
    consolation_pool = 2250
    p1 (contributor): 2 checks → min(1000, 2250) = 1000, consolation→1250, xp=0
    p2 (winner): 750 + 1250 = 2000, xp=int(2000*0.1)=200
    Total distributed: 1000 + 2000 = 3000 ✓
    """
    bounty = _make_reward_bounty(
        reward=3000,
        reward_per_sys=500,
        route=["A", "B", "C", "Sol"],
        answer="Sol",
        checked={"A": 1, "B": 1, "C": -1, "Sol": 2},
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    by_id = {r.player_id: r for r in rewards}
    contributor_total = sum(r.credits_earned for r in rewards if not r.is_winner)
    winner_credits = by_id[2].credits_earned

    assert contributor_total + winner_credits == 3000
    assert winner_credits == 2000
    assert by_id[1].xp_earned == 0  # No XP for failed checker
    assert by_id[2].xp_earned == int(2000 * 0.1)


@pytest.mark.asyncio
async def test_calc_rewards_worst_case_gendol_ethor(service, mock_db):
    """Worst-case scenario: all non-answer systems checked before winner captures.

    AC verification: Gendol Ethor scenario
    reward=7635, route_len=5, factor=0.25
    winner_reserve = int(7635 * 0.25) = 1908
    consolation_pool = 5727
    reward_per_sys = 5727 // 5 = 1145 (as stored on bounty)

    4 wrong systems checked by p2 (4 * 1145 = 4580 consumed):
    consolation_pool after deductions = 5727 - 4580 = 1147
    winner (p1): 1908 + 1147 = 3055
    checker (p2): 4580, xp=0
    """
    bounty = _make_reward_bounty(
        reward=7635,
        reward_per_sys=1145,  # floor(5727 / 5)
        route=["Sys1", "Sys2", "Sys3", "Sys4", "Answer"],
        answer="Answer",
        checked={"Sys1": 2, "Sys2": 2, "Sys3": 2, "Sys4": 2, "Answer": 1},  # p2 checked 4 wrong
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    by_id = {r.player_id: r for r in rewards}

    # Non-winner checker (p2): 4 * 1145 = 4580 credits, xp=0
    assert by_id[2].credits_earned == 4580
    assert by_id[2].xp_earned == 0

    # Winner (p1): 1908 + 1147 = 3055
    assert by_id[1].credits_earned == 3055
    assert by_id[1].is_winner is True
    # Total must equal original reward
    assert by_id[1].credits_earned + by_id[2].credits_earned == 7635


@pytest.mark.asyncio
async def test_calc_rewards_consolation_pool_never_goes_below_zero(service, mock_db):
    """Cap: consolation_pool never goes below 0 even if many systems checked.

    reward=1000, rps=400, winner_reserve=250, consolation_pool=750
    p2 checks 2 systems: would deduct 800 but pool only has 750 → capped at 750
    winner (p1): 250 + 0 = 250
    checker (p2): 750 (capped), xp=0
    total = 250 + 750 = 1000 ✓
    """
    bounty = _make_reward_bounty(
        reward=1000,
        reward_per_sys=400,
        route=["A", "B", "Sol"],
        answer="Sol",
        checked={"A": 2, "B": 2, "Sol": 1},  # p2 checked 2 systems
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    by_id = {r.player_id: r for r in rewards}

    # winner_reserve = int(1000 * 0.25) = 250
    # consolation_pool = 750
    # p2: 2 * 400 = 800 but capped at 750
    assert by_id[2].credits_earned == 750
    assert by_id[2].xp_earned == 0

    # winner: 250 + 0 = 250
    assert by_id[1].credits_earned == 250
    assert by_id[1].is_winner is True

    # Total must equal original reward
    assert by_id[1].credits_earned + by_id[2].credits_earned == 1000


# ===========================================================================
# distribute_rewards tests moved to integration — see
# tests/integration/test_bounty_service_integration.py
# Rationale: distribute_rewards mutates ORM player.credits / xp / bounty_wins
# via player_repo.get_by_id + direct attribute assignment + db.commit(); the
# identity-map behaviour can only be verified against a real SQLite session.
# ===========================================================================


# ===========================================================================
# Tests: expire_bounty
# ===========================================================================


def _make_expiry_bounty(
    bounty_id: int = 1,
    status: str = "active",
    criminal_name: str = "Test Criminal",
    route: list | None = None,
) -> SimpleNamespace:
    """Return a Bounty-like SimpleNamespace for expiry/escape/respawn tests."""
    if route is None:
        route = ["Alpha", "Beta", "Gamma", "Sol"]
    return SimpleNamespace(
        id=bounty_id,
        status=status,
        criminal_name=criminal_name,
        route=route,
        answer="Sol",
        checked={s: -1 for s in route},
        escape_count=0,
        respawn_time=None,
        end_time=None,
        guild_id=1,
        division="bronze",
        reward=1000,
        reward_per_sys=250,
        tech_level=3,
        criminal_faction="terran",
        criminal_ship={},
    )


@pytest.mark.asyncio
async def test_expire_bounty_success(service, mock_db):
    """An active bounty's status is set to 'expired'."""
    bounty = _make_expiry_bounty(status="active")
    service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
    service.bounty_repo.update = AsyncMock(return_value=bounty)

    result = await service.expire_bounty(mock_db, bounty_id=1)

    assert result is not None
    assert result.status == "expired"
    service.bounty_repo.update.assert_called_once()


@pytest.mark.asyncio
async def test_expire_bounty_not_found(service, mock_db):
    """Returns None when the bounty does not exist."""
    service.bounty_repo.get_by_id = AsyncMock(return_value=None)

    result = await service.expire_bounty(mock_db, bounty_id=999)

    assert result is None
    service.bounty_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_expire_bounty_already_expired(service, mock_db):
    """Returns None when bounty status is not 'active'."""
    bounty = _make_expiry_bounty(status="expired")
    service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)

    result = await service.expire_bounty(mock_db, bounty_id=1)

    assert result is None
    service.bounty_repo.update.assert_not_called()


# ===========================================================================
# Tests: escape_bounty
# ===========================================================================


@pytest.mark.asyncio
async def test_escape_bounty_success(service, mock_db):
    """An active bounty's status is set to 'escaped' and escape_count incremented."""
    bounty = _make_expiry_bounty(status="active")
    service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
    service.bounty_repo.update = AsyncMock(return_value=bounty)

    result_bounty, _respawn_delay = await service.escape_bounty(mock_db, bounty_id=1)

    assert result_bounty is not None
    assert result_bounty.status == "escaped"
    assert result_bounty.escape_count == 1
    service.bounty_repo.update.assert_called_once()


@pytest.mark.asyncio
async def test_escape_bounty_respawn_delay(service, mock_db):
    """Respawn delay equals the number of systems in the route."""
    route = ["Alpha", "Beta", "Gamma", "Sol"]
    bounty = _make_expiry_bounty(status="active", route=route)
    service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
    service.bounty_repo.update = AsyncMock(return_value=bounty)

    _, respawn_delay = await service.escape_bounty(mock_db, bounty_id=1)

    assert respawn_delay == len(route)


@pytest.mark.asyncio
async def test_escape_bounty_not_found(service, mock_db):
    """Returns (None, 0) when the bounty does not exist."""
    service.bounty_repo.get_by_id = AsyncMock(return_value=None)

    result_bounty, respawn_delay = await service.escape_bounty(mock_db, bounty_id=999)

    assert result_bounty is None
    assert respawn_delay == 0
    service.bounty_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_escape_bounty_sets_respawn_time(service, mock_db):
    """respawn_time is set on the bounty after escape."""
    bounty = _make_expiry_bounty(status="active")
    assert bounty.respawn_time is None

    service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
    service.bounty_repo.update = AsyncMock(return_value=bounty)

    result_bounty, _respawn_delay = await service.escape_bounty(mock_db, bounty_id=1)

    assert result_bounty is not None
    assert result_bounty.respawn_time is not None


# ===========================================================================
# Tests: respawn_bounty
# ===========================================================================


@pytest.fixture
def respawn_service(service) -> BountyService:
    """Return a BountyService with graph/pathfinding mocks pre-attached for respawn tests."""
    service.graph_service = MagicMock()
    service.graph_service.load_graph = AsyncMock()
    service.graph_service.get_systems_with_jump_gates = MagicMock(
        return_value=["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
    )
    service.pathfinding_service = MagicMock()
    service.pathfinding_service.make_route = MagicMock(return_value=["Delta", "Epsilon", "Alpha"])
    return service


@pytest.mark.asyncio
async def test_respawn_bounty_success(respawn_service, mock_db):
    """An escaped bounty gets a new route/answer and status is reset to 'active'."""
    bounty = _make_expiry_bounty(status="escaped")
    respawn_service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
    respawn_service.bounty_repo.update = AsyncMock(return_value=bounty)

    result = await respawn_service.respawn_bounty(mock_db, bounty_id=1)

    assert result is not None
    assert result.status == "active"
    assert result.route == ["Delta", "Epsilon", "Alpha"]
    assert result.answer in result.route
    respawn_service.bounty_repo.update.assert_called_once()


@pytest.mark.asyncio
async def test_respawn_bounty_not_escaped(respawn_service, mock_db):
    """Returns None when bounty status is not 'escaped'."""
    bounty = _make_expiry_bounty(status="active")
    respawn_service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)

    result = await respawn_service.respawn_bounty(mock_db, bounty_id=1)

    assert result is None
    respawn_service.bounty_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_respawn_bounty_resets_checked(respawn_service, mock_db):
    """All systems in the new route have a checked value of -1."""
    bounty = _make_expiry_bounty(status="escaped")
    # Simulate some prior checks
    bounty.checked = {"Alpha": 42, "Beta": -1, "Gamma": 7, "Sol": 99}

    respawn_service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
    respawn_service.bounty_repo.update = AsyncMock(return_value=bounty)

    result = await respawn_service.respawn_bounty(mock_db, bounty_id=1)

    assert result is not None
    assert all(v == -1 for v in result.checked.values())
    # All keys should be from the new route
    assert set(result.checked.keys()) == set(result.route)


@pytest.mark.asyncio
async def test_respawn_bounty_keeps_criminal(respawn_service, mock_db):
    """criminal_name is unchanged after respawn."""
    bounty = _make_expiry_bounty(status="escaped", criminal_name="Big Boss")
    respawn_service.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
    respawn_service.bounty_repo.update = AsyncMock(return_value=bounty)

    result = await respawn_service.respawn_bounty(mock_db, bounty_id=1)

    assert result is not None
    assert result.criminal_name == "Big Boss"


# ===========================================================================
# Tests: clear_bounties
# ===========================================================================


class TestClearBounties:
    """Tests for BountyService.clear_bounties."""

    @pytest.fixture
    def clear_service(self, mock_db):
        """BountyService with mock repos for clearing tests."""
        from services.bounty_service import BountyService

        svc = BountyService()
        svc.bounty_repo = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_clear_all_tiers_returns_summary(self, clear_service, mock_db):
        """clear_bounties returns correct summary for all tiers."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[1, 2, 3])

        result = await clear_service.clear_bounties(mock_db, guild_id=555)

        assert result["guild_id"] == 555
        assert result["tier"] is None
        assert result["cleared_count"] == 3
        assert result["bounty_ids"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_clear_specific_tier(self, clear_service, mock_db):
        """clear_bounties filters by tier."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[5])

        result = await clear_service.clear_bounties(mock_db, guild_id=555, tier="bronze")

        assert result["tier"] == "bronze"
        assert result["cleared_count"] == 1
        clear_service.bounty_repo.clear_active_by_guild.assert_awaited_once_with(mock_db, 555, "bronze")

    @pytest.mark.asyncio
    async def test_clear_no_active_bounties(self, clear_service, mock_db):
        """clear_bounties returns zero counts when no active bounties exist."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[])

        result = await clear_service.clear_bounties(mock_db, guild_id=999)

        assert result["cleared_count"] == 0
        assert result["bounty_ids"] == []
        assert result["announcements_deleted"] == 0

    @pytest.mark.asyncio
    async def test_clear_bounties_calls_gateway_delete_for_each_message(self, clear_service, mock_db):
        """Bug 5: clear_bounties calls discord-gateway DELETE for each Discord message."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[10, 11])

        # Mock the discord message repo to return a message record
        mock_msg = MagicMock()
        mock_msg.message_id = 999888777
        mock_msg.channel_id = 111222333

        mock_msg_repo = AsyncMock()
        mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=mock_msg)
        mock_msg_repo.delete_by_guild_type_and_reference = AsyncMock(return_value=True)

        captured_deletes: list[str] = []

        class MockHttpxResponse:
            status_code = 200

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def delete(self, url, timeout=10):
                captured_deletes.append(url)
                return MockHttpxResponse()

        # DiscordMessageRepository is imported inside the function body via a deferred
        # import. Patch it at the source module so that DiscordMessageRepository()
        # returns our mock instance.
        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=mock_msg_repo,
            ),
            patch("httpx.AsyncClient", MockAsyncClient),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555)

        # Two bounty IDs → two gateway DELETE calls
        assert len(captured_deletes) == 2
        assert all("999888777" in url for url in captured_deletes)
        assert result["announcements_deleted"] == 2

    @pytest.mark.asyncio
    async def test_clear_bounties_gateway_failure_is_non_fatal(self, clear_service, mock_db):
        """Bug 5: If gateway DELETE fails, clear_bounties still completes and deletes DB records."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[20])

        mock_msg = MagicMock()
        mock_msg.message_id = 777666555

        mock_msg_repo = AsyncMock()
        mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=mock_msg)
        mock_msg_repo.delete_by_guild_type_and_reference = AsyncMock(return_value=True)

        class FailingAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def delete(self, url, timeout=10):
                raise ConnectionError("Gateway unreachable")

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=mock_msg_repo,
            ),
            patch("httpx.AsyncClient", FailingAsyncClient),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555)

        # Gateway failed but DB record should still be deleted
        mock_msg_repo.delete_by_guild_type_and_reference.assert_awaited_once()
        assert result["cleared_count"] == 1

    @pytest.mark.asyncio
    async def test_clear_bounties_no_message_record_skips_gateway(self, clear_service, mock_db):
        """Bug 5: When there is no Discord message record, gateway DELETE is not called."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[30])

        mock_msg_repo = AsyncMock()
        mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=None)
        mock_msg_repo.delete_by_guild_type_and_reference = AsyncMock(return_value=False)

        deleted_urls: list[str] = []

        class TrackingAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def delete(self, url, timeout=10):
                deleted_urls.append(url)
                return MagicMock(status_code=200)

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=mock_msg_repo,
            ),
            patch("httpx.AsyncClient", TrackingAsyncClient),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555)

        # No message record → no gateway call
        assert len(deleted_urls) == 0
        assert result["cleared_count"] == 1


# ===========================================================================
# Tests: clear_bounties scheduler-job cleanup (A.11 regression coverage)
# ===========================================================================


class _SchedulerMockAsyncClient:
    """Reusable httpx.AsyncClient stand-in driven by a scripted job list.

    The test supplies ``jobs_by_host`` (keyed by URL prefix) or a simple
    ``jobs`` list via the ``scripted`` attribute that the fixture sets
    before instantiation.
    """

    scripted_jobs: list = []
    delete_status_map: dict = {}
    deleted_ids: list = []
    deleted_urls: list = []
    list_url: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, timeout=10):
        _SchedulerMockAsyncClient.list_url = url
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=list(_SchedulerMockAsyncClient.scripted_jobs))
        return resp

    async def delete(self, url, timeout=10):
        _SchedulerMockAsyncClient.deleted_urls.append(url)
        # Extract the job ID from the tail of the URL.
        job_id = url.rsplit("/", 1)[-1]
        _SchedulerMockAsyncClient.deleted_ids.append(job_id)
        status = _SchedulerMockAsyncClient.delete_status_map.get(job_id, 200)
        resp = MagicMock()
        resp.status_code = status
        return resp


def _reset_scheduler_mock(jobs, delete_status_map=None):
    """Reset the class-level state on the scheduler mock."""
    _SchedulerMockAsyncClient.scripted_jobs = list(jobs)
    _SchedulerMockAsyncClient.delete_status_map = delete_status_map or {}
    _SchedulerMockAsyncClient.deleted_ids = []
    _SchedulerMockAsyncClient.deleted_urls = []
    _SchedulerMockAsyncClient.list_url = None


class TestClearBountiesSchedulerCleanup:
    """A.11 regression: clear_bounties removes orphaned bounty_expire +
    bounty_respawn scheduler jobs linked to the cleared bounties.

    These tests mock only the outbound HTTP client and the discord-message
    repository (the two existing HTTP boundaries). Everything else uses
    real objects so the orchestration logic is genuinely exercised.
    """

    @pytest.fixture
    def clear_service(self, mock_db):
        """BountyService with mock bounty_repo for clear_bounties tests."""
        from services.bounty_service import BountyService

        svc = BountyService()
        svc.bounty_repo = AsyncMock()
        return svc

    @pytest.fixture
    def no_messages_repo(self):
        """DiscordMessageRepository stand-in that reports no announcements."""
        repo = AsyncMock()
        repo.get_by_guild_type_and_reference = AsyncMock(return_value=None)
        repo.delete_by_guild_type_and_reference = AsyncMock(return_value=False)
        return repo

    @pytest.mark.asyncio
    async def test_clear_bounties_deletes_linked_expire_jobs(self, clear_service, mock_db, no_messages_repo):
        """3 cleared bounties → 3 matching bounty_expire jobs → 3 DELETEs."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[10, 11, 12])

        jobs = [
            {"id": "job-A", "args": [None, {"job_type": "bounty_expire", "bounty_id": 10}]},
            {"id": "job-B", "args": [None, {"job_type": "bounty_expire", "bounty_id": 11}]},
            {"id": "job-C", "args": [None, {"job_type": "bounty_expire", "bounty_id": 12}]},
        ]
        _reset_scheduler_mock(jobs)

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=no_messages_repo,
            ),
            patch("httpx.AsyncClient", _SchedulerMockAsyncClient),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555)

        assert result["scheduler_jobs_deleted"] == 3
        assert sorted(_SchedulerMockAsyncClient.deleted_ids) == ["job-A", "job-B", "job-C"]

    @pytest.mark.asyncio
    async def test_clear_bounties_deletes_linked_respawn_jobs(self, clear_service, mock_db, no_messages_repo):
        """Q1=B: both bounty_expire AND bounty_respawn jobs are removed."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[20, 21])

        jobs = [
            {"id": "exp-1", "args": [None, {"job_type": "bounty_expire", "bounty_id": 20}]},
            {"id": "exp-2", "args": [None, {"job_type": "bounty_expire", "bounty_id": 21}]},
            {"id": "rsp-1", "args": [None, {"job_type": "bounty_respawn", "bounty_id": 20}]},
            {"id": "rsp-2", "args": [None, {"job_type": "bounty_respawn", "bounty_id": 21}]},
        ]
        _reset_scheduler_mock(jobs)

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=no_messages_repo,
            ),
            patch("httpx.AsyncClient", _SchedulerMockAsyncClient),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555)

        assert result["scheduler_jobs_deleted"] == 4
        assert set(_SchedulerMockAsyncClient.deleted_ids) == {"exp-1", "exp-2", "rsp-1", "rsp-2"}

    @pytest.mark.asyncio
    async def test_clear_bounties_ignores_unrelated_scheduler_jobs(self, clear_service, mock_db, no_messages_repo):
        """Jobs of unrelated types or with non-matching bounty_ids are left alone."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[100])

        jobs = [
            {"id": "refresh", "args": [None, {"job_type": "shop_refresh", "guild_id": 555}]},
            {"id": "time", "args": [None, {"job_type": "time_announcement", "guild_id": 555}]},
            # bounty_expire but a DIFFERENT bounty — must not be deleted
            {"id": "other-bounty", "args": [None, {"job_type": "bounty_expire", "bounty_id": 9999}]},
            # Missing args shape — must be tolerated
            {"id": "malformed", "args": []},
        ]
        _reset_scheduler_mock(jobs)

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=no_messages_repo,
            ),
            patch("httpx.AsyncClient", _SchedulerMockAsyncClient),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555)

        assert result["scheduler_jobs_deleted"] == 0
        assert _SchedulerMockAsyncClient.deleted_ids == []

    @pytest.mark.asyncio
    async def test_clear_bounties_ignores_already_fired_jobs_404(self, clear_service, mock_db, no_messages_repo):
        """A DELETE that returns 404 is treated as already-fired, not an error.

        In addition to the count check, this test asserts the 404 path is
        log-SILENT at WARNING/ERROR level: an already-fired job is an
        expected outcome, not a fault.
        """
        from services import bounty_service as bounty_service_module

        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[30, 31])

        jobs = [
            {"id": "live", "args": [None, {"job_type": "bounty_expire", "bounty_id": 30}]},
            {"id": "stale", "args": [None, {"job_type": "bounty_expire", "bounty_id": 31}]},
        ]
        _reset_scheduler_mock(jobs, delete_status_map={"stale": 404})

        # Replace only warning/error so we can assert no spurious emissions
        # are produced by the 404 path. The module-level ``flogger`` is
        # already a MagicMock (see tests/conftest.py), so scoped attribute
        # replacement gives us a clean call ledger.
        warning_mock = MagicMock()
        error_mock = MagicMock()
        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=no_messages_repo,
            ),
            patch("httpx.AsyncClient", _SchedulerMockAsyncClient),
            patch.object(bounty_service_module.flogger, "warning", warning_mock),
            patch.object(bounty_service_module.flogger, "error", error_mock),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555)

        # Only the 200 counts; the 404 is silent-success.
        assert result["scheduler_jobs_deleted"] == 1
        assert set(_SchedulerMockAsyncClient.deleted_ids) == {"live", "stale"}

        # 404-specific silence: no WARNING or ERROR should reference the
        # stale job or its 404 status.  (We do NOT assert zero warnings
        # overall, because clear_bounties may emit unrelated warnings
        # under exotic test states; we scope the check to the 404 path.)
        def _mentions_404_path(call) -> bool:
            if not call.args:
                return False
            msg = call.args[0] if isinstance(call.args[0], str) else ""
            return "404" in msg or ("stale" in msg and "scheduler" in msg.lower())

        offending_warnings = [c for c in warning_mock.call_args_list if _mentions_404_path(c)]
        offending_errors = [c for c in error_mock.call_args_list if _mentions_404_path(c)]
        assert offending_warnings == [], f"404 path must be log-silent at WARNING; got: {offending_warnings}"
        assert offending_errors == [], f"404 path must be log-silent at ERROR; got: {offending_errors}"

    @pytest.mark.asyncio
    async def test_clear_bounties_graceful_when_scheduler_down(self, clear_service, mock_db, no_messages_repo):
        """If the scheduler API is unreachable, the DB clear still succeeds."""
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[40])

        class UnreachableClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, timeout=10):
                raise ConnectionError("scheduler unreachable")

            async def delete(self, url, timeout=10):
                raise AssertionError("delete must not run when list fails")

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=no_messages_repo,
            ),
            patch("httpx.AsyncClient", UnreachableClient),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555)

        assert result["cleared_count"] == 1
        assert result["scheduler_jobs_deleted"] == 0

    @pytest.mark.asyncio
    async def test_clear_bounties_tier_filter_only_deletes_matching_tier_jobs(
        self, clear_service, mock_db, no_messages_repo
    ):
        """tier='bronze' clears only bronze bounties; only those jobs get deleted."""
        # Repository filters to bronze-only (bounties 1 and 2).
        clear_service.bounty_repo.clear_active_by_guild = AsyncMock(return_value=[1, 2])

        jobs = [
            {"id": "b1", "args": [None, {"job_type": "bounty_expire", "bounty_id": 1}]},
            {"id": "b2", "args": [None, {"job_type": "bounty_expire", "bounty_id": 2}]},
            # bounty 3 is silver — not in bronze clear set; must not be deleted.
            {"id": "s3", "args": [None, {"job_type": "bounty_expire", "bounty_id": 3}]},
        ]
        _reset_scheduler_mock(jobs)

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=no_messages_repo,
            ),
            patch("httpx.AsyncClient", _SchedulerMockAsyncClient),
        ):
            result = await clear_service.clear_bounties(mock_db, guild_id=555, tier="bronze")

        assert result["tier"] == "bronze"
        assert result["scheduler_jobs_deleted"] == 2
        assert set(_SchedulerMockAsyncClient.deleted_ids) == {"b1", "b2"}
        assert "s3" not in _SchedulerMockAsyncClient.deleted_ids


# ===========================================================================
# Tests: spawn_bounty expiry_minutes parameter
# ===========================================================================


class TestSpawnBountyExpiry:
    """Tests for BountyService.spawn_bounty with expiry_minutes parameter."""

    @pytest.mark.asyncio
    async def test_custom_expiry_minutes_used(self, spawn_service, mock_db):
        """When expiry_minutes provided, end_time = issue_time + timedelta(minutes=expiry)."""
        from datetime import timedelta

        criminal = _make_criminal("Raider", "terran")
        spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
        spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

        captured_bounties = []

        async def capture_create(db, bounty):
            captured_bounties.append(bounty)
            return SimpleNamespace(id=5, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

        spawn_service.bounty_repo.create = capture_create

        with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
            await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", expiry_minutes=120)

        assert len(captured_bounties) == 1
        b = captured_bounties[0]
        expected_end = b.issue_time + timedelta(minutes=120)
        assert b.end_time == expected_end

    @pytest.mark.asyncio
    async def test_default_expiry_480_minutes(self, spawn_service, mock_db):
        """When expiry_minutes not provided, defaults to 480 minutes."""
        from datetime import timedelta

        criminal = _make_criminal("Bandit", "terran")
        spawn_service.criminal_repo.list_all = AsyncMock(return_value=[criminal])
        spawn_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

        captured_bounties = []

        async def capture_create(db, bounty):
            captured_bounties.append(bounty)
            return SimpleNamespace(id=6, **{k: getattr(bounty, k) for k in vars(bounty) if not k.startswith("_")})

        spawn_service.bounty_repo.create = capture_create

        with patch.object(spawn_service, "generate_loadout", new=AsyncMock(return_value=SAMPLE_LOADOUT)):
            await spawn_service.spawn_bounty(mock_db, guild_id=1, division="bronze", tech_level=3)

        assert len(captured_bounties) == 1
        b = captured_bounties[0]
        expected_end = b.issue_time + timedelta(minutes=480)
        assert b.end_time == expected_end


# ===========================================================================
# Tests: generate_loadout — HP calculation (armor_hp, shield_hp, total_hp)
# ===========================================================================


@pytest.mark.asyncio
async def test_generate_loadout_hp_no_modules(service, mock_db):
    """When no HP modules, armor_hp = base armour, shield_hp = 0, total_hp = armor_hp."""
    ship = _make_ship("Betty", value=16038, max_primaries=0, max_modules=0)
    # Manually set armour on the ship so we can verify it's picked up
    ship.armour = 120

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=-1)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    assert result["ship_armour"] == 120
    assert result["armor_hp"] == 120
    assert result["shield_hp"] == 0
    assert result["total_hp"] == 120


@pytest.mark.asyncio
async def test_generate_loadout_hp_with_armour_module(service, mock_db):
    """ArmourModule extra_atts.armour is added to base armour to get armor_hp."""
    ship = _make_ship("Betty", value=16038, max_primaries=0, max_modules=1)
    ship.armour = 100
    armour_mod = _make_module(
        "D'iol Armour",
        value=51449,
        tech_level=1,
        type="ArmourModule",
        extra_atts={"armour": 160},
    )

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(
            service,
            "_find_typed_module",
            new=AsyncMock(side_effect=lambda db, kw, tl: armour_mod if kw == "armour" else None),
        ),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    assert result["ship_armour"] == 100
    assert result["armor_hp"] == 100 + 160
    assert result["shield_hp"] == 0
    assert result["total_hp"] == 260


@pytest.mark.asyncio
async def test_generate_loadout_hp_with_shield_module(service, mock_db):
    """ShieldModule extra_atts.shield contributes to shield_hp."""
    ship = _make_ship("Ghost", value=6000000, max_primaries=0, max_modules=2)
    ship.armour = 200
    armour_mod = _make_module(
        "Armour Plate",
        value=1070,
        tech_level=1,
        type="ArmourModule",
        extra_atts={"armour": 100},
    )
    shield_mod = _make_module(
        "Particle Shield",
        value=39331,
        tech_level=4,
        type="ShieldModule",
        extra_atts={"shield": 380},
    )

    async def fake_find_typed_module(db, kw, tl):
        if kw == "armour":
            return armour_mod
        if kw == "shield":
            return shield_mod
        return None

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=3)),
        patch.object(service, "_find_typed_module", new=AsyncMock(side_effect=fake_find_typed_module)),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=4)

    assert result["ship_armour"] == 200
    assert result["armor_hp"] == 200 + 100  # base + ArmourModule
    assert result["shield_hp"] == 380
    assert result["total_hp"] == 300 + 380


@pytest.mark.asyncio
async def test_generate_loadout_hp_with_gamma_shield_module(service, mock_db):
    """GammaShieldModule extra_atts.shield also contributes to shield_hp."""
    ship = _make_ship("Ghost", value=6000000, max_primaries=0, max_modules=1)
    ship.armour = 300
    gamma_mod = _make_module(
        "Gamma Shield",
        value=50000,
        tech_level=5,
        type="GammaShieldModule",
        extra_atts={"shield": 500},
    )

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[gamma_mod])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=5)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=6)

    assert result["armor_hp"] == 300
    assert result["shield_hp"] == 500
    assert result["total_hp"] == 800


@pytest.mark.asyncio
async def test_generate_loadout_hp_armour_module_no_extra_atts(service, mock_db):
    """ArmourModule with extra_atts=None (or missing armour key) contributes 0."""
    ship = _make_ship("Betty", value=16038, max_primaries=0, max_modules=1)
    ship.armour = 100
    armour_mod = _make_module(
        "Bare Armour",
        value=500,
        tech_level=1,
        type="ArmourModule",
        extra_atts=None,  # No extra_atts
    )

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(
            service,
            "_find_typed_module",
            new=AsyncMock(side_effect=lambda db, kw, tl: armour_mod if kw == "armour" else None),
        ),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    # ArmourModule with no extra_atts contributes 0 armour bonus
    assert result["armor_hp"] == 100
    assert result["shield_hp"] == 0
    assert result["total_hp"] == 100


@pytest.mark.asyncio
async def test_generate_loadout_module_extra_atts_in_dict(service, mock_db):
    """Module dicts in loadout include 'extra_atts' key."""
    ship = _make_ship("Betty", value=16038, max_primaries=0, max_modules=1)
    ship.armour = 100
    armour_mod = _make_module(
        "D'iol",
        value=51449,
        tech_level=1,
        type="ArmourModule",
        extra_atts={"armour": 160, "other_stat": 42},
    )

    service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[])

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(
            service,
            "_find_typed_module",
            new=AsyncMock(side_effect=lambda db, kw, tl: armour_mod if kw == "armour" else None),
        ),
    ):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    assert len(result["modules"]) == 1
    mod_dict = result["modules"][0]
    assert "extra_atts" in mod_dict
    assert mod_dict["extra_atts"] == {"armour": 160, "other_stat": 42}


@pytest.mark.asyncio
async def test_generate_loadout_tl0_beginner_has_hp_fields(service, mock_db):
    """Tech level 0 beginner loadout includes armor_hp, shield_hp, total_hp."""
    result = await service.generate_loadout(mock_db, tech_level=0)

    assert result["armor_hp"] == 50
    assert result["shield_hp"] == 0
    assert result["total_hp"] == 50


# ===========================================================================
# Tests: division-based combat system (Bronze auto-capture + Silver+ mandatory)
# ===========================================================================


def _make_player_with_tier(
    player_id: int = 1,
    tier: str = "Bronze",
    classic_mode: bool = False,
    bounty_cooldown_end=None,
    active_ship=None,
) -> SimpleNamespace:
    """Return a Player-like SimpleNamespace with an active_ship but no active_ship_id.

    T10: guild_id and user_id added so fight_ships callsites can extract them.
    """
    return SimpleNamespace(
        id=player_id,
        user_id=player_id * 1000,  # T10: Discord user_id for combat_log
        guild_id=9999,  # T10: guild_id for combat_log
        tier=tier,
        classic_mode=classic_mode,
        bounty_cooldown_end=bounty_cooldown_end,
        active_ship=active_ship,
    )


@pytest.mark.asyncio
async def test_check_bounty_bronze_auto_capture_returns_correct(service, mock_db):
    """Bronze player finds correct system → CORRECT result with combat_won=True (auto-capture)."""
    from services.bounty_service import RewardInfo

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    player = _make_player_with_tier(tier="Bronze")
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []}

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=500, xp_earned=25, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    # Bronze: always captured regardless of combat
    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    assert "captured" in result.message.lower() or "cr" in result.message
    assert result.division == "bronze"
    assert result.criminal_ship is not None  # Returned for cog to offer bonus duel


@pytest.mark.asyncio
async def test_check_bounty_bronze_with_ship_bonus_won(service, mock_db):
    """Bronze player with ship wins combat → bonus_won=True, total_reward=2x base."""
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    active_ship = SimpleNamespace(ship_name="Betty", armour=200)
    player = _make_player_with_tier(tier="Bronze", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Bandit", "ship_armour": 50, "weapons": [], "turrets": []}

    _fs1 = SimpleNamespace(ship_name="Betty", raw_hp=200, raw_dps=0.0, varied_hp=200, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Bandit", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Betty",
        loser_name="Bandit",
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=500, xp_earned=25, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Betty", base_armour=200)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    assert result.bonus_won is True
    assert result.total_reward == 1000  # 500 * 2
    assert result.reward == 500
    # Bonus was awarded
    service._award_combat_bonus.assert_awaited_once_with(mock_db, 1, 500)


@pytest.mark.asyncio
async def test_check_bounty_bronze_with_ship_bonus_lost(service, mock_db):
    """Bronze player with ship loses combat → bonus_won=False, reward = base only."""
    from services.bounty_service import RewardInfo

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    active_ship = SimpleNamespace(ship_name="Betty", armour=50)
    player = _make_player_with_tier(tier="Bronze", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Dreadnought",
        "ship_armour": 500,
        "weapons": [{"name": "Cannon", "dps": 99}],
        "turrets": [],
    }

    _fs1 = SimpleNamespace(ship_name="Betty", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Dreadnought", raw_hp=500, raw_dps=99.0, varied_hp=499, varied_dps=99.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Dreadnought",
        loser_name="Betty",
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=2,  # P2-T8b: criminal is side-2; criminal wins here
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=400, xp_earned=20, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True  # Still captured
    assert result.bonus_won is False
    assert result.total_reward == 400  # Just the base
    service._award_combat_bonus.assert_not_called()


@pytest.mark.asyncio
async def test_check_bounty_silver_mandatory_combat_win(service, mock_db):
    """Silver player wins mandatory combat → CORRECT with combat_win message."""
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    active_ship = SimpleNamespace(ship_name="Groza", armour=500)
    player = _make_player_with_tier(tier="Silver", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []}

    _fs1 = SimpleNamespace(ship_name="Groza", raw_hp=500, raw_dps=0.0, varied_hp=500, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Bandit", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Groza",
        loser_name="Bandit",
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=800, xp_earned=80, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Groza", base_armour=500)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    assert result.division == "silver"
    # Silver win message references combat victory
    assert "combat" in result.message.lower() or "victory" in result.message.lower()
    assert result.bonus_won is False  # No bronze bonus for silver+


@pytest.mark.asyncio
async def test_check_bounty_silver_mandatory_combat_loss_resets_checks(service, mock_db):
    """Silver player loses mandatory combat → _reset_bounty_checks called, combat_won=False."""
    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    active_ship = SimpleNamespace(ship_name="Betty", armour=50)
    player = _make_player_with_tier(tier="Silver", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Overlord",
        "ship_armour": 1000,
        "weapons": [{"name": "Death Cannon", "dps": 999}],
        "turrets": [],
    }

    _fs1 = SimpleNamespace(ship_name="Betty", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Overlord", raw_hp=1000, raw_dps=999.0, varied_hp=999, varied_dps=999.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Overlord",
        loser_name="Betty",
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=2,  # P2-T8b: criminal is side-2; criminal wins here
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service._reset_bounty_checks = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is False
    assert result.division == "silver"
    assert "defeated" in result.message.lower() or "escaped" in result.message.lower()
    # No reward awarded on loss
    assert result.reward is None
    # Reset was called (not escape)
    service._reset_bounty_checks.assert_awaited_once_with(mock_db, bounty)


@pytest.mark.asyncio
async def test_check_bounty_gold_mandatory_combat_win(service, mock_db):
    """Gold player wins mandatory combat → captured with reward."""
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    active_ship = SimpleNamespace(ship_name="Wraith", armour=800)
    player = _make_player_with_tier(tier="Gold", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Pirate", "ship_armour": 200, "weapons": [], "turrets": []}

    _fs1 = SimpleNamespace(ship_name="Wraith", raw_hp=800, raw_dps=0.0, varied_hp=800, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Pirate", raw_hp=200, raw_dps=0.0, varied_hp=200, varied_dps=0.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Wraith",
        loser_name="Pirate",
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1500, xp_earned=150, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Wraith", base_armour=800)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    assert result.division == "gold"


@pytest.mark.asyncio
async def test_check_bounty_silver_stalemate_counts_as_win(service, mock_db):
    """Silver player — stalemate counts as player win (bounty captured)."""
    from services.bounty_service import RewardInfo

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    active_ship = SimpleNamespace(ship_name="Betty", armour=100)
    player = _make_player_with_tier(tier="Silver", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Raider", "ship_armour": 100, "weapons": [], "turrets": []}

    _fs1 = SimpleNamespace(ship_name="Betty", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name="Raider", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name=None,
        loser_name=None,
        is_stalemate=True,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=None,  # P2-T8b: stalemate has no winner side
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=600, xp_earned=60, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True  # Stalemate = player win for silver+


@pytest.mark.asyncio
async def test_check_bounty_silver_no_ship_auto_win(service, mock_db):
    """Silver player with no ship auto-wins (no combat possible)."""
    from services.bounty_service import RewardInfo

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    player = _make_player_with_tier(tier="Silver", active_ship=None)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []}

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=900, xp_earned=90, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    # No ship → no combat → auto-win for silver too
    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True


@pytest.mark.asyncio
async def test_check_bounty_bronze_combat_result_serialized(service, mock_db):
    """Bronze check with ship returns combat_result dict in the response."""
    from services.bounty_service import RewardInfo

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    active_ship = SimpleNamespace(ship_name="Betty", armour=200)
    player = _make_player_with_tier(tier="Bronze", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Bandit", "ship_armour": 50, "weapons": [], "turrets": []}

    fight_stats1 = SimpleNamespace(ship_name="Betty", raw_hp=200, raw_dps=0.0, varied_hp=200, varied_dps=0.0, ttk=None)
    fight_stats2 = SimpleNamespace(ship_name="Bandit", raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Betty",
        loser_name="Bandit",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=None,
        winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=500, xp_earned=25, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.combat_result is not None
    assert result.combat_result["winner_name"] == "Betty"
    assert result.combat_result["is_stalemate"] is False
    assert "ship1_stats" in result.combat_result
    assert "ship2_stats" in result.combat_result


@pytest.mark.asyncio
async def test_check_bounty_silver_combat_result_serialized(service, mock_db):
    """Silver win check returns combat_result dict in the response."""
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    active_ship = SimpleNamespace(ship_name="Groza", armour=500)
    player = _make_player_with_tier(tier="Silver", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Pirate", "ship_armour": 100, "weapons": [], "turrets": []}

    fight_stats1 = SimpleNamespace(ship_name="Groza", raw_hp=500, raw_dps=0.0, varied_hp=500, varied_dps=0.0, ttk=None)
    fight_stats2 = SimpleNamespace(ship_name="Pirate", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    mock_fight = SimpleNamespace(
        winner_name="Groza",
        loser_name="Pirate",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=None,
        winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=800, xp_earned=80, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Groza", base_armour=500)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.combat_result is not None
    assert result.combat_result["winner_name"] == "Groza"
    assert "ship1_stats" in result.combat_result


# ===========================================================================
# P2-T8b: Same-name ship tests — id/side keying for win determination
# ===========================================================================


@pytest.mark.asyncio
async def test_check_bounty_bronze_same_name_criminal_wins_no_bonus(service, mock_db):
    """P2-T8b SAME-NAME anti-vacuous: player and criminal share the same ship name.

    Criminal wins (winner_side=2). A name-keyed impl (winner_name == player_loadout.ship_name)
    would incorrectly assign bonus_won=True because winner_name == criminal_ship_name
    == player_ship_name.  The side-keyed impl (winner_side == 1) correctly yields
    bonus_won=False.
    """
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    shared_name = "CloneShip"
    active_ship = SimpleNamespace(ship_name=shared_name, armour=100)
    player = _make_player_with_tier(tier="Bronze", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": shared_name, "ship_armour": 500, "weapons": [], "turrets": []}

    _fs1 = SimpleNamespace(ship_name=shared_name, raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name=shared_name, raw_hp=500, raw_dps=50.0, varied_hp=500, varied_dps=50.0, ttk=2.0)
    mock_fight = SimpleNamespace(
        winner_name=shared_name,  # same as player ship name — name-key would be ambiguous
        loser_name=shared_name,
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=2,  # Criminal (side-2) wins — correct side-keyed determination
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=400, xp_earned=20, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name=shared_name, base_armour=100)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True  # Bronze: auto-capture still succeeds
    # P2-T8b: criminal won (side-2) → no bonus; a name-keyed impl would give bonus=True
    assert result.bonus_won is False, (
        "bonus_won must be False when criminal wins (side-2), even if player and criminal share the same ship name"
    )
    service._award_combat_bonus.assert_not_called()


@pytest.mark.asyncio
async def test_check_bounty_silver_same_name_criminal_wins_no_duel_won(service, mock_db):
    """P2-T8b SAME-NAME anti-vacuous: Silver player and criminal share the same ship name.

    Criminal wins (winner_side=2). A name-keyed impl (winner_name == player_loadout.ship_name)
    would incorrectly treat this as a player win (duel_won=True) because both names match.
    The side-keyed impl (winner_side == 1) correctly yields duel_won=False → combat_won=False.

    calc_rewards / distribute_rewards / _build_payout_breakdown are mocked so that
    when a name-keyed mutation incorrectly takes the duel_won=True win-path, the code
    completes and fails CLEANLY at the combat_won assertion (AssertionError) rather
    than crashing with an AttributeError deep in the reward-calculation machinery.
    """
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout

    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()

    shared_name = "CloneShip"
    active_ship = SimpleNamespace(ship_name=shared_name, armour=50)
    player = _make_player_with_tier(tier="Silver", active_ship=active_ship)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": shared_name, "ship_armour": 1000, "weapons": [], "turrets": []}

    _fs1 = SimpleNamespace(ship_name=shared_name, raw_hp=50, raw_dps=0.0, varied_hp=50, varied_dps=0.0, ttk=None)
    _fs2 = SimpleNamespace(ship_name=shared_name, raw_hp=1000, raw_dps=99.0, varied_hp=1000, varied_dps=99.0, ttk=0.5)
    mock_fight = SimpleNamespace(
        winner_name=shared_name,  # same as player ship name — name-key would be ambiguous
        loser_name=shared_name,
        is_stalemate=False,
        ship1_stats=_fs1,
        ship2_stats=_fs2,
        combat_log_id=None,
        winner_side=2,  # Criminal (side-2) wins — correct side-keyed determination
    )
    service.combat_service.fight_ships = AsyncMock(return_value=mock_fight)
    service._reset_bounty_checks = AsyncMock()
    # Mock the reward win-path so a name-keyed mutation (duel_won=True) completes
    # and reaches the assertion rather than crashing inside calc_rewards/distribute_rewards.
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=500, xp_earned=25, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._build_payout_breakdown = AsyncMock(return_value=None)

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name=shared_name, base_armour=50)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    # P2-T8b: criminal won (side-2) → combat_won=False for silver; name-keyed impl gives True
    assert result.combat_won is False, (
        "combat_won must be False when criminal wins (side-2), even if player and criminal share the same ship name"
    )
    # Reset should be called on combat loss
    service._reset_bounty_checks.assert_awaited_once_with(mock_db, bounty)


# ===========================================================================
# Tests: _reset_bounty_checks
# ===========================================================================


@pytest.mark.asyncio
async def test_reset_bounty_checks_clears_all_checked(service, mock_db):
    """_reset_bounty_checks sets all route systems back to -1."""
    bounty = _make_expiry_bounty(status="active")
    # Simulate some prior checks
    bounty.checked = {"Alpha": 42, "Beta": -1, "Gamma": 7, "Sol": 99}
    bounty.route = ["Alpha", "Beta", "Gamma", "Sol"]

    service.bounty_repo.update = AsyncMock()

    await service._reset_bounty_checks(mock_db, bounty)

    # All systems should be -1 after reset
    assert all(v == -1 for v in bounty.checked.values())


@pytest.mark.asyncio
async def test_reset_bounty_checks_picks_new_answer_from_route(service, mock_db):
    """_reset_bounty_checks sets a new answer randomly from the route."""
    route = ["Alpha", "Beta", "Gamma", "Sol"]
    bounty = _make_expiry_bounty(status="active", route=route)
    bounty.answer = "Sol"
    service.bounty_repo.update = AsyncMock()

    # Run many times to confirm answer stays within route
    for _ in range(20):
        await service._reset_bounty_checks(mock_db, bounty)
        assert bounty.answer in route, f"New answer {bounty.answer!r} not in route {route}"


@pytest.mark.asyncio
async def test_reset_bounty_checks_updates_repo(service, mock_db):
    """_reset_bounty_checks calls bounty_repo.update to persist changes."""
    bounty = _make_expiry_bounty(status="active")
    service.bounty_repo.update = AsyncMock()

    await service._reset_bounty_checks(mock_db, bounty)

    service.bounty_repo.update.assert_called_once_with(mock_db, bounty)


@pytest.mark.asyncio
async def test_reset_bounty_checks_preserves_route_keys(service, mock_db):
    """After reset, checked dict has exactly the same keys as the route."""
    route = ["SystemA", "SystemB", "SystemC", "SystemD"]
    bounty = _make_expiry_bounty(status="active", route=route)
    bounty.checked = {s: (42 if s.startswith("S") else -1) for s in route}  # partial checks
    service.bounty_repo.update = AsyncMock()

    await service._reset_bounty_checks(mock_db, bounty)

    assert set(bounty.checked.keys()) == set(route)
    assert all(v == -1 for v in bounty.checked.values())


# ===========================================================================
# _award_combat_bonus tests moved to integration — see
# tests/integration/test_bounty_service_integration.py
# Rationale: _award_combat_bonus mutates player.credits / lifetime_credits / xp
# on an ORM object fetched via player_repo.get_by_id; the identity-map
# behaviour can only be verified against a real SQLite session.
# ===========================================================================


# ===========================================================================
# Tests: _serialize_fight_results
# ===========================================================================


def test_serialize_fight_results_none():
    """Returns None when fight_results is None."""
    from services.bounty_service import _serialize_fight_results

    result = _serialize_fight_results(None)
    assert result is None


def test_serialize_fight_results_win():
    """Serializes a win FightResults to a dict with all expected keys (T10 schema)."""
    from services.bounty_service import _serialize_fight_results

    fight_stats1 = SimpleNamespace(
        ship_name="Betty", raw_hp=200, raw_dps=10.0, varied_hp=195, varied_dps=10.5, ttk=18.57
    )
    fight_stats2 = SimpleNamespace(ship_name="Bandit", raw_hp=100, raw_dps=8.0, varied_hp=98, varied_dps=8.2, ttk=23.78)
    fight = SimpleNamespace(
        winner_name="Betty",
        loser_name="Bandit",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=42,
    )

    result = _serialize_fight_results(fight)

    assert result is not None
    assert result["winner_name"] == "Betty"
    assert result["loser_name"] == "Bandit"
    assert result["is_stalemate"] is False
    # T10: variance_percent removed from serialized output
    assert "variance_percent" not in result
    assert result["ship1_stats"]["ship_name"] == "Betty"
    assert result["ship1_stats"]["ttk"] == 18.57
    assert result["ship2_stats"]["ship_name"] == "Bandit"
    # T10: pvc_armour_buff retired
    assert "pvc_armour_buff" not in result
    # T10: combat_log_id present
    assert result["combat_log_id"] == 42


def test_serialize_fight_results_win_with_pvc_buff():
    """T10: pvc_armour_buff is retired — _serialize_fight_results no longer accepts it."""
    from services.bounty_service import _serialize_fight_results

    fight_stats1 = SimpleNamespace(
        ship_name="Betty", raw_hp=200, raw_dps=10.0, varied_hp=300, varied_dps=10.5, ttk=18.57
    )
    fight_stats2 = SimpleNamespace(ship_name="Bandit", raw_hp=100, raw_dps=8.0, varied_hp=98, varied_dps=8.2, ttk=23.78)
    fight = SimpleNamespace(
        winner_name="Betty",
        loser_name="Bandit",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=None,
    )

    # T10: pvc_armour_buff kwarg is removed from _serialize_fight_results
    import pytest as _pytest

    with _pytest.raises(TypeError):
        _serialize_fight_results(fight, pvc_armour_buff=1.5)  # type: ignore[call-arg]


def test_serialize_fight_results_stalemate():
    """Serializes a stalemate FightResults correctly (T10 schema)."""
    from services.bounty_service import _serialize_fight_results

    fight_stats1 = SimpleNamespace(ship_name="A", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    fight_stats2 = SimpleNamespace(ship_name="B", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None)
    fight = SimpleNamespace(
        winner_name=None,
        loser_name=None,
        is_stalemate=True,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=None,
    )

    result = _serialize_fight_results(fight)

    assert result is not None
    assert result["winner_name"] is None
    assert result["is_stalemate"] is True
    assert result["ship1_stats"]["ttk"] is None
    assert "pvc_armour_buff" not in result
    assert "variance_percent" not in result


def test_serialize_fight_results_includes_pvc_damage_reduction_for_pvc():
    """pvc_damage_reduction is included from FightResults.metadata for a PvC fight."""
    from services.bounty_service import _serialize_fight_results

    fight_stats1 = SimpleNamespace(
        ship_name="Betty", raw_hp=200, raw_dps=10.0, varied_hp=195, varied_dps=10.5, ttk=18.57
    )
    fight_stats2 = SimpleNamespace(ship_name="Bandit", raw_hp=100, raw_dps=8.0, varied_hp=98, varied_dps=8.2, ttk=23.78)
    fight = SimpleNamespace(
        winner_name="Betty",
        loser_name="Bandit",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=7,
        metadata={"pvc_damage_reduction": 0.33, "resolver": "tick_v1"},
    )

    result = _serialize_fight_results(fight)

    assert result is not None
    assert result["pvc_damage_reduction"] == pytest.approx(0.33)


def test_serialize_fight_results_pvc_damage_reduction_zero_for_pvp():
    """pvc_damage_reduction is 0.0 when metadata has no pvc_damage_reduction key (PvP fight)."""
    from services.bounty_service import _serialize_fight_results

    fight_stats1 = SimpleNamespace(ship_name="Alpha", raw_hp=150, raw_dps=9.0, varied_hp=148, varied_dps=9.1, ttk=15.0)
    fight_stats2 = SimpleNamespace(ship_name="Bravo", raw_hp=140, raw_dps=8.5, varied_hp=137, varied_dps=8.6, ttk=16.0)
    fight = SimpleNamespace(
        winner_name="Alpha",
        loser_name="Bravo",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=None,
        metadata={},
    )

    result = _serialize_fight_results(fight)

    assert result is not None
    assert result["pvc_damage_reduction"] == pytest.approx(0.0)


def test_serialize_fight_results_pvc_damage_reduction_zero_when_no_metadata():
    """pvc_damage_reduction is 0.0 when fight has no metadata attribute at all."""
    from services.bounty_service import _serialize_fight_results

    fight_stats1 = SimpleNamespace(ship_name="Alpha", raw_hp=150, raw_dps=9.0, varied_hp=148, varied_dps=9.1, ttk=15.0)
    fight_stats2 = SimpleNamespace(ship_name="Bravo", raw_hp=140, raw_dps=8.5, varied_hp=137, varied_dps=8.6, ttk=16.0)
    # Deliberately no `metadata` attribute
    fight = SimpleNamespace(
        winner_name="Alpha",
        loser_name="Bravo",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=None,
    )

    result = _serialize_fight_results(fight)

    assert result is not None
    assert result["pvc_damage_reduction"] == pytest.approx(0.0)


def test_serialize_fight_results_includes_summary_fields():
    """CI-2: summary fields (combatants, duration_s, outcome, reason) are serialized."""
    from services.bounty_service import _serialize_fight_results

    fight_stats1 = SimpleNamespace(
        ship_name="Betty", raw_hp=200, raw_dps=10.0, varied_hp=200, varied_dps=10.0, ttk=None
    )
    fight_stats2 = SimpleNamespace(
        ship_name="Crusher", raw_hp=100, raw_dps=8.0, varied_hp=100, varied_dps=8.0, ttk=25.0
    )
    summary = {
        "outcome": "win",
        "reason": "hp_depleted",
        "duration_ticks": 2650,
        "combatants": {
            "1": {
                "name": "Betty",
                "ship": "Betty",
                "final_hp": {"shield": 0, "armour": 0, "hull": 95},
                "damage_dealt": 312,
                "damage_taken": 95,
                "shots_fired": 81,
                "shots_hit": 47,
                "accuracy": 0.58,
            },
            "2": {
                "name": "Crusher",
                "ship": "Crusher",
                "final_hp": {"shield": 0, "armour": 0, "hull": 0},
                "damage_dealt": 95,
                "damage_taken": 312,
                "shots_fired": 78,
                "shots_hit": 40,
                "accuracy": 0.51,
            },
        },
    }
    fight = SimpleNamespace(
        winner_name="Betty",
        loser_name="Crusher",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=99,
        metadata={
            "summary": summary,
            "metadata": {"tick_ms": 10, "total_ticks": 2650, "resolver": "tick_v1", "pvc_damage_reduction": 0.33},
        },
    )

    result = _serialize_fight_results(fight)

    assert result is not None
    # After-action fields present
    assert result["outcome"] == "win"
    assert result["reason"] == "hp_depleted"
    assert result["duration_ticks"] == 2650
    assert result["duration_s"] == pytest.approx(26.5)
    assert result["pvc_damage_reduction"] == pytest.approx(0.33)
    cb = result["combatants"]
    assert cb is not None
    assert cb["1"]["accuracy"] == pytest.approx(0.58)
    assert cb["1"]["damage_dealt"] == 312
    assert cb["2"]["final_hp"]["hull"] == 0
    # Legacy projection fields still present
    assert result["ship1_stats"]["ship_name"] == "Betty"
    assert result["combat_log_id"] == 99


def test_serialize_fight_results_no_summary_fields_are_none():
    """CI-2: when fight has no metadata/summary, after-action fields default to None."""
    from services.bounty_service import _serialize_fight_results

    fight_stats1 = SimpleNamespace(ship_name="A", raw_hp=100, raw_dps=5.0, varied_hp=100, varied_dps=5.0, ttk=20.0)
    fight_stats2 = SimpleNamespace(ship_name="B", raw_hp=80, raw_dps=5.0, varied_hp=80, varied_dps=5.0, ttk=20.0)
    fight = SimpleNamespace(
        winner_name="A",
        loser_name="B",
        is_stalemate=False,
        ship1_stats=fight_stats1,
        ship2_stats=fight_stats2,
        combat_log_id=None,
        # No metadata attribute
    )

    result = _serialize_fight_results(fight)

    assert result is not None
    assert result["outcome"] is None
    assert result["duration_ticks"] is None
    assert result["duration_s"] is None
    assert result["combatants"] is None


# ===========================================================================
# Tests: _edit_bounty_announcement — criminal_icon lookup
# ===========================================================================


def _make_edit_bounty(
    bounty_id: int = 42,
    guild_id: int = 555,
    criminal_name: str = "BlackViper",
    criminal_faction: str = "Outlaws",
    division: str = "bronze",
    tech_level: int = 2,
    reward: int = 5000,
    route: list[str] | None = None,
    answer: str = "Beta",
    end_time=None,
    criminal_ship: dict | None = None,
    checked: dict | None = None,
) -> SimpleNamespace:
    """Build a Bounty-like SimpleNamespace for _edit_bounty_announcement tests."""
    from datetime import UTC, datetime

    return SimpleNamespace(
        id=bounty_id,
        guild_id=guild_id,
        criminal_name=criminal_name,
        criminal_faction=criminal_faction,
        division=division,
        tech_level=tech_level,
        reward=reward,
        route=route or ["Alpha", "Beta", "Gamma"],
        answer=answer,
        end_time=end_time or datetime(2026, 6, 1, tzinfo=UTC),
        criminal_ship=criminal_ship,
        checked=checked or {},
    )


class TestEditBountyAnnouncementCriminalIcon:
    """Tests for _edit_bounty_announcement criminal_icon lookup fix."""

    @pytest.fixture
    def svc(self):
        svc = BountyService()
        svc.bounty_repo = MagicMock()
        svc.criminal_repo = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_criminal_icon_included_in_embed_payload(self, svc, mock_db):
        """_edit_bounty_announcement passes criminal_icon kwarg to build_bounty_announcement_request."""
        import os

        bounty = _make_edit_bounty()

        mock_msg = MagicMock()
        mock_msg.channel_id = 1234
        mock_msg.message_id = 9999
        mock_msg_repo = AsyncMock()
        mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=mock_msg)

        # Criminal repo returns a criminal with an icon
        mock_criminal = MagicMock()
        mock_criminal.icon = "https://example.com/blackviper.png"
        mock_criminal_repo = AsyncMock()
        mock_criminal_repo.get_by_name = AsyncMock(return_value=mock_criminal)

        # A.48 wire shape: build_bounty_announcement_request receives criminal_icon as kwarg.
        mock_helper = AsyncMock(
            return_value={
                "text_content": None,
                "loadout_response": {"subject_kind": "criminal", "subject_name": "BlackViper"},
                "metadata": {
                    "title": "BlackViper",
                    "color": 0,
                    "footer_text": None,
                    "image_url": None,
                    "prefix_fields": [],
                    "suffix_fields": [],
                },
            }
        )

        class FakeHttpxResponse:
            def raise_for_status(self):
                pass

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def put(self, url, json=None, timeout=10):
                return FakeHttpxResponse()

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=mock_msg_repo,
            ),
            patch(
                "persist.repositories.criminal_repository.CriminalRepository",
                return_value=mock_criminal_repo,
            ),
            patch(
                "utils.bounty_announcement_payload.build_bounty_announcement_request",
                new=mock_helper,
            ),
            patch("httpx.AsyncClient", FakeAsyncClient),
            patch.dict(os.environ, {"DISCORD_GATEWAY_HOST": "gateway", "GATEWAY_PORT": "7999"}),
        ):
            await svc._edit_bounty_announcement(mock_db, bounty)

        # A.48: criminal_icon is passed as a kwarg to build_bounty_announcement_request.
        mock_helper.assert_awaited_once()
        assert mock_helper.call_args.kwargs.get("criminal_icon") == "https://example.com/blackviper.png"

    @pytest.mark.asyncio
    async def test_criminal_icon_none_when_criminal_not_found(self, svc, mock_db):
        """_edit_bounty_announcement passes criminal_icon=None when criminal lookup returns None."""
        import os

        bounty = _make_edit_bounty()

        mock_msg = MagicMock()
        mock_msg.channel_id = 1234
        mock_msg.message_id = 9999
        mock_msg_repo = AsyncMock()
        mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=mock_msg)

        # Criminal repo returns nothing
        mock_criminal_repo = AsyncMock()
        mock_criminal_repo.get_by_name = AsyncMock(return_value=None)

        mock_helper = AsyncMock(
            return_value={
                "text_content": None,
                "loadout_response": {"subject_kind": "criminal", "subject_name": "BlackViper"},
                "metadata": {
                    "title": "BlackViper",
                    "color": 0,
                    "footer_text": None,
                    "image_url": None,
                    "prefix_fields": [],
                    "suffix_fields": [],
                },
            }
        )

        class FakeHttpxResponse:
            def raise_for_status(self):
                pass

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def put(self, url, json=None, timeout=10):
                return FakeHttpxResponse()

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=mock_msg_repo,
            ),
            patch(
                "persist.repositories.criminal_repository.CriminalRepository",
                return_value=mock_criminal_repo,
            ),
            patch(
                "utils.bounty_announcement_payload.build_bounty_announcement_request",
                new=mock_helper,
            ),
            patch("httpx.AsyncClient", FakeAsyncClient),
            patch.dict(os.environ, {"DISCORD_GATEWAY_HOST": "gateway", "GATEWAY_PORT": "7999"}),
        ):
            await svc._edit_bounty_announcement(mock_db, bounty)

        mock_helper.assert_awaited_once()
        assert mock_helper.call_args.kwargs.get("criminal_icon") is None

    @pytest.mark.asyncio
    async def test_criminal_icon_lookup_failure_is_non_fatal(self, svc, mock_db):
        """_edit_bounty_announcement continues with criminal_icon=None when lookup raises."""
        import os

        bounty = _make_edit_bounty()

        mock_msg = MagicMock()
        mock_msg.channel_id = 1234
        mock_msg.message_id = 9999
        mock_msg_repo = AsyncMock()
        mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=mock_msg)

        # Criminal repo raises an exception
        mock_criminal_repo = AsyncMock()
        mock_criminal_repo.get_by_name = AsyncMock(side_effect=Exception("DB failure"))

        mock_helper = AsyncMock(
            return_value={
                "text_content": None,
                "loadout_response": {"subject_kind": "criminal", "subject_name": "BlackViper"},
                "metadata": {
                    "title": "BlackViper",
                    "color": 0,
                    "footer_text": None,
                    "image_url": None,
                    "prefix_fields": [],
                    "suffix_fields": [],
                },
            }
        )

        class FakeHttpxResponse:
            def raise_for_status(self):
                pass

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def put(self, url, json=None, timeout=10):
                return FakeHttpxResponse()

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=mock_msg_repo,
            ),
            patch(
                "persist.repositories.criminal_repository.CriminalRepository",
                return_value=mock_criminal_repo,
            ),
            patch(
                "utils.bounty_announcement_payload.build_bounty_announcement_request",
                new=mock_helper,
            ),
            patch("httpx.AsyncClient", FakeAsyncClient),
            patch.dict(os.environ, {"DISCORD_GATEWAY_HOST": "gateway", "GATEWAY_PORT": "7999"}),
        ):
            # Should not raise even when criminal lookup fails
            await svc._edit_bounty_announcement(mock_db, bounty)

        # A.48: build_bounty_announcement_request still called; criminal_icon defaults to None.
        mock_helper.assert_awaited_once()
        assert mock_helper.call_args.kwargs.get("criminal_icon") is None

    @pytest.mark.asyncio
    async def test_no_discord_message_skips_build(self, svc, mock_db):
        """_edit_bounty_announcement returns early when no Discord message is found."""
        bounty = _make_edit_bounty()

        mock_msg_repo = AsyncMock()
        mock_msg_repo.get_by_guild_type_and_reference = AsyncMock(return_value=None)

        captured_icons: list = []

        mock_criminal_repo = AsyncMock()
        mock_criminal_repo.get_by_name = AsyncMock(side_effect=lambda db, name: captured_icons.append(name) or None)

        with (
            patch(
                "persist.repositories.discord_message_repository.DiscordMessageRepository",
                return_value=mock_msg_repo,
            ),
            patch(
                "persist.repositories.criminal_repository.CriminalRepository",
                return_value=mock_criminal_repo,
            ),
        ):
            await svc._edit_bounty_announcement(mock_db, bounty)

        # Criminal lookup should NOT have happened (returned early)
        assert len(captured_icons) == 0


# ===========================================================================
# B.12 — Multi-bounty shared-system /check tests
# ===========================================================================
#
# These tests exercise the case where a player checks a system that appears
# in the routes of multiple active bounties for the player's division. Prior
# to B.12 the service exited the iteration loop after the first matching
# bounty (3 separate `return` statements at lines 1040, 1096, 1135 of the
# pre-fix bounty_service.py). The fix processes ALL matching bounties and
# returns a `MultiCheckResponse` with one outcome per matched bounty.


@pytest.mark.asyncio
async def test_b12_two_bounties_share_intermediate_system_both_marked_incorrect(check_bounty_setup):
    """B.12: Two bounties share an intermediate system → both get systems_checked updated.

    Neither system is the answer for either bounty, so both outcomes are
    INCORRECT and both bounties' ``checked`` dicts are mutated.
    """
    from services.bounty_service import CheckResult, MultiCheckResponse

    service, mock_db = check_bounty_setup
    player = _make_player(player_id=7)
    # Both bounties have "Beta" in their route but NEITHER answer is "Beta".
    bounty_a = _make_active_bounty(
        bounty_id=10,
        route=["Alpha", "Beta", "Gamma"],
        answer="Alpha",
        criminal_name="Alice",
    )
    bounty_b = _make_active_bounty(
        bounty_id=11,
        route=["Beta", "Delta", "Epsilon"],
        answer="Epsilon",
        criminal_name="Bob",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty_a, bounty_b]
    service.bounty_repo.update.return_value = None

    result = await service.check_bounty(mock_db, player_id=7, system_name="Beta", guild_id=1)

    assert isinstance(result, MultiCheckResponse)
    assert len(result.outcomes) == 2
    # Both outcomes must be INCORRECT (Beta is in route but not the answer for either)
    bounty_ids_seen = sorted(o.bounty_id for o in result.outcomes)
    assert bounty_ids_seen == [10, 11]
    for outcome in result.outcomes:
        assert outcome.result == CheckResult.INCORRECT
    # Both bounties must have their checked dict updated with this player
    assert bounty_a.checked["Beta"] == 7
    assert bounty_b.checked["Beta"] == 7


@pytest.mark.asyncio
async def test_b12_three_bounties_share_intermediate_system_all_updated(check_bounty_setup):
    """B.12: Three bounties share an intermediate system → all 3 get systems_checked updated."""
    from services.bounty_service import CheckResult

    service, mock_db = check_bounty_setup
    player = _make_player(player_id=42)
    # All three contain "Hub" but none is "Hub" the answer.
    bounty_a = _make_active_bounty(
        bounty_id=21,
        route=["Hub", "X", "Y"],
        answer="Y",
        criminal_name="A",
    )
    bounty_b = _make_active_bounty(
        bounty_id=22,
        route=["Z", "Hub", "W"],
        answer="W",
        criminal_name="B",
    )
    bounty_c = _make_active_bounty(
        bounty_id=23,
        route=["Hub", "Q", "R"],
        answer="R",
        criminal_name="C",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty_a, bounty_b, bounty_c]
    service.bounty_repo.update.return_value = None

    result = await service.check_bounty(mock_db, player_id=42, system_name="Hub", guild_id=1)

    assert len(result.outcomes) == 3
    # Every outcome should be INCORRECT and reference one of the three bounties.
    assert {o.bounty_id for o in result.outcomes} == {21, 22, 23}
    for outcome in result.outcomes:
        assert outcome.result == CheckResult.INCORRECT
    # All three bounties had their checked dict updated.
    assert bounty_a.checked["Hub"] == 42
    assert bounty_b.checked["Hub"] == 42
    assert bounty_c.checked["Hub"] == 42


@pytest.mark.asyncio
async def test_b12_two_bounties_share_terminal_system_both_terminate_and_pay_out(check_bounty_setup):
    """B.12: Two bounties share their terminal (answer) system → BOTH terminate and pay out.

    This is the smoking-gun gameplay scenario: a player solves two bounties at
    once. The reward / XP must be granted PER bounty (independently).
    """
    from services.bounty_service import CheckResult, RewardInfo

    service, mock_db = check_bounty_setup
    player = _make_player(player_id=99, classic_mode=True)  # bronze auto-capture path
    bounty_a = _make_active_bounty(
        bounty_id=30,
        route=["A1", "A2", "Sol"],
        answer="Sol",
        criminal_name="AlphaCrim",
    )
    bounty_a.criminal_ship = {}
    bounty_b = _make_active_bounty(
        bounty_id=31,
        route=["B1", "B2", "Sol"],
        answer="Sol",
        criminal_name="BetaCrim",
    )
    bounty_b.criminal_ship = {}
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty_a, bounty_b]
    service.bounty_repo.update.return_value = None
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=99, credits_earned=1000, xp_earned=50, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=99, system_name="Sol", guild_id=1)

    assert len(result.outcomes) == 2
    # Both outcomes are CORRECT and combat_won=True (bronze auto-capture)
    for outcome in result.outcomes:
        assert outcome.result == CheckResult.CORRECT
        assert outcome.combat_won is True
        assert outcome.reward == 1000
    # Reward distribution must be called twice — ONCE per terminating bounty.
    assert service.calc_rewards.call_count == 2
    assert service.distribute_rewards.call_count == 2


@pytest.mark.asyncio
async def test_b12_mixed_one_terminates_two_intermediate_in_same_call(check_bounty_setup):
    """B.12: Mixed scenario — one bounty terminates, two get intermediate-check, all in one call."""
    from services.bounty_service import CheckResult, RewardInfo

    service, mock_db = check_bounty_setup
    player = _make_player(player_id=5, classic_mode=True)
    bounty_terminal = _make_active_bounty(
        bounty_id=40,
        route=["X", "Hub", "Y"],
        answer="Hub",
        criminal_name="Boss",
    )
    bounty_terminal.criminal_ship = {}
    bounty_intermediate_a = _make_active_bounty(
        bounty_id=41,
        route=["Hub", "X", "Y"],
        answer="Y",
        criminal_name="Minion1",
    )
    bounty_intermediate_b = _make_active_bounty(
        bounty_id=42,
        route=["Z", "Hub", "Q"],
        answer="Q",
        criminal_name="Minion2",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [
        bounty_terminal,
        bounty_intermediate_a,
        bounty_intermediate_b,
    ]
    service.bounty_repo.update.return_value = None
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=5, credits_earned=2000, xp_earned=100, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=5, system_name="Hub", guild_id=1)

    assert len(result.outcomes) == 3
    by_id = {o.bounty_id: o for o in result.outcomes}
    assert by_id[40].result == CheckResult.CORRECT
    assert by_id[40].combat_won is True
    assert by_id[41].result == CheckResult.INCORRECT
    assert by_id[42].result == CheckResult.INCORRECT
    # Reward distribution should run exactly once (only the terminal bounty)
    assert service.calc_rewards.call_count == 1
    assert service.distribute_rewards.call_count == 1


@pytest.mark.asyncio
async def test_b12_same_division_overlap_bronze_division(check_bounty_setup):
    """B.12: Two bronze bounties sharing a system both update on /check.

    Mirrors the live DB state described in the findings doc — same-division
    overlap is the most common B.12 trigger.
    """
    from services.bounty_service import CheckResult

    service, mock_db = check_bounty_setup
    player = _make_player(player_id=1, tier="Bronze")  # bronze division
    bounty_a = _make_active_bounty(
        bounty_id=51,
        route=["Eanya", "Ginoya", "Nesla", "Weymire"],
        answer="Weymire",
        criminal_name="Mashon Redal",
    )
    bounty_b = _make_active_bounty(
        bounty_id=52,
        route=["Nesla", "Eanya", "Ginoya"],
        answer="Ginoya",
        criminal_name="Heinrich Wickel",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty_a, bounty_b]
    service.bounty_repo.update.return_value = None

    # Player checks Nesla — shared system that is NOT the answer for either bounty
    result = await service.check_bounty(mock_db, player_id=1, system_name="Nesla", guild_id=1)

    # Verify the bronze division was queried (regression guard for division derivation)
    service.bounty_repo.get_active_by_guild_and_division.assert_called_once_with(mock_db, 1, "bronze")
    assert len(result.outcomes) == 2
    for outcome in result.outcomes:
        assert outcome.result == CheckResult.INCORRECT
    assert bounty_a.checked["Nesla"] == 1
    assert bounty_b.checked["Nesla"] == 1


@pytest.mark.asyncio
async def test_b12_no_overlap_single_bounty_still_works(check_bounty_setup):
    """B.12 regression guard: the no-overlap (single-bounty) case must still produce one outcome."""
    from services.bounty_service import CheckResult

    service, mock_db = check_bounty_setup
    player = _make_player(player_id=3)
    bounty_only = _make_active_bounty(
        bounty_id=60,
        route=["A", "B", "C"],
        answer="C",
        criminal_name="Solo",
    )
    # Other bounty exists but does NOT contain the checked system
    bounty_other = _make_active_bounty(
        bounty_id=61,
        route=["X", "Y", "Z"],
        answer="Z",
        criminal_name="Unrelated",
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty_only, bounty_other]
    service.bounty_repo.update.return_value = None

    result = await service.check_bounty(mock_db, player_id=3, system_name="A", guild_id=1)

    assert len(result.outcomes) == 1
    assert result.outcomes[0].bounty_id == 60
    assert result.outcomes[0].result == CheckResult.INCORRECT
    # Backwards-compat proxy: top-level access still works for single-outcome case.
    assert result.result == CheckResult.INCORRECT
    assert result.bounty_id == 60


@pytest.mark.asyncio
async def test_b12_cooldown_applied_once_per_check_invocation(check_bounty_setup):
    """B.12: Even when /check touches multiple bounties, cooldown is set ONCE on the player."""
    from datetime import UTC, datetime

    service, mock_db = check_bounty_setup
    player = _make_player(player_id=8, bounty_cooldown_end=None)
    bounty_a = _make_active_bounty(bounty_id=70, route=["Alpha", "Shared"], answer="Alpha", criminal_name="A")
    bounty_b = _make_active_bounty(bounty_id=71, route=["Shared", "Beta"], answer="Beta", criminal_name="B")
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty_a, bounty_b]
    service.bounty_repo.update.return_value = None

    before = datetime.now(UTC)
    await service.check_bounty(mock_db, player_id=8, system_name="Shared", guild_id=1)

    # Cooldown applied exactly once — but the bounty_cooldown_end attribute
    # should still reflect a future timestamp.
    assert player.bounty_cooldown_end is not None
    assert player.bounty_cooldown_end > before


@pytest.mark.asyncio
async def test_b12_idempotency_already_checked_for_overlapping_bounties(check_bounty_setup):
    """B.12: Re-running /check on a system already-checked for some bounties.

    Verifies the per-bounty ALREADY_CHECKED guard survives the multi-bounty refactor.
    """
    from services.bounty_service import CheckResult

    service, mock_db = check_bounty_setup
    player = _make_player(player_id=4)
    # Both bounties contain "Shared" but Bounty A already has it marked
    bounty_a = _make_active_bounty(
        bounty_id=80,
        route=["Shared", "X"],
        answer="X",
        criminal_name="A",
        checked={"Shared": 99, "X": -1},  # already checked by another player
    )
    bounty_b = _make_active_bounty(
        bounty_id=81,
        route=["Shared", "Y"],
        answer="Y",
        criminal_name="B",
        checked={"Shared": -1, "Y": -1},
    )
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty_a, bounty_b]
    service.bounty_repo.update.return_value = None

    result = await service.check_bounty(mock_db, player_id=4, system_name="Shared", guild_id=1)

    by_id = {o.bounty_id: o for o in result.outcomes}
    assert by_id[80].result == CheckResult.ALREADY_CHECKED
    assert by_id[81].result == CheckResult.INCORRECT
    # The already-checked bounty's marker must be untouched (regression guard)
    assert bounty_a.checked["Shared"] == 99
    # The new bounty got its marker
    assert bounty_b.checked["Shared"] == 4


# ===========================================================================
# Tests: B.52 — generate_loadout ship selection excludes non-combat ships
# ===========================================================================


def _setup_mock_db_query_with_filter(mock_db, combat_ships, all_ships=None):
    """Configure mock_db.execute() to filter by max_primaries > 0.

    When Ship.max_primaries > 0 is used as a WHERE clause, we simulate it
    by intercepting db.execute() calls and returning combat_ships.
    If a single list is provided, always return combat_ships.
    """
    scalars = MagicMock()
    scalars.all.return_value = combat_ships
    result = MagicMock()
    result.scalars.return_value = scalars
    mock_db.execute = AsyncMock(return_value=result)
    return mock_db


@pytest.mark.asyncio
async def test_generate_loadout_tl_matched_path_excludes_non_combat_ships(service, mock_db):
    """B.52: TL-matched ship selection only returns ships with max_primaries > 0.

    When db.execute(select(Ship).where(Ship.max_primaries > 0)) is called,
    non-combat ships (max_primaries=0) are never in the result set, so they
    can never be selected even if matching_ships would include them.
    """
    # Only combat ships returned by the filtered DB query
    combat_ship = _make_ship("Groza", value=251600, max_primaries=3, max_modules=8)
    _make_ship("Cormorant", value=100000, max_primaries=0, max_modules=4)  # non-combat ship

    # The DB query with max_primaries > 0 filter returns only combat ships
    _setup_mock_db_query_with_filter(mock_db, combat_ships=[combat_ship])

    weapon = _make_weapon()
    module = _make_module()

    async def _get_all_by_tl(db, tl, item_type=None):
        if item_type == "primary_weapon":
            return [weapon]
        if item_type == "module":
            return [module]
        return []

    service.item_repo.get_all_by_tech_level = _get_all_by_tl

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
    ):
        result = await service.generate_loadout(mock_db, tech_level=2)

    # The non-combat ship (Cormorant) was never in the query result, so it is never selected
    assert result["ship_name"] == "Groza"
    assert result["ship_name"] != "Cormorant"


@pytest.mark.asyncio
async def test_generate_loadout_fallback_path_excludes_non_combat_ships(service, mock_db):
    """B.52: Fallback ship selection also only returns ships with max_primaries > 0.

    When the TL-matched path yields no matching_ships (no ship at the right TL),
    the fallback path calls db.execute with the same max_primaries > 0 filter,
    so non-combat ships are excluded there too.
    """
    # Only a combat ship is returned by both DB queries
    combat_ship = _make_ship("Betty", value=16038, max_primaries=1, max_modules=3)

    _setup_mock_db_query_with_filter(mock_db, combat_ships=[combat_ship])

    # find_item_tl returns a TL but matching_ships will be empty because
    # combat_ship has a different value TL — the fallback path will be triggered.
    # We simulate by making matching_ships empty: we give the ship a value such
    # that ship_tech_level_for_value(value) != ship_tl.
    # Betty value=16038 → TL 1; find_item_tl returns 5 → no match → fallback.
    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=5)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
    ):
        service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[])
        result = await service.generate_loadout(mock_db, tech_level=6)

    # Fallback path selected the only available (combat) ship
    assert result["ship_name"] == "Betty"


@pytest.mark.asyncio
async def test_generate_loadout_never_produces_non_combat_ship(service, mock_db):
    """B.52: Over 20 runs, generate_loadout never picks a non-combat ship.

    Simulates the DB returning only combat ships (as the WHERE filter does).
    Confirms max_primaries > 0 invariant holds for every selected ship.
    """
    combat_ships = [
        _make_ship("Betty", value=16038, max_primaries=1, max_modules=3),
        _make_ship("Groza", value=251600, max_primaries=3, max_modules=8),
        _make_ship("Ghost", value=6000000, max_primaries=4, max_modules=10),
    ]

    _setup_mock_db_query_with_filter(mock_db, combat_ships=combat_ships)

    weapon = _make_weapon()

    with (
        patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
        patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
    ):
        service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[weapon])
        for _ in range(20):
            result = await service.generate_loadout(mock_db, tech_level=2)
            # Every selected ship must have at least 1 primary weapon slot
            assert result["ship_max_primaries"] > 0, (
                f"Non-combat ship selected: {result['ship_name']} with max_primaries=0"
            )


@pytest.mark.asyncio
async def test_generate_loadout_empty_combat_ship_pool_warns_and_returns_unknown(service, mock_db):
    """B.52: If no combat ships exist (empty filtered pool), a warning is logged and
    the function returns the 'Unknown' fallback dict (not a crash).
    """
    # DB returns empty list for both TL-matched and fallback queries
    _setup_mock_db_query_with_filter(mock_db, combat_ships=[])

    with patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)):
        result = await service.generate_loadout(mock_db, tech_level=2)

    # No ships available → returns the Unknown fallback
    assert result["ship_name"] == "Unknown"
    assert result["weapons"] == []
    assert result["total_value"] == 0


# ===========================================================================
# Keith T Maxwell bonus — PvC damage reduction at both fight_ships call sites (T10)
# ===========================================================================


@pytest.mark.asyncio
async def test_pvc_fight_applies_armour_buff_bronze_path(combat_integration_setup):
    """T10: Bronze path fight_ships call passes pvc_damage_reduction=GameConstants.PVC_DAMAGE_REDUCTION.

    Verifies that ``_process_single_bounty_check`` passes the PvC DR to
    ``combat_service.fight_ships`` on the Bronze (auto-capture) code path.

    Max mocks used: 2 (combat_service, loadout_builder).
    """
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout
    from services.game_constants import GameConstants

    service, mock_db = combat_integration_setup

    active_ship = SimpleNamespace(ship_name="Betty", armour=100)
    player = _make_player(active_ship=active_ship, classic_mode=False, tier="Bronze")
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Raider", "ship_armour": 80, "weapons": [], "turrets": []}

    # Capture kwargs passed to fight_ships
    captured_kwargs: dict = {}
    _fight_stats1 = SimpleNamespace(
        ship_name="Betty", raw_hp=100, raw_dps=10.0, varied_hp=100, varied_dps=10.0, ttk=8.0
    )
    _fight_stats2 = SimpleNamespace(ship_name="Raider", raw_hp=80, raw_dps=5.0, varied_hp=80, varied_dps=5.0, ttk=16.0)

    async def _capture_fight(p_loadout, c_loadout, **kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            winner_name="Betty",
            loser_name="Raider",
            is_stalemate=False,
            ship1_stats=_fight_stats1,
            ship2_stats=_fight_stats2,
            combat_log_id=None,
            winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
        )

    service.combat_service.fight_ships = _capture_fight
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=800, xp_earned=40, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service._award_combat_bonus = AsyncMock()

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Betty", base_armour=100)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    # T10: pvc_damage_reduction replaces player_armour_buff
    assert "pvc_damage_reduction" in captured_kwargs, (
        "fight_ships was not called with pvc_damage_reduction kwarg on bronze path"
    )
    assert captured_kwargs["pvc_damage_reduction"] == pytest.approx(GameConstants.PVC_DAMAGE_REDUCTION), (
        f"Expected pvc_damage_reduction={GameConstants.PVC_DAMAGE_REDUCTION}, "
        f"got {captured_kwargs.get('pvc_damage_reduction')}"
    )


@pytest.mark.asyncio
async def test_pvc_fight_applies_armour_buff_silver_path(combat_integration_setup):
    """T10: Silver/Gold/Platinum fight_ships call passes pvc_damage_reduction=GameConstants.PVC_DAMAGE_REDUCTION.

    Verifies that the second fight_ships call site (the Silver/Gold/Platinum
    mandatory combat gate) forwards the PvC DR kwarg.

    Max mocks used: 2 (combat_service, loadout_builder).
    """
    from services.bounty_service import RewardInfo
    from services.combat_models import ShipLoadout
    from services.game_constants import GameConstants

    service, mock_db = combat_integration_setup

    active_ship = SimpleNamespace(ship_name="Falcon", armour=200)
    # Silver tier — NOT bronze, so mandatory combat gate runs
    player = _make_player(active_ship=active_ship, classic_mode=False, tier="Silver")
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Guardian", "ship_armour": 150, "weapons": [], "turrets": []}

    # Capture kwargs passed to fight_ships
    captured_kwargs: dict = {}
    _fight_stats1 = SimpleNamespace(
        ship_name="Falcon", raw_hp=200, raw_dps=20.0, varied_hp=200, varied_dps=20.0, ttk=7.5
    )
    _fight_stats2 = SimpleNamespace(
        ship_name="Guardian", raw_hp=150, raw_dps=10.0, varied_hp=150, varied_dps=10.0, ttk=20.0
    )

    async def _capture_fight(p_loadout, c_loadout, **kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            winner_name="Falcon",
            loser_name="Guardian",
            is_stalemate=False,
            ship1_stats=_fight_stats1,
            ship2_stats=_fight_stats2,
            combat_log_id=None,
            winner_side=1,  # P2-T8b: player is always side-1 (combatant1)
        )

    service.combat_service.fight_ships = _capture_fight
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1200, xp_earned=60, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    with patch(
        "services.loadout_builder.LoadoutBuilder.from_player",
        new=AsyncMock(return_value=ShipLoadout(ship_name="Falcon", base_armour=200)),
    ):
        result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    # T10: pvc_damage_reduction replaces player_armour_buff on silver path too
    assert "pvc_damage_reduction" in captured_kwargs, (
        "fight_ships was not called with pvc_damage_reduction kwarg on silver/gold/platinum path"
    )
    assert captured_kwargs["pvc_damage_reduction"] == pytest.approx(GameConstants.PVC_DAMAGE_REDUCTION), (
        f"Expected pvc_damage_reduction={GameConstants.PVC_DAMAGE_REDUCTION}, "
        f"got {captured_kwargs.get('pvc_damage_reduction')}"
    )


# ===========================================================================
# Tests: BountyService.scrub_player_checks_outside_tier
# ===========================================================================


class TestScrubPlayerChecksOutsideTier:
    """Tests for BountyService.scrub_player_checks_outside_tier (FORFEITED_CHECK sentinel)."""

    def _make_service(self):
        svc = BountyService.__new__(BountyService)
        svc.bounty_repo = MagicMock()
        svc.item_repo = MagicMock()
        return svc

    def _make_db(self):
        db = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    def _make_bounty(self, bounty_id: int = 1, checked: dict | None = None) -> object:
        return SimpleNamespace(id=bounty_id, checked=checked or {}, answer="Sol")

    @pytest.mark.asyncio
    async def test_player_checks_in_old_tier_become_forfeited(self):
        """Player's checks in divisions outside the new tier become FORFEITED_CHECK (-2)."""
        from services.bounty_service import FORFEITED_CHECK

        svc = self._make_service()
        db = self._make_db()
        player_id = 42

        bronze_bounty = self._make_bounty(checked={"Alpha": player_id, "Beta": 99})
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(
            side_effect=lambda _db, _gid, div: [bronze_bounty] if div == "bronze" else []
        )

        with patch("sqlalchemy.orm.attributes.flag_modified"):
            count = await svc.scrub_player_checks_outside_tier(db, player_id=player_id, guild_id=1, new_tier="Silver")

        assert count == 1
        assert bronze_bounty.checked["Alpha"] == FORFEITED_CHECK
        assert bronze_bounty.checked["Beta"] == 99  # other player untouched

    @pytest.mark.asyncio
    async def test_returns_zero_when_player_has_no_checks_in_old_divisions(self):
        """Returns 0 when player has no check entries in any division outside new tier."""
        svc = self._make_service()
        db = self._make_db()

        bounty = self._make_bounty(checked={"Alpha": 99})  # another player's check
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[bounty])

        with patch("sqlalchemy.orm.attributes.flag_modified"):
            count = await svc.scrub_player_checks_outside_tier(db, player_id=42, guild_id=1, new_tier="Silver")

        assert count == 0
        assert bounty.checked["Alpha"] == 99

    @pytest.mark.asyncio
    async def test_new_tier_division_is_not_scrubbed(self):
        """Checks in the player's new tier division are skipped entirely."""
        svc = self._make_service()
        db = self._make_db()
        player_id = 42

        silver_bounty = self._make_bounty(checked={"Alpha": player_id})

        def _get_active(_db, _gid, div):
            return [silver_bounty] if div == "silver" else []

        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(side_effect=_get_active)

        with patch("sqlalchemy.orm.attributes.flag_modified"):
            count = await svc.scrub_player_checks_outside_tier(db, player_id=player_id, guild_id=1, new_tier="Silver")

        assert count == 0
        assert silver_bounty.checked["Alpha"] == player_id  # untouched

    @pytest.mark.asyncio
    async def test_multiple_bounties_all_scrubbed(self):
        """All active bounties with player checks in affected divisions are mutated."""
        from services.bounty_service import FORFEITED_CHECK

        svc = self._make_service()
        db = self._make_db()
        player_id = 42

        bounty1 = self._make_bounty(1, checked={"Alpha": player_id})
        bounty2 = SimpleNamespace(id=2, checked={"Beta": player_id, "Gamma": 7}, answer="Gamma")
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(
            side_effect=lambda _db, _gid, div: [bounty1, bounty2] if div == "bronze" else []
        )

        with patch("sqlalchemy.orm.attributes.flag_modified"):
            count = await svc.scrub_player_checks_outside_tier(db, player_id=player_id, guild_id=1, new_tier="Silver")

        assert count == 2
        assert bounty1.checked["Alpha"] == FORFEITED_CHECK
        assert bounty2.checked["Beta"] == FORFEITED_CHECK
        assert bounty2.checked["Gamma"] == 7

    @pytest.mark.asyncio
    async def test_demotion_scrubs_higher_tier_divisions(self):
        """On demotion to Bronze, Silver/Gold/Platinum divisions are all scanned."""
        from services.bounty_service import FORFEITED_CHECK

        svc = self._make_service()
        db = self._make_db()
        player_id = 5

        silver_bounty = self._make_bounty(10, checked={"Alpha": player_id})
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(
            side_effect=lambda _db, _gid, div: [silver_bounty] if div == "silver" else []
        )

        with patch("sqlalchemy.orm.attributes.flag_modified"):
            count = await svc.scrub_player_checks_outside_tier(db, player_id=player_id, guild_id=1, new_tier="Bronze")

        assert count == 1
        assert silver_bounty.checked["Alpha"] == FORFEITED_CHECK


# ===========================================================================
# Tests: BountyService.calc_rewards — FORFEITED_CHECK sentinel exclusion
# ===========================================================================


class TestCalcRewardsForfeited:
    """Verify FORFEITED_CHECK (-2) and UNCHECKED (-1) are excluded from reward payout."""

    def _make_service(self):
        return BountyService.__new__(BountyService)

    def _make_bounty(self, checked: dict, answer: str, reward: int = 5000, reward_per_sys: int = 200):
        return SimpleNamespace(checked=checked, answer=answer, reward=reward, reward_per_sys=reward_per_sys)

    @pytest.mark.asyncio
    async def test_forfeited_checker_excluded_from_payout(self):
        """FORFEITED_CHECK (-2) entries do not receive any credits."""
        from services.bounty_service import FORFEITED_CHECK

        svc = self._make_service()
        bounty = self._make_bounty(
            checked={"Sol": 1, "Alpha": FORFEITED_CHECK, "Beta": FORFEITED_CHECK},
            answer="Sol",
        )

        rewards = await svc.calc_rewards(MagicMock(), bounty)

        for r in rewards:
            assert r.player_id > 0  # no sentinel IDs in payout

    @pytest.mark.asyncio
    async def test_unchecked_sentinel_excluded_from_payout(self):
        """UNCHECKED (-1) entries are excluded from reward distribution."""
        from services.bounty_service import UNCHECKED

        svc = self._make_service()
        bounty = self._make_bounty(
            checked={"Sol": 1, "Alpha": UNCHECKED, "Beta": UNCHECKED},
            answer="Sol",
        )

        rewards = await svc.calc_rewards(MagicMock(), bounty)

        for r in rewards:
            assert r.player_id > 0

    @pytest.mark.asyncio
    async def test_winner_gets_full_reward_when_all_other_checks_forfeited(self):
        """When all non-winner checks are forfeited the consolation pool is undepleted — winner takes all."""
        from services.bounty_service import FORFEITED_CHECK

        svc = self._make_service()
        bounty = self._make_bounty(
            checked={"Sol": 1, "Alpha": FORFEITED_CHECK, "Beta": FORFEITED_CHECK},
            answer="Sol",
            reward=5000,
        )

        rewards = await svc.calc_rewards(MagicMock(), bounty)

        winner_reward = next(r for r in rewards if r.player_id == 1)
        assert winner_reward.credits_earned == 5000
        assert winner_reward.is_winner is True

    @pytest.mark.asyncio
    async def test_real_checker_still_paid_alongside_forfeited(self):
        """A real non-winner checker is still paid even when other checkers are forfeited."""
        from services.bounty_service import FORFEITED_CHECK

        svc = self._make_service()
        bounty = self._make_bounty(
            checked={"Sol": 1, "Alpha": 2, "Beta": FORFEITED_CHECK},
            answer="Sol",
            reward=5000,
            reward_per_sys=200,
        )

        rewards = await svc.calc_rewards(MagicMock(), bounty)

        player_ids = [r.player_id for r in rewards]
        assert 1 in player_ids  # winner
        assert 2 in player_ids  # real checker
        for r in rewards:
            assert r.player_id > 0


# ===========================================================================
# Tests: _post_capture_payout (C.3 — 422 fix + display_name + payload shape)
# ===========================================================================


class TestPostCapturePayoutPayloadShape:
    """Verify _post_capture_payout sends the correct JSON payload shape (C.3 422-fix).

    The 422 error was caused by sending {"embeds": [embed_dict]} instead of
    {"content": embed_dict, "text_content": None}.  These tests assert the
    correct payload shape and the display_name fallback chain.
    """

    # Helper factories
    @staticmethod
    def _make_config(hunting_channel_id=9999):
        cfg = MagicMock()
        cfg.hunting_channel_id = hunting_channel_id
        return cfg

    @staticmethod
    def _make_bounty(
        bounty_id=42,
        criminal_name="Pal Tyyrt",
        division="gold",
        reward=80000,
        reward_per_sys=3000,
        win_user_id=101,
        route=None,
    ):
        b = MagicMock()
        b.id = bounty_id
        b.criminal_name = criminal_name
        b.division = division
        b.reward = reward
        b.reward_per_sys = reward_per_sys
        b.win_user_id = win_user_id
        b.route = route or ["Pan", "Mido", "Pescal Ansen"]
        return b

    @staticmethod
    def _make_outcome(reward=80000, total_reward=100000, bonus_won=True):
        o = MagicMock()
        o.reward = reward
        o.total_reward = total_reward
        o.bonus_won = bonus_won
        # Explicitly set combat_result to None so build_capture_payout_embed
        # skips the combat summary section (Bug 2B fix: getattr uses this value).
        o.combat_result = None
        return o

    @staticmethod
    def _make_user(discord_username="SamX", display_name=None):
        u = MagicMock()
        u.discord_username = discord_username
        u.display_name = display_name
        return u

    @pytest.fixture
    def service(self):
        from services.bounty_service import BountyService

        return BountyService()

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_payload_sends_content_not_embeds(self, service, mock_db):
        """C.3: _post_capture_payout must send {content: ..., text_content: None}, NOT {embeds: [...]}."""
        captured_json: list = []

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, timeout=None):
                captured_json.append(json)
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                return resp

        config = self._make_config(hunting_channel_id=12345)
        bounty = self._make_bounty(win_user_id=None)
        outcome = self._make_outcome()

        with (
            patch(
                "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
                new=AsyncMock(return_value=config),
            ),
            patch(
                "persist.repositories.user_repository.UserRepository.get_by_discord_id",
                new=AsyncMock(return_value=None),
            ),
            patch("httpx.AsyncClient", MockAsyncClient),
        ):
            # Should not raise
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

        assert len(captured_json) == 1
        payload = captured_json[0]
        # C.3 fix: must use 'content' key, NOT 'embeds'
        assert "content" in payload, f"Payload must have 'content' key, got keys: {list(payload.keys())}"
        assert "embeds" not in payload, "Payload must NOT have 'embeds' key after C.3 fix"
        assert "text_content" in payload, "Payload must have 'text_content' key"
        assert payload["text_content"] is None

    @pytest.mark.asyncio
    async def test_payload_content_is_embed_dict(self, service, mock_db):
        """C.3: The 'content' value must be a dict (the embed payload)."""
        captured_json: list = []

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, timeout=None):
                captured_json.append(json)
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                return resp

        config = self._make_config(hunting_channel_id=12345)
        bounty = self._make_bounty(win_user_id=None)
        outcome = self._make_outcome()

        with (
            patch(
                "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
                new=AsyncMock(return_value=config),
            ),
            patch(
                "persist.repositories.user_repository.UserRepository.get_by_discord_id",
                new=AsyncMock(return_value=None),
            ),
            patch("httpx.AsyncClient", MockAsyncClient),
        ):
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

        assert len(captured_json) == 1
        content = captured_json[0]["content"]
        assert isinstance(content, dict), f"'content' must be a dict, got {type(content)}"
        # Must have embed title and color
        assert content.get("title") == "💰 Bounty Captured!", f"Embed title wrong: {content.get('title')}"
        assert content.get("color") == 0xFFD700, "Embed color must be gold (0xFFD700)"

    @pytest.mark.asyncio
    async def test_winner_name_prefers_display_name_over_username(self, service, mock_db):
        """C.3: winner_name prefers display_name → discord_username → 'A bounty hunter'."""
        captured_json: list = []

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, timeout=None):
                captured_json.append(json)
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                return resp

        config = self._make_config(hunting_channel_id=12345)
        bounty = self._make_bounty(win_user_id=101)
        outcome = self._make_outcome()
        # User with BOTH display_name and discord_username set
        user = self._make_user(discord_username="SamX_username", display_name="SamX Display")

        with (
            patch(
                "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
                new=AsyncMock(return_value=config),
            ),
            patch(
                "persist.repositories.user_repository.UserRepository.get_by_discord_id",
                new=AsyncMock(return_value=user),
            ),
            patch("httpx.AsyncClient", MockAsyncClient),
        ):
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

        assert len(captured_json) == 1
        embed = captured_json[0]["content"]
        fields = {f["name"]: f["value"] for f in embed.get("fields", [])}
        # display_name should win over discord_username
        assert fields.get("⚔️ Claimed by") == "SamX Display", (
            f"Expected display_name='SamX Display' to win, got: {fields.get('⚔️ Claimed by')}"
        )

    @pytest.mark.asyncio
    async def test_winner_name_falls_back_to_username_when_no_display_name(self, service, mock_db):
        """C.3: Falls back to discord_username when display_name is None."""
        captured_json: list = []

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, timeout=None):
                captured_json.append(json)
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                return resp

        config = self._make_config(hunting_channel_id=12345)
        bounty = self._make_bounty(win_user_id=101)
        outcome = self._make_outcome()
        # User with display_name=None → should fall back to discord_username
        user = self._make_user(discord_username="SamX_username", display_name=None)

        with (
            patch(
                "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
                new=AsyncMock(return_value=config),
            ),
            patch(
                "persist.repositories.user_repository.UserRepository.get_by_discord_id",
                new=AsyncMock(return_value=user),
            ),
            patch("httpx.AsyncClient", MockAsyncClient),
        ):
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

        assert len(captured_json) == 1
        embed = captured_json[0]["content"]
        fields = {f["name"]: f["value"] for f in embed.get("fields", [])}
        assert fields.get("⚔️ Claimed by") == "SamX_username", (
            f"Expected discord_username='SamX_username' as fallback, got: {fields.get('⚔️ Claimed by')}"
        )

    @pytest.mark.asyncio
    async def test_winner_name_falls_back_to_default_when_no_user(self, service, mock_db):
        """C.3: Falls back to 'A bounty hunter' when user record not found."""
        captured_json: list = []

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, timeout=None):
                captured_json.append(json)
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                return resp

        config = self._make_config(hunting_channel_id=12345)
        bounty = self._make_bounty(win_user_id=101)
        outcome = self._make_outcome()

        with (
            patch(
                "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
                new=AsyncMock(return_value=config),
            ),
            patch(
                "persist.repositories.user_repository.UserRepository.get_by_discord_id",
                new=AsyncMock(return_value=None),
            ),
            patch("httpx.AsyncClient", MockAsyncClient),
        ):
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

        assert len(captured_json) == 1
        embed = captured_json[0]["content"]
        fields = {f["name"]: f["value"] for f in embed.get("fields", [])}
        assert fields.get("⚔️ Claimed by") == "A bounty hunter", (
            f"Expected 'A bounty hunter' default, got: {fields.get('⚔️ Claimed by')}"
        )

    @pytest.mark.asyncio
    async def test_exception_is_caught_and_logged_as_warning(self, service, mock_db):
        """C.3: _post_capture_payout is non-fatal — exceptions must not propagate."""
        config = self._make_config(hunting_channel_id=12345)
        bounty = self._make_bounty(win_user_id=None)
        outcome = self._make_outcome()

        class ExplodingAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, timeout=None):
                raise RuntimeError("Gateway connection refused")

        with (
            patch(
                "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
                new=AsyncMock(return_value=config),
            ),
            patch(
                "persist.repositories.user_repository.UserRepository.get_by_discord_id",
                new=AsyncMock(return_value=None),
            ),
            patch("httpx.AsyncClient", ExplodingAsyncClient),
        ):
            # Must NOT raise — non-fatal
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

    @pytest.mark.asyncio
    async def test_skips_when_no_config(self, service, mock_db):
        """C.3: When no config exists, _post_capture_payout silently returns."""
        bounty = self._make_bounty()
        outcome = self._make_outcome()

        with patch(
            "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
            new=AsyncMock(return_value=None),
        ):
            # Should not raise and should NOT attempt any HTTP call
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

    @pytest.mark.asyncio
    async def test_skips_when_no_hunting_channel(self, service, mock_db):
        """C.3: When hunting_channel_id is None, silently returns."""
        config = self._make_config(hunting_channel_id=None)
        bounty = self._make_bounty()
        outcome = self._make_outcome()

        with patch(
            "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
            new=AsyncMock(return_value=config),
        ):
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

    @pytest.mark.asyncio
    async def test_sys_checks_field_absent_when_no_reward_per_sys(self, service, mock_db):
        """C.3/C.2: System Checks field omitted when reward_per_sys is None."""
        captured_json: list = []

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, timeout=None):
                captured_json.append(json)
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                return resp

        config = self._make_config(hunting_channel_id=12345)
        # Bounty with no reward_per_sys
        bounty = self._make_bounty(win_user_id=None, reward_per_sys=None)
        bounty.reward_per_sys = None
        outcome = self._make_outcome()

        with (
            patch(
                "persist.repositories.config_repository.ConfigRepository.get_by_guild_id",
                new=AsyncMock(return_value=config),
            ),
            patch(
                "persist.repositories.user_repository.UserRepository.get_by_discord_id",
                new=AsyncMock(return_value=None),
            ),
            patch("httpx.AsyncClient", MockAsyncClient),
        ):
            await service._post_capture_payout(mock_db, guild_id=1, bounty=bounty, outcome=outcome)

        assert len(captured_json) == 1
        embed = captured_json[0]["content"]
        field_names = [f["name"] for f in embed.get("fields", [])]
        assert "📍 System Checks" not in field_names, "System Checks field must be absent when reward_per_sys is None"


# ===========================================================================
# Tests: _build_payout_breakdown — new private method (capture redesign)
# ===========================================================================


class TestBuildPayoutBreakdown:
    """Unit tests for BountyService._build_payout_breakdown().

    Acceptance criteria:
    - Winner (is_winner=True) gets role 'capture claim'
    - Non-winner (is_winner=False) gets role 'system check'
    - display_name used when available and non-empty
    - Falls back to str(player.user_id) when display_name is absent/None/empty
    - Falls back to str(reward.player_id) when player not found
    - Returns empty list when rewards is empty
    - Amount is credits_earned from RewardInfo

    P6-T1 update: _build_payout_breakdown now calls player_repo.get_by_ids
    (batched WHERE id IN) instead of per-reward get_by_id.  Tests mock
    player_repo.get_by_ids rather than player_repo.get_by_id.
    """

    @pytest.fixture
    def service(self):
        svc = BountyService()
        svc.player_repo = MagicMock()
        return svc

    def _make_reward_info(self, player_id=1, credits_earned=5000, is_winner=True, xp_earned=0):
        from services.bounty_service import RewardInfo

        return RewardInfo(
            player_id=player_id,
            credits_earned=credits_earned,
            xp_earned=xp_earned,
            is_winner=is_winner,
            systems_checked_count=1,
        )

    def _make_player(self, player_id=1, user_id=100, display_name="SamX"):
        p = SimpleNamespace()
        p.id = player_id
        p.user_id = user_id
        p.display_name = display_name
        return p

    @pytest.mark.asyncio
    async def test_winner_gets_capture_claim_role(self, service):
        """RewardInfo with is_winner=True → role='capture claim'."""
        reward = self._make_reward_info(is_winner=True, credits_earned=5000)
        player = self._make_player(display_name="WinnerPlayer")
        service.player_repo.get_by_ids = AsyncMock(return_value=[player])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [reward])

        assert len(result) == 1
        assert result[0]["role"] == "capture claim", (
            f"Winner must have role='capture claim', got: {result[0]['role']!r}"
        )

    @pytest.mark.asyncio
    async def test_non_winner_gets_system_check_role(self, service):
        """RewardInfo with is_winner=False → role='system check'."""
        reward = self._make_reward_info(is_winner=False, credits_earned=200)
        player = self._make_player(display_name="CheckerPlayer")
        service.player_repo.get_by_ids = AsyncMock(return_value=[player])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [reward])

        assert len(result) == 1
        assert result[0]["role"] == "system check", (
            f"Non-winner must have role='system check', got: {result[0]['role']!r}"
        )

    @pytest.mark.asyncio
    async def test_display_name_used_when_present(self, service):
        """player_display_name uses Player.display_name when it is non-empty."""
        reward = self._make_reward_info(is_winner=True)
        player = self._make_player(user_id=555, display_name="SamAccountX")
        service.player_repo.get_by_ids = AsyncMock(return_value=[player])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [reward])

        assert result[0]["player_display_name"] == "SamAccountX"

    @pytest.mark.asyncio
    async def test_falls_back_to_user_id_when_display_name_none(self, service):
        """Falls back to str(player.user_id) when display_name is None."""
        reward = self._make_reward_info(player_id=1)
        player = self._make_player(user_id=123456, display_name=None)
        service.player_repo.get_by_ids = AsyncMock(return_value=[player])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [reward])

        assert result[0]["player_display_name"] == "123456", "When display_name is None, must fall back to str(user_id)"

    @pytest.mark.asyncio
    async def test_falls_back_to_user_id_when_display_name_empty_string(self, service):
        """Falls back to str(player.user_id) when display_name is empty string."""
        reward = self._make_reward_info(player_id=1)
        player = self._make_player(user_id=789, display_name="")
        service.player_repo.get_by_ids = AsyncMock(return_value=[player])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [reward])

        assert result[0]["player_display_name"] == "789", (
            "When display_name is empty string, must fall back to str(user_id)"
        )

    @pytest.mark.asyncio
    async def test_skips_entry_when_player_not_found(self, service):
        """When player is not found in DB, the entry is silently skipped."""
        reward = self._make_reward_info(player_id=999)
        # Return empty list → no players found → entry skipped
        service.player_repo.get_by_ids = AsyncMock(return_value=[])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [reward])

        # Entry should be silently skipped (not included in breakdown)
        assert result == [], "Player not found should be silently skipped, not included in breakdown"

    @pytest.mark.asyncio
    async def test_empty_rewards_returns_empty_list(self, service):
        """Empty rewards list returns empty breakdown."""
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [])

        assert result == []

    @pytest.mark.asyncio
    async def test_amount_matches_credits_earned(self, service):
        """breakdown entry 'amount' must equal reward.credits_earned."""
        reward = self._make_reward_info(credits_earned=7049, is_winner=True)
        player = self._make_player(display_name="SamX")
        service.player_repo.get_by_ids = AsyncMock(return_value=[player])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [reward])

        assert result[0]["amount"] == 7049

    @pytest.mark.asyncio
    async def test_multiple_rewards_correct_roles(self, service):
        """Multiple rewards produce correct winner/checker role assignments."""
        winner_reward = self._make_reward_info(player_id=1, credits_earned=5000, is_winner=True)
        checker1_reward = self._make_reward_info(player_id=2, credits_earned=200, is_winner=False)
        checker2_reward = self._make_reward_info(player_id=3, credits_earned=100, is_winner=False)

        winner_player = self._make_player(player_id=1, display_name="Winner")
        checker1_player = self._make_player(player_id=2, display_name="Checker1")
        checker2_player = self._make_player(player_id=3, display_name="Checker2")

        # get_by_ids returns all three players; output order must follow rewards list
        service.player_repo.get_by_ids = AsyncMock(return_value=[winner_player, checker1_player, checker2_player])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [winner_reward, checker1_reward, checker2_reward])

        assert len(result) == 3
        roles_by_name = {entry["player_display_name"]: entry["role"] for entry in result}
        assert roles_by_name["Winner"] == "capture claim"
        assert roles_by_name["Checker1"] == "system check"
        assert roles_by_name["Checker2"] == "system check"

    @pytest.mark.asyncio
    async def test_result_dict_has_required_keys(self, service):
        """Each entry in payout_breakdown must have player_display_name, role, amount."""
        reward = self._make_reward_info(is_winner=True, credits_earned=3000)
        player = self._make_player(display_name="X")
        service.player_repo.get_by_ids = AsyncMock(return_value=[player])
        mock_db = AsyncMock()

        result = await service._build_payout_breakdown(mock_db, [reward])

        assert len(result) == 1
        entry = result[0]
        assert "player_display_name" in entry
        assert "role" in entry
        assert "amount" in entry


# ===========================================================================
# Tests: CI-17 — Criminal secondary weapon generation
# ===========================================================================


def _make_loadout_for_secondary_test(
    db,
    service,
    ship,
    all_secondaries: list,
    *,
    tech_level: int = 3,
):
    """Shared async helper factory — call inside an async test body.

    Returns an awaitable calling service.generate_loadout with:
    - ship selection mocked to return ``ship``
    - SecondaryWeaponRepository.list_all mocked to return ``all_secondaries``
    - item_repo returning empty lists (no primaries/turrets needed)
    - _find_typed_module returning None (no modules needed)
    """

    async def _run():
        with (
            patch.object(service, "find_item_tl", new=AsyncMock(return_value=1)),
            patch.object(service, "_find_typed_module", new=AsyncMock(return_value=None)),
            patch(
                "services.bounty_service.SecondaryWeaponRepository",
                return_value=MagicMock(list_all=AsyncMock(return_value=all_secondaries)),
            ),
        ):
            service.item_repo.get_all_by_tech_level = AsyncMock(return_value=[])
            _setup_mock_db_query(db, [ship])
            return await service.generate_loadout(db, tech_level=tech_level)

    return _run()


class TestCi17SecondaryGeneration:
    """CI-17: criminal secondary weapon generation in generate_loadout."""

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _make_db():
        db = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    # -----------------------------------------------------------------------
    # TL0 Betty unchanged
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_tl0_betty_unchanged_secondaries_empty(self, service):
        """TL0 Betty path returns empty secondaries (no generation, no crash)."""
        result = await service.generate_loadout(self._make_db(), tech_level=0)
        assert result["secondaries"] == []
        assert result["ship_max_secondaries"] == 0

    # -----------------------------------------------------------------------
    # Edge: max_secondaries=0
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_max_secondaries_zero_returns_empty(self, service):
        """Ship with max_secondaries=0 → secondaries=[] (block skipped, no crash)."""
        ship = _make_ship("Scout", value=50000, max_primaries=1, max_secondaries=0)
        db = self._make_db()
        secondary = _make_secondary("Nuclear Nuke", tech_level=1, subtype="nuke", damage=800)

        result = await _make_loadout_for_secondary_test(db, service, ship, [secondary])

        assert result["secondaries"] == []
        assert result["ship_max_secondaries"] == 0

    # -----------------------------------------------------------------------
    # Edge: deferred-only pool → empty, no crash
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_deferred_only_pool_returns_empty(self, service):
        """TL whose only secondaries are deferred subtypes → secondaries=[] no crash."""
        ship = _make_ship("Warrior", value=200000, max_primaries=2, max_secondaries=3)
        db = self._make_db()
        # All deferred subtypes
        deferred_weapons = [
            _make_secondary("EMP Bomb", tech_level=1, subtype="emp-bomb", damage=500),
            _make_secondary("Land Mine", tech_level=1, subtype="mine", damage=300),
            _make_secondary("Sentry Gun", tech_level=1, subtype="sentry-gun", damage=200),
        ]

        result = await _make_loadout_for_secondary_test(db, service, ship, deferred_weapons)

        assert result["secondaries"] == []

    # -----------------------------------------------------------------------
    # Edge: dead-weight exclusion (damage ≤ threshold)
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_dead_weight_damage_excluded(self, service):
        """Secondaries with damage ≤ CRIMINAL_SECONDARY_MIN_DAMAGE are excluded."""
        from services.game_constants import GameConstants

        ship = _make_ship("Warrior", value=200000, max_primaries=2, max_secondaries=3)
        db = self._make_db()
        # damage=0 and damage=1 should be excluded (default threshold=1)
        zero_dmg = _make_secondary("Zero Dmg", tech_level=1, subtype="missile", damage=0)
        one_dmg = _make_secondary("One Dmg", tech_level=1, subtype="missile", damage=1)
        good = _make_secondary("Good Missile", tech_level=1, subtype="missile", damage=100)

        result = await _make_loadout_for_secondary_test(db, service, ship, [zero_dmg, one_dmg, good])

        names = [s["name"] for s in result["secondaries"]]
        assert "Zero Dmg" not in names
        assert "One Dmg" not in names
        assert "Good Missile" in names

    # -----------------------------------------------------------------------
    # Generation: length ≤ max_secondaries
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_secondaries_count_respects_max_secondaries(self, service):
        """Generated secondaries count never exceeds ship.max_secondaries."""
        ship = _make_ship("Groza", value=251600, max_primaries=3, max_secondaries=2)
        db = self._make_db()
        pool = [
            _make_secondary("Rocket A", tech_level=1, subtype="rocket", damage=200),
            _make_secondary("Missile B", tech_level=1, subtype="missile", damage=300),
            _make_secondary("Nuke C", tech_level=1, subtype="nuke", damage=800),
        ]

        result = await _make_loadout_for_secondary_test(db, service, ship, pool)

        assert len(result["secondaries"]) <= ship.max_secondaries

    # -----------------------------------------------------------------------
    # Generation: distinct names (no duplicates)
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_secondaries_names_are_distinct(self, service):
        """Sampled secondaries have no duplicate names (slot=type model)."""
        ship = _make_ship("Groza", value=251600, max_primaries=3, max_secondaries=3)
        db = self._make_db()
        pool = [
            _make_secondary("Rocket A", tech_level=1, subtype="rocket", damage=200),
            _make_secondary("Missile B", tech_level=1, subtype="missile", damage=300),
            _make_secondary("Nuke C", tech_level=1, subtype="nuke", damage=800),
        ]

        result = await _make_loadout_for_secondary_test(db, service, ship, pool)

        names = [s["name"] for s in result["secondaries"]]
        assert len(names) == len(set(names)), f"Duplicate secondary names found: {names}"

    # -----------------------------------------------------------------------
    # Generation: no deferred subtypes in output
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_deferred_subtypes_never_in_output(self, service):
        """Deferred subtypes (emp-bomb/mine/sentry-gun) never appear in output."""
        from services.combat_models import DEFERRED_SECONDARY_SUBTYPES

        ship = _make_ship("Groza", value=251600, max_primaries=3, max_secondaries=3)
        db = self._make_db()
        pool = [
            _make_secondary("EMP Bomb", tech_level=1, subtype="emp-bomb", damage=500),
            _make_secondary("Good Nuke", tech_level=1, subtype="nuke", damage=800),
            _make_secondary("Mine", tech_level=1, subtype="mine", damage=300),
        ]

        result = await _make_loadout_for_secondary_test(db, service, ship, pool)

        subtypes = [s["subtype"] for s in result["secondaries"]]
        for st in subtypes:
            assert st not in DEFERRED_SECONDARY_SUBTYPES, f"Deferred subtype {st!r} found in output"

    # -----------------------------------------------------------------------
    # Generation: all combat fields present
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_secondaries_carry_all_combat_fields(self, service):
        """Each secondary dict carries the full set of combat fields."""
        ship = _make_ship("Groza", value=251600, max_primaries=3, max_secondaries=2)
        db = self._make_db()
        pool = [
            _make_secondary(
                "Nuke Alpha",
                tech_level=1,
                subtype="nuke",
                damage=800,
                loading_speed_ms=3000,
                range_m=2000.0,
                burst_count=0,
                emp_damage=0,
                magnitude_m=500.0,
                steerable=True,
            ),
        ]

        result = await _make_loadout_for_secondary_test(db, service, ship, pool)

        assert len(result["secondaries"]) == 1
        s = result["secondaries"][0]
        required_fields = {
            "name",
            "emoji",
            "value",
            "dps",
            "rounds",
            "damage",
            "loading_speed_ms",
            "range_m",
            "subtype",
            "burst_count",
            "emp_damage",
            "magnitude_m",
            "steerable",
        }
        assert required_fields.issubset(set(s.keys())), f"Missing fields: {required_fields - set(s.keys())}"

    # -----------------------------------------------------------------------
    # Generation: rounds ≥ 1
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_secondaries_rounds_at_least_1(self, service):
        """All generated secondaries have rounds ≥ 1 (floor applied)."""
        ship = _make_ship("Groza", value=251600, max_primaries=3, max_secondaries=3)
        db = self._make_db()
        pool = [
            _make_secondary("Rocket X", tech_level=1, subtype="rocket", damage=200),
            _make_secondary("Shock Y", tech_level=1, subtype="shock-blast", damage=150),
        ]

        result = await _make_loadout_for_secondary_test(db, service, ship, pool)

        for s in result["secondaries"]:
            assert s["rounds"] >= 1, f"rounds < 1 for {s['name']!r}: {s['rounds']}"

    # -----------------------------------------------------------------------
    # Knob #1: nuke rounds == configured cap
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_nuke_rounds_equal_configured_cap(self, service):
        """Nuke secondaries get exactly CRIMINAL_SECONDARY_ROUNDS['nuke'] rounds."""
        from services.game_constants import GameConstants

        ship = _make_ship("Groza", value=251600, max_primaries=3, max_secondaries=2)
        db = self._make_db()
        pool = [_make_secondary("Nuke X", tech_level=1, subtype="nuke", damage=800)]

        result = await _make_loadout_for_secondary_test(db, service, ship, pool)

        nuke_entries = [s for s in result["secondaries"] if s["subtype"] == "nuke"]
        assert len(nuke_entries) == 1
        assert nuke_entries[0]["rounds"] == GameConstants.CRIMINAL_SECONDARY_ROUNDS["nuke"]

    # -----------------------------------------------------------------------
    # Knob #4: value counted once per type
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_secondary_value_added_once_per_type(self, service):
        """total_value includes each secondary's value exactly once."""
        ship = _make_ship("Groza", value=100000, max_primaries=0, max_secondaries=2)
        db = self._make_db()
        pool = [
            _make_secondary("Nuke X", tech_level=1, subtype="nuke", damage=800, value=5000),
            _make_secondary("Missile Y", tech_level=1, subtype="missile", damage=200, value=3000),
        ]

        result = await _make_loadout_for_secondary_test(db, service, ship, pool)

        secondary_values = sum(s["value"] for s in result["secondaries"])
        assert result["total_value"] == ship.value + secondary_values

    # -----------------------------------------------------------------------
    # Multiple TLs (TLs 1/3/4/5/9)
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tl", [1, 3, 4, 5, 9])
    async def test_generation_across_tls_stable(self, service, tl):
        """Generation at various TLs produces valid secondaries (distinct, no deferred, rounds≥1)."""
        from services.combat_models import DEFERRED_SECONDARY_SUBTYPES

        ship = _make_ship("Warship", value=500000, max_primaries=2, max_secondaries=2)
        db = self._make_db()
        item_tl = max(1, tl - 1)
        pool = [
            _make_secondary("Missile A", tech_level=item_tl, subtype="missile", damage=200),
            _make_secondary("Rocket B", tech_level=item_tl, subtype="rocket", damage=300),
        ]

        result = await _make_loadout_for_secondary_test(db, service, ship, pool, tech_level=tl)

        secondaries = result["secondaries"]
        assert len(secondaries) <= ship.max_secondaries
        names = [s["name"] for s in secondaries]
        assert len(names) == len(set(names)), "Duplicate names"
        for s in secondaries:
            assert s["subtype"] not in DEFERRED_SECONDARY_SUBTYPES
            assert s["rounds"] >= 1

    # -----------------------------------------------------------------------
    # ship_max_secondaries key is present
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_ship_max_secondaries_key_present(self, service):
        """generate_loadout always includes ship_max_secondaries in the return dict."""
        ship = _make_ship("Scout", value=50000, max_primaries=1, max_secondaries=2)
        db = self._make_db()

        result = await _make_loadout_for_secondary_test(db, service, ship, [])

        assert "ship_max_secondaries" in result
        assert result["ship_max_secondaries"] == 2


# ===========================================================================
# Tests: CI-17 — helper functions
# ===========================================================================


class TestCi17Helpers:
    """Unit tests for the module-level helper functions added in CI-17."""

    def test_get_secondary_subtype_unwraps_inner_extra_atts(self):
        """get_secondary_subtype reads subtype from the inner extra_atts dict."""
        item = SimpleNamespace(extra_atts={"extra_atts": {"subtype": "nuke"}})
        assert get_secondary_subtype(item) == "nuke"

    def test_get_secondary_subtype_missing_returns_empty(self):
        """get_secondary_subtype returns '' when extra_atts is absent/empty."""
        item = SimpleNamespace(extra_atts={})
        assert get_secondary_subtype(item) == ""

    def test_get_secondary_subtype_flat_fallback(self):
        """get_secondary_subtype falls back to outer dict for flat seeds."""
        item = SimpleNamespace(extra_atts={"subtype": "missile"})
        assert get_secondary_subtype(item) == "missile"

    def test_extract_secondary_combat_fields_all_fields(self):
        """_extract_secondary_combat_fields returns all required combat fields."""
        item = SimpleNamespace(
            damage=800,
            extra_atts={
                "extra_atts": {
                    "loading_speed_ms": 3000,
                    "range_m": 2000.0,
                    "subtype": "nuke",
                    "burst_count": 0,
                    "emp_damage": 50,
                    "magnitude_m": 500.0,
                    "steerable": True,
                }
            },
        )
        fields = _extract_secondary_combat_fields(item)
        assert fields["damage"] == 800
        assert fields["loading_speed_ms"] == 3000
        assert fields["range_m"] == 2000.0
        assert fields["subtype"] == "nuke"
        assert fields["burst_count"] == 0
        assert fields["emp_damage"] == 50
        assert fields["magnitude_m"] == 500.0
        assert fields["steerable"] is True

    def test_extract_secondary_combat_fields_defaults(self):
        """_extract_secondary_combat_fields provides safe defaults for missing fields."""
        item = SimpleNamespace(damage=0, extra_atts={})
        fields = _extract_secondary_combat_fields(item)
        assert fields["damage"] == 0
        assert fields["loading_speed_ms"] == 0
        assert fields["range_m"] == 0.0
        assert fields["subtype"] == ""
        assert fields["burst_count"] == 0
        assert fields["emp_damage"] == 0
        assert fields["magnitude_m"] == 0.0
        assert fields["steerable"] is False


@pytest.mark.asyncio
async def test_generate_loadout_no_turret_slots_skips_turret_selection(service, mock_db):
    """Ships with max_turrets=0 never enter the turret selection block (no fallback either)."""
    ship = _make_ship("Betty", value=16038, max_primaries=1, max_modules=2, max_turrets=0)

    async def _get_all_by_tl(db, tl, item_type=None):
        if item_type == "turret_weapon":
            # This should never be called for a ship with max_turrets=0
            raise AssertionError("turret_weapon query must NOT be called when max_turrets=0")
        return []

    service.item_repo.get_all_by_tech_level = _get_all_by_tl

    with patch.object(service, "find_item_tl", new=AsyncMock(return_value=-1)):
        _setup_mock_db_query(mock_db, [ship])
        result = await service.generate_loadout(mock_db, tech_level=2)

    assert result["turrets"] == []
