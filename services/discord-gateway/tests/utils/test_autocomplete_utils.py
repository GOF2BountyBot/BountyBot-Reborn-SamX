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

    def test_spaces_stripped(self):
        """normalize_for_search should strip spaces (new behavior: spaces removed for search).

        Space-stripping allows users to type 'herjaza' and match 'Her Jaza',
        or 'alphacentauri' to match 'Alpha Centauri'.
        """
        normalize_for_search = self._import()
        assert normalize_for_search("Alpha Centauri") == "alphacentauri"
        assert normalize_for_search("Her Jaza") == "herjaza"
        assert normalize_for_search("Skor Terpa") == "skorterpa"

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

    def test_only_spaces_returns_empty_string(self):
        """normalize_for_search on a string of only spaces should return empty string."""
        normalize_for_search = self._import()
        assert normalize_for_search("   ") == ""

    def test_space_stripping_enables_run_together_search(self):
        """normalize_for_search strips spaces: 'herjaza' matches 'Her Jaza'."""
        normalize_for_search = self._import()
        assert normalize_for_search("herjaza") in normalize_for_search("Her Jaza")
        assert normalize_for_search("skorterpa") in normalize_for_search("Skor Terpa")


class TestFuzzyFilter:
    """Tests for fuzzy_filter() — substring + fuzzy fallback search."""

    def _import(self):
        from utils.autocomplete_utils import fuzzy_filter

        return fuzzy_filter

    # --- Empty input ---

    def test_empty_current_returns_all_candidates_up_to_limit(self):
        """fuzzy_filter with empty current should return ALL candidates up to limit."""
        fuzzy_filter = self._import()
        candidates = ["Sol", "Alpha", "Beta", "Delta"]
        result = fuzzy_filter("", candidates)
        assert result == candidates  # preserves order, returns all

    def test_empty_current_caps_at_limit(self):
        """fuzzy_filter with empty current caps at `limit` items."""
        fuzzy_filter = self._import()
        candidates = [f"System{i}" for i in range(50)]
        result = fuzzy_filter("", candidates, limit=25)
        assert len(result) == 25

    def test_empty_candidates_returns_empty_list(self):
        """fuzzy_filter with empty candidates list returns []."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("alpha", [])
        assert result == []

    # --- Exact substring match (phase 1) ---

    def test_substring_match_exact(self):
        """fuzzy_filter should return candidates where current is a substring."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("sol", ["Sol", "Alpha", "Beta"])
        assert result == ["Sol"]

    def test_substring_match_case_insensitive(self):
        """fuzzy_filter substring match ignores case via normalize_for_search."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("SOL", ["Sol", "Alpha"])
        assert "Sol" in result

    def test_substring_match_accent_stripped(self):
        """fuzzy_filter substring match strips accents: 'behen' matches 'Behén'."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("behen", ["Behén", "Sol", "Alpha"])
        assert "Behén" in result
        assert "Sol" not in result

    def test_substring_match_space_stripped(self):
        """fuzzy_filter matches 'herjaza' against 'Her Jaza' via space stripping."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("herjaza", ["Her Jaza", "Sol", "Alpha"])
        assert "Her Jaza" in result
        assert result[0] == "Her Jaza"  # substring match comes first

    def test_substring_match_apostrophe_stripped(self):
        """fuzzy_filter matches 'nsaan' against \"N'saan\" via apostrophe stripping."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("nsaan", ["N'saan", "Sol"])
        assert "N'saan" in result

    def test_substring_match_hyphen_stripped(self):
        """fuzzy_filter matches 'kaamostation' against 'Kaamo-Station'."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("kaamostation", ["Kaamo-Station", "Sol"])
        assert "Kaamo-Station" in result

    # --- Fuzzy fallback (phase 2) ---

    def test_fuzzy_typo_magntar_matches_magnetar(self):
        """fuzzy_filter should fuzzy-match 'magntar' -> 'Magnetar'."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("magntar", ["Magnetar", "Sol", "Alpha"])
        assert "Magnetar" in result

    def test_fuzzy_phonetic_nimrod_matches_nimrrod(self):
        """fuzzy_filter should fuzzy-match 'nimrod' -> \"Ni'Mrrod\"."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("nimrod", ["Ni'Mrrod", "Sol", "Alpha"])
        assert "Ni'Mrrod" in result

    def test_fuzzy_match_preserves_original_canonical_name(self):
        """fuzzy_filter returns original names (with apostrophes/accents), not normalized forms."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("nimrod", ["Ni'Mrrod", "Sol"])
        # Must be the canonical name with apostrophe, not 'nimrrod'
        assert any("'" in name for name in result)
        assert "Ni'Mrrod" in result

    def test_no_match_returns_empty_list(self):
        """fuzzy_filter with garbage input returns []."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("xyzqwerty12345", ["Sol", "Alpha", "Beta"])
        assert result == []

    def test_result_capped_at_limit(self):
        """fuzzy_filter returns at most `limit` results."""
        fuzzy_filter = self._import()
        candidates = [f"System{i}" for i in range(50)]
        result = fuzzy_filter("sys", candidates, limit=10)
        assert len(result) <= 10

    def test_deduplicated_results_phase2_vs_phase1(self):
        """fuzzy_filter deduplicates both input duplicates AND fuzzy hits against phase-1 hits.

        DEF-FUZZY-001 (FIXED): fuzzy_filter now deduplicates the *input* candidates list
        at the top of the function using dict.fromkeys(). Duplicate entries in the caller's
        candidates list must NOT appear multiple times in the output.

        Two assertions:
        1. Input deduplication: ["Sol", "Sol", "Alpha"] → "Sol" appears exactly once.
        2. Phase-2 dedup: a name that is both a substring match (Phase 1) and a fuzzy
           hit (Phase 2) must appear exactly once in output — no double-add from fuzzy.
        """
        fuzzy_filter = self._import()

        # Assertion 1 — DEF-FUZZY-001 fix: duplicate input candidates must be deduplicated.
        # Before the fix, fuzzy_filter(["Sol", "Sol", "Alpha"], "sol") returned ["Sol", "Sol"].
        # After the fix, it must return ["Sol"] (exactly one occurrence).
        dup_candidates = ["Sol", "Sol", "Alpha"]
        dup_result = fuzzy_filter("sol", dup_candidates)
        assert dup_result.count("Sol") == 1, (
            "Duplicate input candidates must be deduplicated. "
            f"Got {dup_result!r} — 'Sol' appeared {dup_result.count('Sol')} time(s)."
        )

        # Assertion 2 — Phase 2 dedup: fuzzy should not re-add a name already in Phase-1 results.
        # Use a name that fuzzy would score highly AND also substring-matches.
        candidates = ["Magnetar", "Sol", "Alpha"]
        result = fuzzy_filter("magnetar", candidates)
        # "Magnetar" is a substring match in Phase 1; fuzzy should not add it again.
        assert result.count("Magnetar") == 1

    def test_five_or_more_substring_hits_skip_fuzzy(self):
        """If 5+ substring hits, fuzzy phase is skipped (no spurious extras added)."""
        fuzzy_filter = self._import()
        # 6 substring matches → fuzzy not triggered
        candidates = [f"System{i}" for i in range(6)] + ["Typo_system"]
        result = fuzzy_filter("system", candidates)
        # All 6 substring matches present
        for i in range(6):
            assert f"System{i}" in result
        # "Typo_system" only added if fuzzy triggered; with 6 hits it should not be triggered
        # BUT "Typo_system" normalized = "typosystem" which CONTAINS "system" — so it IS a substring match
        # Use a name that doesn't substring-match but might fuzzy-match
        candidates2 = [f"system{i}" for i in range(6)] + ["syst3m_fuzzy_only"]
        result2 = fuzzy_filter("system", candidates2)
        # All 6 substring matches are present; no fuzzy phase triggered
        assert len([r for r in result2 if r.startswith("system")]) == 6

    def test_short_input_single_char_does_not_match_unrelated_names(self):
        """Single-char input 'p' should not fuzzy-match names with no 'p' in them."""
        fuzzy_filter = self._import()
        # "Zeta" and "Omega" don't contain 'p' at all
        result = fuzzy_filter("p", ["Zeta", "Omega", "Pan"])
        # "Pan" is a substring match; "Zeta"/"Omega" should NOT appear
        assert "Pan" in result
        assert "Zeta" not in result
        assert "Omega" not in result

    def test_current_longer_than_all_candidates_does_not_crash(self):
        """fuzzy_filter with current longer than all candidates should not crash."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("averylongquerythatexceedseveryname", ["Sol", "A", "Beta"])
        # Should return empty or very few matches, no exception
        assert isinstance(result, list)

    def test_returns_list_not_generator(self):
        """fuzzy_filter always returns a list, not a generator or other iterable."""
        fuzzy_filter = self._import()
        result = fuzzy_filter("sol", ["Sol", "Alpha"])
        assert isinstance(result, list)


