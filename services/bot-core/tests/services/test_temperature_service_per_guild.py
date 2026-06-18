"""Tests for per-guild override path in TemperatureService (B.49).

Verifies that ``guild_config`` overrides are respected for:
- ``decay_temperature``: uses per-guild ``guild_activity_decay_rate`` and
  ``min_guild_activity`` when set.
- ``calculate_spawn_delay``: uses per-guild ``bounty_delay_random_min`` /
  ``bounty_delay_random_max`` and ``min_guild_activity`` when set.
- ``decay_temperature_n_hours``: propagates guild_config through each tick.

All tests use MagicMock for guild_config with specific attributes (max 2 mocks).
"""

import random
from unittest.mock import MagicMock

import pytest
from services.temperature_service import TemperatureService

# ---------------------------------------------------------------------------
# decay_temperature — guild_config=None (global defaults)
# ---------------------------------------------------------------------------


def test_decay_temperature_uses_global_default_when_config_is_none():
    """With no guild config, decay uses GameConstants.GUILD_ACTIVITY_DECAY_RATE (~0.667)."""
    result = TemperatureService.decay_temperature(3.0, guild_config=None)
    assert result == pytest.approx(2.0)  # 3.0 * 2/3 = 2.0


def test_decay_temperature_floor_uses_global_min_when_config_is_none():
    """With no guild config, floor is GameConstants.MIN_GUILD_ACTIVITY (1.0)."""
    result = TemperatureService.decay_temperature(0.5, guild_config=None)
    assert result == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# decay_temperature — per-guild guild_activity_decay_rate
# ---------------------------------------------------------------------------


def test_decay_temperature_uses_per_guild_decay_rate():
    """When guild_config has guild_activity_decay_rate set, it overrides the global."""
    cfg = MagicMock()
    cfg.guild_activity_decay_rate = 0.5  # slower decay than global 2/3
    cfg.min_guild_activity = None  # use global min

    result = TemperatureService.decay_temperature(4.0, guild_config=cfg)
    # 4.0 * 0.5 = 2.0  (rounded to 1 decimal = 2.0)
    assert result == pytest.approx(2.0)


def test_decay_temperature_per_guild_rate_of_one_no_change():
    """A decay rate of 1.0 means no decay."""
    cfg = MagicMock()
    cfg.guild_activity_decay_rate = 1.0
    cfg.min_guild_activity = None

    result = TemperatureService.decay_temperature(5.0, guild_config=cfg)
    assert result == pytest.approx(5.0)


def test_decay_temperature_per_guild_rate_differs_from_global():
    """Verify override produces a different result than the global default would."""
    cfg = MagicMock()
    cfg.guild_activity_decay_rate = 0.9  # slower than global 2/3
    cfg.min_guild_activity = None

    global_result = TemperatureService.decay_temperature(10.0, guild_config=None)
    override_result = TemperatureService.decay_temperature(10.0, guild_config=cfg)
    # Global: 10.0 * 0.667 ≈ 6.7;  override: 10.0 * 0.9 = 9.0
    assert override_result > global_result


# ---------------------------------------------------------------------------
# decay_temperature — per-guild min_guild_activity (floor)
# ---------------------------------------------------------------------------


def test_decay_temperature_clamps_to_per_guild_min_activity():
    """When guild_config has min_guild_activity set, it's used as the floor."""
    cfg = MagicMock()
    cfg.guild_activity_decay_rate = None  # use global rate
    cfg.min_guild_activity = 2.0  # higher floor than global 1.0

    result = TemperatureService.decay_temperature(1.5, guild_config=cfg)
    # 1.5 * (2/3) = 1.0 — but floor is 2.0, so result should be 2.0
    assert result == pytest.approx(2.0)


def test_decay_temperature_floor_higher_than_global_is_respected():
    """A per-guild floor of 3.0 prevents decay below 3.0."""
    cfg = MagicMock()
    cfg.guild_activity_decay_rate = None  # use global rate
    cfg.min_guild_activity = 3.0

    result = TemperatureService.decay_temperature(3.0, guild_config=cfg)
    assert result >= 3.0


# ---------------------------------------------------------------------------
# calculate_spawn_delay — per-guild bounty_delay_random_min / max
# ---------------------------------------------------------------------------


def test_calculate_spawn_delay_uses_global_defaults_when_config_is_none():
    """With no guild config, spawn delay range is [5, 7] minutes (global defaults)."""
    random.seed(0)
    for _ in range(20):
        delay = TemperatureService.calculate_spawn_delay(1.0, 1, guild_config=None)
        assert 5.0 <= delay <= 7.0


def test_calculate_spawn_delay_uses_per_guild_delay_min_max():
    """When guild_config has bounty_delay_random_min/max, they replace the global range."""
    cfg = MagicMock()
    cfg.bounty_delay_random_min = 1
    cfg.bounty_delay_random_max = 2
    cfg.min_guild_activity = None  # use global min

    random.seed(42)
    for _ in range(20):
        delay = TemperatureService.calculate_spawn_delay(1.0, 1, guild_config=cfg)
        # With temp=1, route=1: delay = base * 1.0 * 1 → should be in [1, 2]
        assert 1.0 <= delay <= 2.0


def test_calculate_spawn_delay_per_guild_min_max_differ_from_global():
    """Override delay range produces values outside the global [5,7] range."""
    cfg = MagicMock()
    cfg.bounty_delay_random_min = 20
    cfg.bounty_delay_random_max = 30
    cfg.min_guild_activity = None

    random.seed(5)
    delay = TemperatureService.calculate_spawn_delay(1.0, 1, guild_config=cfg)
    # Global would give [5, 7]; override gives [20, 30]
    assert delay >= 20.0


# ---------------------------------------------------------------------------
# decay_temperature_n_hours — propagation of guild_config
# ---------------------------------------------------------------------------


def test_decay_n_hours_propagates_guild_config():
    """guild_config is applied at every decay tick, not just the first."""
    cfg = MagicMock()
    cfg.guild_activity_decay_rate = 0.5
    cfg.min_guild_activity = None

    # Without override: 8.0 → 5.3 → 3.6 → 2.4 (3 steps at rate 2/3)
    global_result = TemperatureService.decay_temperature_n_hours(8.0, 3, guild_config=None)
    # With override rate=0.5: 8.0 → 4.0 → 2.0 → 1.0
    override_result = TemperatureService.decay_temperature_n_hours(8.0, 3, guild_config=cfg)

    # Both are valid; the override is slower decay — verify they differ
    assert override_result != pytest.approx(global_result)
    # Override result should be lower (more aggressive 0.5 rate)
    assert override_result < global_result


def test_decay_n_hours_with_higher_floor_never_drops_below():
    """Per-guild floor prevents multi-hour decay from going below the configured min."""
    cfg = MagicMock()
    cfg.guild_activity_decay_rate = None
    cfg.min_guild_activity = 4.0

    result = TemperatureService.decay_temperature_n_hours(5.0, 10, guild_config=cfg)
    assert result >= 4.0
