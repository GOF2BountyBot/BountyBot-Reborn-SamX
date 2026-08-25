"""Tests for CombatTuning dataclass (issue #70, unit A1).

Covers:
  1. from_guild_config(None) — all fields equal GameConstants defaults
  2. from_guild_config with overrides — overridden fields win
  3. defaults() convenience wrapper
  4. Pickle round-trip (required for forkserver process boundary)
  5. Resolver honours tuning= struct (integration: changed cloak_set_value)
  6. Schema bounds spot-checks for 6 representative new fields
"""

from __future__ import annotations

import pickle
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Shared bblogger guard — must run before any project import
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from api.schemas.config_schema import GameConstantsOverridesMixin
from pydantic import ValidationError
from services.combat_models import CombatTuning
from services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**kwargs) -> SimpleNamespace:
    """Build a minimal guild-config-like namespace with only the given overrides."""
    return SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCombatTuningDefaults:
    """from_guild_config(None) must match every GameConstants default."""

    def test_cloak_set_value_default(self):
        t = CombatTuning.defaults()
        assert t.cloak_set_value == float(GameConstants.CLOAK_SET_VALUE)

    def test_player_base_accuracy_default(self):
        t = CombatTuning.defaults()
        assert t.player_base_accuracy == float(GameConstants.PLAYER_BASE_ACCURACY)

    def test_npc_base_accuracy_default(self):
        t = CombatTuning.defaults()
        assert t.npc_base_accuracy == float(GameConstants.NPC_BASE_ACCURACY)

    def test_scanner_tier_b_bonus_pp_default(self):
        t = CombatTuning.defaults()
        assert t.scanner_tier_b_bonus_pp == float(GameConstants.SCANNER_TIER_B_BONUS_PP)

    def test_starting_distance_m_default(self):
        t = CombatTuning.defaults()
        assert t.starting_distance_m == float(GameConstants.STARTING_DISTANCE_M)

    def test_nuke_range_regime_threshold_m_default(self):
        t = CombatTuning.defaults()
        assert t.nuke_range_regime_threshold_m == float(GameConstants.NUKE_RANGE_REGIME_THRESHOLD_M)

    def test_nuke_lr_near_frac_default(self):
        t = CombatTuning.defaults()
        assert t.nuke_lr_near_frac == float(GameConstants.NUKE_LR_NEAR_FRAC)

    def test_shock_blast_trigger_range_m_default(self):
        t = CombatTuning.defaults()
        assert t.shock_blast_trigger_range_m == float(GameConstants.SHOCK_BLAST_TRIGGER_RANGE_M)

    def test_combat_layer_reemit_fraction_default(self):
        t = CombatTuning.defaults()
        assert t.combat_layer_reemit_fraction == float(GameConstants.COMBAT_LAYER_REEMIT_FRACTION)

    def test_field_types_match_annotations(self):
        """Each field must match the declared type annotation (int or float).

        Int fields: scanner_pp, metre-distances, m/s speeds, whole-second durations.
        Float fields: dimensionless ratios, scale factors, fractions, probabilities.
        """
        _INT_FIELDS = frozenset(
            {
                "scanner_tier_b_bonus_pp",
                "scanner_tier_c_bonus_pp",
                "starting_distance_m",
                "base_ship_speed_mps",
                "min_distance_m",
                "thruster_window_m",
                "emergency_system_invuln_s",
                "nuke_range_regime_threshold_m",
                "nuke_cr_short_m",
                "nuke_cr_overshoot_m",
                "shock_blast_trigger_range_m",
            }
        )
        t = CombatTuning.defaults()
        for f in t.__dataclass_fields__:
            v = getattr(t, f)
            if f in _INT_FIELDS:
                assert isinstance(v, int), f"Field {f!r} should be int, got {type(v)}"
            else:
                assert isinstance(v, float), f"Field {f!r} should be float, got {type(v)}"


