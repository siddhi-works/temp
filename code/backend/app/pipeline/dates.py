"""Date verification: the defence against an AI inventing a deadline.

A date the LLM extracts is marked *verified* only if:
  1. its quoted line really exists in the OCR text,
  2. that line contains a date that parses to the same day (Indian day/month order),
  3. any weekday written next to it matches the calendar,
  4. it is plausible (not long past, not years away).
Otherwise it is *needs_check* with a plain-language reason the reviewer sees.

Works on English, Marathi and Hindi, including Devanagari digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

MONTHS: dict[str, int] = {}
for _num, _names in {
    1: ["january", "jan", "जानेवारी", "जनवरी"],
    2: ["february", "feb", "फेब्रुवारी", "फरवरी"],
    3: ["march", "mar", "मार्च"],
    4: ["april", "apr", "एप्रिल", "अप्रैल", "अप्रेल"],
    5: ["may", "मे", "मई"],
    6: ["june", "jun", "जून"],
    7: ["july", "jul", "जुलै", "जुलाई"],
    8: ["august", "aug", "ऑगस्ट", "अगस्त", "ऑगस्त"],
    9: ["september", "sept", "sep", "सप्टेंबर", "सितंबर", "सितम्बर"],
    10: ["october", "oct", "ऑक्टोबर", "अक्टूबर", "अक्तूबर"],
    11: ["november", "nov", "नोव्हेंबर", "नवंबर", "नवम्बर"],
    12: ["december", "dec", "डिसेंबर", "दिसंबर", "दिसम्बर"],
}.items():
    for _n in _names:
        MONTHS[_n] = _num

WEEKDAYS: dict[str, int] = {}
for _num, _names in {
    0: ["monday", "mon", "सोमवार"],
    1: ["tuesday", "tue", "tues", "मंगळवार", "मंगलवार"],
    2: ["wednesday", "wed", "बुधवार"],
    3: ["thursday", "thu", "thur", "thurs", "गुरुवार", "गुरूवार", "बृहस्पतिवार"],
    4: ["friday", "fri", "शुक्रवार"],
    5: ["saturday", "sat", "शनिवार"],
    6: ["sunday", "sun", "रविवार"],
}.items():
    for _n in _names:
        WEEKDAYS[_n] = _num

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

_month_alt = "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
_weekday_alt = "|".join(sorted(map(re.escape, WEEKDAYS), key=len, reverse=True))
# Word boundary that also works for Devanagari (\b does not, because of combining marks).
_L = r"(?<![\wऀ-ॿ])"
_R = r"(?![\wऀ-ॿ])"

NUMERIC = re.compile(r"(?<!\d)(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{4}|\d{2})(?!\d)")
DAY_MONTH = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*(?:st|nd|rd|th)?\s*[-/ ,.]?\s*{_L}({_month_alt}){_R}\.?\s*[-/ ,.]?\s*(\d{{4}}|\d{{2}}(?!\d))?",
    re.IGNORECASE,
)
MONTH_DAY = re.compile(rf"{_L}({_month_alt}){_R}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})?(?!\d)", re.IGNORECASE)
WEEKDAY_RE = re.compile(rf"{_L}({_weekday_alt}){_R}", re.IGNORECASE)
TIME_RE = re.compile(
    r"(?<![\d./\-])(\d{1,2})(?:\s*[:.]\s*(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|वाजता|वा\.|बजे|hrs|hours)?(?![\d]|[./\-]\d)",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    text = text.translate(DEVANAGARI_DIGITS)
    text = re.sub(r"</?[a-zA-Z][^>]*>?", " ", text)  # OCR returns scanned tables as HTML (<td>, <br/>); compare the words, not the markup
    text = text.replace("‌", "").replace("‍", "")  # zero-width joiners from OCR
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class FoundDate:
    value: date
    raw: str
    start: int
    ambiguous: bool = False  # numeric with day and month both <= 12
    alt_value: date | None = None  # the month/day reading of an ambiguous date
    weekday_written: int | None = None


def _year(y: str | None, ref: date, month: int, day: int) -> int | None:
    if y:
        yi = int(y)
        return yi + 2000 if yi < 100 else yi
    # No year written: pick the occurrence nearest to (and preferably after) the reference date.
    for candidate in (ref.year, ref.year + 1):
        try:
            if date(candidate, month, day) >= ref - timedelta(days=60):
                return candidate
        except ValueError:
            return None
    return ref.year + 1


def _safe_date(y: int | None, m: int, d: int) -> date | None:
    if y is None:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str, ref: date) -> list[FoundDate]:
    norm = normalize(text)
    found: list[FoundDate] = []
    taken: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(a < y and x < b for x, y in taken)

    for m in NUMERIC.finditer(norm):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        yi = _year(y, ref, mo, d) if 1 <= mo <= 12 else None
        val = _safe_date(yi, mo, d) if 1 <= mo <= 12 else None
        alt = _safe_date(_year(y, ref, d, mo), d, mo) if 1 <= d <= 12 else None
        if val is None and alt is not None:
            # Only readable as month/day (e.g. 11/28/2026): keep it, flagged.
            found.append(FoundDate(alt, m.group(0), m.start(), ambiguous=True, alt_value=None))
        elif val is not None:
            ambiguous = d <= 12 and mo <= 12 and d != mo
            found.append(FoundDate(val, m.group(0), m.start(), ambiguous=ambiguous, alt_value=alt if ambiguous else None))
        else:
            continue
        taken.append((m.start(), m.end()))

    for m in DAY_MONTH.finditer(norm):
        if overlaps(m.start(), m.end()):
            continue
        d, mo = int(m.group(1)), MONTHS[m.group(2).lower()]
        val = _safe_date(_year(m.group(3), ref, mo, d), mo, d)
        if val:
            found.append(FoundDate(val, m.group(0).strip(), m.start()))
            taken.append((m.start(), m.end()))

    for m in MONTH_DAY.finditer(norm):
        if overlaps(m.start(), m.end()):
            continue
        mo, d = MONTHS[m.group(1).lower()], int(m.group(2))
        val = _safe_date(_year(m.group(3), ref, mo, d), mo, d)
        if val:
            found.append(FoundDate(val, m.group(0).strip(), m.start()))
            taken.append((m.start(), m.end()))

    # Attach a weekday written within a few words of the date, e.g. "Friday, 28/11/2026" or "28/11/2026 (शुक्रवार)".
    for fd in found:
        window = norm[max(0, fd.start - 18) : fd.start + len(fd.raw) + 18]
        w = WEEKDAY_RE.search(window)
        if w:
            fd.weekday_written = WEEKDAYS[w.group(1).lower()]
    found.sort(key=lambda f: f.start)
    return found


def find_times(text: str) -> set[str]:
    """Times as HH:MM. Requires a separator or an am/pm marker so bare numbers are ignored."""
    out: set[str] = set()
    for m in TIME_RE.finditer(normalize(text)):
        h, mi, suffix = int(m.group(1)), m.group(2), (m.group(3) or "").replace(".", "").replace(" ", "")
        if mi is None and not suffix:
            continue
        minute = int(mi) if mi else 0
        if minute > 59 or h > 24:
            continue
        if suffix.startswith("p") and h < 12:
            h += 12
        if suffix.startswith("a") and h == 12:
            h = 0
        out.add(f"{h:02d}:{minute:02d}")
        # "2.30 वाजता" in a college notice almost always means afternoon if hour < 8.
        if not suffix.startswith(("a", "p")) and 1 <= h <= 7:
            out.add(f"{h + 12:02d}:{minute:02d}")
    return out


def quote_in_text(quote: str, text: str) -> float:
    """1.0 if the quote is verbatim in the text, else the share of quote tokens found in order-insensitive form."""
    q, t = normalize(quote), normalize(text)
    if not q:
        return 0.0
    if q in t:
        return 1.0
    split = r"[\s,;:()\[\]|]+"
    tokens = [tok.strip(".") for tok in re.split(split, q) if tok.strip(".")]
    if not tokens:
        return 0.0
    t_tokens = {tok.strip(".") for tok in re.split(split, t)}
    # Any token with a digit (dates, times, amounts, rooms) must be present exactly:
    # a paraphrased sentence with an invented date must not pass on word overlap alone.
    if any(any(ch.isdigit() for ch in tok) and tok not in t_tokens for tok in tokens):
        return 0.0
    return sum(1 for tok in tokens if tok in t_tokens) / len(tokens)


def speak_date(d: date) -> str:
    return f"{WEEKDAY_NAMES[d.weekday()]}, {d.day} {MONTH_NAMES[d.month - 1]} {d.year}"


@dataclass
class VerifyResult:
    status: str  # verified | needs_check
    reasons: list[str] = field(default_factory=list)


def verify_action(
    *,
    due_date: str | None,
    due_time: str | None,
    amount: str | None,
    source_quote: str | None,
    ocr_text: str,
    ref: date,
) -> VerifyResult:
    reasons: list[str] = []
    ok = True

    if not due_date:
        if amount and not _amount_in(amount, ocr_text):
            return VerifyResult("needs_check", [f"Amount '{amount}' was not found in the notice."])
        return VerifyResult("verified", [])

    try:
        target = date.fromisoformat(due_date)
    except ValueError:
        return VerifyResult("needs_check", [f"The date '{due_date}' is not a valid date."])

    quote_score = quote_in_text(source_quote or "", ocr_text)
    if quote_score < 0.85:
        ok = False
        reasons.append("The line this date came from was not found in the scanned notice.")

    in_quote = [f for f in find_dates(source_quote or "", ref) if f.value == target or f.alt_value == target]
    in_text = [f for f in find_dates(ocr_text, ref) if f.value == target or f.alt_value == target]

    # The date must exist in the scan itself; a date that only exists in the LLM's "quote" is invented.
    match = next((f for f in in_text if any(f.raw == q.raw for q in in_quote)), None) or (in_text[0] if in_text else None)
    if match is None:
        ok = False
        reasons.append(f"{speak_date(target)} does not appear anywhere in the notice.")
    else:
        if not in_quote:
            reasons.append("Date found in the notice, but not in the quoted line.")
            ok = False
        if match.ambiguous and match.alt_value == target:
            ok = False
            reasons.append(f"'{match.raw}' was read as month/day; Indian notices use day/month.")
        elif match.ambiguous:
            reasons.append(f"'{match.raw}' read as day/month (Indian format).")
        if match.weekday_written is not None and match.weekday_written != target.weekday():
            ok = False
            reasons.append(
                f"The notice says {WEEKDAY_NAMES[match.weekday_written]}, but {target.day} "
                f"{MONTH_NAMES[target.month - 1]} {target.year} is a {WEEKDAY_NAMES[target.weekday()]}. "
                "Check with the office."
            )

    if target < ref - timedelta(days=7):
        ok = False
        reasons.append("This date is already in the past.")
    elif target > ref + timedelta(days=400):
        ok = False
        reasons.append("This date is more than a year away.")

    if due_time:
        times = find_times(source_quote or "") | find_times(ocr_text)
        if due_time not in times:
            ok = False
            reasons.append(f"The time {due_time} was not found in the notice.")

    if amount and not _amount_in(amount, ocr_text):
        ok = False
        reasons.append(f"Amount '{amount}' was not found in the notice.")

    return VerifyResult("verified" if ok else "needs_check", reasons)


def _amount_in(amount: str, text: str) -> bool:
    digits = re.sub(r"[^\d]", "", amount.translate(DEVANAGARI_DIGITS))
    if not digits:
        return True
    text_digits = re.sub(r"[,\s]", "", normalize(text))
    return digits in text_digits


def verify_timetable_times(rows: list[dict], ocr_text: str) -> list[str]:
    times = find_times(ocr_text)
    missing = sorted({t for r in rows for t in (r.get("start"), r.get("end")) if t and t not in times})
    if missing and times:
        return [f"Timetable times not found in the notice: {', '.join(missing[:6])}."]
    return []


# --- Fallback extraction when the LLM is unavailable --------------------------------


def rule_based_actions(ocr_text: str, ref: date) -> list[dict]:
    """Every date in the notice becomes a 'check this' item with its sentence as the quote."""
    actions = []
    seen: set[date] = set()
    sentences = re.split(r"(?<=[.!?।])\s+|\n+", ocr_text)
    for sentence in sentences:
        for fd in find_dates(sentence, ref):
            if fd.value in seen or fd.value < ref - timedelta(days=7):
                continue
            seen.add(fd.value)
            actions.append(
                {
                    "kind": "other",
                    "label": "Date mentioned in the notice (check what it is for)",
                    "due_date": fd.value.isoformat(),
                    "due_time": None,
                    "amount": None,
                    "venue": None,
                    "source_quote": sentence.strip()[:400],
                }
            )
    return actions[:10]


def first_title_line(ocr_text: str) -> str:
    for line in ocr_text.splitlines():
        clean = line.strip(" #*|-\t")
        if 6 <= len(clean) <= 140 and not NUMERIC.search(clean):
            return clean
    return "College notice"


# --- Version diff -----------------------------------------------------------------------


def diff_actions(old: list[dict], new: list[dict]) -> list[str]:
    """Human-readable changes between two versions of a notice, for 'what changed' alerts."""
    changes: list[str] = []
    unmatched_old = list(old)
    for n in new:
        best, score = None, 0.0
        for o in unmatched_old:
            s = SequenceMatcher(None, (o.get("label") or "").lower(), (n.get("label") or "").lower()).ratio()
            if o.get("kind") == n.get("kind"):
                s += 0.2
            if s > score:
                best, score = o, s
        if best is None or score < 0.6:
            when = f" on {speak_date(date.fromisoformat(n['due_date']))}" if n.get("due_date") else ""
            changes.append(f"New: {n.get('label')}{when}.")
            continue
        unmatched_old.remove(best)
        if best.get("due_date") != n.get("due_date") and n.get("due_date"):
            old_s = speak_date(date.fromisoformat(best["due_date"])) if best.get("due_date") else "no date"
            changes.append(f"{n.get('label')}: date changed from {old_s} to {speak_date(date.fromisoformat(n['due_date']))}.")
        if (best.get("due_time") or None) != (n.get("due_time") or None) and n.get("due_time"):
            changes.append(f"{n.get('label')}: time changed to {n['due_time']}.")
        if (best.get("venue") or None) != (n.get("venue") or None) and n.get("venue"):
            changes.append(f"{n.get('label')}: venue changed to {n['venue']}.")
        if (best.get("amount") or None) != (n.get("amount") or None) and n.get("amount"):
            changes.append(f"{n.get('label')}: amount changed to {n['amount']}.")
    for o in unmatched_old:
        changes.append(f"Removed: {o.get('label')}.")
    return changes


# --- Hindi and Marathi wording for spoken dates and times (used by the student pages) ----------------------------

_WEEKDAYS_L = {
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "mr": ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
}
_MONTHS_L = {
    "hi": ["जनवरी", "फरवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर"],
}
_MR_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def speak_date_lang(d: date, lang: str) -> str:
    """'Monday, 30 November 2026' in the student's language (Marathi with Devanagari digits)."""
    if lang not in _MONTHS_L:
        return speak_date(d)
    day, year = str(d.day), str(d.year)
    if lang == "mr":
        day, year = day.translate(_MR_DIGITS), year.translate(_MR_DIGITS)
    return f"{_WEEKDAYS_L[lang][d.weekday()]}, {day} {_MONTHS_L[lang][d.month - 1]} {year}"


def speak_time_lang(hm: str, lang: str) -> str:
    """'14:30' as '2:30 PM', '14:30 बजे' or '१४:३० वाजता'."""
    if lang not in ("hi", "mr"):
        h, m = int(hm[:2]), hm[3:5]
        return f"{h % 12 or 12}{':' + m if m != '00' else ''} {'AM' if h < 12 else 'PM'}"
    text = f"{int(hm[:2])}:{hm[3:5]}"
    return (text.translate(_MR_DIGITS) + " वाजता") if lang == "mr" else (text + " बजे")
