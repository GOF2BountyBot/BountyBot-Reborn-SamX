"""
Unit tests for CombatService stat collection helpers.

T10: SimpleTTKResolver, variance helpers, and the old synchronous fight_ships API
are retired. Tests for the new async fight_ships / TickResolver path live in
test_combat_persistence.py and test_combat_cutover.py.

Remaining coverage: get_dps, get_armour, get_shield, collect_stats — unchanged.
"""

from __future__ import annotations

import pytest
from src.services.combat_models import (
    ModuleStats,
    ShipLoadout,
    UpgradeStats,
    WeaponStats,
)
from src.services.combat_service import CombatService

# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


def make_loadout(
    ship_name: str = "TestShip",
    base_armour: int = 1000,
    weapon_dps: list[float] | None = None,
    turret_dps: list[float] | None = None,
    modules: list[ModuleStats] | None = None,
    upgrades: list[UpgradeStats] | None = None,
) -> ShipLoadout:
    """Create a ShipLoadout for testing with sensible defaults."""
    weapons = [WeaponStats(name=f"Weapon{i}", dps=d) for i, d in enumerate(weapon_dps or [])]
    turrets = [WeaponStats(name=f"Turret{i}", dps=d) for i, d in enumerate(turret_dps or [])]
    return ShipLoadout(
        ship_name=ship_name,
        base_armour=base_armour,
        weapons=weapons,
        turrets=turrets,
        modules=modules or [],
        upgrades=upgrades or [],
    )


# ---------------------------------------------------------------------------
# TestGetDPS
# ---------------------------------------------------------------------------


class TestGetDPS:
    """Tests for CombatService.get_dps()."""

    def test_weapons_only(self):
        """Ship with only primary weapons — DPS = sum of weapon DPS."""
        loadout = make_loadout(weapon_dps=[50.0, 75.0])
        assert CombatService.get_dps(loadout) == 125.0

    def test_turrets_only(self):
        """Ship with only turrets — DPS = sum of turret DPS."""
        loadout = make_loadout(turret_dps=[30.0])
        assert CombatService.get_dps(loadout) == 30.0

    def test_weapons_and_turrets(self):
        """DPS from weapons and turrets are additive."""
        loadout = make_loadout(weapon_dps=[50.0], turret_dps=[30.0])
        assert CombatService.get_dps(loadout) == 80.0

    def test_module_flat_dps_bonus(self):
        """Module flat DPS is added to total."""
        loadout = make_loadout(
            weapon_dps=[100.0],
            modules=[ModuleStats(name="DPSMod", dps=20)],
        )
        assert CombatService.get_dps(loadout) == 120.0

    def test_module_dps_multiplier(self):
        """Module DPS multiplier scales total DPS."""
        loadout = make_loadout(
            weapon_dps=[100.0],
            modules=[ModuleStats(name="Booster", dps_multiplier=1.5)],
        )
        assert CombatService.get_dps(loadout) == 150.0

    def test_multiple_multipliers_stack_multiplicatively(self):
        """Two modules with x1.2 multiplier = x1.44 total."""
        loadout = make_loadout(
            weapon_dps=[100.0],
            modules=[
                ModuleStats(name="Booster1", dps_multiplier=1.2),
                ModuleStats(name="Booster2", dps_multiplier=1.2),
            ],
        )
        result = CombatService.get_dps(loadout)
        assert abs(result - 144.0) < 1e-9

    def test_no_weapons_zero_dps(self):
        """Ship with no weapons, turrets, or DPS modules has 0 DPS."""
        loadout = make_loadout()
        assert CombatService.get_dps(loadout) == 0.0

    def test_module_dps_and_multiplier_combined(self):
        """Module with both flat DPS and multiplier."""
        loadout = make_loadout(
            weapon_dps=[100.0],
            modules=[ModuleStats(name="CombinedMod", dps=10, dps_multiplier=1.1)],
        )
        result = CombatService.get_dps(loadout)
        assert abs(result - 121.0) < 1e-9


# ---------------------------------------------------------------------------
# TestGetArmour
# ---------------------------------------------------------------------------


