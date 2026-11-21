"""Pure logic for the student-facing features (no database, no network): easy to test.

- Right Now: where am I and what is next.
- Clash Radar: does a new event overlap my own timetable.
- Verbal Deadline Catcher: turn "assignment is due next Friday" into a real date, in code.
- Exam Eve Pack: the night-before briefing text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .dates import (
    MONTH_NAMES,
    WEEKDAY_NAMES,
    WEEKDAYS,
    find_dates,
    find_times,
    normalize,
    speak_date,
)

LANGS = ("en", "mr", "hi")


def hm_to_min(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def min_to_hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def speak_time(hm: str) -> str:
    """'14:05' -> '2:05 PM'. Spoken and typed forms agree, so screen readers and voice match."""
    h, m = divmod(hm_to_min(hm), 60)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


@dataclass(frozen=True)
class Slot:
    weekday: int
    start: str
    end: str
    subject: str
    room: str | None = None


# --------------------------------------------------------------------------- Right Now


def slots_on(slots: list[Slot], weekday: int) -> list[Slot]:
    return sorted((s for s in slots if s.weekday == weekday), key=lambda s: hm_to_min(s.start))


def right_now(slots: list[Slot], now: datetime) -> dict:
    """Describe the student's current situation. `now` must be in local (IST) time."""
    minute = now.hour * 60 + now.minute
    today = slots_on(slots, now.weekday())
    current = next((s for s in today if hm_to_min(s.start) <= minute < hm_to_min(s.end)), None)
    upcoming = [s for s in today if hm_to_min(s.start) > minute]
    nxt = upcoming[0] if upcoming else None
    next_day = None
    if nxt is None:
        for offset in range(1, 8):
            day = slots_on(slots, (now.weekday() + offset) % 7)
            if day:
                next_day = (offset, day[0])
                break
    return {"current": current, "next": nxt, "next_day": next_day, "minute": minute}


def _place(room: str | None) -> str:
    return f" in {room}" if room else ""


def right_now_text(slots: list[Slot], now: datetime, deadlines_today: list[str] | None = None) -> str:
    if not slots:
        return "I don't have your timetable yet. Ask your college office, or send BATCH followed by your batch name."
    r = right_now(slots, now)
    parts: list[str] = []
    cur, nxt = r["current"], r["next"]
    if cur:
        left = hm_to_min(cur.end) - r["minute"]
        parts.append(f"Right now: {cur.subject}{_place(cur.room)}, until {speak_time(cur.end)}, {left} minutes left.")
    else:
        parts.append("You are free right now.")
    if nxt:
        gap = hm_to_min(nxt.start) - r["minute"]
        parts.append(f"Next: {nxt.subject} at {speak_time(nxt.start)}{_place(nxt.room)}, in {gap} minutes.")
    elif r["next_day"]:
        offset, s = r["next_day"]
        when = "tomorrow" if offset == 1 else WEEKDAY_NAMES[s.weekday]
        parts.append(f"No more classes today. Next: {s.subject} {when} at {speak_time(s.start)}{_place(s.room)}.")
    else:
        parts.append("No more classes in your timetable.")
    if deadlines_today:
        parts.append("Due today: " + "; ".join(deadlines_today) + ".")
    return " ".join(parts)


# --------------------------------------------------------------------------- Clash Radar

DEFAULT_MINUTES = {"exam": 180, "event": 60, "other": 60, "venue_change": 60}


def find_clashes(slots: list[Slot], on: date, start: str, minutes: int) -> list[Slot]:
    a0 = hm_to_min(start)
    a1 = a0 + minutes
    return [s for s in slots_on(slots, on.weekday()) if a0 < hm_to_min(s.end) and hm_to_min(s.start) < a1]


def _norm(text: str | None) -> str:
    return re.sub(r"[^\wऀ-ॿ]+", " ", (text or "").lower()).strip()


def subject_matches(a: str | None, b: str | None) -> bool:
    na, nb = _norm(a), _norm(b)
    return bool(na and nb and (na in nb or nb in na))


