"""
Unit tests for LoadoutBuilder.

Tests cover:
- from_criminal_ship(): pure dict → ShipLoadout, no DB needed
- from_player(): async DB-driven loadout construction (mocked)
- Integration with CombatService.collect_stats() to verify HP/DPS computation
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Guard: ensure shared.bblogger is mocked if running in isolation.
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# Stub sqlalchemy_utils (needed by model auto-import via DiscordMessage model)
if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

from services.combat_models import ModuleStats, ShipLoadout
from services.combat_service import CombatService
from services.loadout_builder import LoadoutBuilder, _module_stats_from_extra

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_criminal_ship(
    ship_name: str = "Betty",
    ship_armour: int = 95,
    weapons: list[dict] | None = None,
    turrets: list[dict] | None = None,
    modules: list[dict] | None = None,
    secondaries: list[dict] | None = None,
) -> dict:
    """Create a criminal_ship dict matching the format from BountyService."""
    return {
        "ship_name": ship_name,
        "ship_emoji": "<:betty:123>",
        "ship_armour": ship_armour,
        "armor_hp": ship_armour,
        "shield_hp": 0,
        "total_hp": ship_armour,
        "weapons": weapons or [],
        "turrets": turrets or [],
        "modules": modules or [],
        "secondaries": secondaries or [],
    }


def make_secondary_dict(
    name: str = "Nuke X",
    dps: float = 0.0,
    value: int = 5000,
    damage: int = 800,
    subtype: str = "nuke",
    loading_speed_ms: int = 3000,
    range_m: float = 2000.0,
    burst_count: int = 0,
    emp_damage: int = 0,
    magnitude_m: float = 500.0,
    steerable: bool = True,
    rounds: int = 1,
) -> dict:
    """Create a secondary weapon dict matching the CI-17 generate_loadout format."""
    return {
        "name": name,
        "emoji": None,
        "dps": dps,
        "value": value,
        "damage": damage,
        "loading_speed_ms": loading_speed_ms,
        "range_m": range_m,
        "subtype": subtype,
        "burst_count": burst_count,
        "emp_damage": emp_damage,
        "magnitude_m": magnitude_m,
        "steerable": steerable,
        "rounds": rounds,
    }


def make_weapon_dict(name: str = "128MJ Railgun", dps: float = 25.0, value: int = 24675) -> dict:
    """Create a weapon dict matching the BountyService format."""
    return {"name": name, "emoji": "<:weapon:1>", "dps": dps, "value": value}


def make_turret_dict(name: str = "Turret X", dps: float = 10.0, value: int = 5000) -> dict:
    """Create a turret dict matching the BountyService format."""
    return {"name": name, "emoji": "<:turret:1>", "dps": dps, "value": value}


def make_module_dict(
    name: str = "E2 Exoclad",
    module_type: str = "ArmourModule",
    value: int = 1070,
    tech_level: int = 1,
    extra_atts: dict | None = None,
) -> dict:
    """Create a module dict matching the BountyService format."""
    return {
        "name": name,
        "emoji": "<:module:1>",
        "type": module_type,
        "value": value,
        "tech_level": tech_level,
        "extra_atts": extra_atts or {},
    }


def make_player(player_id: int = 1, active_ship_id: int | None = 1):
    """Real Player ORM instance (no session — plain kwargs).

    from_player() only reads .active_ship_id off the Player it gets back from
    player_repo, so that's the only attribute that matters here; id is set for
    realism/identity.
    """
    from persist.models.player import Player

    return Player(id=player_id, active_ship_id=active_ship_id)


def make_player_ship(
    ship_id: int = 1,
    ship_name: str = "Betty",
    weapons: list[str] | None = None,
    turrets: list[str] | None = None,
    modules: list[str] | None = None,
    secondary_weapons: list[str] | None = None,
    secondary_ammo: dict | None = None,
):
    """Real PlayerShip ORM instance (no session — plain kwargs).

    Passes every attribute from_player() reads off the PlayerShip row: ship_name,
    weapons, turrets, modules, secondary_weapons, secondary_ammo.
    """
    from persist.models.player_ship import PlayerShip

    return PlayerShip(
        id=ship_id,
        player_id=1,
        ship_name=ship_name,
        weapons=weapons,
        turrets=turrets,
        modules=modules,
        secondary_weapons=secondary_weapons,
        secondary_ammo=secondary_ammo,
        is_active=True,
    )


def make_static_ship(name: str = "Betty", armour: int = 300) -> SimpleNamespace:
    """Static Ship row — SimpleNamespace (Ship has ARRAY cols, not SQLite-createable)."""
    return SimpleNamespace(name=name, armour=armour, builtin_modules=None)


def make_weapon(name: str = "128MJ Railgun", dps: float = 25.0) -> SimpleNamespace:
    """Static weapon-catalogue row (PrimaryWeapon/TurretWeapon) — SimpleNamespace."""
    return SimpleNamespace(name=name, dps=dps, extra_atts=None, automatic=False)


def make_module(name: str = "E2 Exoclad", extra_atts: dict | None = None) -> SimpleNamespace:
    """Static Module-catalogue row — SimpleNamespace."""
    return SimpleNamespace(name=name, extra_atts=extra_atts or {}, type="")


def make_dispatch_db(player_ship=None, ship=None, turret=None, module=None, secondary=None) -> MagicMock:
    """Build a db whose execute() dispatches by the ORM model class being queried.

    Replaces the old ordered ``side_effect`` list of canned results: that approach
    silently mis-maps results if from_player() ever reorders/adds queries. Dispatching
    on ``stmt.column_descriptions[0]["type"]`` (the mapped class of the SELECT) is
    order-independent and robust to such reorders.
    """
    from persist.models.module import Module
    from persist.models.player_ship import PlayerShip
    from persist.models.secondary_weapon import SecondaryWeapon
    from persist.models.ship import Ship
    from persist.models.turret_weapon import TurretWeapon

    by_model = {
        PlayerShip: player_ship,
        Ship: ship,
        TurretWeapon: turret,
        Module: module,
        SecondaryWeapon: secondary,
    }

    async def _execute(stmt, *_args, **_kwargs):
        model = stmt.column_descriptions[0]["type"]
        result = MagicMock()
        scalars_result = MagicMock()
        scalars_result.first.return_value = by_model.get(model)
        result.scalars.return_value = scalars_result
        return result

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


# ---------------------------------------------------------------------------
# TestFromCriminalShipBasic
# ---------------------------------------------------------------------------


class TestFromCriminalShipBasic:
    """AC: from_criminal_ship with a basic criminal_ship dict returns correct ShipLoadout."""

    def test_ship_name_and_armour_extracted(self):
        """ship_name and base_armour are read from the dict."""
        criminal_ship = make_criminal_ship(ship_name="Vossk Warrior", ship_armour=200)
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)

        assert isinstance(loadout, ShipLoadout)
        assert loadout.ship_name == "Vossk Warrior"
        assert loadout.base_armour == 200

    def test_returns_ship_loadout_type(self):
        """Return type is ShipLoadout."""
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship())
        assert isinstance(loadout, ShipLoadout)

    def test_defaults_for_missing_keys(self):
        """Missing ship_name and ship_armour fall back to defaults."""
        loadout = LoadoutBuilder.from_criminal_ship({})
        assert loadout.ship_name == "Unknown"
        assert loadout.base_armour == 100

    def test_empty_equipment_lists(self):
        """Empty weapons/turrets/modules produce empty lists in loadout."""
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship())
        assert loadout.weapons == []
        assert loadout.turrets == []
        assert loadout.modules == []


# ---------------------------------------------------------------------------
# TestFromCriminalShipEmptyLoadout
# ---------------------------------------------------------------------------


class TestFromCriminalShipEmptyLoadout:
    """AC: from_criminal_ship with ship only, no equipment."""

    def test_ship_only_no_equipment(self):
        """Criminal ship with no weapons, turrets, or modules → minimal loadout."""
        criminal_ship = make_criminal_ship(ship_name="Solo Ship", ship_armour=150)
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)

        assert loadout.ship_name == "Solo Ship"
        assert loadout.base_armour == 150
        assert len(loadout.weapons) == 0
        assert len(loadout.turrets) == 0
        assert len(loadout.modules) == 0

    def test_zero_dps_from_no_weapons(self):
        """No weapons → CombatService computes 0 DPS."""
        criminal_ship = make_criminal_ship(ship_armour=200)
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.dps == 0.0

    def test_base_armour_becomes_total_hp(self):
        """No modules → total HP equals base armour."""
        criminal_ship = make_criminal_ship(ship_armour=200)
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.armour == 200
        assert stats.shield == 0
        assert stats.total_hp == 200


# ---------------------------------------------------------------------------
# TestFromCriminalShipWithWeaponsAndTurrets
# ---------------------------------------------------------------------------


class TestFromCriminalShipWithWeaponsAndTurrets:
    """AC: from_criminal_ship with weapons and turrets — verifies DPS."""

    def test_single_weapon_dps(self):
        """Single weapon DPS is correctly mapped to WeaponStats."""
        criminal_ship = make_criminal_ship(
            weapons=[make_weapon_dict(name="Rail Gun", dps=30.0)],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)

        assert len(loadout.weapons) == 1
        assert loadout.weapons[0].name == "Rail Gun"
        assert loadout.weapons[0].dps == 30.0

    def test_multiple_weapons_dps_summed_by_combat_service(self):
        """Multiple weapons: CombatService sums all weapon DPS."""
        criminal_ship = make_criminal_ship(
            weapons=[
                make_weapon_dict(name="Weapon1", dps=25.0),
                make_weapon_dict(name="Weapon2", dps=15.0),
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.dps == pytest.approx(40.0)

    def test_single_turret_dps(self):
        """Single turret DPS is correctly mapped."""
        criminal_ship = make_criminal_ship(
            turrets=[make_turret_dict(name="Turret X", dps=12.5)],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)

        assert len(loadout.turrets) == 1
        assert loadout.turrets[0].name == "Turret X"
        assert loadout.turrets[0].dps == 12.5

    def test_weapons_and_turrets_combined_dps(self):
        """Weapons + turrets: total DPS is sum of all."""
        criminal_ship = make_criminal_ship(
            ship_armour=100,
            weapons=[make_weapon_dict(name="W1", dps=20.0)],
            turrets=[make_turret_dict(name="T1", dps=10.0)],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.dps == pytest.approx(30.0)

    def test_weapon_names_preserved(self):
        """Weapon names are preserved exactly."""
        criminal_ship = make_criminal_ship(
            weapons=[make_weapon_dict(name="128MJ Railgun", dps=25.0)],
            turrets=[make_turret_dict(name="Tug Beam", dps=5.0)],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)

        assert loadout.weapons[0].name == "128MJ Railgun"
        assert loadout.turrets[0].name == "Tug Beam"

    def test_missing_dps_defaults_to_zero(self):
        """Weapon dict without 'dps' key defaults to 0.0."""
        criminal_ship = make_criminal_ship(
            weapons=[{"name": "Mystery Gun", "emoji": "<:gun:1>", "value": 100}],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)

        assert loadout.weapons[0].dps == 0.0


# ---------------------------------------------------------------------------
# TestFromCriminalShipWithModules
# ---------------------------------------------------------------------------


class TestFromCriminalShipWithModules:
    """AC: from_criminal_ship with armour and shield modules."""

    def test_armour_module_increases_armour_hp(self):
        """Armour module's extra_atts['armour'] adds to effective armour."""
        criminal_ship = make_criminal_ship(
            ship_armour=100,
            modules=[
                make_module_dict(
                    name="E2 Exoclad",
                    module_type="ArmourModule",
                    extra_atts={"armour": 40},
                )
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.armour == 140  # 100 base + 40 module
        assert stats.shield == 0

    def test_shield_module_adds_shield_hp(self):
        """Shield module's extra_atts['shield'] provides shield HP."""
        criminal_ship = make_criminal_ship(
            ship_armour=100,
            modules=[
                make_module_dict(
                    name="Particle Shield",
                    module_type="ShieldModule",
                    extra_atts={"shield": 380},
                )
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.armour == 100  # base only
        assert stats.shield == 380
        assert stats.total_hp == 480

    def test_armour_and_shield_modules_combined(self):
        """Both armour and shield modules contribute to total HP."""
        criminal_ship = make_criminal_ship(
            ship_armour=200,
            modules=[
                make_module_dict(name="Diol", extra_atts={"armour": 160}),
                make_module_dict(name="Particle Shield", extra_atts={"shield": 380}),
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.armour == 360  # 200 + 160
        assert stats.shield == 380
        assert stats.total_hp == 740

    def test_dps_multiplier_module_scales_dps(self):
        """DPS multiplier module (camelCase key) scales total DPS."""
        criminal_ship = make_criminal_ship(
            ship_armour=100,
            weapons=[make_weapon_dict(name="Rail Gun", dps=100.0)],
            modules=[
                make_module_dict(
                    name="Nirai Overcharge",
                    module_type="PrimaryWeaponModModule",
                    extra_atts={"dpsMultiplier": 1.1},
                )
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.dps == pytest.approx(110.0)

    def test_snake_case_dps_multiplier_also_works(self):
        """DPS multiplier module (snake_case key) also scales DPS."""
        criminal_ship = make_criminal_ship(
            ship_armour=100,
            weapons=[make_weapon_dict(name="Rail Gun", dps=100.0)],
            modules=[
                make_module_dict(
                    name="Nirai Overcharge",
                    module_type="PrimaryWeaponModModule",
                    extra_atts={"dps_multiplier": 1.1},
                )
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.dps == pytest.approx(110.0)

    def test_module_with_no_combat_stats(self):
        """Module with no combat-relevant extra_atts has zero effect."""
        criminal_ship = make_criminal_ship(
            ship_armour=100,
            modules=[
                make_module_dict(
                    name="Autopacker 2",
                    module_type="CompressorModule",
                    extra_atts={"cargoMultiplier": 1.25},  # no combat stats
                )
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.armour == 100  # unchanged
        assert stats.shield == 0
        assert stats.dps == 0.0

    def test_empty_module_extra_atts(self):
        """Module with empty extra_atts dict has zero effect."""
        criminal_ship = make_criminal_ship(
            ship_armour=100,
            modules=[make_module_dict(name="Scanner", extra_atts={})],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.armour == 100
        assert stats.shield == 0
        assert stats.dps == 0.0

    def test_module_count_preserved(self):
        """All modules in the list are added to the loadout."""
        criminal_ship = make_criminal_ship(
            modules=[
                make_module_dict(name="Module A", extra_atts={"armour": 10}),
                make_module_dict(name="Module B", extra_atts={"shield": 50}),
                make_module_dict(name="Module C", extra_atts={}),
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        assert len(loadout.modules) == 3


# ---------------------------------------------------------------------------
# TestModuleStatsFromExtra (unit test for private helper)
# ---------------------------------------------------------------------------


class TestModuleStatsFromExtra:
    """Unit tests for the _module_stats_from_extra helper function."""

    def test_armour_extracted(self):
        """armour field is mapped to ModuleStats.armour."""
        stats = _module_stats_from_extra("Test", {"armour": 160})
        assert stats.armour == 160

    def test_shield_extracted(self):
        """shield field is mapped to ModuleStats.shield."""
        stats = _module_stats_from_extra("Test", {"shield": 380})
        assert stats.shield == 380

    def test_camel_case_dps_multiplier(self):
        """camelCase dpsMultiplier → ModuleStats.dps_multiplier."""
        stats = _module_stats_from_extra("Test", {"dpsMultiplier": 1.1})
        assert stats.dps_multiplier == pytest.approx(1.1)

    def test_snake_case_dps_multiplier(self):
        """snake_case dps_multiplier → ModuleStats.dps_multiplier."""
        stats = _module_stats_from_extra("Test", {"dps_multiplier": 1.2})
        assert stats.dps_multiplier == pytest.approx(1.2)

    def test_snake_case_takes_priority_over_camel_case(self):
        """When both snake_case and camelCase exist, snake_case wins."""
        stats = _module_stats_from_extra("Test", {"dps_multiplier": 1.5, "dpsMultiplier": 1.1})
        assert stats.dps_multiplier == pytest.approx(1.5)

    def test_defaults_all_neutral(self):
        """Empty dict → all defaults: armour=0, shield=0, multipliers=1.0, dps=0."""
        stats = _module_stats_from_extra("Empty", {})
        assert stats.name == "Empty"
        assert stats.armour == 0
        assert stats.armour_multiplier == pytest.approx(1.0)
        assert stats.shield == 0
        assert stats.shield_multiplier == pytest.approx(1.0)
        assert stats.dps == 0
        assert stats.dps_multiplier == pytest.approx(1.0)

    def test_name_preserved(self):
        """Module name is preserved in ModuleStats."""
        stats = _module_stats_from_extra("My Module", {})
        assert stats.name == "My Module"

    def test_returns_module_stats_type(self):
        """Return type is ModuleStats."""
        stats = _module_stats_from_extra("Test", {})
        assert isinstance(stats, ModuleStats)


# ---------------------------------------------------------------------------
# TestFromPlayer
# ---------------------------------------------------------------------------


class TestFromPlayer:
    """AC: from_player() builds correct ShipLoadout via mocked DB calls.

    Since LoadoutBuilder.from_player() uses deferred imports (imports inside
    the function body), we patch at the definition module level:
    - 'persist.repositories.player_repository.PlayerRepository'
    - 'persist.repositories.item_repository.ItemRepository'
    """

    @pytest.mark.asyncio
    async def test_player_not_found_returns_unarmed(self):
        """Player not found in DB → default unarmed loadout."""
        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=None)

        from unittest.mock import patch

        db = AsyncMock()
        with patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo):
            loadout = await LoadoutBuilder.from_player(db, player_id=99)

        assert loadout.ship_name == "Unarmed"
        assert loadout.base_armour == 100
        assert loadout.weapons == []
        assert loadout.turrets == []
        assert loadout.modules == []

    @pytest.mark.asyncio
    async def test_player_with_no_active_ship_returns_unarmed(self):
        """Player with active_ship_id=None → default unarmed loadout."""
        player = make_player(active_ship_id=None)
        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        from unittest.mock import patch

        db = AsyncMock()
        with patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert loadout.ship_name == "Unarmed"
        assert loadout.base_armour == 100

    @pytest.mark.asyncio
    async def test_player_with_active_ship_no_equipment(self):
        """Player with active ship but no equipped items → base armour only, 0 DPS."""
        player = make_player(active_ship_id=10)
        player_ship = make_player_ship(
            ship_id=10,
            ship_name="Betty",
            weapons=None,
            turrets=None,
            modules=None,
        )
        ship = make_static_ship(name="Betty", armour=300)

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)

        db = make_dispatch_db(player_ship=player_ship, ship=ship)

        from unittest.mock import patch

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert loadout.ship_name == "Betty"
        assert loadout.base_armour == 300
        assert loadout.weapons == []
        assert loadout.turrets == []
        assert loadout.modules == []

    @pytest.mark.asyncio
    async def test_player_with_weapons_builds_weapon_stats(self):
        """Player with equipped weapons → WeaponStats with DPS from DB."""
        player = make_player(active_ship_id=10)
        player_ship = make_player_ship(
            ship_id=10,
            ship_name="Betty",
            weapons=["128MJ Railgun"],
            turrets=[],
            modules=[],
        )
        ship = make_static_ship(name="Betty", armour=300)
        weapon = make_weapon(name="128MJ Railgun", dps=25.0)

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=weapon)

        db = make_dispatch_db(player_ship=player_ship, ship=ship)

        from unittest.mock import patch

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert len(loadout.weapons) == 1
        assert loadout.weapons[0].name == "128MJ Railgun"
        assert loadout.weapons[0].dps == 25.0

    @pytest.mark.asyncio
    async def test_player_with_modules_builds_module_stats(self):
        """Player with equipped armour module → ModuleStats with armour from extra_atts."""
        player = make_player(active_ship_id=10)
        player_ship = make_player_ship(
            ship_id=10,
            ship_name="Betty",
            weapons=[],
            turrets=[],
            modules=["E2 Exoclad"],
        )
        ship = make_static_ship(name="Betty", armour=300)
        module = make_module(name="E2 Exoclad", extra_atts={"armour": 40})

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)

        db = make_dispatch_db(player_ship=player_ship, ship=ship, module=module)

        from unittest.mock import patch

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert len(loadout.modules) == 1
        assert loadout.modules[0].name == "E2 Exoclad"
        assert loadout.modules[0].armour == 40

    @pytest.mark.asyncio
    async def test_from_player_full_loadout_collect_stats(self):
        """Full player loadout with weapon + shield module → correct HP and DPS."""
        player = make_player(active_ship_id=10)
        player_ship = make_player_ship(
            ship_id=10,
            ship_name="Betty",
            weapons=["128MJ Railgun"],
            turrets=["Tug Beam"],
            modules=["Particle Shield"],
        )
        ship = make_static_ship(name="Betty", armour=300)
        weapon = make_weapon(name="128MJ Railgun", dps=25.0)
        # T7: turret builder queries TurretWeapon directly (for the `automatic` column) —
        # default manual-turret (automatic=False), no extra_atts for this simple case.
        turret = make_weapon(name="Tug Beam", dps=10.0)

        shield_module = make_module(name="Particle Shield", extra_atts={"shield": 380})

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        # item_repo returns weapon for primary_weapon lookup, turret for turret_weapon
        item_repo = MagicMock()

        async def item_repo_get_by_name(db_arg, name, item_type=None):
            if name == "128MJ Railgun":
                return weapon
            if name == "Tug Beam":
                return turret
            return None

        item_repo.get_by_name = item_repo_get_by_name

        db = make_dispatch_db(player_ship=player_ship, ship=ship, turret=turret, module=shield_module)

        from unittest.mock import patch

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        # Verify loadout structure
        assert loadout.ship_name == "Betty"
        assert loadout.base_armour == 300
        assert len(loadout.weapons) == 1
        assert len(loadout.turrets) == 1
        assert len(loadout.modules) == 1

        # Verify combat stats
        service = CombatService()
        stats = service.collect_stats(loadout)

        assert stats.dps == pytest.approx(35.0)  # 25.0 + 10.0
        assert stats.armour == 300  # base only, no armour module
        assert stats.shield == 380  # from shield module
        assert stats.total_hp == 680  # 300 + 380

    @pytest.mark.asyncio
    async def test_weapon_not_in_db_defaults_to_zero_dps(self):
        """Weapon name not found in DB → WeaponStats with dps=0."""
        player = make_player(active_ship_id=10)
        player_ship = make_player_ship(
            ship_id=10,
            ship_name="Betty",
            weapons=["Unknown Weapon"],
            turrets=[],
            modules=[],
        )
        ship = make_static_ship(name="Betty", armour=100)

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)  # not found

        db = make_dispatch_db(player_ship=player_ship, ship=ship)

        from unittest.mock import patch

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert len(loadout.weapons) == 1
        assert loadout.weapons[0].name == "Unknown Weapon"
        assert loadout.weapons[0].dps == 0.0

    @pytest.mark.asyncio
    async def test_module_not_in_db_uses_zero_effect_module(self):
        """Module name not found in DB → ModuleStats with all-zero/neutral stats."""
        player = make_player(active_ship_id=10)
        player_ship = make_player_ship(
            ship_id=10,
            ship_name="Betty",
            weapons=[],
            turrets=[],
            modules=["Ghost Module"],
        )
        ship = make_static_ship(name="Betty", armour=100)

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)

        # module=None (default) — dispatch returns None for the Module query, same as
        # "not found in DB"; item_repo.get_by_name fallback also returns None (above).
        db = make_dispatch_db(player_ship=player_ship, ship=ship)

        from unittest.mock import patch

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert len(loadout.modules) == 1
        assert loadout.modules[0].name == "Ghost Module"
        assert loadout.modules[0].armour == 0
        assert loadout.modules[0].shield == 0
        assert loadout.modules[0].dps_multiplier == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TestCollectStatsIntegration
# ---------------------------------------------------------------------------


class TestCollectStatsIntegration:
    """AC: ShipLoadout from LoadoutBuilder passes CombatService.collect_stats() correctly."""

    def test_full_criminal_ship_combat_stats(self):
        """Full criminal ship dict → correct CombatStats from collect_stats()."""
        criminal_ship = {
            "ship_name": "Betty",
            "ship_armour": 95,
            "weapons": [
                {"name": "128MJ Railgun", "dps": 25.0, "value": 24675},
                {"name": "64MJ Railgun", "dps": 15.0, "value": 12000},
            ],
            "turrets": [
                {"name": "Tug Beam", "dps": 8.0, "value": 3000},
            ],
            "modules": [
                {
                    "name": "E2 Exoclad",
                    "type": "ArmourModule",
                    "value": 1070,
                    "tech_level": 1,
                    "extra_atts": {"armour": 40},
                },
                {
                    "name": "Particle Shield",
                    "type": "ShieldModule",
                    "value": 189194,
                    "tech_level": 10,
                    "extra_atts": {"shield": 380},
                },
            ],
        }

        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        # DPS: 25.0 + 15.0 + 8.0 = 48.0 (no multiplier modules)
        assert stats.dps == pytest.approx(48.0)

        # Armour: 95 base + 40 module = 135
        assert stats.armour == 135

        # Shield: 380
        assert stats.shield == 380

        # Total HP: 135 + 380 = 515
        assert stats.total_hp == 515

        assert stats.ship_name == "Betty"

    def test_criminal_ship_with_dps_multiplier_combat_stats(self):
        """Criminal ship with DPS multiplier module scales total DPS."""
        criminal_ship = {
            "ship_name": "Raider",
            "ship_armour": 200,
            "weapons": [{"name": "Blaster", "dps": 100.0, "value": 50000}],
            "turrets": [],
            "modules": [
                {
                    "name": "Nirai Overcharge",
                    "type": "PrimaryWeaponModModule",
                    "value": 29224,
                    "tech_level": 5,
                    "extra_atts": {"dpsMultiplier": 1.1},
                }
            ],
        }

        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        service = CombatService()
        stats = service.collect_stats(loadout)

        # DPS: 100.0 * 1.1 = 110.0
        assert stats.dps == pytest.approx(110.0)
        # Armour: 200 (no armour module)
        assert stats.armour == 200


# ---------------------------------------------------------------------------
# TestCriminalWeaponSelfHealing — CI-1 fix: self-healing fallback for legacy
# JSONB weapon dicts that lack combat fields (damage_per_shot / range_m).
# ---------------------------------------------------------------------------


class TestCriminalWeaponSelfHealing:
    """AC: from_criminal_ship with a dps-only weapon dict still bakes to
    non-zero effective_damage_per_shot and non-zero range_m (self-healing
    fallback for legacy bounties stored before Change A).
    """

    def test_dps_only_weapon_bakes_nonzero_effective_damage(self):
        """Weapon dict with only 'dps' (no damage_per_shot/loading_speed_ms)
        produces effective_damage_per_shot > 0 via the cadence fallback."""
        from services.combat_resolver import _init_combatant

        criminal_ship = make_criminal_ship(
            ship_name="Hiro",
            weapons=[{"name": "N'saan", "dps": 13.33}],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        state = _init_combatant(loadout, is_player=False)

        assert len(state.effective_primaries) == 1
        p = state.effective_primaries[0]
        assert p.effective_damage_per_shot > 0, (
            f"Expected effective_damage_per_shot > 0, got {p.effective_damage_per_shot}"
        )

    def test_dps_only_weapon_bakes_nonzero_range_m(self):
        """Weapon dict with only 'dps' (no range_m) produces range_m > 0 via the floor fallback."""
        from services.combat_resolver import _init_combatant

        criminal_ship = make_criminal_ship(
            ship_name="Inflict",
            weapons=[{"name": "N'saan", "dps": 13.33}],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        state = _init_combatant(loadout, is_player=False)

        assert len(state.effective_primaries) == 1
        p = state.effective_primaries[0]
        assert p.range_m > 0, f"Expected range_m > 0, got {p.range_m}"

    def test_dps_only_weapon_is_not_pure_emp(self):
        """Weapon with dps > 0 and no explicit damage_per_shot must not be classified pure-EMP."""
        from services.combat_resolver import _init_combatant

        criminal_ship = make_criminal_ship(
            ship_name="Betty",
            weapons=[{"name": "Micro Gun MK I", "dps": 9.09}],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        state = _init_combatant(loadout, is_player=False)

        assert len(state.effective_primaries) == 1
        p = state.effective_primaries[0]
        assert p.is_pure_emp is False, "Weapon with dps > 0 should not be pure-EMP"

    def test_full_combat_fields_dict_preserves_values(self):
        """Weapon dict with all combat fields set preserves them exactly (no fallback applied)."""
        from services.combat_resolver import _init_combatant

        criminal_ship = make_criminal_ship(
            ship_name="Betty",
            weapons=[
                {
                    "name": "N'saan",
                    "dps": 13.33,
                    "damage_per_shot": 8.0,
                    "loading_speed_ms": 600,
                    "range_m": 1400.0,
                    "subtype": "blaster",
                }
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        assert loadout.weapons[0].damage_per_shot == pytest.approx(8.0)
        assert loadout.weapons[0].loading_speed_ms == 600
        assert loadout.weapons[0].range_m == pytest.approx(1400.0)
        assert loadout.weapons[0].subtype == "blaster"

        state = _init_combatant(loadout, is_player=False)
        p = state.effective_primaries[0]
        assert p.effective_damage_per_shot == 8  # round(8.0 * 1.0) = 8
        assert p.range_m == pytest.approx(1400.0)
        assert p.is_pure_emp is False


# ---------------------------------------------------------------------------
# TestCriminalWeaponSelfHealingEdgeCases — additional coverage for CI-1 fix
# ---------------------------------------------------------------------------


class TestCriminalWeaponSelfHealingEdgeCases:
    """Edge-case coverage for the self-healing fallback in from_criminal_ship().

    Verifies that the fallback only fires when damage_per_shot is absent (None),
    NOT when it is explicitly 0 (pure-EMP weapon); and that turrets self-heal
    the same way primary weapons do.
    """

    def test_pure_emp_primary_not_promoted(self):
        """A weapon with explicit damage_per_shot=0 and dps>0 is a pure-EMP weapon.
        The self-healing fallback must NOT overwrite the explicit 0 with a derived value —
        is_pure_emp must be True and baked damage must stay 0.
        """
        from services.combat_resolver import _init_combatant

        criminal_ship = make_criminal_ship(
            ship_name="EMP Raider",
            weapons=[
                {
                    "name": "EMP Pulse",
                    "dps": 17.77,
                    "damage_per_shot": 0,
                    "loading_speed_ms": 600,
                    "range_m": 1400.0,
                    "subtype": "emp",
                }
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        # The WeaponStats must preserve the explicit 0
        assert loadout.weapons[0].damage_per_shot == pytest.approx(0.0), (
            "damage_per_shot=0 must be preserved, not overwritten by self-heal fallback"
        )
        state = _init_combatant(loadout, is_player=False)
        assert len(state.effective_primaries) == 1
        p = state.effective_primaries[0]
        assert p.is_pure_emp is True, "Weapon with explicit damage_per_shot=0 must be pure-EMP"
        assert p.effective_damage_per_shot == 0, (
            f"pure-EMP weapon baked damage must be 0, got {p.effective_damage_per_shot}"
        )

    def test_legacy_turret_self_heals_nonzero_baked_damage_and_range(self):
        """A legacy criminal turret dict (dps>0, damage_per_shot absent, loading_speed_ms=0)
        must self-heal to non-zero baked damage and non-zero range — mirroring the
        primary-weapon self-heal test.
        """
        from services.combat_resolver import _init_combatant

        criminal_ship = make_criminal_ship(
            ship_name="Legacy Gunship",
            turrets=[
                {
                    "name": "Old Beam",
                    "dps": 8.5,
                    # damage_per_shot intentionally absent (legacy dict)
                    # loading_speed_ms absent → defaults to 0 → fallback cadence used
                }
            ],
        )
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        # damage_per_shot should have been derived (non-None, non-zero)
        turret_ws = loadout.turrets[0]
        assert turret_ws.damage_per_shot is not None, "Self-heal must set damage_per_shot"
        assert turret_ws.damage_per_shot > 0, (
            f"Self-healed damage_per_shot must be > 0, got {turret_ws.damage_per_shot}"
        )
        # range_m should have been given a non-zero floor
        assert turret_ws.range_m > 0, f"Self-healed range_m must be > 0, got {turret_ws.range_m}"

        # Confirm _init_combatant does not raise (no crash on self-healed turret)
        _init_combatant(loadout, is_player=False)
        assert len(loadout.turrets) == 1


# ---------------------------------------------------------------------------
# TestExtractWeaponCombatFields — unit tests for the bounty_service helper
# that extracts combat fields from ORM extra_atts onto the JSONB dict.
# ---------------------------------------------------------------------------


class TestExtractWeaponCombatFields:
    """AC: _extract_weapon_combat_fields returns correct combat fields for
    the various extra_atts nesting patterns found in the DB.
    """

    def test_nested_extra_atts_pattern(self):
        """Standard DB nesting: outer.extra_atts has the combat fields."""
        import types

        from services.bounty_service import _extract_weapon_combat_fields

        item = types.SimpleNamespace(
            extra_atts={
                "extra_atts": {
                    "loading_speed_ms": 600,
                    "range_m": 1400.0,
                    "damage_per_shot": 8,
                    "subtype": "blaster",
                }
            }
        )
        fields = _extract_weapon_combat_fields(item)
        assert fields["damage_per_shot"] == 8
        assert fields["loading_speed_ms"] == 600
        assert fields["range_m"] == pytest.approx(1400.0)
        assert fields["subtype"] == "blaster"

    def test_flat_extra_atts_fallback(self):
        """Legacy flat extra_atts (no inner nesting) is also read correctly."""
        import types

        from services.bounty_service import _extract_weapon_combat_fields

        item = types.SimpleNamespace(
            extra_atts={
                "loading_speed_ms": 220,
                "range_m": 1300.0,
                "damage_per_shot": 2,
                "subtype": "auto-cannon",
            }
        )
        fields = _extract_weapon_combat_fields(item)
        assert fields["damage_per_shot"] == 2
        assert fields["loading_speed_ms"] == 220
        assert fields["range_m"] == pytest.approx(1300.0)
        assert fields["subtype"] == "auto-cannon"

    def test_no_extra_atts_returns_safe_defaults(self):
        """Item without extra_atts → safe zero/empty defaults (never raises)."""
        import types

        from services.bounty_service import _extract_weapon_combat_fields

        item = types.SimpleNamespace(extra_atts=None)
        fields = _extract_weapon_combat_fields(item)
        assert fields["damage_per_shot"] is None
        assert fields["loading_speed_ms"] == 0
        assert fields["range_m"] == pytest.approx(0.0)
        assert fields["subtype"] == ""

    def test_weapon_dict_includes_combat_fields_after_extract(self):
        """Weapon dict produced by generate_loadout() includes all four combat fields."""
        import types

        from services.bounty_service import _extract_weapon_combat_fields

        item = types.SimpleNamespace(
            name="N'saan",
            dps=13.33,
            value=11478,
            emoji="<:nsaan:1>",
            extra_atts={
                "extra_atts": {
                    "loading_speed_ms": 600,
                    "range_m": 1400.0,
                    "damage_per_shot": 8,
                    "subtype": "blaster",
                }
            },
        )
        fields = _extract_weapon_combat_fields(item)
        weapon_dict = {
            "name": item.name,
            "dps": item.dps,
            "value": item.value,
            **fields,
        }
        assert "damage_per_shot" in weapon_dict
        assert "loading_speed_ms" in weapon_dict
        assert "range_m" in weapon_dict
        assert "subtype" in weapon_dict
        assert weapon_dict["damage_per_shot"] == 8
        assert weapon_dict["range_m"] == pytest.approx(1400.0)


# ===========================================================================
# Tests: CI-17 — from_criminal_ship secondaries round-trip
# ===========================================================================


class TestFromCriminalShipSecondaries:
    """CI-17: from_criminal_ship reads 'secondaries' list and builds WeaponStats correctly.

    Secondary slot model: slot=TYPE, quantity=ammo (rounds), NOT repeated entries.
    damage_per_shot is populated from the 'damage' field (NOT 'dps').
    """

    def test_secondaries_absent_produces_empty_list(self):
        """criminal_ship dict without 'secondaries' key → secondary_weapons=[]."""
        criminal_ship = make_criminal_ship()  # no secondaries key
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        assert loadout.secondary_weapons == []

    def test_secondaries_empty_list_produces_empty_list(self):
        """criminal_ship with secondaries=[] → secondary_weapons=[]."""
        criminal_ship = make_criminal_ship(secondaries=[])
        loadout = LoadoutBuilder.from_criminal_ship(criminal_ship)
        assert loadout.secondary_weapons == []

    def test_single_secondary_round_trips_name(self):
        """Single secondary → WeaponStats with correct name."""
        sw = make_secondary_dict(name="Nuke X", subtype="nuke", damage=800, rounds=1)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert len(loadout.secondary_weapons) == 1
        assert loadout.secondary_weapons[0].name == "Nuke X"

    def test_damage_maps_to_damage_per_shot(self):
        """'damage' field maps to WeaponStats.damage_per_shot (not dps)."""
        sw = make_secondary_dict(name="Nuke X", dps=0.0, damage=800, rounds=1)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].damage_per_shot == pytest.approx(800.0)

    def test_dps_field_preserved(self):
        """dps field is preserved as WeaponStats.dps."""
        sw = make_secondary_dict(name="Nuke X", dps=2.5, damage=800, rounds=1)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].dps == pytest.approx(2.5)

    def test_subtype_round_trips(self):
        """subtype field round-trips correctly."""
        sw = make_secondary_dict(name="Rocket A", subtype="rocket", damage=200, rounds=5)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].subtype == "rocket"

    def test_rounds_maps_to_ammo(self):
        """'rounds' field maps to WeaponStats.ammo."""
        sw = make_secondary_dict(name="Missile B", subtype="missile", damage=200, rounds=5)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].ammo == 5

    def test_rounds_floored_at_1(self):
        """rounds=0 is floored to 1 so the weapon always fires at least once."""
        sw = make_secondary_dict(name="Missile B", subtype="missile", damage=200, rounds=0)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].ammo >= 1

    def test_burst_count_round_trips(self):
        """burst_count field round-trips correctly."""
        sw = make_secondary_dict(name="Cluster", subtype="cluster-missile", damage=150, burst_count=4, rounds=3)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].burst_count == 4

    def test_emp_damage_round_trips(self):
        """emp_damage field round-trips correctly."""
        sw = make_secondary_dict(name="EMP Weapon", subtype="missile", damage=100, emp_damage=50, rounds=2)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].emp_damage == 50

    def test_magnitude_m_round_trips(self):
        """magnitude_m field round-trips correctly (nuke blast radius)."""
        sw = make_secondary_dict(name="Nuke X", subtype="nuke", damage=800, magnitude_m=500.0, rounds=1)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].magnitude_m == pytest.approx(500.0)

    def test_steerable_round_trips(self):
        """steerable field round-trips correctly."""
        sw = make_secondary_dict(name="Steerable Nuke", subtype="nuke", damage=800, steerable=True, rounds=1)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].steerable is True

    def test_loading_speed_ms_round_trips(self):
        """loading_speed_ms field round-trips correctly."""
        sw = make_secondary_dict(name="Slow Nuke", subtype="nuke", damage=800, loading_speed_ms=4500, rounds=1)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].loading_speed_ms == 4500

    def test_range_m_round_trips(self):
        """range_m field round-trips correctly."""
        sw = make_secondary_dict(name="Short Rocket", subtype="rocket", damage=200, range_m=1500.0, rounds=5)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert loadout.secondary_weapons[0].range_m == pytest.approx(1500.0)

    def test_multiple_secondaries_all_round_trip(self):
        """Multiple secondaries all produce distinct WeaponStats entries."""
        sw1 = make_secondary_dict(name="Nuke X", subtype="nuke", damage=800, rounds=1)
        sw2 = make_secondary_dict(name="Rocket A", subtype="rocket", damage=200, rounds=5)
        sw3 = make_secondary_dict(name="Missile B", subtype="missile", damage=300, rounds=5)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw1, sw2, sw3]))
        assert len(loadout.secondary_weapons) == 3
        names = [sw.name for sw in loadout.secondary_weapons]
        assert "Nuke X" in names
        assert "Rocket A" in names
        assert "Missile B" in names

    def test_existing_weapons_turrets_unaffected_by_secondaries(self):
        """Adding secondaries does not affect primary weapons or turrets in the loadout."""
        weapon = {"name": "Rail Gun", "dps": 30.0, "emoji": None, "value": 5000}
        sw = make_secondary_dict(name="Nuke X", subtype="nuke", damage=800, rounds=1)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(weapons=[weapon], secondaries=[sw]))
        assert len(loadout.weapons) == 1
        assert loadout.weapons[0].name == "Rail Gun"
        assert len(loadout.secondary_weapons) == 1
        assert loadout.secondary_weapons[0].name == "Nuke X"

    def test_secondary_dps_preserved_in_weapon_stats(self):
        """Secondary dps field is preserved in the WeaponStats object."""
        sw = make_secondary_dict(name="Damage Missile", subtype="missile", dps=5.0, damage=200, rounds=5)
        loadout = LoadoutBuilder.from_criminal_ship(make_criminal_ship(secondaries=[sw]))
        assert len(loadout.secondary_weapons) == 1
        assert loadout.secondary_weapons[0].dps == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# FIX 1 — RepairBotModule HPps → pct mapping (_module_stats_from_extra)
# ---------------------------------------------------------------------------


class TestRepairBotModuleMapping:
    """_module_stats_from_extra maps seed HPps to locked pct constants for RepairBotModules."""

    def _make_extra(self, hpps: int) -> dict:
        """Build an outer extra_atts dict matching the DB nesting pattern."""
        return {"extra_atts": {"HPps": hpps}}

    def test_hpps_7_maps_to_ketar_i(self):
        """HPps=7 (id122 Ketar I) → KETAR_I_REPAIR_PCT_PER_SEC."""
        from services.game_constants import GameConstants

        stats = _module_stats_from_extra("Ketar Repair Bot", self._make_extra(7), module_type="RepairBotModule")
        assert stats.repair_rate == pytest.approx(GameConstants.KETAR_I_REPAIR_PCT_PER_SEC)

    def test_hpps_15_maps_to_ketar_ii(self):
        """HPps=15 (id129 Ketar II) → KETAR_II_REPAIR_PCT_PER_SEC."""
        from services.game_constants import GameConstants

        stats = _module_stats_from_extra("Ketar Repair Bot II", self._make_extra(15), module_type="RepairBotModule")
        assert stats.repair_rate == pytest.approx(GameConstants.KETAR_II_REPAIR_PCT_PER_SEC)

    def test_hpps_99_maps_to_ketar_ii(self):
        """HPps=99 (any future high-value bot) → KETAR_II_REPAIR_PCT_PER_SEC (>=15 path)."""
        from services.game_constants import GameConstants

        stats = _module_stats_from_extra("Future Repair Bot", self._make_extra(99), module_type="RepairBotModule")
        assert stats.repair_rate == pytest.approx(GameConstants.KETAR_II_REPAIR_PCT_PER_SEC)

    def test_hpps_0_maps_to_ketar_i(self):
        """HPps=0 (unknown/missing) → KETAR_I_REPAIR_PCT_PER_SEC (safe base default, never 0.0)."""
        from services.game_constants import GameConstants

        stats = _module_stats_from_extra("Unknown Bot", self._make_extra(0), module_type="RepairBotModule")
        assert stats.repair_rate == pytest.approx(GameConstants.KETAR_I_REPAIR_PCT_PER_SEC)

    def test_explicit_repair_pct_per_sec_wins(self):
        """repair_pct_per_sec in inner extra_atts overrides HPps threshold logic."""
        explicit_rate = 0.075
        extra = {"extra_atts": {"HPps": 7, "repair_pct_per_sec": explicit_rate}}
        stats = _module_stats_from_extra("Custom Bot", extra, module_type="RepairBotModule")
        assert stats.repair_rate == pytest.approx(explicit_rate)

    def test_non_repair_bot_module_unaffected(self):
        """Non-RepairBotModule with HPps in extra is NOT mapped to pct constants."""
        # HPps present but module_type != RepairBotModule → repair_rate stays 0.0 (generic path)
        extra = {"extra_atts": {"HPps": 7}}
        stats = _module_stats_from_extra("Some Other Module", extra, module_type="CloakModule")
        assert stats.repair_rate == pytest.approx(0.0)

    def test_explicit_zero_repair_pct_honored(self):
        """An explicit repair_pct_per_sec of 0.0 is honored (NOT treated as absent).

        Guards the `is not None` precedence: a seed author who writes 0.0 means
        "no regen", which must win over the HPps fallback (here HPps=99 would
        otherwise map to the II rate).
        """
        extra = {"extra_atts": {"HPps": 99, "repair_pct_per_sec": 0.0}}
        stats = _module_stats_from_extra("Deliberately Inert Bot", extra, module_type="RepairBotModule")
        assert stats.repair_rate == pytest.approx(0.0)

    def test_detected_by_subclass_not_name(self):
        """Detection keys on module_type, NOT the item name (the original bug was name-matching).

        A RepairBotModule whose name contains no "Ketar"/"Repair" token still gets
        a regen rate purely from its subclass.
        """
        from services.game_constants import GameConstants

        stats = _module_stats_from_extra("Zzz Gadget 9000", self._make_extra(7), module_type="RepairBotModule")
        assert stats.repair_rate == pytest.approx(GameConstants.KETAR_I_REPAIR_PCT_PER_SEC)