class TestGetArmour:
    """Tests for CombatService.get_armour()."""

    def test_base_armour_only(self):
        """Ship with no modules or upgrades — armour = base_armour."""
        loadout = make_loadout(base_armour=500)
        assert CombatService.get_armour(loadout) == 500

    def test_module_armour_additive(self):
        """Module armour is added to base."""
        loadout = make_loadout(
            base_armour=500,
            modules=[ModuleStats(name="ArmourMod", armour=100)],
        )
        assert CombatService.get_armour(loadout) == 600

    def test_module_armour_multiplier(self):
        """Module armour multiplier scales total."""
        loadout = make_loadout(
            base_armour=500,
            modules=[ModuleStats(name="ArmourBoost", armour_multiplier=1.5)],
        )
        assert CombatService.get_armour(loadout) == 750

    def test_upgrade_armour_additive(self):
        """Upgrade armour is added to base."""
        loadout = make_loadout(
            base_armour=500,
            upgrades=[UpgradeStats(name="HullUpgrade", armour=200)],
        )
        assert CombatService.get_armour(loadout) == 700

    def test_upgrade_armour_multiplier(self):
        """Upgrade armour multiplier scales total."""
        loadout = make_loadout(
            base_armour=500,
            upgrades=[UpgradeStats(name="HullBoost", armour_multiplier=1.2)],
        )
        assert CombatService.get_armour(loadout) == 600

    def test_module_and_upgrade_combined(self):
        """Both module and upgrade contribute to armour."""
        # base=500, module armour=100 + multiplier 1.1, upgrade armour=50 + multiplier 1.2
        # Expected: int((500 + 100 + 50) * 1.1 * 1.2) = int(858.0) = 858
        loadout = make_loadout(
            base_armour=500,
            modules=[ModuleStats(name="ArmourMod", armour=100, armour_multiplier=1.1)],
            upgrades=[UpgradeStats(name="HullUpgrade", armour=50, armour_multiplier=1.2)],
        )
        assert CombatService.get_armour(loadout) == 858

    def test_no_modules_no_upgrades(self):
        """Base armour returned unchanged with no modifiers."""
        loadout = make_loadout(base_armour=1000)
        assert CombatService.get_armour(loadout) == 1000


# ---------------------------------------------------------------------------
# TestGetShield
# ---------------------------------------------------------------------------


class TestGetShield:
    """Tests for CombatService.get_shield()."""

    def test_no_modules_zero_shield(self):
        """Ship with no modules has 0 shield."""
        loadout = make_loadout()
        assert CombatService.get_shield(loadout) == 0

    def test_single_shield_module(self):
        """Single shield module provides flat shield."""
        loadout = make_loadout(
            modules=[ModuleStats(name="ShieldMod", shield=200)],
        )
        assert CombatService.get_shield(loadout) == 200

    def test_shield_multiplier(self):
        """Shield multiplier scales total shield."""
        loadout = make_loadout(
            modules=[ModuleStats(name="ShieldBoost", shield=200, shield_multiplier=1.5)],
        )
        assert CombatService.get_shield(loadout) == 300

    def test_multiple_shield_modules(self):
        """Multiple shield modules are additive before multiplier."""
        loadout = make_loadout(
            modules=[
                ModuleStats(name="ShieldMod1", shield=200),
                ModuleStats(name="ShieldMod2", shield=100),
            ],
        )
        assert CombatService.get_shield(loadout) == 300

    def test_shield_multipliers_stack(self):
        """Multiple shield multipliers stack multiplicatively."""
        # Module1 shield=200 + multiplier 1.2, Module2 shield=100 + multiplier 1.1
        # Expected: int((200 + 100) * 1.2 * 1.1) = int(396.0) = 396
        loadout = make_loadout(
            modules=[
                ModuleStats(name="ShieldMod1", shield=200, shield_multiplier=1.2),
                ModuleStats(name="ShieldMod2", shield=100, shield_multiplier=1.1),
            ],
        )
        assert CombatService.get_shield(loadout) == 396


# ---------------------------------------------------------------------------
# TestCollectStats
# ---------------------------------------------------------------------------


class TestCollectStats:
    """Tests for CombatService.collect_stats()."""

    def test_total_hp_equals_armour_plus_shield(self):
        """total_hp = armour + shield."""
        loadout = make_loadout(
            base_armour=500,
            modules=[ModuleStats(name="ShieldMod", shield=200)],
        )
        service = CombatService()
        stats = service.collect_stats(loadout)
        assert stats.total_hp == stats.armour + stats.shield
        assert stats.armour == 500
        assert stats.shield == 200
        assert stats.total_hp == 700

    def test_ship_name_carried_through(self):
        """ship_name from loadout appears in CombatStats."""
        loadout = make_loadout(ship_name="Betty")
        service = CombatService()
        stats = service.collect_stats(loadout)
        assert stats.ship_name == "Betty"

    def test_all_stats_populated(self):
        """All CombatStats fields are populated from a full loadout."""
        loadout = make_loadout(
            ship_name="FullShip",
            base_armour=800,
            weapon_dps=[100.0, 50.0],
            modules=[
                ModuleStats(name="ArmourMod", armour=200),
                ModuleStats(name="ShieldMod", shield=150),
            ],
        )
        service = CombatService()
        stats = service.collect_stats(loadout)
        assert stats.ship_name == "FullShip"
        assert stats.dps == 150.0
        assert stats.armour == 1000
        assert stats.shield == 150
        assert stats.total_hp == 1150
        assert stats.accuracy == 1.0
        assert stats.evasion == 0.0


