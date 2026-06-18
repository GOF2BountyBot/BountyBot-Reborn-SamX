"""Tests for the criminal-loadout balance config knobs (BALANCE_JOURNAL §A, Task 1).

Covers, for each of the 7 new per-guild tunables:
1. The GameConstants default value matches the locked journal value.
2. resolve_constant() returns the per-guild override when set, and falls back to
   the GameConstants default when the field is None / config is None.
3. The GameConstantsOverridesMixin schema accepts well-formed overrides and rejects
   malformed ones (wrong dict shape, out-of-range values, wrong type).

Also asserts cluster-missile is now a HEAVY shop secondary (Thread 1 reclassify).

Test rules followed: real deterministic objects preferred; at most one MagicMock per
test (only as a lightweight stand-in for a GuildConfig row in resolve_constant tests).
Mirrors tests/services/test_game_constants_resolve.py style.
"""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from src.api.schemas.config_schema import GameConstantsOverridesMixin
from src.services.game_constants import GameConstants, resolve_constant

# ---------------------------------------------------------------------------
# 1. GameConstants defaults (locked journal values)
# ---------------------------------------------------------------------------


def test_long_range_threshold_default():
    assert GameConstants.LONG_RANGE_THRESHOLD_M == 2600


def test_criminal_long_range_pct_default():
    assert GameConstants.CRIMINAL_LONG_RANGE_PCT == 0.50


def test_primary_tl_band_weights_default():
    assert GameConstants.PRIMARY_TL_BAND_WEIGHTS == {"center": 70, "minus1": 20, "plus1": 10}


def test_criminal_cloak_chance_default():
    assert GameConstants.CRIMINAL_CLOAK_CHANCE_BY_DIVISION == {
        "bronze": 0,
        "silver": 25,
        "gold": 66,
        "platinum": 100,
    }


def test_criminal_booster_chance_default():
    assert GameConstants.CRIMINAL_BOOSTER_CHANCE_BY_DIVISION == {
        "bronze": 50,
        "silver": 100,
        "gold": 100,
        "platinum": 100,
    }


def test_criminal_emergency_chance_default():
    assert GameConstants.CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION == {
        "bronze": 0,
        "silver": 25,
        "gold": 50,
        "platinum": 100,
    }


def test_criminal_weaponmod_chance_default():
    assert GameConstants.CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION == {
        "bronze": 0,
        "silver": 25,
        "gold": 50,
        "platinum": 100,
    }


# ---------------------------------------------------------------------------
# Thread 1 — cluster-missile is now HEAVY (5x shop scaler)
# ---------------------------------------------------------------------------


def test_cluster_missile_is_heavy_secondary():
    assert "cluster-missile" in GameConstants.SHOP_HEAVY_SECONDARY_SUBTYPES
    # The original two heavy subtypes are preserved.
    assert "nuke" in GameConstants.SHOP_HEAVY_SECONDARY_SUBTYPES
    assert "shock-blast" in GameConstants.SHOP_HEAVY_SECONDARY_SUBTYPES


# ---------------------------------------------------------------------------
# 2. resolve_constant — override wins, None / no-config falls back
# ---------------------------------------------------------------------------