def clash_text(label: str, on: date, start: str, clashes: list[Slot], lang: str = "en") -> str:
    c = clashes[0]
    place = f", {c.room}" if c.room else ""
    when = f"{speak_date(on)} at {speak_time(start)}"
    if lang == "mr":
        return f"सावध राहा: {label} ({when}) तुमच्या {c.subject} तासाशी ({speak_time(c.start)} ते {speak_time(c.end)}{place}) जुळतो."
    if lang == "hi":
        return f"ध्यान दें: {label} ({when}) आपकी {c.subject} क्लास ({speak_time(c.start)} से {speak_time(c.end)}{place}) से टकराता है."
    extra = f" and {len(clashes) - 1} more class" + ("es" if len(clashes) > 2 else "") if len(clashes) > 1 else ""
    return f"Clash: {label}, {when}, overlaps your {c.subject} class ({speak_time(c.start)} to {speak_time(c.end)}{place}){extra}."


def changed_class_text(slot: Slot, on: date, row_start: str | None, row_room: str | None, row_subject: str) -> str:
    bits = []
    if row_start and row_start != slot.start:
        bits.append(f"now at {speak_time(row_start)}")
    if row_room and row_room != slot.room:
        bits.append(f"now in {row_room}")
    detail = " and ".join(bits) if bits else "has an update"
    return f"Your {row_subject} class on {speak_date(on)} {detail}."


# --------------------------------------------------------------------------- Verbal Deadline Catcher

TODAY_WORDS = ("today", "tonight", "आज")
TOMORROW_WORDS = ("tomorrow", "उद्या", "कल")
DAY_AFTER_WORDS = ("day after tomorrow", "परवा", "परसों")
NEXT_WORDS = ("next", "पुढच्या", "अगले", "अगली", "पुढील")

_weekday_alt = "|".join(sorted(map(re.escape, WEEKDAYS), key=len, reverse=True))
_WEEKDAY_RE = re.compile(rf"(?<![\wऀ-ॿ])({_weekday_alt})[ीे]?(?![\wऀ-ॿ])", re.IGNORECASE)  # Marathi 'शुक्रवारी' = on Friday
_IN_DAYS_RE = re.compile(r"(?:in|after|within)\s+(\d{1,2})\s+days?", re.IGNORECASE)
_DEV_IN_DAYS_RE = re.compile(r"(\d{1,2})\s*(?:दिवसांनी|दिन में|दिनों में|दिवसात)")


@dataclass
class Resolved:
    due: date | None
    time: str | None
    how: str  # "explicit date", "tomorrow", "next friday", ... (read back to the student)
    ambiguous: bool = False