# ---------------------------------------------------------------------------
# TestCombatService T10: legacy retirement checks
# ---------------------------------------------------------------------------


class TestCombatServiceT10:
    """Verify legacy symbols are gone (T10 retirement)."""

    def test_no_simple_ttk_resolver(self):
        """SimpleTTKResolver is retired — not importable from combat_service."""
        import importlib

        cs = importlib.import_module("src.services.combat_service")
        assert not hasattr(cs, "SimpleTTKResolver"), "SimpleTTKResolver must be deleted in T10"

    def test_no_variance_helpers(self):
        """_apply_variance / _apply_variance_float are retired — not in combat_service."""
        import importlib

        cs = importlib.import_module("src.services.combat_service")
        assert not hasattr(cs, "_apply_variance"), "_apply_variance must be deleted in T10"
        assert not hasattr(cs, "_apply_variance_float"), "_apply_variance_float must be deleted in T10"

    def test_fight_ships_is_coroutine(self):
        """fight_ships must be an async method (coroutine function)."""
        import asyncio

        service = CombatService()
        assert asyncio.iscoroutinefunction(service.fight_ships), "fight_ships must be async in T10"

    @pytest.mark.asyncio
    async def test_fight_ships_rejects_player_armour_buff(self):
        """fight_ships raises TypeError on legacy player_armour_buff kwarg."""
        from src.services.combat_models import ShipLoadout

        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        with pytest.raises(TypeError):
            # player_armour_buff is an unexpected kwarg — should raise TypeError
            await service.fight_ships(l1, l2, player_armour_buff=1.5)  # type: ignore[call-arg]

    def test_combat_service_uses_tick_resolver(self):
        """CombatService._tick_resolver is a TickResolver instance."""
        from src.services.combat_service import TickResolver

        service = CombatService()
        assert isinstance(service._tick_resolver, TickResolver), "CombatService must use TickResolver"

    @pytest.mark.asyncio
    async def test_fight_ships_log_result_false_requires_no_context(self):
        """fight_ships(log_result=False) does not require context — returns FightResults."""
        from src.services.combat_models import ShipLoadout

        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        result = await service.fight_ships(l1, l2, log_result=False)
        assert result is not None
        assert result.combat_log_id is None

    @pytest.mark.asyncio
    async def test_fight_ships_log_result_true_requires_context(self):
        """fight_ships(log_result=True, context=None) raises ValueError."""
        from src.services.combat_models import ShipLoadout

        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        with pytest.raises(ValueError, match="context is required"):
            await service.fight_ships(l1, l2, log_result=True, context=None)

    @pytest.mark.asyncio
    async def test_winner_side_rebuild_path_retains_winner_side(self):
        """log_result=True REBUILD path must carry winner_side into the returned FightResults.

        CombatService constructs a fresh FightResults (frozen dataclass) after persist(),
        passing combat_log_id.  A future regression dropping winner_side= from that
        constructor call would leave winner_side=None.  This test catches that.

        CombatLogService.persist is mocked to avoid a real DB; everything else runs real.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.combat_models import ShipLoadout

        # C1 survives (armour=100), C2 dies (armour=0) → decisive outcome, winner_side must be 1
        l1 = ShipLoadout(ship_name="Winner", base_armour=100)
        l2 = ShipLoadout(ship_name="Loser", base_armour=0)

        fake_log_id = 9999
        mock_session = MagicMock()

        service = CombatService()

        with patch(
            "services.combat_log_service.CombatLogService.persist",
            new=AsyncMock(return_value=fake_log_id),
        ):
            result = await service.fight_ships(
                l1,
                l2,
                log_result=True,
                context="duel",
                session=mock_session,
                guild_id=1,
            )

        # Rebuild path must have set combat_log_id
        assert result.combat_log_id == fake_log_id, (
            f"combat_log_id expected {fake_log_id}, got {result.combat_log_id}"
        )
        # And winner_side must survive the rebuild — not be reset to None
        assert result.winner_side == 1, (
            f"winner_side expected 1 after log_result=True rebuild, got {result.winner_side}"
        )
