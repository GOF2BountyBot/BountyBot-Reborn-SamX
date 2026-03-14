"""
Unit tests for BountyService.

All repository calls are mocked via AsyncMock so no real DB is needed.
The shared.bblogger module is already mocked in conftest.py.
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

from services.bounty_service import BountyService, CheckResult

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
) -> SimpleNamespace:
    """Return a Ship-like SimpleNamespace."""
    return SimpleNamespace(
        name=name,
        value=value,
        max_primaries=max_primaries,
        max_modules=max_modules,
        max_turrets=max_turrets,
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
) -> SimpleNamespace:
    """Return a Module-like SimpleNamespace."""
    return SimpleNamespace(name=name, value=value, tech_level=tech_level)


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
    """Return a BountyService with all repositories replaced by MagicMocks."""
    svc = BountyService()
    svc.bounty_repo = MagicMock()
    svc.criminal_repo = MagicMock()
    svc.item_repo = MagicMock()
    svc.player_repo = MagicMock()
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
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(
        return_value=active_bounties
    )

    # Run enough times to confirm Alice is never selected
    for _ in range(20):
        result = await service.select_criminal(mock_db, guild_id=1, division="bronze")
        assert result is not None
        assert result.name == "Bob", f"Expected Bob, got {result.name}"


@pytest.mark.asyncio
async def test_select_criminal_no_available(service, mock_db):
    """Returns None when all criminals are already active in the division."""
    criminals = [_make_criminal("Alice", "terran")]
    active_bounties = [_make_bounty("Alice")]

    service.criminal_repo.list_all = AsyncMock(return_value=criminals)
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(
        return_value=active_bounties
    )

    result = await service.select_criminal(mock_db, guild_id=1, division="bronze")

    assert result is None


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
        "ship_value",
        "ship_max_primaries",
        "ship_max_modules",
        "ship_max_turrets",
        "weapons",
        "modules",
        "turrets",
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

        # generic modules list (for filling remaining slots)
        generic_mod = _make_module("Generic Module", value=500, tech_level=1)
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

        generic_mod = _make_module("Generic", value=500, tech_level=3)
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
    module1 = _make_module("E2 Exoclad", value=1070, tech_level=1)
    armour_mod = _make_module("Armour Plate", value=500, tech_level=1)

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
    spawn_service.pathfinding_service.make_route = MagicMock(
        return_value=PathfindingError.NO_ROUTE_FOUND
    )

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
    """Reward must equal reward_per_sys * len(route)."""
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
    assert b.reward == b.reward_per_sys * len(b.route)


@pytest.mark.asyncio
async def test_spawn_bounty_end_time_calculation(spawn_service, mock_db):
    """end_time must be issue_time + timedelta(days=len(route))."""
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
    expected_end = b.issue_time + timedelta(days=len(b.route))
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
        result = await spawn_service.spawn_bounty(
            mock_db, guild_id=1, division="silver", tech_level=None
        )

    # silver division maps to center_tl=5
    mock_pick_tl.assert_called_once_with(5)
    assert result is not None


# ===========================================================================
# Tests: check_bounty
# ===========================================================================


@pytest.fixture
def check_bounty_setup(service, mock_db):
    """Pre-configure common mocks for core check_bounty tests.

    Returns (service, mock_db) with player_repo, bounty_repo.get_active_by_guild_and_division,
    and bounty_repo.update pre-configured as AsyncMocks. Tests set .return_value on each.
    """
    service.player_repo.get_by_id = AsyncMock()
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
    """Return a Player-like SimpleNamespace."""
    return SimpleNamespace(
        id=player_id,
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

    result = await service.check_bounty(
        mock_db, player_id=1, system_name="Nonexistent", guild_id=1
    )

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
    service.bounty_repo.update.return_value = bounty

    result = await service.check_bounty(mock_db, player_id=1, system_name="Alpha", guild_id=1)

    assert result.result == CheckResult.INCORRECT
    assert result.bounty_id == bounty.id
    assert "No sign of" in result.message


@pytest.mark.asyncio
async def test_check_bounty_correct(check_bounty_setup):
    """Returns CORRECT when system matches the bounty answer (classic mode → auto-win)."""
    from services.bounty_service import RewardInfo

    service, mock_db = check_bounty_setup
    player = _make_player(classic_mode=True)  # auto-win, no combat
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {}
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1000, xp_earned=50, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.bounty_id == bounty.id
    assert bounty.criminal_name in result.message
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
    service.bounty_repo.update = AsyncMock(return_value=bounty)

    await service.check_bounty(mock_db, player_id=1, system_name="Alpha", guild_id=1)

    # Should query bronze division regardless of player tier
    service.bounty_repo.get_active_by_guild_and_division.assert_called_once_with(
        mock_db, 1, "bronze"
    )


# ===========================================================================
# Tests: check_bounty — combat integration
# ===========================================================================


@pytest.fixture
def combat_integration_setup(service, mock_db):
    """Pre-configure common mocks for check_bounty combat integration tests.

    Returns (service, mock_db) with player_repo, bounty_repo methods, and
    combat_service pre-configured. Tests set .return_value on each.
    """
    service.player_repo.get_by_id = AsyncMock()
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock()
    service.bounty_repo.update = AsyncMock()
    service.combat_service = MagicMock()
    return service, mock_db


@pytest.mark.asyncio
async def test_check_bounty_correct_classic_mode_auto_win(combat_integration_setup):
    """Classic mode players auto-win without combat; rewards are distributed."""
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
    assert "Defeated" in result.message
    service.calc_rewards.assert_called_once()
    service.distribute_rewards.assert_called_once()


@pytest.mark.asyncio
async def test_check_bounty_correct_no_ship_auto_win(combat_integration_setup):
    """Player with no active ship auto-wins without combat."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    player = _make_player(active_ship=None, classic_mode=False)
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
    assert "Defeated" in result.message
    service.calc_rewards.assert_called_once()
    service.distribute_rewards.assert_called_once()


