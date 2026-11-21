from datetime import date

import pytest

from app.pipeline.dates import (
    diff_actions,
    find_dates,
    find_times,
    rule_based_actions,
    verify_action,
)
from app.pipeline.matching import MATCH_THRESHOLD, similarity

from .conftest import NOTICE_TEXT, NOTICE_V2_TEXT

REF = date(2026, 11, 21)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Last date: 28/11/2026", date(2026, 11, 28)),
        ("अंतिम तारीख २८ नोव्हेंबर २०२६", date(2026, 11, 28)),
        ("अंतिम तिथि 28 नवंबर 2026", date(2026, 11, 28)),
        ("on Nov 28, 2026", date(2026, 11, 28)),
        ("28th November", date(2026, 11, 28)),  # year inferred from reference date
        ("दि. ०२.१२.२०२६ रोजी", date(2026, 12, 2)),
        ("exam on 5-1-27", date(2027, 1, 5)),
        ("by 10 January", date(2027, 1, 10)),  # rolls into next year
    ],
)
def test_finds_dates_in_english_marathi_hindi(text, expected):
    assert expected in [f.value for f in find_dates(text, REF)]


def test_times_ignore_dates_and_bare_numbers():
    assert find_times("दि. ०२.१२.२०२६ रोजी सकाळी १०.३० वाजता") == {"10:30"}
    assert find_times("Room 14, 3.45 p.m.") == {"15:45"}
    assert find_times("Room 14 on floor 2") == set()


def test_verified_when_quote_and_date_match():
    r = verify_action(
        due_date="2026-12-02", due_time="10:30", amount=None,
        source_quote="Hall tickets will be distributed on 02.12.2026 at 10.30 am in Room 14.", ocr_text=NOTICE_TEXT, ref=REF,
    )
    assert r.status == "verified", r.reasons


def test_catches_weekday_that_contradicts_calendar():
    # 27 November 2026 is a Friday, but the notice says Thursday. A real class of notice errors.
    r = verify_action(
        due_date="2026-11-27", due_time=None, amount="Rs. 500/-",
        source_quote="Last date for exam form submission: 27/11/2026 (Thursday).", ocr_text=NOTICE_TEXT, ref=REF,
    )
    assert r.status == "needs_check"
    assert any("Thursday" in x and "Friday" in x for x in r.reasons)


def test_catches_invented_date():
    r = verify_action(due_date="2026-11-30", due_time=None, amount=None, source_quote="Last date for exam form submission: 30/11/2026.", ocr_text=NOTICE_TEXT, ref=REF)
    assert r.status == "needs_check"
    assert any("not found" in x or "does not appear" in x for x in r.reasons)


def test_catches_month_day_swap():
    text = "Fee due 05/12/2026."
    r = verify_action(due_date="2026-05-12", due_time=None, amount=None, source_quote=text, ocr_text=text, ref=date(2026, 4, 1))
    assert r.status == "needs_check"
    assert any("month/day" in x for x in r.reasons)


def test_catches_wrong_time_and_amount():
    r = verify_action(
        due_date="2026-12-02", due_time="11:30", amount="Rs. 700",
        source_quote="Hall tickets will be distributed on 02.12.2026 at 10.30 am in Room 14.", ocr_text=NOTICE_TEXT, ref=REF,
    )
    assert r.status == "needs_check"
    assert any("11:30" in x for x in r.reasons) and any("700" in x for x in r.reasons)


def test_past_date_flagged():
    r = verify_action(due_date="2026-10-01", due_time=None, amount=None, source_quote="on 01/10/2026", ocr_text="on 01/10/2026", ref=REF)
    assert r.status == "needs_check" and any("past" in x for x in r.reasons)


def test_rule_based_fallback_finds_all_future_dates():
    acts = rule_based_actions(NOTICE_TEXT, REF)
    dates = {a["due_date"] for a in acts}
    assert {"2026-11-27", "2026-12-02"} <= dates


def test_diff_between_versions_speaks_the_change():
    old = [{"kind": "deadline", "label": "Submit exam form and pay fee", "due_date": "2026-11-27"}]
    new = [{"kind": "deadline", "label": "Submit exam form and pay fee", "due_date": "2026-11-29"}]
    changes = diff_actions(old, new)
    assert changes == ["Submit exam form and pay fee: date changed from Friday, 27 November 2026 to Sunday, 29 November 2026."]


def test_matching_finds_same_notice_and_rejects_others():
    assert similarity(NOTICE_TEXT, NOTICE_TEXT) == 1.0
    # A cropped photo (only the middle part) still matches.
    crop = "\n".join(NOTICE_TEXT.splitlines()[5:9])
    assert similarity(crop, NOTICE_TEXT) >= MATCH_THRESHOLD
    other = "SCHOLARSHIP NOTICE. Students applying for the state scholarship must submit income certificates by 15/01/2027 at the accounts office."
    assert similarity(other, NOTICE_TEXT) < MATCH_THRESHOLD
    # Two versions are similar to each other, so the series lookup decides which is latest.
    assert similarity(NOTICE_V2_TEXT, NOTICE_TEXT) >= MATCH_THRESHOLD
