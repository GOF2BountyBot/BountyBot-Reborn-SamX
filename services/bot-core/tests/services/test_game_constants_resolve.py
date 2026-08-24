"""Tests for the resolve_constant helper function (B.49).

Covers: None config, None field, set field, missing attribute,
zero-int override, zero-float override, and dict override.
"""

from unittest.mock import MagicMock

import pytest
from persist.models.guild_config import GuildConfig
from services.game_constants import GameConstants, resolve_constant

# ---------------------------------------------------------------------------
# Fallback paths (no config, or field is None / missing)
# ---------------------------------------------------------------------------


def test_resolve_constant_returns_fallback_when_config_is_none():
    result = resolve_constant(None, "bounty_pvc_armour_buff_factor", 1.5)
    assert result == 1.5


def test_resolve_constant_returns_fallback_when_field_is_none():
    cfg = GuildConfig(guild_id=1)
    cfg.bounty_pvc_armour_buff_factor = None
    result = resolve_constant(cfg, "bounty_pvc_armour_buff_factor", 1.5)
    assert result == 1.5


def test_resolve_constant_handles_missing_attribute():
    cfg = MagicMock(spec=[])  # no attributes on spec
    result = resolve_constant(cfg, "nonexistent_field", 42)
    assert result == 42


# ---------------------------------------------------------------------------
# Override paths (field is explicitly set, including zero)
# ---------------------------------------------------------------------------


def test_resolve_constant_returns_override_when_field_is_set():
    cfg = GuildConfig(guild_id=1)
    cfg.bounty_pvc_armour_buff_factor = 2.0
    result = resolve_constant(cfg, "bounty_pvc_armour_buff_factor", 1.5)
    assert result == 2.0


def test_resolve_constant_zero_int_is_valid_override():
    """0 is a legitimate override value and MUST NOT fall back to the default."""
    cfg = GuildConfig(guild_id=1)
    cfg.duel_cloak_chance = 0
    result = resolve_constant(cfg, "duel_cloak_chance", 20)
    assert result == 0  # 0 is a valid override, NOT fallback


def test_resolve_constant_zero_float_is_valid_override():
    """0.0 is a legitimate override value and MUST NOT fall back to the default."""
    cfg = GuildConfig(guild_id=1)
    cfg.duel_variance_percent = 0.0
    result = resolve_constant(cfg, "duel_variance_percent", 0.05)
    assert result == 0.0  # 0.0 is a valid override, NOT fallback


def test_resolve_constant_dict_override():
    cfg = GuildConfig(guild_id=1)
    cfg.division_max_tl = {"bronze": 3, "silver": 6, "gold": 9, "platinum": 10}
    result = resolve_constant(cfg, "division_max_tl", GameConstants.DIVISION_MAX_TL)
    assert result["bronze"] == 3
    assert result["silver"] == 6
    assert result["gold"] == 9
    assert result["platinum"] == 10


# ---------------------------------------------------------------------------
# Multiple field names — table-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, override_val, fallback",
    [
        ("guild_activity_decay_rate", 0.5, GameConstants.GUILD_ACTIVITY_DECAY_RATE),
        ("min_guild_activity", 2.0, GameConstants.MIN_GUILD_ACTIVITY),
        ("bounty_delay_random_min", 3, GameConstants.BOUNTY_DELAY_RANDOM_MIN),
        ("bounty_delay_random_max", 10, GameConstants.BOUNTY_DELAY_RANDOM_MAX),
        ("check_cooldown", 60, GameConstants.CHECK_COOLDOWN),
        ("duel_request_expiry", 3600, GameConstants.DUEL_REQUEST_EXPIRY),
        ("classic_credits_per_check", 500, GameConstants.CLASSIC_CREDITS_PER_CHECK),
        ("turret_spawn_probability", 30, GameConstants.TURRET_SPAWN_PROBABILITY),
    ],
)
def test_resolve_constant_various_fields(field, override_val, fallback):
    """Override takes precedence over fallback for various field types."""
    cfg = GuildConfig(guild_id=1)
    setattr(cfg, field, override_val)
    result = resolve_constant(cfg, field, fallback)
    assert result == override_val


@pytest.mark.parametrize(
    "field, fallback",
    [
        ("guild_activity_decay_rate", GameConstants.GUILD_ACTIVITY_DECAY_RATE),
        ("min_guild_activity", GameConstants.MIN_GUILD_ACTIVITY),
        ("bounty_delay_random_min", GameConstants.BOUNTY_DELAY_RANDOM_MIN),
    ],
)
def test_resolve_constant_none_field_falls_back(field, fallback):
    """When the config attribute is None, the fallback is returned."""
    cfg = GuildConfig(guild_id=1)
    setattr(cfg, field, None)
    result = resolve_constant(cfg, field, fallback)
    assert result == fallback


