"""Tests for LoadoutEffectService — module effect formatting and combat-tier classification."""

from __future__ import annotations

import pytest
from api.schemas.loadout_schema import EffectItem
from services.loadout_effect_service import (
    MODULE_COMBAT_TIER,
    MODULE_EFFECT_MAP,
    LoadoutEffectService,
)

# ---------------------------------------------------------------------------
# format_module_effects — documented types
# ---------------------------------------------------------------------------


class TestDocumentedModuleEffects:
    """All 8 documented module types should produce expected EffectItem lists."""

    def test_armour_module(self):
        result = LoadoutEffectService.format_module_effects("ArmourModule", {"armour": 160})
        assert result == [EffectItem(label="Armour", value="160")]

    def test_shield_module(self):
        result = LoadoutEffectService.format_module_effects("ShieldModule", {"shield": 300})
        assert result == [EffectItem(label="Shield", value="300")]

    def test_shield_injector_module(self):
        result = LoadoutEffectService.format_module_effects("ShieldInjectorModule", {"plasmaConsumption": 30})
        assert result == [EffectItem(label="Plasma Cost", value="30")]

    def test_booster_module_both_fields(self):
        result = LoadoutEffectService.format_module_effects(
            "BoosterModule", {"duration": 10, "effect": 0.8}
        )
        assert result == [
            EffectItem(label="Duration", value="10s"),
            EffectItem(label="Speed", value="80%"),
        ]

    def test_booster_module_missing_effect_skips_silently(self):
        result = LoadoutEffectService.format_module_effects("BoosterModule", {"duration": 10})
        # Only the first key is present — second entry is silently omitted.
        assert result == [EffectItem(label="Duration", value="10s")]

    def test_cabin_module(self):
        result = LoadoutEffectService.format_module_effects("CabinModule", {"cabinSize": 3})
        assert result == [EffectItem(label="Crew", value="3")]

    def test_cloak_module(self):
        result = LoadoutEffectService.format_module_effects("CloakModule", {"duration": 8})
        assert result == [EffectItem(label="Duration", value="8s")]

    def test_compressor_module(self):
        result = LoadoutEffectService.format_module_effects("CompressorModule", {"cargoMultiplier": 1.25})
        assert result == [EffectItem(label="Cargo Bonus", value="×1.25")]

    def test_compressor_neutral_multiplier_still_emitted(self):
        # Per spec §2.5: multiplier 1.0 should still emit "×1" (users may want to see neutral).
        result = LoadoutEffectService.format_module_effects("CompressorModule", {"cargoMultiplier": 1.0})
        assert result == [EffectItem(label="Cargo Bonus", value="×1")]


# ---------------------------------------------------------------------------
# format_module_effects — special cases
# ---------------------------------------------------------------------------


class TestGammaShieldSpecialCase:
    """GammaShieldModule always returns [] regardless of extra_atts content (spec §2.5)."""

    def test_gamma_shield_always_empty_with_values(self):
        result = LoadoutEffectService.format_module_effects("GammaShieldModule", {"effect": 0.6})
        assert result == []

    def test_gamma_shield_empty_with_none(self):
        result = LoadoutEffectService.format_module_effects("GammaShieldModule", None)
        assert result == []

    def test_gamma_shield_empty_with_empty_dict(self):
        result = LoadoutEffectService.format_module_effects("GammaShieldModule", {})
        assert result == []


class TestUnknownModuleTypes:
    """Unknown types return [] and emit a WARN log (spec §2.5)."""

    def test_unknown_type_returns_empty(self):
        result = LoadoutEffectService.format_module_effects("SomeFutureModule", {"x": 1})
        assert result == []

    def test_unknown_type_with_none_atts_returns_empty(self):
        result = LoadoutEffectService.format_module_effects("AnotherNewType", None)
        assert result == []

    def test_empty_module_type_returns_empty(self):
        assert LoadoutEffectService.format_module_effects("", {"armour": 100}) == []

    def test_none_module_type_returns_empty(self):
        assert LoadoutEffectService.format_module_effects(None, {"armour": 100}) == []


