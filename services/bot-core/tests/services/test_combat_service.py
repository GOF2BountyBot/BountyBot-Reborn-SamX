"""
Unit tests for CombatService, SimpleTTKResolver, and variance helpers.

All tests are pure computation — no database, no Docker, no async.
"""

from __future__ import annotations

import random

import pytest
from src.services.combat_models import (
    ModuleStats,
    ShipLoadout,
    UpgradeStats,
    WeaponStats,
)
from src.services.combat_service import (
    CombatService,
    SimpleTTKResolver,
    _apply_variance,
    _apply_variance_float,
)
from src.services.game_constants import GameConstants

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
# TestFightShips
# ---------------------------------------------------------------------------


class TestFightShips:
    """Tests for CombatService.fight_ships()."""

    def test_stronger_ship_wins_deterministic(self):
        """With 0% variance, ship with better stats always wins."""
        # Ship1: DPS=100, HP=1000. Ship2: DPS=50, HP=500.
        # TTK1 = 1000/50 = 20 (ship1 survives 20s of ship2's fire)
        # TTK2 = 500/100 = 5  (ship2 survives 5s of ship1's fire)
        # Ship1 wins (longer TTK)
        loadout1 = make_loadout(ship_name="Ship1", base_armour=1000, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="Ship2", base_armour=500, weapon_dps=[50.0])
        service = CombatService()
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        assert result.winner_name == "Ship1"
        assert result.loser_name == "Ship2"
        assert result.is_stalemate is False

    def test_stalemate_equal_ratios(self):
        """Equal HP/DPS ratios with 0% variance → stalemate."""
        # Ship1: DPS=100, HP=1000. Ship2: DPS=100, HP=1000.
        # TTK1 = 1000/100 = 10, TTK2 = 1000/100 = 10 → stalemate
        loadout1 = make_loadout(ship_name="Ship1", base_armour=1000, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="Ship2", base_armour=1000, weapon_dps=[100.0])
        service = CombatService()
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        assert result.is_stalemate is True
        assert result.winner_name is None
        assert result.loser_name is None

    def test_both_zero_dps_stalemate(self):
        """Both ships with 0 DPS → stalemate."""
        loadout1 = make_loadout(ship_name="Ship1", base_armour=1000)
        loadout2 = make_loadout(ship_name="Ship2", base_armour=1000)
        service = CombatService()
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        assert result.is_stalemate is True
        assert result.winner_name is None
        assert result.loser_name is None
        assert result.ship1_stats.ttk is None
        assert result.ship2_stats.ttk is None

    def test_one_zero_dps_loses(self):
        """Ship with 0 DPS loses to ship with any DPS."""
        loadout1 = make_loadout(ship_name="Attacker", base_armour=500, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="NoGuns", base_armour=1000)
        service = CombatService()
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        assert result.winner_name == "Attacker"
        assert result.loser_name == "NoGuns"
        assert result.is_stalemate is False

    def test_zero_dps_winner_ttk_is_none(self):
        """Winner against 0-DPS opponent has ttk=None (survives forever)."""
        loadout1 = make_loadout(ship_name="Attacker", base_armour=500, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="NoGuns", base_armour=1000)
        service = CombatService()
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        # Attacker wins; Attacker's TTK (ship1_stats) = how long attacker survives NoGuns' fire
        # NoGuns has 0 DPS, so Attacker survives indefinitely → ttk=None
        assert result.ship1_stats.ttk is None
        # NoGuns' TTK = how long NoGuns survives Attacker's fire = 1000/100 = 10
        assert result.ship2_stats.ttk == pytest.approx(10.0)

    def test_fight_results_structure(self):
        """FightResults has all expected fields populated."""
        loadout1 = make_loadout(ship_name="Ship1", base_armour=1000, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="Ship2", base_armour=500, weapon_dps=[50.0])
        service = CombatService()
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        # Check all top-level fields
        assert hasattr(result, "winner_name")
        assert hasattr(result, "loser_name")
        assert hasattr(result, "is_stalemate")
        assert hasattr(result, "ship1_stats")
        assert hasattr(result, "ship2_stats")
        assert hasattr(result, "variance_percent")
        assert hasattr(result, "combat_log")
        assert hasattr(result, "metadata")
        assert result.variance_percent == 0.0
        # Check ship stats structure
        s1 = result.ship1_stats
        assert hasattr(s1, "ship_name")
        assert hasattr(s1, "raw_hp")
        assert hasattr(s1, "raw_dps")
        assert hasattr(s1, "varied_hp")
        assert hasattr(s1, "varied_dps")
        assert hasattr(s1, "ttk")

    def test_default_variance_from_constants(self):
        """Omitting variance_percent uses GameConstants.DUEL_VARIANCE_PERCENT."""
        loadout1 = make_loadout(ship_name="Ship1", base_armour=1000, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="Ship2", base_armour=500, weapon_dps=[50.0])
        service = CombatService()
        result = service.fight_ships(loadout1, loadout2)
        assert result.variance_percent == GameConstants.DUEL_VARIANCE_PERCENT

    def test_variance_affects_outcome(self):
        """With seeded random, verify variance creates expected varied values."""
        loadout1 = make_loadout(ship_name="Ship1", base_armour=1000, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="Ship2", base_armour=1000, weapon_dps=[100.0])
        service = CombatService()
        # With a fixed seed, variance should produce different (non-raw) values
        random.seed(42)
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.1)
        # With 10% variance on HP=1000, varied_hp in [900, 1100]
        assert 900 <= result.ship1_stats.varied_hp <= 1100
        assert 900 <= result.ship2_stats.varied_hp <= 1100
        # With 10% variance on DPS=100, varied_dps in [90, 110]
        assert 90 <= result.ship1_stats.varied_dps <= 110
        assert 90 <= result.ship2_stats.varied_dps <= 110

    def test_results_contain_raw_and_varied_stats(self):
        """FightStats include both raw (pre-variance) and varied values."""
        loadout1 = make_loadout(ship_name="Ship1", base_armour=1000, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="Ship2", base_armour=500, weapon_dps=[50.0])
        service = CombatService()
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        # At 0% variance, raw == varied
        s1 = result.ship1_stats
        assert s1.raw_hp == 1000
        assert s1.raw_dps == 100.0
        assert s1.varied_hp == 1000
        assert s1.varied_dps == 100.0
        s2 = result.ship2_stats
        assert s2.raw_hp == 500
        assert s2.raw_dps == 50.0
        assert s2.varied_hp == 500
        assert s2.varied_dps == 50.0


