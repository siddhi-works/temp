"""Match a forwarded photo's text to published notices ("is this the real, latest one?").

Character trigram similarity is robust to OCR noise, works for Devanagari, and
needs no extra service. For one college's few hundred notices this is fast in
Python; at district scale, move it to Postgres pg_trgm with the same threshold.
"""

from __future__ import annotations

import re

from .dates import normalize


def trigrams(text: str) -> set[str]:
    t = re.sub(r"[^\wऀ-ॿ]+", "", normalize(text))
    return {t[i : i + 3] for i in range(max(0, len(t) - 2))}


def similarity(a: str, b: str) -> float:
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    # Containment-weighted Jaccard: a cropped photo of part of a notice should still match.
    inter = len(ta & tb)
    jaccard = inter / len(ta | tb)
    containment = inter / min(len(ta), len(tb))
    return round(0.4 * jaccard + 0.6 * containment, 3)


MATCH_THRESHOLD = 0.55