def _contains(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def resolve_phrase(transcript: str, today: date) -> Resolved:
    """Turn what the teacher said into a date, in code, so a language model can never invent it."""
    text = normalize(transcript)
    times = sorted(find_times(transcript))
    time_ = times[0] if times else None

    found = [f for f in find_dates(transcript, today) if f.value >= today - timedelta(days=1)]
    if found:
        f = found[0]
        return Resolved(f.value, time_, "the date you said", ambiguous=f.ambiguous)
    if _contains(text, DAY_AFTER_WORDS):
        return Resolved(today + timedelta(days=2), time_, "day after tomorrow")
    if _contains(text, TOMORROW_WORDS):
        return Resolved(today + timedelta(days=1), time_, "tomorrow")
    if _contains(text, TODAY_WORDS):
        return Resolved(today, time_, "today")
    m = _IN_DAYS_RE.search(text) or _DEV_IN_DAYS_RE.search(text)
    if m:
        return Resolved(today + timedelta(days=int(m.group(1))), time_, f"in {m.group(1)} days")
    w = _WEEKDAY_RE.search(text)
    if w:
        target = WEEKDAYS[w.group(1).lower()]
        ahead = (target - today.weekday()) % 7 or 7  # strictly after today
        nxt = _contains(text[max(0, w.start() - 16) : w.start()], NEXT_WORDS)
        ambiguous = False
        if nxt:
            week_end = 6 - today.weekday()  # days left in this calendar week
            if ahead <= week_end:
                ahead += 7  # "next Friday" said on Monday means the Friday of next week
            ambiguous = True  # people mean both; the read-back settles it
        return Resolved(today + timedelta(days=ahead), time_, f"{'next ' if nxt else ''}{WEEKDAY_NAMES[target]}", ambiguous=ambiguous)
    return Resolved(None, time_, "no date found")


_READBACK = {
    "hi": {
        "nodate": "मैंने सुना: \"{label}\"। मैं तारीख नहीं समझ सका। तारीख बोलें या लिखें, जैसे \"अगले शुक्रवार\" या \"28 नवंबर\"।",
        "ok": "{label}। अंतिम तारीख {when}।{caution}",
        "caution": " आपने 'अगला' कहा था, इसलिए कृपया यह तारीख जाँच लें।",
    },
    "mr": {
        "nodate": "मी ऐकले: \"{label}\". मला तारीख समजली नाही. तारीख सांगा किंवा लिहा, उदा. \"पुढचा शुक्रवार\" किंवा \"२८ नोव्हेंबर\".",
        "ok": "{label}. अंतिम तारीख {when}.{caution}",
        "caution": " तुम्ही 'पुढचा' म्हटले होते, म्हणून कृपया ही तारीख तपासा.",
    },
}


def catch_readback(label: str, r: Resolved, lang: str = "en") -> str:
    """The spoken read-back. Hindi and Marathi leave out the 'say YES' tail: the web page has Save and Discard buttons."""
    if lang in _READBACK:
        from .dates import speak_date_lang, speak_time_lang

        tpl = _READBACK[lang]
        if r.due is None:
            return tpl["nodate"].format(label=label)
        when = speak_date_lang(r.due, lang) + (f", {speak_time_lang(r.time, lang)}" if r.time else "")
        return tpl["ok"].format(label=label, when=when, caution=tpl["caution"] if r.ambiguous else "")
    if r.due is None:
        return f'I heard: "{label}". I could not tell the date. Say or type the date, for example "next Friday" or "28 November".'
    when = speak_date(r.due) + (f" at {speak_time(r.time)}" if r.time else "")
    caution = " You said next, so please check this date." if r.ambiguous else ""
    return f"{label}. Due {when}.{caution} Say YES to save this reminder, or say the date again."


def fallback_label(transcript: str) -> str:
    """Without an AI: keep the sentence, minus filler, capped so a reminder stays short."""
    t = re.sub(r"\s+", " ", transcript).strip(" .,!?")
    t = re.sub(r"^(sir|madam|ma'am|professor|teacher)\s+(said|told|says)\s+(that\s+)?", "", t, flags=re.IGNORECASE)
    return (t[:117] + "...") if len(t) > 120 else (t or "Deadline")


YES_WORDS = {"yes", "y", "ok", "okay", "save", "confirm", "हो", "होय", "हां", "हाँ", "ठीक"}
NO_WORDS = {"no", "n", "cancel", "नाही", "नहीं", "रद्द"}


def is_yes(text: str) -> bool:
    return _norm(text) in YES_WORDS


def is_no(text: str) -> bool:
    return _norm(text) in NO_WORDS


# --------------------------------------------------------------------------- Exam Eve Pack


def exam_eve_text(label: str, on: date, time_: str | None, venue: str | None, checklist: list[str], route_link: str | None, lang: str = "en") -> str:
    items = ", ".join(checklist)
    when = speak_date(on) + (f" at {speak_time(time_)}" if time_ else "")
    place = f" Place: {venue}." if venue else ""
    route = f" To find the room, play the walking route: {route_link}." if route_link else ""
    if lang == "mr":
        return f"उद्या परीक्षा: {label}, {when}.{' ठिकाण: ' + venue + '.' if venue else ''} सोबत घ्या: {items}. आजच तयारी करा.{' खोलीपर्यंत मार्ग: ' + route_link + '.' if route_link else ''}"
    if lang == "hi":
        return f"कल परीक्षा: {label}, {when}.{' स्थान: ' + venue + '.' if venue else ''} साथ रखें: {items}. आज ही तैयारी कर लें.{' कमरे तक का रास्ता: ' + route_link + '.' if route_link else ''}"
    return f"Tomorrow: {label}, {when}.{place} Take with you: {items}. Pack your bag tonight.{route}"


def place_matches(venue: str | None, to_place: str) -> bool:
    nv, nt = _norm(venue), _norm(to_place)
    return bool(nv and nt and (nv in nt or nt in nv))


_ = (MONTH_NAMES,)
