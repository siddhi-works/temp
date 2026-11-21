"""Personal insight: a student's own details pick out what applies to *them* in a long notice.

Privacy by design: the details live only on the student's phone and arrive with each request. Nothing in this
module reads or writes the database, files or logs. Everything the AI claims about the student is re-checked
against the scanned text before it is spoken (see `verify_personal`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from .dates import DEVANAGARI_DIGITS, normalize, quote_in_text

_CONTROL = re.compile(r"[\x00-\x1f\x7f<>{}\[\]`]")


def _clean(value: object, max_len: int) -> str:
    """Details are data, never instructions: no markup, control characters or line breaks, and a short length."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", _CONTROL.sub(" ", value)).strip()[:max_len]


class Profile(BaseModel):
    name: str = ""
    course: str = ""  # e.g. "SY B.Com"
    division: str = ""
    roll_no: str = ""  # roll / seat / PRN / enrolment number
    batch: str = ""
    extra: str = ""  # anything else the student wants considered, e.g. "OBC scholarship"

    def is_empty(self) -> bool:
        return not any(v for v in self.model_dump().values())


def parse_profile(raw: str | None) -> Profile | None:
    """Never raises: a missing, oversized or malformed profile simply means 'no personalisation'."""
    if not raw or len(raw) > 2000:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        limits = {"name": 80, "course": 60, "division": 20, "roll_no": 30, "batch": 30, "extra": 120}
        p = Profile(**{k: _clean(data.get(k), n) for k, n in limits.items()})
    except (ValueError, ValidationError, TypeError):
        return None
    return None if p.is_empty() else p


def describe(p: Profile) -> str:
    """One line for the AI prompt, clearly labelled as data."""
    parts = [
        ("name", p.name),
        ("class", p.course),
        ("division", p.division),
        ("roll/seat/PRN number", p.roll_no),
        ("batch", p.batch),
        ("other", p.extra),
    ]
    return "; ".join(f"{k}={v}" for k, v in parts if v)


def _squash(text: str) -> str:
    """Compare ignoring case, spacing and punctuation: 'S.Y. B.Com' == 'sy bcom'."""
    return re.sub(r"[^\w]+", "", normalize(text), flags=re.UNICODE)


def identifiers(p: Profile) -> list[str]:
    """Strings that identify this student inside a document, strongest first."""
    out = []
    for v in (p.roll_no, p.name):
        if len(v.strip()) >= 3:
            out.append(v.strip())
    return out


def focus_text(text: str, p: Profile | None, limit: int = 18000) -> str:
    """For long documents: the first page plus the passages around the student's roll number or name."""
    if len(text) <= limit or p is None:
        return text[:limit]
    hay = text.translate(DEVANAGARI_DIGITS).lower()
    spans: list[tuple[int, int]] = [(0, min(2500, len(text)))]
    for ident in identifiers(p):
        needle = ident.translate(DEVANAGARI_DIGITS).lower()
        start, hits = 0, 0
        while hits < 12 and (i := hay.find(needle, start)) != -1:
            spans.append((max(0, i - 500), min(len(text), i + 500)))
            start, hits = i + len(needle), hits + 1
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    parts, used = [], 0
    for a, b in merged:
        chunk = text[a:b]
        if used + len(chunk) > limit:
            chunk = chunk[: max(0, limit - used)]
        if chunk:
            parts.append(chunk)
            used += len(chunk)
        if used >= limit:
            break
    return "\n[...]\n".join(parts)


def _digit_tokens(text: str) -> set[str]:
    """Numbers, rooms and times in a line, compared loosely: '10:30', '10.30' and '10.30.' are the same time."""
    toks = (t.replace(":", ".").strip(".-/") for t in re.split(r"[^\w:.\-/]+", normalize(text)))
    return {t for t in toks if any(c.isdigit() for c in t)}


@dataclass
class VerifiedMatch:
    what: str
    detail: str
    quote: str
    basis: str  # roll number | name | class


def verify_personal(matches: list, ocr_text: str, p: Profile) -> list[VerifiedMatch]:
    """Keep only claims that are really in the scan AND really about this student.

    1. the quoted line must be in the scanned text;
    2. it must contain the student's roll number or name, or name their class/division;
    3. every number in the detail (room, bench, time) must appear in that line.
    """
    squash_course = _squash(p.course)
    out: list[VerifiedMatch] = []
    for m in matches:
        quote, detail, what = (getattr(m, "source_quote", "") or ""), (getattr(m, "detail", "") or ""), (getattr(m, "what", "") or "")
        if not quote or quote_in_text(quote, ocr_text) < 0.85:
            continue
        nq, sq = normalize(quote), _squash(quote)
        if p.roll_no and len(p.roll_no) >= 3 and _squash(p.roll_no) in sq:
            basis = "roll number"
        elif p.name and len(p.name) >= 3 and _squash(p.name) in sq:
            basis = "name"
        elif squash_course and len(squash_course) >= 3 and squash_course in sq:
            basis = "class"
        else:
            continue
        if not _digit_tokens(detail) <= _digit_tokens(quote) | _digit_tokens(nq):
            continue  # the AI put a number in the detail that is not in the line it quoted
        out.append(VerifiedMatch(what=what[:80], detail=detail[:200], quote=quote.strip()[:300], basis=basis))
    return out[:6]


def rule_based_personal(ocr_text: str, p: Profile) -> list[VerifiedMatch]:
    """No AI needed: lines that contain the student's roll number or name, quoted as they are."""
    out: list[VerifiedMatch] = []
    for ident, basis in ((p.roll_no, "roll number"), (p.name, "name")):
        if len(ident.strip()) < 3:
            continue
        key = _squash(ident)
        for line in re.split(r"\n+", re.sub(r"</t[dh]\s*>", " | ", re.sub(r"</tr\s*>", "\n", ocr_text, flags=re.I), flags=re.I)):
            clean = re.sub(r"<[^>]*>?", " ", line)
            clean = re.sub(r"\s+", " ", clean).strip(" |")
            if key and key in _squash(clean) and len(out) < 4:
                out.append(VerifiedMatch(what="A line that mentions you", detail=clean[:200], quote=clean[:300], basis=basis))
    return out