def test_resolve_constant_fallback_type_preserved_float():
    """The type of the fallback is preserved when config is None."""
    result = resolve_constant(None, "duel_variance_percent", 0.05)
    assert isinstance(result, float)
    assert result == pytest.approx(0.05)


def test_resolve_constant_fallback_type_preserved_int():
    """The type of the fallback is preserved when config is None."""
    result = resolve_constant(None, "check_cooldown", 180)
    assert isinstance(result, int)
    assert result == 180


def test_resolve_constant_fallback_type_preserved_dict():
    """A dict fallback is returned as-is when config is None."""
    fallback = {"bronze": 2, "silver": 4, "gold": 7, "platinum": 10}
    result = resolve_constant(None, "division_max_tl", fallback)
    assert result == fallback


# ---------------------------------------------------------------------------
# Loot (PvC) tunable knobs (LOOT_JOURNAL §8 / T2) — table-driven over all 19.
# Each: NULL guild column -> GameConstants default; set column -> override.
# ---------------------------------------------------------------------------

# (snake_case guild column, GameConstants default, a distinct override value)
_LOOT_KNOBS = [
    ("loot_chance_tractor_t1", GameConstants.LOOT_CHANCE_TRACTOR_T1, 15),
    ("loot_chance_tractor_t2", GameConstants.LOOT_CHANCE_TRACTOR_T2, 35),
    ("loot_chance_tractor_t3", GameConstants.LOOT_CHANCE_TRACTOR_T3, 55),
    ("loot_chance_tractor_t4", GameConstants.LOOT_CHANCE_TRACTOR_T4, 75),
    ("loot_chance_no_tractor", GameConstants.LOOT_CHANCE_NO_TRACTOR, 5),
    ("loot_band1_select_pct", GameConstants.LOOT_BAND1_SELECT_PCT, 15),
    ("loot_band2_select_pct", GameConstants.LOOT_BAND2_SELECT_PCT, 25),
    ("loot_band3_select_pct", GameConstants.LOOT_BAND3_SELECT_PCT, 60),
    ("loot_band1_tl_window", GameConstants.LOOT_BAND1_TL_WINDOW, 2),
    ("loot_band1_qty_min", GameConstants.LOOT_BAND1_QTY_MIN, 2),
    ("loot_band1_qty_max", GameConstants.LOOT_BAND1_QTY_MAX, 5),
    ("loot_band1_qty_mode", GameConstants.LOOT_BAND1_QTY_MODE, 2),
    ("loot_band2_qty_min", GameConstants.LOOT_BAND2_QTY_MIN, 5),
    ("loot_band2_qty_max", GameConstants.LOOT_BAND2_QTY_MAX, 14),
    ("loot_band2_qty_mode", GameConstants.LOOT_BAND2_QTY_MODE, 9),
    ("loot_band3_qty_min", GameConstants.LOOT_BAND3_QTY_MIN, 12),
    ("loot_band3_qty_max", GameConstants.LOOT_BAND3_QTY_MAX, 24),
    ("loot_band3_qty_mode", GameConstants.LOOT_BAND3_QTY_MODE, 18),
    ("loot_commodity_sell_fraction", GameConstants.LOOT_COMMODITY_SELL_FRACTION, 0.5),
]


def test_loot_knob_count_is_nineteen():
    """Exactly 19 tunable loot knobs are wired (LOOT_DROP_CHANCE stays fixed)."""
    assert len(_LOOT_KNOBS) == 19


@pytest.mark.parametrize("field, default, override_val", _LOOT_KNOBS)
def test_loot_knob_resolves_to_default_when_null(field, default, override_val):
    """NULL guild column resolves to the GameConstants default."""
    cfg = GuildConfig(guild_id=1)
    setattr(cfg, field, None)
    assert resolve_constant(cfg, field, default) == default


@pytest.mark.parametrize("field, default, override_val", _LOOT_KNOBS)
def test_loot_knob_resolves_to_override_when_set(field, default, override_val):
    """A set guild column overrides the GameConstants default."""
    cfg = GuildConfig(guild_id=1)
    setattr(cfg, field, override_val)
    assert resolve_constant(cfg, field, default) == override_val
