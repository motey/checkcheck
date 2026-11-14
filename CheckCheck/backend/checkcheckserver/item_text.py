"""Text rules for the card-level bulk item operations (issue #20).

Both functions have an exact twin in the frontend (``utils/itemBulk.ts``): the
client applies the same rules optimistically (offline included), so on a fully
loaded card the local result must equal what the server computes on replay.
Change one side only together with the other.
"""

import re
import unicodedata
from typing import Tuple

_DIGIT_RUN = re.compile(r"[0-9]+|[^0-9]+")


def normalize_item_text(text: str | None) -> str:
    """Duplicate-matching key, same rule as the client's item suggestions
    (``normalizeItemText``): trim, collapse whitespace runs, lowercase."""
    return " ".join((text or "").split()).lower()


def _fold_for_sort(text: str | None) -> str:
    # NFKD + drop combining marks, so "Äpfel" sorts next to "apfel" instead of
    # after "z" (plain code point order).
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(
        ch for ch in decomposed if not unicodedata.category(ch).startswith("M")
    )
    return " ".join(stripped.split()).lower()


def item_sort_key(text: str | None) -> Tuple:
    """Natural, case and accent insensitive sort key: ASCII digit runs compare
    as numbers ("Item 2" before "Item 10"), digit runs before text runs at the
    same spot. Empty items sort last. Callers add their own tiebreakers."""
    folded = _fold_for_sort(text)
    chunks = []
    for run in _DIGIT_RUN.findall(folded):
        if "0" <= run[0] <= "9":
            digits = run.lstrip("0") or "0"
            chunks.append((0, len(digits), digits))
        else:
            chunks.append((1, 0, run))
    return (0 if folded else 1, tuple(chunks))
