"""
Unit tests for TemperatureService.

All tests are pure unit tests — zero mocks needed because TemperatureService
contains no I/O, no async operations, and no database access.  The only
non-determinism (``random.uniform`` inside ``calculate_spawn_delay``) is
controlled via ``random.seed()``.

Coverage:
- raise_temperature  — default and explicit amounts
- decay_temperature  — arithmetic, rounding, and floor behaviour
- get_max_bounties   — boundary levels and cap
- calculate_spawn_delay — range and temperature ordering
- decay_temperature_n_hours — multi-step decay and floor persistence
"""

import random

import pytest
from src.services.temperature_service import TemperatureService

# ---------------------------------------------------------------------------
# TestRaiseTemperature
# ---------------------------------------------------------------------------


class TestRaiseTemperature:
    """Tests for TemperatureService.raise_temperature()."""

    @pytest.mark.parametrize(
        "current_temp, amount, expected",
        [
            (1.0, None, 2.0),  # default amount (+1)
            (5.0, 2.0, 7.0),  # explicit amount
            (0.5, None, 1.5),  # below-floor start with default raise
        ],
    )
    def test_raise_temperature(self, current_temp: float, amount: float | None, expected: float) -> None:
        if amount is None:
            result = TemperatureService.raise_temperature(current_temp)
        else:
            result = TemperatureService.raise_temperature(current_temp, amount)
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# TestDecayTemperature
# ---------------------------------------------------------------------------


class TestDecayTemperature:
    """Tests for TemperatureService.decay_temperature()."""

    @pytest.mark.parametrize(
        "current_temp, expected",
        [
            (3.0, 2.0),  # 3.0 * 2/3 = 2.0
            (1.5, 1.0),  # 1.5 * 2/3 = 1.0
            (1.0, 1.0),  # floor: max(1.0, 1.0 * 2/3) = 1.0
            (0.5, 1.0),  # floor: max(1.0, 0.5 * 2/3) = 1.0
            (10.0, 6.7),  # 10.0 * 2/3 = 6.666… → rounds to 6.7
        ],
    )
    def test_decay_temperature(self, current_temp: float, expected: float) -> None:
        result = TemperatureService.decay_temperature(current_temp)
        assert result == pytest.approx(expected)

    def test_decay_never_goes_below_floor(self) -> None:
        """Multiple decay steps should never produce a value below 1.0."""
        temp = 1.0
        for _ in range(10):
            temp = TemperatureService.decay_temperature(temp)
        assert temp >= 1.0


# ---------------------------------------------------------------------------
# TestGetMaxBounties
# ---------------------------------------------------------------------------


class TestGetMaxBounties:
    """Tests for TemperatureService.get_max_bounties()."""

    @pytest.mark.parametrize(
        "temperature, expected",
        [
            (1.0, 1),  # int(1.0) = 1
            (2.5, 2),  # int(2.5) = 2
            (3.0, 3),  # int(3.0) = 3
            (5.0, 5),  # at the cap
            (10.0, 5),  # capped at MAX_BOUNTIES_PER_DIVISION = 5
            (0.5, 1),  # below floor → max(1, int(0.5)) = max(1, 0) = 1
        ],
    )
    def test_get_max_bounties(self, temperature: float, expected: int) -> None:
        result = TemperatureService.get_max_bounties(temperature)
        assert result == expected


# ---------------------------------------------------------------------------
# TestCalculateSpawnDelay
# ---------------------------------------------------------------------------


class TestCalculateSpawnDelay:
    """Tests for TemperatureService.calculate_spawn_delay()."""

    def test_route_length_1_temp_1_within_base_range(self) -> None:
        """With temp=1 and route=1 the delay should be in [5, 7] minutes."""
        random.seed(42)
        # temp^-0.1 = 1.0^-0.1 = 1.0, so delay = base_delay * 1.0 * 1
        for _ in range(20):
            delay = TemperatureService.calculate_spawn_delay(1.0, 1)
            assert 5.0 <= delay <= 7.0

    def test_route_length_8_temp_1_within_scaled_range(self) -> None:
        """With temp=1 and route=8 the delay should be in [40, 56] minutes."""
        random.seed(0)
        # temp^-0.1 = 1.0, scale by route=8 → [5*8, 7*8] = [40, 56]
        for _ in range(20):
            delay = TemperatureService.calculate_spawn_delay(1.0, 8)
            assert 40.0 <= delay <= 56.0

    def test_higher_temp_gives_shorter_delay(self) -> None:
        """A higher temperature should produce a shorter (or equal) delay."""
        # Fix seed so base_delay is identical in both calls
        random.seed(7)
        delay_low_temp = TemperatureService.calculate_spawn_delay(1.0, 8)
        random.seed(7)
        delay_high_temp = TemperatureService.calculate_spawn_delay(5.0, 8)
        assert delay_high_temp < delay_low_temp

    def test_deterministic_with_seed(self) -> None:
        """Same seed must produce the same result."""
        random.seed(99)
        delay1 = TemperatureService.calculate_spawn_delay(3.0, 4)
        random.seed(99)
        delay2 = TemperatureService.calculate_spawn_delay(3.0, 4)
        assert delay1 == pytest.approx(delay2)


# ---------------------------------------------------------------------------
# TestDecayTemperatureNHours
# ---------------------------------------------------------------------------


class TestDecayTemperatureNHours:
    """Tests for TemperatureService.decay_temperature_n_hours()."""

    def test_one_hour_decay(self) -> None:
        """10.0 decayed for 1 hour should equal a single decay step (6.7)."""
        result = TemperatureService.decay_temperature_n_hours(10.0, 1)
        assert result == pytest.approx(6.7)

    def test_three_hour_decay(self) -> None:
        """10.0 decayed for 3 hours: 10 * (2/3)^3 = 2.96… → apply rounding steps."""
        # Step-by-step: 10 → 6.7 → 4.5 → 3.0
        result = TemperatureService.decay_temperature_n_hours(10.0, 3)
        assert result == pytest.approx(3.0)

    def test_floor_holds_for_many_hours(self) -> None:
        """Temperature at floor (1.0) should never drop further, regardless of hours."""
        result = TemperatureService.decay_temperature_n_hours(1.0, 5)
        assert result == pytest.approx(1.0)

    def test_zero_hours_returns_unchanged(self) -> None:
        """Decaying for 0 hours should return the original temperature."""
        result = TemperatureService.decay_temperature_n_hours(7.5, 0)
        assert result == pytest.approx(7.5)
