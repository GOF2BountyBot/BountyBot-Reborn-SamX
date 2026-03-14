"""
Unit tests for DivisionService.

All tests are pure unit tests — zero mocks needed because DivisionService
contains no I/O, no async operations, and no database access.

Coverage:
- get_division_for_level  — all boundary levels and clamping behaviour
- get_division_boundaries — happy-path and ValueError for unknown division
- get_all_divisions       — structure and count of returned list
- get_division_for_player_xp — XP → level → division convenience path
"""

import pytest
from src.services.division_service import DivisionService


# ---------------------------------------------------------------------------
# TestGetDivisionForLevel
# ---------------------------------------------------------------------------


class TestGetDivisionForLevel:
    """Tests for DivisionService.get_division_for_level()."""

    @pytest.mark.parametrize(
        "level, expected_division",
        [
            # Bronze boundaries
            (0, "bronze"),
            (1, "bronze"),
            (2, "bronze"),
            (3, "bronze"),  # max bronze
            # Silver boundaries
            (4, "silver"),  # min silver
            (5, "silver"),
            (6, "silver"),
            (7, "silver"),  # max silver
            # Gold boundaries
            (8, "gold"),    # min gold
            (9, "gold"),
            (10, "gold"),   # max gold
        ],
    )
    def test_standard_levels(self, level: int, expected_division: str) -> None:
        """Each in-range level maps to the correct division."""
        assert DivisionService.get_division_for_level(level) == expected_division

    @pytest.mark.parametrize(
        "level, expected_division",
        [
            (-1, "bronze"),   # one below minimum
            (-100, "bronze"), # far below minimum
        ],
    )
    def test_negative_level_clamped_to_bronze(
        self, level: int, expected_division: str
    ) -> None:
        """Negative levels clamp to bronze (lowest division)."""
        assert DivisionService.get_division_for_level(level) == expected_division

    @pytest.mark.parametrize(
        "level, expected_division",
        [
            (11, "gold"),   # one above maximum
            (100, "gold"),  # far above maximum
        ],
    )
    def test_overlimit_level_clamped_to_gold(
        self, level: int, expected_division: str
    ) -> None:
        """Levels above 10 clamp to gold (highest division)."""
        assert DivisionService.get_division_for_level(level) == expected_division


# ---------------------------------------------------------------------------
# TestGetDivisionBoundaries
# ---------------------------------------------------------------------------


class TestGetDivisionBoundaries:
    """Tests for DivisionService.get_division_boundaries()."""

    @pytest.mark.parametrize(
        "division_name, expected_min, expected_max",
        [
            ("bronze", 0, 3),
            ("silver", 4, 7),
            ("gold", 8, 10),
        ],
    )
    def test_known_divisions(
        self, division_name: str, expected_min: int, expected_max: int
    ) -> None:
        """Each known division returns the correct (min_level, max_level) tuple."""
        result = DivisionService.get_division_boundaries(division_name)
        assert result == (expected_min, expected_max)

    def test_unknown_division_raises_value_error(self) -> None:
        """An unrecognised division name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown division"):
            DivisionService.get_division_boundaries("invalid")

    @pytest.mark.parametrize("bad_name", ["Bronze", "GOLD", "platinum", "", " "])
    def test_case_sensitive_and_unknown_names_raise(self, bad_name: str) -> None:
        """Division lookup is case-sensitive; wrong case raises ValueError."""
        with pytest.raises(ValueError):
            DivisionService.get_division_boundaries(bad_name)


# ---------------------------------------------------------------------------
# TestGetAllDivisions
# ---------------------------------------------------------------------------


class TestGetAllDivisions:
    """Tests for DivisionService.get_all_divisions()."""

    def test_returns_three_entries(self) -> None:
        """Exactly 3 division entries are returned."""
        result = DivisionService.get_all_divisions()
        assert len(result) == 3

    def test_entry_structure(self) -> None:
        """Each entry has name, min_level, and max_level keys."""
        result = DivisionService.get_all_divisions()
        for entry in result:
            assert "name" in entry
            assert "min_level" in entry
            assert "max_level" in entry

    def test_division_names_and_boundaries(self) -> None:
        """Divisions are ordered bronze → silver → gold with correct boundaries."""
        result = DivisionService.get_all_divisions()
        assert result[0] == {"name": "bronze", "min_level": 0, "max_level": 3}
        assert result[1] == {"name": "silver", "min_level": 4, "max_level": 7}
        assert result[2] == {"name": "gold", "min_level": 8, "max_level": 10}


# ---------------------------------------------------------------------------
# TestGetDivisionForPlayerXp
# ---------------------------------------------------------------------------


class TestGetDivisionForPlayerXp:
    """Tests for DivisionService.get_division_for_player_xp()."""

    @pytest.mark.parametrize(
        "xp, expected_division",
        [
            # XP_LEVEL_BOUNDARIES: [-1, 0, 1050, 2000, 3500, 10000, 18000, 61000, 71000, 90000, 1000000]
            # index 0 is sentinel, level = index - 1 for first match exceeding xp
            (0, "bronze"),     # calculate_user_level(0) == 0 → bronze
            (-1, "bronze"),    # very low XP → level 0 → bronze
            (1049, "bronze"),  # just below level 2 boundary → level 1 → bronze
            (3500, "silver"),  # exactly at level 4 boundary → level 4 → silver
            (10000, "silver"), # exactly at level 5 boundary → level 5 → silver
            (60999, "silver"), # just below level 7 boundary → level 6 → silver
            (71000, "gold"),   # exactly at level 8 boundary → level 8 → gold
            (90000, "gold"),   # exactly at level 9 boundary → level 9 → gold
            (999999, "gold"),  # just below level 10 boundary → level 9 → gold
            (1000000, "gold"), # at level 10 boundary → level 10 → gold
        ],
    )
    def test_xp_to_division(self, xp: int, expected_division: str) -> None:
        """XP is converted to the correct division via level calculation."""
        assert DivisionService.get_division_for_player_xp(xp) == expected_division
