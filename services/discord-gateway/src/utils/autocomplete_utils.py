"""
Autocomplete utility functions for Discord Gateway cogs.

Provides accent-insensitive search normalization for autocomplete matching,
so users can type unaccented characters and still match accented game names.
Also provides fuzzy matching as a fallback for fat-finger typos and partial names.
"""

import unicodedata


def normalize_for_search(text: str) -> str:
    """Strip accents/diacritics, apostrophes, hyphens, spaces and lowercase for search.

    Decomposes unicode characters using NFKD normalization, removes all
    combining marks (diacritics like accents, umlauts, cedillas), removes
    apostrophes, hyphens, and spaces that users commonly skip when typing, then
    lowercases the result.  This allows matching "Behen" against "Behén",
    "sk" against "S'Kolptorr", "kaamostation" against "Kaamo-Station",
    "herjaza" against "Her Jaza", "skorterpa" against "Skor Terpa", etc.

    The original display name with accents/punctuation is preserved in
    ``app_commands.Choice(name=..., value=...)`` — only the comparison
    strings are stripped.

    Args:
        text: The input string to normalize.

    Returns:
        A lowercase string with all combining diacritical marks, apostrophes,
        hyphens, and spaces removed.

    Examples:
        >>> normalize_for_search("Behén")
        'behen'
        >>> normalize_for_search("S'Kolptorr")
        'skolptorr'
        >>> normalize_for_search("Kaamo-Station")
        'kaamostation'
        >>> normalize_for_search("ÜBER")
        'uber'
        >>> normalize_for_search("Bronze")
        'bronze'
        >>> normalize_for_search("Her Jaza")
        'herjaza'
        >>> normalize_for_search("Skor Terpa")
        'skorterpa'
    """
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Remove apostrophes, hyphens, and spaces that users might skip when typing
    stripped = (
        stripped.replace("'", "")
        .replace("\u2019", "")
        .replace("\u02bc", "")
        .replace("-", "")
        .replace(" ", "")
    )
    return stripped.lower()


def fuzzy_filter(
    current: str,
    candidates: list[str],
    cutoff: int = 72,
    limit: int = 25,
) -> list[str]:
    """Return candidates matching current — substring first, fuzzy fallback.

    Resolution order:
    1. Exact substring match on normalize_for_search(candidate) (fast, zero deps)
    2. If fewer than 5 substring hits, append fuzzy matches from rapidfuzz
       above `cutoff` score, ranked by score descending, deduplicated.

    Args:
        current: The user's current input string.
        candidates: Full list of canonical name strings to search.
        cutoff: Minimum rapidfuzz WRatio score to include a fuzzy match (0-100).
        limit: Maximum number of results to return.

    Returns:
        Deduplicated list of matching candidate strings, capped at `limit`.
    """
    # Deduplicate candidates while preserving insertion order (O(n), Python 3.7+)
    candidates = list(dict.fromkeys(candidates))

    # Empty input → return all candidates up to limit (same as current behaviour)
    if not current:
        return candidates[:limit]

    norm_current = normalize_for_search(current)

    # Phase 1: substring matching
    exact = [c for c in candidates if norm_current in normalize_for_search(c)]

    # Phase 2: fuzzy fallback when fewer than 5 substring hits
    if len(exact) < 5 and norm_current:
        try:
            import rapidfuzz.fuzz
            import rapidfuzz.process

            normalized_candidates = [normalize_for_search(c) for c in candidates]
            fuzzy_results = rapidfuzz.process.extract(
                norm_current,
                normalized_candidates,
                scorer=rapidfuzz.fuzz.WRatio,
                limit=limit,
                score_cutoff=cutoff,
            )
            exact_set = set(exact)
            # fuzzy_results is list of (matched_string, score, index)
            for _matched_str, _score, idx in fuzzy_results:
                original = candidates[idx]
                if original not in exact_set:
                    exact.append(original)
                    exact_set.add(original)
        except ImportError:
            pass  # rapidfuzz not installed — degrade gracefully to substring only

    return exact[:limit]


def resolve_system_name(typed: str, systems: list[str]) -> str | None:
    """Resolve a user-typed string to a canonical system name.

    Resolution order:
    1. Exact match (case-insensitive)
    2. Normalized exact match via normalize_for_search
    3. Best fuzzy match above score 75

    Args:
        typed: The raw string the user typed.
        systems: List of canonical system name strings to resolve against.

    Returns:
        The canonical system name, or None if no confident match found.
    """
    if not typed or not systems:
        return None

    # Step 1: exact case-insensitive match
    typed_lower = typed.strip().lower()
    for name in systems:
        if name.lower() == typed_lower:
            return name

    # Step 2: normalized exact match (strips accents, apostrophes, hyphens, spaces)
    norm_typed = normalize_for_search(typed)
    for name in systems:
        if normalize_for_search(name) == norm_typed:
            return name

    # Step 3: best fuzzy match above 75
    try:
        import rapidfuzz.fuzz
        import rapidfuzz.process

        normalized_systems = [normalize_for_search(s) for s in systems]
        result = rapidfuzz.process.extractOne(
            norm_typed,
            normalized_systems,
            scorer=rapidfuzz.fuzz.WRatio,
            score_cutoff=75,
        )
        if result is not None:
            _matched_str, _score, idx = result
            return systems[idx]
    except ImportError:
        pass  # rapidfuzz not installed

    return None