@pytest.mark.asyncio
async def test_check_bounty_correct_player_wins_combat(combat_integration_setup):
    """Player with ship wins combat → rewards distributed."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=100)
    player = _make_player(active_ship=active_ship, classic_mode=False)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Raider", "ship_armour": 80, "weapons": [], "turrets": []}

    mock_fight = SimpleNamespace(winner_name="Betty", loser_name="Raider", is_stalemate=False)
    service.combat_service.fight_ships.return_value = mock_fight

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=800, xp_earned=40, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    assert "Defeated" in result.message
    service.calc_rewards.assert_called_once()
    service.distribute_rewards.assert_called_once()


@pytest.mark.asyncio
async def test_check_bounty_correct_player_loses_combat(combat_integration_setup):
    """Player loses combat → bounty escapes, escape_bounty called."""
    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=50)
    player = _make_player(active_ship=active_ship, classic_mode=False)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Dreadnought",
        "ship_armour": 500,
        "weapons": [{"name": "Cannon", "dps": 99}],
        "turrets": [],
    }

    mock_fight = SimpleNamespace(winner_name="Dreadnought", loser_name="Betty", is_stalemate=False)
    service.combat_service.fight_ships.return_value = mock_fight

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.escape_bounty = AsyncMock(return_value=(bounty, 5))

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is False
    assert "escaped" in result.message
    service.escape_bounty.assert_called_once_with(mock_db, bounty.id)


@pytest.mark.asyncio
async def test_check_bounty_correct_stalemate_counts_as_win(combat_integration_setup):
    """Stalemate result counts as player win (legacy behavior)."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=100)
    player = _make_player(active_ship=active_ship, classic_mode=False)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {"ship_name": "Raider", "ship_armour": 100, "weapons": [], "turrets": []}

    mock_fight = SimpleNamespace(winner_name=None, loser_name=None, is_stalemate=True)
    service.combat_service.fight_ships.return_value = mock_fight

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=600, xp_earned=30, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
    assert "Defeated" in result.message