class TestResolveSystemName:
    """Tests for resolve_system_name() — canonical name resolution."""

    def _import(self):
        from utils.autocomplete_utils import resolve_system_name

        return resolve_system_name

    def test_exact_match_case_insensitive(self):
        """resolve_system_name matches exact input regardless of case."""
        resolve_system_name = self._import()
        systems = ["Sol", "Alpha Centauri", "Beta Cygni"]
        assert resolve_system_name("Sol", systems) == "Sol"
        assert resolve_system_name("sol", systems) == "Sol"
        assert resolve_system_name("SOL", systems) == "Sol"
        assert resolve_system_name("sOl", systems) == "Sol"

    def test_normalized_match_strips_accents(self):
        """resolve_system_name matches via normalize_for_search: 'behen' -> 'Behén'."""
        resolve_system_name = self._import()
        systems = ["Behén", "Sol", "N'saan"]
        assert resolve_system_name("behen", systems) == "Behén"

    def test_normalized_match_strips_apostrophe(self):
        """resolve_system_name matches \"nsaan\" -> \"N'saan\" via apostrophe stripping."""
        resolve_system_name = self._import()
        systems = ["N'saan", "Sol"]
        assert resolve_system_name("nsaan", systems) == "N'saan"

    def test_normalized_match_strips_spaces(self):
        """resolve_system_name matches 'alphacentauri' -> 'Alpha Centauri' via space stripping."""
        resolve_system_name = self._import()
        systems = ["Alpha Centauri", "Sol"]
        assert resolve_system_name("alphacentauri", systems) == "Alpha Centauri"

    def test_fuzzy_typo_match(self):
        """resolve_system_name fuzzy-matches a typo: 'magntar' -> 'Magnetar'."""
        resolve_system_name = self._import()
        systems = ["Magnetar", "Sol", "Alpha"]
        assert resolve_system_name("magntar", systems) == "Magnetar"

    def test_garbage_input_returns_none(self):
        """resolve_system_name returns None when no confident match is found."""
        resolve_system_name = self._import()
        systems = ["Sol", "Alpha", "Beta"]
        assert resolve_system_name("xyzqwerty12345", systems) is None

    def test_empty_typed_returns_none(self):
        """resolve_system_name returns None for empty typed string."""
        resolve_system_name = self._import()
        systems = ["Sol", "Alpha"]
        assert resolve_system_name("", systems) is None

    def test_empty_systems_returns_none(self):
        """resolve_system_name returns None when systems list is empty."""
        resolve_system_name = self._import()
        assert resolve_system_name("Sol", []) is None

    def test_both_empty_returns_none(self):
        """resolve_system_name returns None when both typed and systems are empty."""
        resolve_system_name = self._import()
        assert resolve_system_name("", []) is None

    def test_returns_canonical_name_with_original_casing_and_accents(self):
        """resolve_system_name returns the canonical list name, not the typed form."""
        resolve_system_name = self._import()
        systems = ["Behén", "Sol"]
        result = resolve_system_name("BEHEN", systems)
        assert result == "Behén"  # original form preserved

    def test_whitespace_only_input_returns_none(self):
        """resolve_system_name returns None for whitespace-only input."""
        resolve_system_name = self._import()
        systems = ["Sol"]
        # typed.strip().lower() == "" → treated same as empty
        assert resolve_system_name("   ", systems) is None

    def test_fuzzy_low_confidence_garbage_does_not_match(self):
        """resolve_system_name does not fuzzy-match truly unrelated strings."""
        resolve_system_name = self._import()
        systems = ["Sol", "Alpha", "Beta", "Gamma"]
        # A random string should not match anything
        assert resolve_system_name("zzzzzz", systems) is None

    def test_exact_match_preferred_over_fuzzy(self):
        """resolve_system_name prefers exact case-insensitive match over fuzzy."""
        resolve_system_name = self._import()
        systems = ["Sol", "Solara", "Solaris"]
        # "sol" exact-matches "Sol" — should not fuzzy to "Solara"
        assert resolve_system_name("sol", systems) == "Sol"
