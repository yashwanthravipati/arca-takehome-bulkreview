"""Text normalization and quote location shared by ingestion and citation checks."""

import re
import unicodedata

from rapidfuzz import fuzz

# Curly quotes -> straight; dash/hyphen/minus variants -> "-".
_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
})

FUZZY_CUTOFF = 95


def normalize(s: str) -> str:
    """NFKC, straighten quotes, unify dashes, collapse whitespace. Case is kept."""
    s = unicodedata.normalize("NFKC", s).translate(_PUNCT)
    return re.sub(r"\s+", " ", s).strip()


def locate(quote: str, text: str) -> tuple[int, int, str] | None:
    """Find `quote` in already-normalized `text`.

    Returns (char_start, char_end, method) with method "exact" or "fuzzy",
    or None when there is no exact match and no fuzzy match >= FUZZY_CUTOFF.
    """
    q = normalize(quote)
    if not q:
        return None
    start = text.find(q)
    if start >= 0:
        return start, start + len(q), "exact"
    m = fuzz.partial_ratio_alignment(q, text, score_cutoff=FUZZY_CUTOFF)
    if m is not None:
        return m.dest_start, m.dest_end, "fuzzy"
    return None