def test_resolve_long_range_threshold_override_wins():
    cfg = MagicMock()
    cfg.long_range_threshold_m = 3000
    result = resolve_constant(cfg, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
    assert result == 3000


def test_resolve_long_range_threshold_none_falls_back():
    cfg = MagicMock()
    cfg.long_range_threshold_m = None
    result = resolve_constant(cfg, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
    assert result == 2600


def test_resolve_long_range_threshold_no_config_falls_back():
    result = resolve_constant(None, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
    assert result == 2600


def test_resolve_criminal_long_range_pct_override_wins():
    cfg = MagicMock()
    cfg.criminal_long_range_pct = 0.75
    result = resolve_constant(cfg, "criminal_long_range_pct", GameConstants.CRIMINAL_LONG_RANGE_PCT)
    assert result == pytest.approx(0.75)


def test_resolve_criminal_long_range_pct_zero_is_valid_override():
    """0.0 is a legitimate override (disable the long-range floor) — NOT a fallback."""
    cfg = MagicMock()
    cfg.criminal_long_range_pct = 0.0
    result = resolve_constant(cfg, "criminal_long_range_pct", GameConstants.CRIMINAL_LONG_RANGE_PCT)
    assert result == pytest.approx(0.0)


def test_resolve_primary_tl_band_weights_override_wins():
    cfg = MagicMock()
    cfg.primary_tl_band_weights = {"center": 50, "minus1": 30, "plus1": 20}
    result = resolve_constant(cfg, "primary_tl_band_weights", GameConstants.PRIMARY_TL_BAND_WEIGHTS)
    assert result == {"center": 50, "minus1": 30, "plus1": 20}


def test_resolve_primary_tl_band_weights_none_falls_back():
    cfg = MagicMock()
    cfg.primary_tl_band_weights = None
    result = resolve_constant(cfg, "primary_tl_band_weights", GameConstants.PRIMARY_TL_BAND_WEIGHTS)
    assert result == {"center": 70, "minus1": 20, "plus1": 10}


@pytest.mark.parametrize(
    "field, default",
    [
        ("criminal_cloak_chance_by_division", GameConstants.CRIMINAL_CLOAK_CHANCE_BY_DIVISION),
        ("criminal_booster_chance_by_division", GameConstants.CRIMINAL_BOOSTER_CHANCE_BY_DIVISION),
        ("criminal_emergency_chance_by_division", GameConstants.CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION),
        ("criminal_weaponmod_chance_by_division", GameConstants.CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION),
    ],
)
def test_resolve_criminal_division_chance_override_wins(field, default):
    override = {"bronze": 10, "silver": 20, "gold": 30, "platinum": 40}
    cfg = MagicMock()
    setattr(cfg, field, override)
    assert resolve_constant(cfg, field, default) == override


@pytest.mark.parametrize(
    "field, default",
    [
        ("criminal_cloak_chance_by_division", GameConstants.CRIMINAL_CLOAK_CHANCE_BY_DIVISION),
        ("criminal_booster_chance_by_division", GameConstants.CRIMINAL_BOOSTER_CHANCE_BY_DIVISION),
        ("criminal_emergency_chance_by_division", GameConstants.CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION),
        ("criminal_weaponmod_chance_by_division", GameConstants.CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION),
    ],
)
def test_resolve_criminal_division_chance_none_falls_back(field, default):
    cfg = MagicMock()
    setattr(cfg, field, None)
    assert resolve_constant(cfg, field, default) == default


# ---------------------------------------------------------------------------
# 3a. Schema — accepts well-formed overrides
# ---------------------------------------------------------------------------


def test_schema_accepts_valid_scalar_overrides():
    m = GameConstantsOverridesMixin(long_range_threshold_m=3000, criminal_long_range_pct=0.6)
    assert m.long_range_threshold_m == 3000
    assert m.criminal_long_range_pct == pytest.approx(0.6)


def test_schema_accepts_valid_band_weights():
    m = GameConstantsOverridesMixin(primary_tl_band_weights={"center": 60, "minus1": 25, "plus1": 15})
    assert m.primary_tl_band_weights == {"center": 60, "minus1": 25, "plus1": 15}


def test_schema_accepts_valid_division_chance():
    m = GameConstantsOverridesMixin(
        criminal_cloak_chance_by_division={"bronze": 0, "silver": 25, "gold": 66, "platinum": 100}
    )
    assert m.criminal_cloak_chance_by_division["gold"] == 66


def test_schema_accepts_all_none():
    """A fresh-guild mixin (everything None) validates fine."""
    m = GameConstantsOverridesMixin()
    assert m.long_range_threshold_m is None
    assert m.criminal_cloak_chance_by_division is None
    assert m.primary_tl_band_weights is None


# ---------------------------------------------------------------------------
# 3b. Schema — rejects malformed overrides
# ---------------------------------------------------------------------------


def test_schema_rejects_criminal_long_range_pct_above_one():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_long_range_pct=1.5)


def test_schema_rejects_criminal_long_range_pct_negative():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_long_range_pct=-0.1)


def test_schema_rejects_long_range_threshold_negative():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(long_range_threshold_m=-1)


def test_schema_rejects_band_weights_wrong_keys():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(primary_tl_band_weights={"center": 70, "minus1": 20})


def test_schema_rejects_band_weights_negative_value():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(primary_tl_band_weights={"center": 70, "minus1": -5, "plus1": 10})


def test_schema_rejects_division_chance_missing_key():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_cloak_chance_by_division={"bronze": 0, "silver": 25, "gold": 66})


