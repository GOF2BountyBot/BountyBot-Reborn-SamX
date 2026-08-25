"""Tests for the criminal-loadout balance config knobs (BALANCE_JOURNAL §A, Task 1).

Covers, for each of the 7 new per-guild tunables:
1. The GameConstants default value matches the locked journal value.
2. resolve_constant() returns the per-guild override when set, and falls back to
   the GameConstants default when the field is None / config is None.
3. The GameConstantsOverridesMixin schema accepts well-formed overrides and rejects
   malformed ones (wrong dict shape, out-of-range values, wrong type).

Also asserts cluster-missile is now a HEAVY shop secondary (Thread 1 reclassify).

Test rules followed: real deterministic objects preferred; resolve_constant tests use a
real GuildConfig row (unset override columns default None) rather than a MagicMock stand-in.
Mirrors tests/services/test_game_constants_resolve.py style.
"""

import pytest
from api.schemas.config_schema import GameConstantsOverridesMixin
from persist.models.guild_config import GuildConfig
from pydantic import ValidationError
from services.game_constants import GameConstants, resolve_constant, resolve_flattened

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
    cfg = GuildConfig(guild_id=1)
    cfg.long_range_threshold_m = 3000
    result = resolve_constant(cfg, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
    assert result == 3000


def test_resolve_long_range_threshold_none_falls_back():
    cfg = GuildConfig(guild_id=1)
    cfg.long_range_threshold_m = None
    result = resolve_constant(cfg, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
    assert result == 2600


def test_resolve_long_range_threshold_no_config_falls_back():
    result = resolve_constant(None, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
    assert result == 2600


def test_resolve_criminal_long_range_pct_override_wins():
    cfg = GuildConfig(guild_id=1)
    cfg.criminal_long_range_pct = 0.75
    result = resolve_constant(cfg, "criminal_long_range_pct", GameConstants.CRIMINAL_LONG_RANGE_PCT)
    assert result == pytest.approx(0.75)


def test_resolve_criminal_long_range_pct_zero_is_valid_override():
    """0.0 is a legitimate override (disable the long-range floor) — NOT a fallback."""
    cfg = GuildConfig(guild_id=1)
    cfg.criminal_long_range_pct = 0.0
    result = resolve_constant(cfg, "criminal_long_range_pct", GameConstants.CRIMINAL_LONG_RANGE_PCT)
    assert result == pytest.approx(0.0)


def test_resolve_primary_tl_band_weight_scalar_override_wins():
    """Flat scalar band-weight scalar wins when set (rev 0033 — JSONB dict dropped)."""
    cfg = GuildConfig(guild_id=1)
    cfg.primary_tl_band_weight_center = 50
    result = resolve_constant(cfg, "primary_tl_band_weight_center", GameConstants.PRIMARY_TL_BAND_WEIGHT_CENTER)
    assert result == 50


def test_resolve_primary_tl_band_weight_scalar_none_falls_back():
    """Flat scalar falls back to global constant when None."""
    cfg = GuildConfig(guild_id=1)
    result = resolve_constant(cfg, "primary_tl_band_weight_center", GameConstants.PRIMARY_TL_BAND_WEIGHT_CENTER)
    assert result == GameConstants.PRIMARY_TL_BAND_WEIGHT_CENTER


# ---------------------------------------------------------------------------
# 3a. Schema — accepts well-formed overrides
# ---------------------------------------------------------------------------


def test_schema_accepts_valid_scalar_overrides():
    m = GameConstantsOverridesMixin(long_range_threshold_m=3000, criminal_long_range_pct=0.6)
    assert m.long_range_threshold_m == 3000
    assert m.criminal_long_range_pct == pytest.approx(0.6)


def test_schema_accepts_valid_flat_criminal_chance():
    """Flat scalar criminal-chance overrides are accepted (rev 0033 — JSONB dict dropped)."""
    m = GameConstantsOverridesMixin(criminal_cloak_chance_bronze=25, criminal_cloak_chance_gold=66)
    assert m.criminal_cloak_chance_bronze == 25
    assert m.criminal_cloak_chance_gold == 66


def test_schema_accepts_valid_flat_band_weights():
    """Flat scalar band-weight overrides are accepted (rev 0033 — JSONB dict dropped)."""
    m = GameConstantsOverridesMixin(primary_tl_band_weight_center=50, primary_tl_band_weight_minus1=30)
    assert m.primary_tl_band_weight_center == 50
    assert m.primary_tl_band_weight_minus1 == 30


def test_schema_accepts_all_none():
    """A fresh-guild mixin (everything None) validates fine."""
    m = GameConstantsOverridesMixin()
    assert m.long_range_threshold_m is None
    assert m.criminal_cloak_chance_bronze is None
    assert m.primary_tl_band_weight_center is None


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


def test_schema_rejects_criminal_cloak_chance_bronze_over_100():
    """Flat scalar criminal-chance out-of-range is rejected (rev 0033)."""
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_cloak_chance_bronze=101)


def test_schema_rejects_criminal_cloak_chance_bronze_negative():
    """Flat scalar criminal-chance negative value is rejected."""
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_cloak_chance_bronze=-1)


def test_schema_rejects_primary_tl_band_weight_center_negative():
    """Flat scalar band-weight negative value is rejected (rev 0033)."""
    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(primary_tl_band_weight_center=-5)


# ---------------------------------------------------------------------------
# 3c. Schema + resolve — zero is a VALID override, not "unset"
# ---------------------------------------------------------------------------


def test_schema_accepts_long_range_threshold_zero():
    """long_range_threshold_m = 0 is a valid override (ge=0), not rejected as falsy."""
    m = GameConstantsOverridesMixin(long_range_threshold_m=0)
    assert m.long_range_threshold_m == 0


def test_resolve_long_range_threshold_zero_is_valid_override():
    """0 override must be returned verbatim, NOT swallowed back to the default 2600."""
    cfg = GuildConfig(guild_id=1)
    cfg.long_range_threshold_m = 0
    result = resolve_constant(cfg, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
    assert result == 0


# ---------------------------------------------------------------------------
# Thread 6 — criminal_exclude_emp_weapons toggle (default ON, per-guild, strict bool)
# ---------------------------------------------------------------------------


def test_criminal_exclude_emp_weapons_default_on():
    assert GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS is True


def test_resolve_exclude_emp_override_false_wins():
    cfg = GuildConfig(guild_id=1)
    cfg.criminal_exclude_emp_weapons = False
    result = resolve_constant(cfg, "criminal_exclude_emp_weapons", GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS)
    assert result is False


def test_resolve_exclude_emp_none_falls_back_to_true():
    cfg = GuildConfig(guild_id=1)
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


# ===========================================================================
# D-trivial batch (issue #70, revision 0028): criminal_secondary_min_damage
# ===========================================================================


def test_criminal_secondary_min_damage_global_default():
    """CRIMINAL_SECONDARY_MIN_DAMAGE default is 1 (drops 0-damage + Fireworks dummy)."""
    assert GameConstants.CRIMINAL_SECONDARY_MIN_DAMAGE == 1


def test_criminal_secondary_min_damage_resolve_override():
    """resolve_constant returns the per-guild override when the column is set."""
    cfg = GuildConfig(guild_id=42)
    cfg.criminal_secondary_min_damage = 10
    result = resolve_constant(cfg, "criminal_secondary_min_damage", GameConstants.CRIMINAL_SECONDARY_MIN_DAMAGE)
    assert result == 10


def test_criminal_secondary_min_damage_resolve_fallback_on_none():
    """resolve_constant falls back to the global default when the column is NULL."""
    cfg = GuildConfig(guild_id=42)
    # Column is None by default (nullable)
    result = resolve_constant(cfg, "criminal_secondary_min_damage", GameConstants.CRIMINAL_SECONDARY_MIN_DAMAGE)
    assert result == GameConstants.CRIMINAL_SECONDARY_MIN_DAMAGE


def test_criminal_secondary_min_damage_resolve_fallback_no_config():
    """resolve_constant falls back when cfg is None."""
    result = resolve_constant(None, "criminal_secondary_min_damage", GameConstants.CRIMINAL_SECONDARY_MIN_DAMAGE)
    assert result == GameConstants.CRIMINAL_SECONDARY_MIN_DAMAGE


def test_criminal_secondary_min_damage_schema_bounds():
    """criminal_secondary_min_damage: ge=0, le=1000."""
    m_low = GameConstantsOverridesMixin(criminal_secondary_min_damage=0)
    assert m_low.criminal_secondary_min_damage == 0

    m_high = GameConstantsOverridesMixin(criminal_secondary_min_damage=1000)
    assert m_high.criminal_secondary_min_damage == 1000

    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_secondary_min_damage=-1)

    with pytest.raises(ValidationError):
        GameConstantsOverridesMixin(criminal_secondary_min_damage=1001)


# ===========================================================================
# Per-key resolution tests (issue #70, revision 0030): resolve_flattened
# One per consumer family: division_max_tl, reward_mult, band_weights, criminal chances
# ===========================================================================


class TestResolveFlattened:
    """resolve_flattened() 2-step fallback chain per consumer family (revision 0033).

    Step 1: scalar column set → returns scalar.
    Step 2: scalar None → returns global fallback.

    The JSONB dict fallback step was removed in revision 0033 (columns dropped).
    """

    # -----------------------------------------------------------------------
    # Family: division_max_tl
    # -----------------------------------------------------------------------

    def test_division_max_tl_scalar_wins(self):
        """Scalar column is returned when set."""
        cfg = GuildConfig(guild_id=1)
        cfg.division_max_tl_gold = 5
        result = resolve_flattened(cfg, "division_max_tl_gold", GameConstants.DIVISION_MAX_TL_GOLD)
        assert result == 5

    def test_division_max_tl_global_fallback(self):
        """When scalar is None, returns the global constant."""
        cfg = GuildConfig(guild_id=1)
        result = resolve_flattened(cfg, "division_max_tl_gold", GameConstants.DIVISION_MAX_TL_GOLD)
        assert result == GameConstants.DIVISION_MAX_TL_GOLD

    def test_division_max_tl_none_cfg_returns_fallback(self):
        """None cfg always returns the global fallback."""
        result = resolve_flattened(None, "division_max_tl_bronze", GameConstants.DIVISION_MAX_TL_BRONZE)
        assert result == GameConstants.DIVISION_MAX_TL_BRONZE

    # -----------------------------------------------------------------------
    # Family: bounty_division_reward_mult
    # -----------------------------------------------------------------------

    def test_reward_mult_scalar_wins(self):
        """Scalar reward_mult is returned when set."""
        cfg = GuildConfig(guild_id=2)
        cfg.bounty_division_reward_mult_silver = 3.5
        result = resolve_flattened(
            cfg, "bounty_division_reward_mult_silver", GameConstants.BOUNTY_DIVISION_REWARD_MULT_SILVER
        )
        assert result == pytest.approx(3.5)

    def test_reward_mult_global_fallback(self):
        """Falls back to global constant when scalar is absent."""
        cfg = GuildConfig(guild_id=2)
        result = resolve_flattened(
            cfg, "bounty_division_reward_mult_silver", GameConstants.BOUNTY_DIVISION_REWARD_MULT_SILVER
        )
        assert result == pytest.approx(GameConstants.BOUNTY_DIVISION_REWARD_MULT_SILVER)

    # -----------------------------------------------------------------------
    # Family: primary_tl_band_weights
    # -----------------------------------------------------------------------

    def test_band_weight_scalar_wins(self):
        """Flat scalar band weight is returned when set."""
        cfg = GuildConfig(guild_id=3)
        cfg.primary_tl_band_weight_center = 50
        result = resolve_flattened(cfg, "primary_tl_band_weight_center", GameConstants.PRIMARY_TL_BAND_WEIGHT_CENTER)
        assert result == 50

    def test_band_weight_global_fallback(self):
        """Falls back to global constant when scalar is absent."""
        cfg = GuildConfig(guild_id=3)
        result = resolve_flattened(cfg, "primary_tl_band_weight_center", GameConstants.PRIMARY_TL_BAND_WEIGHT_CENTER)
        assert result == GameConstants.PRIMARY_TL_BAND_WEIGHT_CENTER

    # -----------------------------------------------------------------------
    # Family: criminal chance (cloak representative)
    # -----------------------------------------------------------------------

    def test_cloak_chance_scalar_wins(self):
        """Flat scalar cloak chance is returned when set."""
        cfg = GuildConfig(guild_id=4)
        cfg.criminal_cloak_chance_bronze = 25
        result = resolve_flattened(cfg, "criminal_cloak_chance_bronze", GameConstants.CRIMINAL_CLOAK_CHANCE_BRONZE)
        assert result == 25

    def test_cloak_chance_global_fallback(self):
        """Falls back to global constant when scalar is absent."""
        cfg = GuildConfig(guild_id=4)
        result = resolve_flattened(cfg, "criminal_cloak_chance_bronze", GameConstants.CRIMINAL_CLOAK_CHANCE_BRONZE)
        assert result == GameConstants.CRIMINAL_CLOAK_CHANCE_BRONZE
