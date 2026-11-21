"""Buzz Codes: a distinct vibration pattern per kind of message, so a student knows what
arrived without listening to anything. Delivered by Web Push; Android Chrome only
(iPhone does not vibrate for web notifications)."""

from __future__ import annotations

BUZZ_TYPES: dict[str, dict] = {
    "notice": {"name": "New notice", "pattern": [600], "describe": "one long buzz"},
    "deadline": {"name": "Deadline reminder", "pattern": [250, 150, 250], "describe": "two medium buzzes"},
    "clash": {"name": "Clash with your timetable", "pattern": [90, 70, 90, 70, 90, 70, 90], "describe": "four quick buzzes"},
    "exam": {"name": "Exam tomorrow", "pattern": [450, 150, 450, 150, 450], "describe": "three long buzzes"},
    "update": {"name": "Notice changed", "pattern": [120, 120, 500], "describe": "short, then long"},
    "personal": {"name": "Your own reminder", "pattern": [200], "describe": "one short buzz"},
}


def valid_pattern(p) -> bool:
    return isinstance(p, list) and 1 <= len(p) <= 12 and all(isinstance(x, int) and 10 <= x <= 1000 for x in p)


def pattern_for(kind: str, prefs: dict | None) -> list[int]:
    custom = (prefs or {}).get(kind)
    if valid_pattern(custom):
        return custom
    return list(BUZZ_TYPES.get(kind, BUZZ_TYPES["notice"])["pattern"])


def catalog(prefs: dict | None) -> list[dict]:
    return [
        {"type": k, "name": v["name"], "describe": v["describe"], "pattern": pattern_for(k, prefs), "default": v["pattern"], "customised": valid_pattern((prefs or {}).get(k))}
        for k, v in BUZZ_TYPES.items()
    ]