class TestCombatTuningOverride:
    """from_guild_config(cfg) with per-guild values wins over GameConstants."""

    def test_cloak_set_value_override(self):
        cfg = _cfg(cloak_set_value=0.10)
        t = CombatTuning.from_guild_config(cfg)
        assert t.cloak_set_value == 0.10

    def test_nuke_friendly_factor_override(self):
        cfg = _cfg(nuke_friendly_factor=0.20)
        t = CombatTuning.from_guild_config(cfg)
        assert t.nuke_friendly_factor == 0.20

    def test_combat_layer_reemit_fraction_override(self):
        cfg = _cfg(combat_layer_reemit_fraction=0.50)
        t = CombatTuning.from_guild_config(cfg)
        assert t.combat_layer_reemit_fraction == 0.50

    def test_shock_blast_trigger_range_m_override(self):
        cfg = _cfg(shock_blast_trigger_range_m=250)
        t = CombatTuning.from_guild_config(cfg)
        assert t.shock_blast_trigger_range_m == 250
        assert isinstance(t.shock_blast_trigger_range_m, int)

    def test_nuke_stack_falloff_override(self):
        cfg = _cfg(nuke_stack_falloff=0.75)
        t = CombatTuning.from_guild_config(cfg)
        assert t.nuke_stack_falloff == 0.75

    def test_unset_field_falls_back_to_global(self):
        """A cfg with only one field set leaves others at GameConstants defaults."""
        cfg = _cfg(cloak_set_value=0.05)
        t = CombatTuning.from_guild_config(cfg)
        # Only cloak_set_value overridden; starting_distance_m falls back.
        assert t.starting_distance_m == float(GameConstants.STARTING_DISTANCE_M)

    def test_zero_is_a_valid_override_not_treated_as_none(self):
        """A guild override of 0.0 is honoured, not skipped as falsy."""
        cfg = _cfg(nuke_friendly_factor=0.0)
        t = CombatTuning.from_guild_config(cfg)
        assert t.nuke_friendly_factor == 0.0


class TestCombatTuningPickle:
    """CombatTuning must be picklable for the forkserver process boundary (C1a-4)."""

    def test_pickle_round_trip_defaults(self):
        t = CombatTuning.defaults()
        restored = pickle.loads(pickle.dumps(t))
        assert restored == t

    def test_pickle_round_trip_with_overrides(self):
        cfg = _cfg(cloak_set_value=0.05, combat_layer_reemit_fraction=0.10)
        t = CombatTuning.from_guild_config(cfg)
        restored = pickle.loads(pickle.dumps(t))
        assert restored == t
        assert restored.cloak_set_value == 0.05
        assert restored.combat_layer_reemit_fraction == 0.10


class TestSchemaBoundsSpotCheck:
    """Pydantic schema bounds for 6 representative new fields from rev 0032."""

    def test_cloak_set_value_below_lower_bound_rejected(self):
        with pytest.raises(ValidationError):
            GameConstantsOverridesMixin.model_validate({"cloak_set_value": 0.04})

    def test_cloak_set_value_above_upper_bound_rejected(self):
        with pytest.raises(ValidationError):
            GameConstantsOverridesMixin.model_validate({"cloak_set_value": 1.0})

    def test_starting_distance_m_below_bound_rejected(self):
        with pytest.raises(ValidationError):
            GameConstantsOverridesMixin.model_validate({"starting_distance_m": 299})

    def test_nuke_range_regime_threshold_m_above_bound_rejected(self):
        with pytest.raises(ValidationError):
            GameConstantsOverridesMixin.model_validate({"nuke_range_regime_threshold_m": 10_001})

    def test_shock_blast_trigger_range_m_valid_at_boundary(self):
        obj = GameConstantsOverridesMixin.model_validate({"shock_blast_trigger_range_m": 10_000})
        assert obj.shock_blast_trigger_range_m == 10_000

    def test_combat_layer_reemit_fraction_valid_zero(self):
        obj = GameConstantsOverridesMixin.model_validate({"combat_layer_reemit_fraction": 0.0})
        assert obj.combat_layer_reemit_fraction == 0.0

    def test_nuke_lr_near_frac_above_bound_rejected(self):
        with pytest.raises(ValidationError):
            GameConstantsOverridesMixin.model_validate({"nuke_lr_near_frac": 1.01})