# ---------------------------------------------------------------------------
# TestVarianceHelpers
# ---------------------------------------------------------------------------


class TestVarianceHelpers:
    """Tests for _apply_variance and _apply_variance_float."""

    def test_zero_variance_returns_original(self):
        """0% variance returns the input unchanged."""
        assert _apply_variance(1000, 0.0) == 1000
        assert _apply_variance_float(100.0, 0.0) == 100.0

    def test_zero_value_returns_zero(self):
        """Variance on 0 returns 0."""
        assert _apply_variance(0, 0.1) == 0
        assert _apply_variance_float(0.0, 0.1) == 0.0

    def test_variance_range_is_symmetric(self):
        """Varied value is within [value - delta, value + delta]."""
        value = 1000
        var = 0.1
        delta = int(value * var)  # = 100
        low = value - delta
        high = value + delta
        for _ in range(200):
            result_int = _apply_variance(value, var)
            assert low <= result_int <= high
        for _ in range(200):
            result_float = _apply_variance_float(float(value), var)
            assert low <= result_float <= high

    def test_int_truncation_matches_legacy(self):
        """int() truncation on the range bounds matches legacy behavior."""
        # value=10, variance=0.15 → delta = int(10 * 0.15) = int(1.5) = 1
        # range: [9, 11]
        value = 10
        var = 0.15
        expected_delta = int(value * var)  # = 1 (truncated)
        expected_low = value - expected_delta
        expected_high = value + expected_delta
        for _ in range(100):
            result = _apply_variance(value, var)
            assert expected_low <= result <= expected_high


# ---------------------------------------------------------------------------
# TestCombatResolver Protocol
# ---------------------------------------------------------------------------


class TestCombatResolverProtocol:
    """Tests that SimpleTTKResolver satisfies the CombatResolver protocol."""

    def test_simple_ttk_resolver_is_default(self):
        """CombatService uses SimpleTTKResolver by default."""
        service = CombatService()
        assert isinstance(service._resolver, SimpleTTKResolver)

    def test_custom_resolver_injection(self):
        """CombatService accepts a custom resolver via constructor."""

        class MockResolver:
            """Mock resolver for testing injection."""

            def resolve(self, ship1_stats, ship2_stats, variance_percent):
                from src.services.combat_models import FightResults, FightStats

                dummy_stats = FightStats(
                    ship_name=ship1_stats.ship_name,
                    raw_hp=ship1_stats.total_hp,
                    raw_dps=ship1_stats.dps,
                    varied_hp=ship1_stats.total_hp,
                    varied_dps=ship1_stats.dps,
                    ttk=99.0,
                )
                dummy_stats2 = FightStats(
                    ship_name=ship2_stats.ship_name,
                    raw_hp=ship2_stats.total_hp,
                    raw_dps=ship2_stats.dps,
                    varied_hp=ship2_stats.total_hp,
                    varied_dps=ship2_stats.dps,
                    ttk=1.0,
                )
                return FightResults(
                    winner_name="MockWinner",
                    loser_name="MockLoser",
                    is_stalemate=False,
                    ship1_stats=dummy_stats,
                    ship2_stats=dummy_stats2,
                    variance_percent=variance_percent,
                )

        mock_resolver = MockResolver()
        service = CombatService(resolver=mock_resolver)
        assert service._resolver is mock_resolver

        loadout1 = make_loadout(ship_name="Ship1", base_armour=1000, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="Ship2", base_armour=500, weapon_dps=[50.0])
        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        assert result.winner_name == "MockWinner"