@pytest.mark.asyncio
async def test_check_bounty_correct_no_criminal_ship_data(combat_integration_setup):
    """Graceful handling when criminal_ship is None (empty loadout used)."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Betty", armour=100)
    player = _make_player(active_ship=active_ship, classic_mode=False)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = None  # no ship data

    # With both sides having 0 DPS, result is a stalemate → player wins
    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=500, xp_earned=25, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])
    service.escape_bounty = AsyncMock(return_value=(bounty, 5))

    # Should not raise — graceful handling of None criminal_ship
    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    # Either wins or loses — no crash is the key test
    assert result.combat_won in (True, False)


@pytest.mark.asyncio
async def test_check_bounty_correct_combat_with_full_criminal_loadout(combat_integration_setup):
    """Criminal with weapons and turrets builds a correct loadout for combat."""
    from services.bounty_service import RewardInfo

    service, mock_db = combat_integration_setup
    active_ship = SimpleNamespace(ship_name="Falcon", armour=200)
    player = _make_player(active_ship=active_ship, classic_mode=False)
    bounty = _make_active_bounty(answer="Sol")
    bounty.criminal_ship = {
        "ship_name": "Bandit",
        "ship_armour": 150,
        "weapons": [{"name": "Blaster MK I", "dps": 15.0}, {"name": "Laser MK I", "dps": 10.0}],
        "turrets": [{"name": "Turret MK I", "dps": 5.0}],
    }

    # Capture loadouts passed to fight_ships
    captured_loadouts = {}

    def capture_fight(p_loadout, c_loadout, **kwargs):
        captured_loadouts["player"] = p_loadout
        captured_loadouts["criminal"] = c_loadout
        return SimpleNamespace(winner_name="Falcon", loser_name="Bandit", is_stalemate=False)

    service.combat_service.fight_ships.side_effect = capture_fight

    service.player_repo.get_by_id.return_value = player
    service.bounty_repo.get_active_by_guild_and_division.return_value = [bounty]
    service.bounty_repo.update.return_value = bounty
    service.calc_rewards = AsyncMock(
        return_value=[RewardInfo(player_id=1, credits_earned=1200, xp_earned=60, is_winner=True)]
    )
    service.distribute_rewards = AsyncMock(return_value=[])

    result = await service.check_bounty(mock_db, player_id=1, system_name="Sol", guild_id=1)

    assert result.result == CheckResult.CORRECT
    assert result.combat_won is True
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


def _make_full_player(
    player_id: int = 1,
    credits: int = 5000,
    lifetime_credits: int = 0,
    xp: int = 0,
    systems_checked: int = 0,
    bounty_wins: int = 0,
    classic_mode: bool = False,
) -> SimpleNamespace:
    """Return a full Player-like SimpleNamespace for reward distribution tests."""
    return SimpleNamespace(
        id=player_id,
        credits=credits,
        lifetime_credits=lifetime_credits,
        xp=xp,
        systems_checked=systems_checked,
        bounty_wins=bounty_wins,
        classic_mode=classic_mode,
    )


# ===========================================================================
# Tests: calc_rewards
# ===========================================================================


@pytest.mark.asyncio
async def test_calc_rewards_single_winner_only(service, mock_db):
    """One player checked the answer system — they get the full pool."""
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
    assert winner.credits_earned == 10000
    assert winner.systems_checked_count == 1


@pytest.mark.asyncio
async def test_calc_rewards_multi_contributor(service, mock_db):
    """3 players checked systems; contributors get rps*count, winner gets remainder."""
    # route: Alpha(p1), Beta(p2), Gamma(p2), Sol(p3=winner), Omega(-1)
    # rps=1000, pool=5000
    # p1: 1 check → 1000 credits
    # p2: 2 checks → 2000 credits
    # p3 (winner): 5000 - 1000 - 2000 = 2000 credits
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
    assert by_id[1].systems_checked_count == 1

    assert by_id[2].credits_earned == 2000
    assert by_id[2].is_winner is False
    assert by_id[2].systems_checked_count == 2

    assert by_id[3].credits_earned == 2000
    assert by_id[3].is_winner is True
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
async def test_calc_rewards_xp_calculation(service, mock_db):
    """XP earned equals credits_earned * 0.1 (BOUNTY_REWARD_TO_XP_GAIN_MULT)."""
    bounty = _make_reward_bounty(
        reward=10000,
        reward_per_sys=1000,
        route=["Alpha", "Sol"],
        answer="Sol",
        checked={"Alpha": 1, "Sol": 2},
    )

    rewards = await service.calc_rewards(mock_db, bounty)

    by_id = {r.player_id: r for r in rewards}

    # Contributor: 1000 credits → 100 xp
    assert by_id[1].credits_earned == 1000
    assert by_id[1].xp_earned == int(1000 * 0.1)

    # Winner: 10000 - 1000 = 9000 credits → 900 xp
    assert by_id[2].credits_earned == 9000
    assert by_id[2].xp_earned == int(9000 * 0.1)


@pytest.mark.asyncio
async def test_calc_rewards_winner_gets_remainder(service, mock_db):
    """Winner receives exactly pool - sum(contributor credits)."""
    # rps=500, pool=3000
    # contributor (p1): 2 checks → 1000 credits
    # winner (p2): 3000 - 1000 = 2000
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


# ===========================================================================
# Tests: distribute_rewards
# ===========================================================================


@pytest.mark.asyncio
async def test_distribute_rewards_updates_credits(service, mock_db):
    """Player credits increase by the reward amount."""
    from services.bounty_service import RewardInfo

    player = _make_full_player(player_id=1, credits=5000)
    bounty = _make_reward_bounty()
    bounty.status = "active"
    reward = RewardInfo(player_id=1, credits_earned=2000, xp_earned=200, is_winner=True)

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.update = AsyncMock()

    await service.distribute_rewards(mock_db, bounty, [reward])

    assert player.credits == 7000
    assert player.lifetime_credits == 2000


@pytest.mark.asyncio
async def test_distribute_rewards_updates_xp(service, mock_db):
    """Player XP increases by the XP reward amount (non-classic mode)."""
    from services.bounty_service import RewardInfo

    player = _make_full_player(player_id=1, xp=500, classic_mode=False)
    bounty = _make_reward_bounty()
    bounty.status = "active"
    reward = RewardInfo(player_id=1, credits_earned=1000, xp_earned=100, is_winner=False)

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.update = AsyncMock()

    await service.distribute_rewards(mock_db, bounty, [reward])

    assert player.xp == 600


@pytest.mark.asyncio
async def test_distribute_rewards_classic_mode_no_xp(service, mock_db):
    """Classic mode players receive credits but no XP."""
    from services.bounty_service import RewardInfo

    player = _make_full_player(player_id=1, xp=0, credits=1000, classic_mode=True)
    bounty = _make_reward_bounty()
    bounty.status = "active"
    reward = RewardInfo(player_id=1, credits_earned=3000, xp_earned=300, is_winner=False)

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.update = AsyncMock()

    await service.distribute_rewards(mock_db, bounty, [reward])

    assert player.credits == 4000   # credits updated
    assert player.xp == 0           # XP NOT updated for classic mode


@pytest.mark.asyncio
async def test_distribute_rewards_increments_bounty_wins(service, mock_db):
    """Winner's bounty_wins count increases by 1."""
    from services.bounty_service import RewardInfo

    player = _make_full_player(player_id=1, bounty_wins=3)
    bounty = _make_reward_bounty()
    bounty.status = "active"
    reward = RewardInfo(player_id=1, credits_earned=5000, xp_earned=500, is_winner=True)

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.update = AsyncMock()

    await service.distribute_rewards(mock_db, bounty, [reward])

    assert player.bounty_wins == 4


