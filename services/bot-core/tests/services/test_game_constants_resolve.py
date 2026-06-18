"""Tests for the resolve_constant helper function (B.49).

Covers: None config, None field, set field, missing attribute,
zero-int override, zero-float override, and dict override.
"""

from unittest.mock import MagicMock

import pytest
from services.game_constants import GameConstants, resolve_constant

# ---------------------------------------------------------------------------
# Fallback paths (no config, or field is None / missing)
# ---------------------------------------------------------------------------


def test_resolve_constant_returns_fallback_when_config_is_none():
    result = resolve_constant(None, "bounty_pvc_armour_buff_factor", 1.5)
    assert result == 1.5


def test_resolve_constant_returns_fallback_when_field_is_none():
    cfg = MagicMock()
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
    cfg = MagicMock()
    cfg.bounty_pvc_armour_buff_factor = 2.0
    result = resolve_constant(cfg, "bounty_pvc_armour_buff_factor", 1.5)
    assert result == 2.0


def test_resolve_constant_zero_int_is_valid_override():
    """0 is a legitimate override value and MUST NOT fall back to the default."""
    cfg = MagicMock()
    cfg.duel_cloak_chance = 0
    result = resolve_constant(cfg, "duel_cloak_chance", 20)
    assert result == 0  # 0 is a valid override, NOT fallback


def test_resolve_constant_zero_float_is_valid_override():
    """0.0 is a legitimate override value and MUST NOT fall back to the default."""
    cfg = MagicMock()
    cfg.duel_variance_percent = 0.0
    result = resolve_constant(cfg, "duel_variance_percent", 0.05)
    assert result == 0.0  # 0.0 is a valid override, NOT fallback


def test_resolve_constant_dict_override():
    cfg = MagicMock()
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
        ("kaamo_max_capacity", 100, GameConstants.KAAMO_MAX_CAPACITY),
        ("classic_credits_per_check", 500, GameConstants.CLASSIC_CREDITS_PER_CHECK),
        ("turret_spawn_probability", 30, GameConstants.TURRET_SPAWN_PROBABILITY),
    ],
)
def test_resolve_constant_various_fields(field, override_val, fallback):
    """Override takes precedence over fallback for various field types."""
    cfg = MagicMock()
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
    cfg = MagicMock()
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