class TestEdgeCases:
    """None/empty extra_atts and formatter failures."""

    def test_known_type_with_none_extra_atts(self):
        result = LoadoutEffectService.format_module_effects("ArmourModule", None)
        assert result == []

    def test_known_type_with_empty_dict(self):
        result = LoadoutEffectService.format_module_effects("ArmourModule", {})
        assert result == []

    def test_known_type_with_missing_key(self):
        # Key completely absent — silently omitted
        result = LoadoutEffectService.format_module_effects("ArmourModule", {"other_key": 50})
        assert result == []

    def test_known_type_with_none_value(self):
        result = LoadoutEffectService.format_module_effects("ArmourModule", {"armour": None})
        assert result == []

    def test_bad_seed_data_skipped_silently(self):
        # Non-numeric value for 'int' formatter — should be skipped, not raise.
        result = LoadoutEffectService.format_module_effects(
            "ArmourModule", {"armour": "not-a-number"}
        )
        assert result == []

    def test_seconds_formatter_rounds_down(self):
        result = LoadoutEffectService.format_module_effects("CloakModule", {"duration": 8.9})
        assert result == [EffectItem(label="Duration", value="8s")]

    def test_int_pct_formatter_rounds_correctly(self):
        # 0.6 -> 60%; 0.855 -> 86% (standard rounding used by formatter)
        r1 = LoadoutEffectService.format_module_effects("BoosterModule", {"duration": 1, "effect": 0.6})
        assert r1[1].value == "60%"
        r2 = LoadoutEffectService.format_module_effects("BoosterModule", {"duration": 1, "effect": 0.855})
        assert r2[1].value == "86%"

    def test_non_dict_extra_atts_returns_empty(self):
        # Defensive — callers may pass list/str by accident
        assert LoadoutEffectService.format_module_effects("ArmourModule", ["armour", 160]) == []
        assert LoadoutEffectService.format_module_effects("ArmourModule", "armour=160") == []


# ---------------------------------------------------------------------------
# get_module_combat_tier
# ---------------------------------------------------------------------------


class TestCombatTier:
    """Combat-tier classification for all 21 known types + unknown fallback."""

    @pytest.mark.parametrize(
        "module_type",
        [
            "ArmourModule",
            "ShieldModule",
            "ShieldInjectorModule",
            "BoosterModule",
            "CloakModule",
            "ThrusterModule",
            "RepairBotModule",
            "RepairBeamModule",
            "TransfusionBeamModule",
            "EmergencySystemModule",
            "PrimaryWeaponModModule",
        ],
    )
    def test_combat_tier_modules(self, module_type):
        assert LoadoutEffectService.get_module_combat_tier(module_type) == "combat"

    @pytest.mark.parametrize(
        "module_type",
        [
            "CabinModule",
            "CompressorModule",
            "MiningDrillModule",
            "ScannerModule",
            "JumpDriveModule",
            "TractorBeamModule",
            "SignatureModule",
            "SpectralFilterModule",
            "TimeExtenderModule",
            "GammaShieldModule",
        ],
    )
    def test_utility_tier_modules(self, module_type):
        assert LoadoutEffectService.get_module_combat_tier(module_type) == "utility"

    def test_unknown_type_defaults_to_combat(self):
        assert LoadoutEffectService.get_module_combat_tier("SomeFutureModule") == "combat"

    def test_none_defaults_to_combat(self):
        assert LoadoutEffectService.get_module_combat_tier(None) == "combat"

    def test_empty_string_defaults_to_combat(self):
        assert LoadoutEffectService.get_module_combat_tier("") == "combat"


# ---------------------------------------------------------------------------
# Catalog completeness — all 21 types present in both dicts
# ---------------------------------------------------------------------------


class TestCatalogCompleteness:
    """Verify all 21 known module types are in both MODULE_EFFECT_MAP and MODULE_COMBAT_TIER."""

    ALL_21_TYPES = {
        "ArmourModule",
        "ShieldModule",
        "GammaShieldModule",
        "ShieldInjectorModule",
        "BoosterModule",
        "CabinModule",
        "CloakModule",
        "CompressorModule",
        "EmergencySystemModule",
        "JumpDriveModule",
        "MiningDrillModule",
        "PrimaryWeaponModModule",
        "RepairBeamModule",
        "RepairBotModule",
        "ScannerModule",
        "SignatureModule",
        "SpectralFilterModule",
        "ThrusterModule",
        "TimeExtenderModule",
        "TractorBeamModule",
        "TransfusionBeamModule",
    }

    def test_effect_map_has_all_21(self):
        assert set(MODULE_EFFECT_MAP.keys()) == self.ALL_21_TYPES

    def test_combat_tier_has_all_21(self):
        assert set(MODULE_COMBAT_TIER.keys()) == self.ALL_21_TYPES

    def test_gamma_shield_effect_map_is_empty(self):
        assert MODULE_EFFECT_MAP["GammaShieldModule"] == []

    def test_gamma_shield_tier_is_utility(self):
        assert MODULE_COMBAT_TIER["GammaShieldModule"] == "utility"