@pytest.mark.asyncio
async def test_distribute_rewards_increments_systems_checked(service, mock_db):
    """All players' systems_checked count is updated correctly."""
    from services.bounty_service import RewardInfo

    player1 = _make_full_player(player_id=1, systems_checked=10)
    player2 = _make_full_player(player_id=2, systems_checked=5)
    bounty = _make_reward_bounty()
    bounty.status = "active"

    rewards = [
        RewardInfo(
            player_id=1,
            credits_earned=1000,
            xp_earned=100,
            is_winner=False,
            systems_checked_count=2,
        ),
        RewardInfo(
            player_id=2,
            credits_earned=3000,
            xp_earned=300,
            is_winner=True,
            systems_checked_count=1,
        ),
    ]

    service.player_repo.get_by_id = AsyncMock(side_effect=[player1, player2])
    service.bounty_repo.update = AsyncMock()

    await service.distribute_rewards(mock_db, bounty, rewards)

    assert player1.systems_checked == 12
    assert player2.systems_checked == 6


@pytest.mark.asyncio
async def test_distribute_rewards_detects_level_up(service, mock_db):
    """leveled_up is True when XP crosses a level boundary."""
    from services.bounty_service import RewardInfo

    # XP at 900 (level 1), adding 200 xp → 1100 (level 2, boundary at 1050)
    player = _make_full_player(player_id=1, xp=900, classic_mode=False)
    bounty = _make_reward_bounty()
    bounty.status = "active"
    reward = RewardInfo(
        player_id=1,
        credits_earned=2000,
        xp_earned=200,
        is_winner=True,
    )

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.update = AsyncMock()

    updated = await service.distribute_rewards(mock_db, bounty, [reward])

    assert updated[0].leveled_up is True
    assert updated[0].level_before == 1
    assert updated[0].level_after == 2


@pytest.mark.asyncio
async def test_distribute_rewards_marks_bounty_completed(service, mock_db):
    """Bounty status is set to 'completed' after distribution."""
    from services.bounty_service import RewardInfo

    player = _make_full_player(player_id=1)
    bounty = _make_reward_bounty()
    bounty.status = "active"
    reward = RewardInfo(player_id=1, credits_earned=1000, xp_earned=100, is_winner=True)

    service.player_repo.get_by_id = AsyncMock(return_value=player)
    service.bounty_repo.update = AsyncMock()

    await service.distribute_rewards(mock_db, bounty, [reward])

    assert bounty.status == "completed"
    service.bounty_repo.update.assert_called_once()


@pytest.mark.asyncio
async def test_distribute_rewards_sets_win_user_id(service, mock_db):
    """bounty.win_user_id is set to the winner's player_id."""
    from services.bounty_service import RewardInfo

    player1 = _make_full_player(player_id=5)
    player2 = _make_full_player(player_id=9)
    bounty = _make_reward_bounty()
    bounty.status = "active"

    rewards = [
        RewardInfo(player_id=5, credits_earned=1000, xp_earned=100, is_winner=False),
        RewardInfo(player_id=9, credits_earned=4000, xp_earned=400, is_winner=True),
    ]

    service.player_repo.get_by_id = AsyncMock(side_effect=[player1, player2])
    service.bounty_repo.update = AsyncMock()

    await service.distribute_rewards(mock_db, bounty, rewards)

    assert bounty.win_user_id == 9


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
    service.pathfinding_service.make_route = MagicMock(
        return_value=["Delta", "Epsilon", "Alpha"]
    )
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
