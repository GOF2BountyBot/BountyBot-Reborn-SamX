"""
Autocomplete utility functions for Discord Gateway cogs.

Provides accent-insensitive search normalization for autocomplete matching,
so users can type unaccented characters and still match accented game names.
"""

import unicodedata


def normalize_for_search(text: str) -> str:
    """Strip accents/diacritics, apostrophes, hyphens and lowercase for search.

    Decomposes unicode characters using NFKD normalization, removes all
    combining marks (diacritics like accents, umlauts, cedillas), removes
    apostrophes and hyphens that users commonly skip when typing, then
    lowercases the result.  This allows matching "Behen" against "Behén",
    "sk" against "S'Kolptorr", "kaamostation" against "Kaamo-Station", etc.

    The original display name with accents/punctuation is preserved in
    ``app_commands.Choice(name=..., value=...)`` — only the comparison
    strings are stripped.

    Args:
        text: The input string to normalize.

    Returns:
        A lowercase string with all combining diacritical marks, apostrophes,
        and hyphens removed.

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
    """
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Remove apostrophes and hyphens that users might skip when typing
    stripped = stripped.replace("'", "").replace("\u2019", "").replace("\u02bc", "").replace("-", "")
    return stripped.lower()
