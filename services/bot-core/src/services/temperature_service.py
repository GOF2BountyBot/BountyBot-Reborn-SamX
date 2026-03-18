"""
Temperature Service module for BountyBot.

Manages activity temperature for bounty divisions.  Temperature controls how
many bounties can be active simultaneously and how quickly new bounties spawn
after one ends.

Temperature rises when players participate and decays over time (once per
``GameConstants.GUILD_ACTIVITY_DECAY_INTERVAL``).  This is a pure-logic
service — no database access, no async operations.
"""

import random

from shared import bblogger

from services.game_constants import GameConstants

flogger = bblogger.get_logger(__name__)


class TemperatureService:
    """Manages activity temperature for bounty divisions.

    Temperature controls:
    - How many bounties can be active simultaneously
    - How quickly new bounties spawn after one ends

    Temperature rises when players participate and decays over time.
    """

    @staticmethod
    def raise_temperature(current_temp: float, amount: float | None = None) -> float:
        """Raise temperature by *amount* (default: ``ACTIVITY_TEMP_PER_PLAYER`` = 1).

        Args:
            current_temp: Current temperature value.
            amount: How much to raise.  Defaults to
                ``GameConstants.ACTIVITY_TEMP_PER_PLAYER``.

        Returns:
            New temperature value.
        """
        if amount is None:
            amount = GameConstants.ACTIVITY_TEMP_PER_PLAYER
            flogger.trace(f"Using default ACTIVITY_TEMP_PER_PLAYER={amount}")
        new_temp = current_temp + amount
        flogger.debug(f"Temperature raise input: current={current_temp}, amount={amount}, new={new_temp}")
        flogger.info(f"Temperature raised: {current_temp} → {new_temp} (amount={amount})")
        return new_temp

    @staticmethod
    def decay_temperature(current_temp: float) -> float:
        """Decay temperature by multiplying by the decay rate (2/3).

        The result is floored at ``MIN_GUILD_ACTIVITY`` (1.0) and rounded to
        one decimal place.

        Args:
            current_temp: Current temperature value.

        Returns:
            New temperature after decay.
        """
        flogger.trace(f"Decay calculation: {current_temp} * {GameConstants.GUILD_ACTIVITY_DECAY_RATE}")
        decayed = current_temp * GameConstants.GUILD_ACTIVITY_DECAY_RATE
        flogger.trace(f"After multiplication (before clamp/round): {decayed}")
        new_temp = max(GameConstants.MIN_GUILD_ACTIVITY, round(decayed, 1))
        flogger.info(
            f"Temperature decayed: {current_temp} → {new_temp} (rate={GameConstants.GUILD_ACTIVITY_DECAY_RATE})"
        )
        return new_temp

    @staticmethod
    def get_max_bounties(temperature: float) -> int:
        """Calculate the maximum number of concurrent bounties based on temperature.

        Formula: ``min(MAX_BOUNTIES_PER_DIVISION, max(1, int(temperature)))``

        Examples::

            temp=1  → 1 bounty
            temp=3  → 3 bounties
            temp=5+ → 5 bounties (capped at MAX_BOUNTIES_PER_DIVISION)

        Args:
            temperature: Current activity temperature.

        Returns:
            Maximum number of concurrent bounties.
        """
        flogger.debug(
            f"Calculating max bounties: temperature={temperature},"
            f" MAX_BOUNTIES_PER_DIVISION={GameConstants.MAX_BOUNTIES_PER_DIVISION}"
        )
        max_bounties = min(
            GameConstants.MAX_BOUNTIES_PER_DIVISION,
            max(1, int(temperature)),
        )
        flogger.debug(f"Max bounties calculated: {max_bounties}")
        return max_bounties

    @staticmethod
    def calculate_spawn_delay(temperature: float, route_length: int) -> float:
        """Calculate the spawn delay in minutes for the next bounty.

        Formula: ``random(5-7 min) * temperature^-0.1 * route_length``

        Higher temperature produces a shorter delay (more activity → faster
        respawns).  Longer routes produce a proportionally longer delay.

        Args:
            temperature: Current activity temperature.
            route_length: Number of systems in the bounty route.

        Returns:
            Spawn delay in minutes.
        """
        # Guard against temperature ≤ 0 to avoid math domain errors in
        # the exponentiation below.  Temperature should always be ≥ 1.0 via
        # system invariants, but we clamp defensively.
        flogger.trace(f"calculate_spawn_delay: input temperature={temperature}, route_length={route_length}")
        original_temperature = temperature
        temperature = max(GameConstants.MIN_GUILD_ACTIVITY, temperature)
        if temperature != original_temperature:  # Was clamped
            flogger.trace(f"Temperature clamped to MIN_GUILD_ACTIVITY={GameConstants.MIN_GUILD_ACTIVITY}")

        base_delay = random.uniform(
            GameConstants.BOUNTY_DELAY_RANDOM_MIN,
            GameConstants.BOUNTY_DELAY_RANDOM_MAX,
        )
        flogger.trace(
            f"Random base_delay: {base_delay:.2f} (range={GameConstants.BOUNTY_DELAY_RANDOM_MIN}"
            f"-{GameConstants.BOUNTY_DELAY_RANDOM_MAX})"
        )
        temp_factor = temperature**-0.1  # Higher temp → smaller factor → shorter delay
        flogger.trace(f"Temperature factor: {temp_factor:.4f} (temperature^-0.1)")
        spawn_delay = base_delay * temp_factor * route_length
        flogger.debug(
            f"Spawn delay: base={base_delay:.2f} temp_factor={temp_factor:.4f}"
            f" route_length={route_length} → {spawn_delay:.2f} min"
        )
        return spawn_delay

    @staticmethod
    def decay_temperature_n_hours(current_temp: float, hours: int) -> float:
        """Simulate *hours* hours of temperature decay.

        Applies :meth:`decay_temperature` iteratively for the given number of
        hours.  Useful for calculating temperature after a period of inactivity
        (e.g. server downtime).

        Args:
            current_temp: Current temperature value.
            hours: Number of decay intervals (hours) to simulate.

        Returns:
            Temperature after *hours* of decay (never below ``MIN_GUILD_ACTIVITY``).
        """
        flogger.debug(f"Applying {hours} hours of temperature decay: starting_temp={current_temp}")
        temp = current_temp
        for hour_num in range(hours):
            flogger.trace(f"Decay iteration {hour_num + 1}/{hours}: temp={temp}")
            temp = TemperatureService.decay_temperature(temp)
        flogger.debug(f"Temperature decay complete after {hours} hours: {current_temp} → {temp}")
        return temp
