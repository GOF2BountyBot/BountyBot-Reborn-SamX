"""
Division Service module for BountyBot.

Maps player levels to game divisions (bronze / silver / gold) using
``GameConstants.DIVISION_BOUNDARIES``.  This is a pure-logic service with no
database access and no async operations.
"""

from shared import bblogger

from services.game_constants import GameConstants
from services.game_maths import calculate_user_level

flogger = bblogger.get_logger(__name__)


class DivisionService:
    """Service for mapping player levels to game divisions."""

    @staticmethod
    def get_division_for_level(level: int) -> str:
        """Return the division name for a player level.

        Levels outside [0, 10] are clamped to the nearest boundary before
        the lookup, so very negative XP still yields ``"bronze"`` and any
        level above 10 still yields ``"gold"``.

        Args:
            level: Player level (expected range 0-10).

        Returns:
            One of ``"bronze"``, ``"silver"``, or ``"gold"``.
        """
        names = GameConstants.DIVISION_NAMES
        boundaries = GameConstants.DIVISION_BOUNDARIES

        for i, (_min_lvl, max_lvl) in enumerate(boundaries):
            # Clamp: treat anything <= max boundary of the last division as
            # belonging to that division; anything below min of the first
            # division also falls in the first division.
            if level <= max_lvl:
                division = names[i]
                flogger.debug(f"Division assignment: level={level} → {division}")
                return division

        # level exceeds all upper boundaries — return the last division
        division = names[-1]
        flogger.debug(f"Division assignment: level={level} → {division} (clamped to max)")
        return division

    @staticmethod
    def get_division_boundaries(division_name: str) -> tuple[int, int]:
        """Return (min_level, max_level) for a named division.

        Args:
            division_name: One of ``"bronze"``, ``"silver"``, or ``"gold"``.

        Returns:
            A ``(min_level, max_level)`` tuple.

        Raises:
            ValueError: If *division_name* is not a recognised division.
        """
        names = GameConstants.DIVISION_NAMES
        boundaries = GameConstants.DIVISION_BOUNDARIES

        try:
            index = names.index(division_name)
        except ValueError:
            flogger.debug(f"Division boundary query: unknown division {division_name!r}")
            raise ValueError(
                f"Unknown division: {division_name!r}. "
                f"Valid divisions are: {names}"
            ) from None

        result = boundaries[index]
        flogger.debug(f"Division boundary query: {division_name} → min={result[0]} max={result[1]}")
        return result

    @staticmethod
    def get_all_divisions() -> list[dict]:
        """Return a list of all division configuration dicts.

        Each entry has the keys ``name``, ``min_level``, and ``max_level``.

        Returns:
            List of dicts, one per division, ordered from lowest to highest.
        """
        names = GameConstants.DIVISION_NAMES
        boundaries = GameConstants.DIVISION_BOUNDARIES

        return [
            {"name": name, "min_level": min_lvl, "max_level": max_lvl}
            for name, (min_lvl, max_lvl) in zip(names, boundaries, strict=True)
        ]

    @staticmethod
    def get_division_for_player_xp(xp: int) -> str:
        """Convenience method: derive division directly from accumulated XP.

        Calculates the player level from *xp* using
        :func:`~services.game_maths.calculate_user_level`, then delegates to
        :meth:`get_division_for_level`.

        Args:
            xp: Player's current XP (may be 0 or negative for new players).

        Returns:
            One of ``"bronze"``, ``"silver"``, or ``"gold"``.
        """
        level = calculate_user_level(xp)
        return DivisionService.get_division_for_level(level)
