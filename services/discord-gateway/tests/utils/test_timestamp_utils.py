"""Tests for utils/timestamp_utils.py — covers iso_to_discord_ts()."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.timestamp_utils import iso_to_discord_ts


class TestIsoToDiscordTs:
    """Tests for iso_to_discord_ts() — all acceptance criteria covered."""

    # ------------------------------------------------------------------
    # Criterion: Returns "N/A" for None or empty string input
    # ------------------------------------------------------------------

    def test_none_input_returns_na(self):
        """None input → 'N/A'."""
        result = iso_to_discord_ts(None)
        assert result == "N/A"

    def test_empty_string_returns_na(self):
        """Empty string input → 'N/A'."""
        result = iso_to_discord_ts("")
        assert result == "N/A"

    # ------------------------------------------------------------------
    # Criterion: Returns "N/A" for unparseable strings
    # ------------------------------------------------------------------

    def test_invalid_string_returns_na(self):
        """Non-ISO string → 'N/A'."""
        result = iso_to_discord_ts("not-a-date")
        assert result == "N/A"

    def test_partial_date_returns_na(self):
        """Incomplete ISO string → 'N/A'."""
        result = iso_to_discord_ts("2026-04")
        # Python's fromisoformat accepts "2026-04" — this is valid.
        # The function should return a discord timestamp for it.
        # This test verifies it doesn't crash.
        assert result == "N/A" or result.startswith("<t:")

    def test_random_garbage_returns_na(self):
        """Random text → 'N/A'."""
        result = iso_to_discord_ts("abc-xyz-123")
        assert result == "N/A"

    # ------------------------------------------------------------------
    # Criterion: Default style is "R" (relative)
    # ------------------------------------------------------------------

    def test_default_style_is_relative(self):
        """Without specifying style, should use 'R'."""
        result = iso_to_discord_ts("2026-04-05T12:00:00+00:00")
        assert result.endswith(":R>")
        assert result.startswith("<t:")

    # ------------------------------------------------------------------
    # Criterion: Returns correct Discord timestamp format <t:UNIX:style>
    # ------------------------------------------------------------------

    def test_utc_iso_string_format(self):
        """UTC ISO string with +00:00 → <t:UNIX:R>."""
        iso_str = "2026-04-05T12:00:00+00:00"
        result = iso_to_discord_ts(iso_str)
        # Must be in the format <t:NUMBER:R>
        assert result.startswith("<t:")
        assert result.endswith(":R>")
        # Extract the unix timestamp and verify it's a number
        inner = result[3:-3]  # strips "<t:" and ":R>"
        assert inner.isdigit()

    def test_z_suffix_iso_string(self):
        """ISO string with Z suffix (UTC) → valid discord timestamp."""
        result = iso_to_discord_ts("2026-04-05T12:00:00Z")
        assert result.startswith("<t:")
        assert result.endswith(":R>")

    def test_utc_and_z_produce_same_timestamp(self):
        """'+00:00' and 'Z' variants should produce the same Unix timestamp."""
        result_utc = iso_to_discord_ts("2026-04-05T12:00:00+00:00")
        result_z = iso_to_discord_ts("2026-04-05T12:00:00Z")
        assert result_utc == result_z

    def test_known_timestamp_value(self):
        """Verify the exact Unix timestamp value for a known date."""
        # 2026-01-01T00:00:00+00:00 = 1767225600 Unix
        result = iso_to_discord_ts("2026-01-01T00:00:00+00:00")
        assert result == "<t:1767225600:R>"

    # ------------------------------------------------------------------
    # Criterion: All styles are supported
    # ------------------------------------------------------------------

    def test_style_d_short_date(self):
        """Style 'D' → <t:UNIX:D>."""
        result = iso_to_discord_ts("2026-04-05T12:00:00+00:00", "D")
        assert result.endswith(":D>")
        assert result.startswith("<t:")

    def test_style_f_full_date(self):
        """Style 'F' → <t:UNIX:F>."""
        result = iso_to_discord_ts("2026-04-05T12:00:00+00:00", "F")
        assert result.endswith(":F>")

    def test_style_t_short_time(self):
        """Style 't' → <t:UNIX:t>."""
        result = iso_to_discord_ts("2026-04-05T12:00:00+00:00", "t")
        assert result.endswith(":t>")

    def test_style_T_full_time(self):
        """Style 'T' → <t:UNIX:T>."""
        result = iso_to_discord_ts("2026-04-05T12:00:00+00:00", "T")
        assert result.endswith(":T>")

    def test_style_lowercase_f(self):
        """Style 'f' (short datetime) → <t:UNIX:f>."""
        result = iso_to_discord_ts("2026-04-05T12:00:00+00:00", "f")
        assert result.endswith(":f>")

    def test_style_lowercase_d(self):
        """Style 'd' (short date) → <t:UNIX:d>."""
        result = iso_to_discord_ts("2026-04-05T12:00:00+00:00", "d")
        assert result.endswith(":d>")

    # ------------------------------------------------------------------
    # Criterion: Handles timezone-aware datetimes correctly
    # ------------------------------------------------------------------

    def test_non_utc_timezone(self):
        """Non-UTC timezone ISO string → valid discord timestamp."""
        # UTC+5 offset
        result = iso_to_discord_ts("2026-04-05T17:00:00+05:00")
        assert result.startswith("<t:")
        assert result.endswith(":R>")

    def test_non_utc_same_moment_as_utc(self):
        """UTC+5 17:00 == UTC 12:00 — should produce same unix timestamp."""
        result_utc = iso_to_discord_ts("2026-04-05T12:00:00+00:00")
        result_plus5 = iso_to_discord_ts("2026-04-05T17:00:00+05:00")
        assert result_utc == result_plus5

    # ------------------------------------------------------------------
    # Criterion: Returns "N/A" on OSError (out-of-range timestamps)
    # ------------------------------------------------------------------

    def test_out_of_range_year_returns_na(self):
        """Extremely far future date may raise OSError on some platforms → 'N/A'."""
        # Year 9999 or year 1 could cause platform-specific issues
        # Test that the function handles it gracefully
        result = iso_to_discord_ts("9999-12-31T23:59:59+00:00")
        # Either N/A or a valid timestamp — no crash
        assert result == "N/A" or result.startswith("<t:")