# ---------------------------------------------------------------------------
# TestArmourBuff — B.57: player_armour_buff parameter on fight_ships()
# ---------------------------------------------------------------------------


class TestArmourBuff:
    """Tests for the player_armour_buff parameter added in B.57."""

    def test_buff_1_0_is_identical_to_no_arg(self):
        """fight_ships(buff=1.0) produces identical results to no buff argument.

        AC: player_armour_buff=1.0 behaves identically to the default (no arg).
        """
        # Ship1 clearly stronger — deterministic at 0% variance.
        loadout1 = make_loadout(ship_name="Player", base_armour=1000, weapon_dps=[200.0])
        loadout2 = make_loadout(ship_name="Criminal", base_armour=500, weapon_dps=[50.0])
        service = CombatService()

        result_default = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        result_explicit_1 = service.fight_ships(loadout1, loadout2, variance_percent=0.0, player_armour_buff=1.0)

        assert result_default.winner_name == result_explicit_1.winner_name
        assert result_default.loser_name == result_explicit_1.loser_name
        assert result_default.is_stalemate == result_explicit_1.is_stalemate
        assert result_default.ship1_stats.raw_hp == result_explicit_1.ship1_stats.raw_hp
        assert result_default.ship1_stats.raw_dps == result_explicit_1.ship1_stats.raw_dps

    def test_buff_1_5_changes_winner(self):
        """fight_ships(buff=1.5) correctly multiplies ship1 armour and can flip the outcome.

        AC: A ship that would normally lose wins after the 1.5x armour buff.

        Setup (0% variance):
          Ship1 (player):  armour=200, DPS=100 → HP=200, TTK=200/100=2.0s
          Ship2 (criminal): armour=250, DPS=100 → HP=250, TTK=250/100=2.5s
          Without buff: Ship2 wins (TTK2=2.5 > TTK1=2.0).

          With 1.5x buff on ship1: armour=300 → HP=300, TTK=300/100=3.0s
          TTK1=3.0 > TTK2=2.5 → Ship1 wins.
        """
        loadout1 = make_loadout(ship_name="Player", base_armour=200, weapon_dps=[100.0])
        loadout2 = make_loadout(ship_name="Criminal", base_armour=250, weapon_dps=[100.0])
        service = CombatService()

        # Without buff: criminal wins.
        result_no_buff = service.fight_ships(loadout1, loadout2, variance_percent=0.0)
        assert result_no_buff.winner_name == "Criminal", "pre-condition: Criminal wins without buff"
        assert result_no_buff.loser_name == "Player"

        # With 1.5x buff: player wins.
        result_buffed = service.fight_ships(loadout1, loadout2, variance_percent=0.0, player_armour_buff=1.5)
        assert result_buffed.winner_name == "Player", "Player should win after 1.5x armour buff"
        assert result_buffed.loser_name == "Criminal"
        assert result_buffed.is_stalemate is False

    def test_buff_applies_only_to_armour_not_shield_or_dps(self):
        """Buff multiplies armour only — shield HP and DPS are unchanged.

        AC: player_armour_buff=1.5 affects armour (and therefore total_hp via
            armour contribution), but shield and DPS remain at their base values.

        Setup:
          Ship1: base_armour=200, ShieldModule(shield=100), weapon_dps=[150.0]
            → raw_hp = armour(200) + shield(100) = 300, raw_dps = 150
          With 1.5x buff on armour: buffed_armour = int(200 * 1.5) = 300
            → buffed total_hp = 300 + 100 = 400  (shield 100 unchanged)
            → dps = 150 (unchanged)

        At 0% variance: raw_* == varied_*.
        """
        loadout1 = make_loadout(
            ship_name="Player",
            base_armour=200,
            weapon_dps=[150.0],
            modules=[ModuleStats(name="ShieldMod", shield=100)],
        )
        loadout2 = make_loadout(ship_name="Criminal", base_armour=1000, weapon_dps=[1.0])
        service = CombatService()

        result = service.fight_ships(loadout1, loadout2, variance_percent=0.0, player_armour_buff=1.5)
        s1 = result.ship1_stats

        # DPS unchanged: base DPS = 150.0, no module DPS bonus.
        assert s1.raw_dps == pytest.approx(150.0), "DPS must not be affected by armour buff"

        # raw_hp reflects buffed armour (300) + unchanged shield (100) = 400.
        assert s1.raw_hp == 400, f"Expected raw_hp=400 (buffed armour 300 + shield 100), got {s1.raw_hp}"

        # Verify shield component: without buff raw_hp would be 200+100=300.
        # The extra 100 HP came from armour buff (300-200=100), not from shield change.
        result_no_buff = service.fight_ships(loadout1, loadout2, variance_percent=0.0, player_armour_buff=1.0)
        s1_no_buff = result_no_buff.ship1_stats
        assert s1_no_buff.raw_hp == 300, "No-buff raw_hp should be armour(200)+shield(100)=300"
        assert s1.raw_hp - s1_no_buff.raw_hp == 100, "Buff should add exactly int(200*0.5)=100 HP"
