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

from services.game_constants import GameConstants


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
        return current_temp + amount

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
        decayed = current_temp * GameConstants.GUILD_ACTIVITY_DECAY_RATE
        return max(GameConstants.MIN_GUILD_ACTIVITY, round(decayed, 1))

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
        return min(
            GameConstants.MAX_BOUNTIES_PER_DIVISION,
            max(1, int(temperature)),
        )

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
        base_delay = random.uniform(
            GameConstants.BOUNTY_DELAY_RANDOM_MIN,
            GameConstants.BOUNTY_DELAY_RANDOM_MAX,
        )
        temp_factor = temperature**-0.1  # Higher temp → smaller factor → shorter delay
        return base_delay * temp_factor * route_length

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
        temp = current_temp
        for _ in range(hours):
            temp = TemperatureService.decay_temperature(temp)
        return temp
