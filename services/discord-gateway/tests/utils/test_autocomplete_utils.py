"""
Tests for autocomplete_utils.py — normalize_for_search utility function.

Covers:
- Basic accent/diacritic stripping
- Case insensitivity
- Apostrophes and hyphens stripped (for user convenience)
- ASCII strings unaffected
- Empty string handling
- Autocomplete matching for accented game names
"""

import os
import sys

import pytest

# Ensure the src directory is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


class TestNormalizeForSearch:
    """Tests for normalize_for_search() utility function."""

    def _import(self):
        from utils.autocomplete_utils import normalize_for_search

        return normalize_for_search

    # --- Basic accent stripping ---

    def test_strips_acute_accent(self):
        """normalize_for_search should strip acute accents (é → e)."""
        normalize_for_search = self._import()
        assert normalize_for_search("Behén") == "behen"

    def test_strips_grave_accent(self):
        """normalize_for_search should strip grave accents (è → e)."""
        normalize_for_search = self._import()
        assert normalize_for_search("Behèn") == "behen"

    def test_strips_umlaut(self):
        """normalize_for_search should strip umlauts (ü → u, ö → o, ä → a)."""
        normalize_for_search = self._import()
        assert normalize_for_search("ÜBER") == "uber"
        assert normalize_for_search("Göteborg") == "goteborg"
        assert normalize_for_search("Gärten") == "garten"

    def test_strips_tilde(self):
        """normalize_for_search should strip tildes (ñ → n)."""
        normalize_for_search = self._import()
        assert normalize_for_search("España") == "espana"

    def test_strips_cedilla(self):
        """normalize_for_search should strip cedillas (ç → c)."""
        normalize_for_search = self._import()
        assert normalize_for_search("Façade") == "facade"

    # --- Case insensitivity ---

    def test_lowercases_output(self):
        """normalize_for_search should always return lowercase."""
        normalize_for_search = self._import()
        assert normalize_for_search("BRONZE") == "bronze"
        assert normalize_for_search("Silver") == "silver"
        assert normalize_for_search("PLATINUM") == "platinum"

    def test_lowercases_and_strips_combined(self):
        """normalize_for_search should strip accents AND lowercase in one call."""
        normalize_for_search = self._import()
        assert normalize_for_search("BehÉN") == "behen"

    # --- ASCII strings unaffected ---

    def test_ascii_string_unaffected(self):
        """normalize_for_search on ASCII-only strings should just lowercase (minus apostrophes/hyphens)."""
        normalize_for_search = self._import()
        assert normalize_for_search("Bronze") == "bronze"

    def test_apostrophes_stripped(self):
        """normalize_for_search should strip ASCII apostrophes."""
        normalize_for_search = self._import()
        result = normalize_for_search("N'saan")
        assert "'" not in result
        assert result == "nsaan"

    def test_curly_apostrophe_stripped(self):
        """normalize_for_search should strip curly (Unicode) apostrophes (\u2019)."""
        normalize_for_search = self._import()
        result = normalize_for_search("S\u2019Kolptorr")
        assert result == "skolptorr"

    def test_modifier_apostrophe_stripped(self):
        """normalize_for_search should strip modifier apostrophes (\u02bc)."""
        normalize_for_search = self._import()
        result = normalize_for_search("S\u02bcKolptorr")
        assert result == "skolptorr"

    def test_apostrophe_matching(self):
        """Typing 'sk' should match \"S'Kolptorr\" after normalization."""
        normalize_for_search = self._import()
        assert normalize_for_search("sk") in normalize_for_search("S'Kolptorr")

    def test_hyphens_stripped(self):
        """normalize_for_search should strip hyphens."""
        normalize_for_search = self._import()
        assert normalize_for_search("Kaamo-Station") == "kaamostation"

    def test_hyphen_matching(self):
        """Typing 'kaamostation' should match 'Kaamo-Station' after normalization."""
        normalize_for_search = self._import()
        assert normalize_for_search("kaamostation") in normalize_for_search("Kaamo-Station")

    # --- Edge cases ---

    def test_empty_string(self):
        """normalize_for_search should handle empty string without error."""
        normalize_for_search = self._import()
        assert normalize_for_search("") == ""

    def test_numbers_preserved(self):
        """normalize_for_search should preserve digits."""
        normalize_for_search = self._import()
        assert normalize_for_search("System42") == "system42"

    def test_spaces_preserved(self):
        """normalize_for_search should preserve spaces."""
        normalize_for_search = self._import()
        assert normalize_for_search("Alpha Centauri") == "alpha centauri"

    # --- Autocomplete matching use cases ---

    def test_accent_insensitive_matching(self):
        """Typing 'behen' should match 'Behén' after normalization."""
        normalize_for_search = self._import()
        query = "behen"
        name = "Behén"
        assert normalize_for_search(query) in normalize_for_search(name)

    def test_umlaut_insensitive_matching(self):
        """Typing 'uber' should match 'Über' after normalization."""
        normalize_for_search = self._import()
        assert normalize_for_search("uber") in normalize_for_search("Über")

    def test_partial_accent_match(self):
        """Partial unaccented query should match accented name."""
        normalize_for_search = self._import()
        # 'be' should match in 'Behén'
        assert normalize_for_search("be") in normalize_for_search("Behén")

    def test_full_query_no_accent_matches_full_name_with_accent(self):
        """Full unaccented query should match full accented name."""
        normalize_for_search = self._import()
        # User types "Espana" but name is "España"
        assert normalize_for_search("espana") in normalize_for_search("España")

    def test_mixed_case_query_matches_accented_name(self):
        """Mixed-case query should match accented name after normalization."""
        normalize_for_search = self._import()
        assert normalize_for_search("BehEn") in normalize_for_search("Behén")

    def test_already_matching_names_still_match(self):
        """Names without accents should still match as before."""
        normalize_for_search = self._import()
        assert normalize_for_search("bron") in normalize_for_search("Bronze")
        assert normalize_for_search("silv") in normalize_for_search("Silver")

    def test_non_matching_names_still_do_not_match(self):
        """Names that don't match should still not match after normalization."""
        normalize_for_search = self._import()
        # 'xyz' shouldn't be found in 'Behén'
        assert normalize_for_search("xyz") not in normalize_for_search("Behén")

    def test_multiple_accented_chars_in_name(self):
        """normalize_for_search should strip all accented chars in a single string."""
        normalize_for_search = self._import()
        # e.g., "Ñoño" (2 tildes and 2 umlauts-style marks)
        assert normalize_for_search("Ñoño") == "nono"

    def test_nfkd_normalization_applied(self):
        """normalize_for_search uses NFKD so combined chars like ﬁ (fi ligature) are expanded."""
        normalize_for_search = self._import()
        # \ufb01 is the 'fi' ligature — NFKD decomposes it to 'fi'
        result = normalize_for_search("\ufb01rst")
        assert result == "first"