def test_schema_rejects_division_chance_extra_key():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(
            criminal_booster_chance_by_division={
                "bronze": 50,
                "silver": 100,
                "gold": 100,
                "platinum": 100,
                "diamond": 100,
            }
        )


def test_schema_rejects_division_chance_value_over_100():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(
            criminal_emergency_chance_by_division={"bronze": 0, "silver": 25, "gold": 50, "platinum": 101}
        )


def test_schema_rejects_division_chance_value_negative():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(
            criminal_weaponmod_chance_by_division={"bronze": -1, "silver": 25, "gold": 50, "platinum": 100}
        )


def test_schema_rejects_division_chance_not_a_dict():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_cloak_chance_by_division=[0, 25, 66, 100])


def test_schema_rejects_band_weights_bool_value():
    """A bool in band-weights is rejected (bool is an int subclass — must not slip through)."""
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(primary_tl_band_weights={"center": True, "minus1": 20, "plus1": 10})


def test_schema_rejects_division_chance_float_value():
    """A float in a division-chance dict is rejected (must be a strict int)."""
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(
            criminal_cloak_chance_by_division={"bronze": 25.5, "silver": 25, "gold": 66, "platinum": 100}
        )


# ---------------------------------------------------------------------------
# 3c. Schema + resolve — zero is a VALID override, not "unset"
# ---------------------------------------------------------------------------


def test_schema_accepts_long_range_threshold_zero():
    """long_range_threshold_m = 0 is a valid override (ge=0), not rejected as falsy."""
    m = GameConstantsOverridesMixin(long_range_threshold_m=0)
    assert m.long_range_threshold_m == 0


def test_resolve_long_range_threshold_zero_is_valid_override():
    """0 override must be returned verbatim, NOT swallowed back to the default 2600."""
    cfg = MagicMock()
    cfg.long_range_threshold_m = 0
    result = resolve_constant(cfg, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
    assert result == 0


# ---------------------------------------------------------------------------
# Thread 6 — criminal_exclude_emp_weapons toggle (default ON, per-guild, strict bool)
# ---------------------------------------------------------------------------


def test_criminal_exclude_emp_weapons_default_on():
    assert GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS is True


def test_resolve_exclude_emp_override_false_wins():
    cfg = MagicMock()
    cfg.criminal_exclude_emp_weapons = False
    result = resolve_constant(cfg, "criminal_exclude_emp_weapons", GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS)
    assert result is False


def test_resolve_exclude_emp_none_falls_back_to_true():
    cfg = MagicMock()
    cfg.criminal_exclude_emp_weapons = None
    result = resolve_constant(cfg, "criminal_exclude_emp_weapons", GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS)
    assert result is True


def test_resolve_exclude_emp_no_config_falls_back_to_true():
    result = resolve_constant(None, "criminal_exclude_emp_weapons", GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS)
    assert result is True


def test_schema_accepts_exclude_emp_bool():
    m = GameConstantsOverridesMixin(criminal_exclude_emp_weapons=False)
    assert m.criminal_exclude_emp_weapons is False
    m2 = GameConstantsOverridesMixin(criminal_exclude_emp_weapons=True)
    assert m2.criminal_exclude_emp_weapons is True


def test_schema_exclude_emp_default_none():
    m = GameConstantsOverridesMixin()
    assert m.criminal_exclude_emp_weapons is None


def test_schema_rejects_exclude_emp_int_coercion():
    """Strict bool: an int (0/1) must NOT coerce into the toggle."""
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_exclude_emp_weapons=1)


def test_schema_rejects_exclude_emp_string():
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_exclude_emp_weapons="true")
