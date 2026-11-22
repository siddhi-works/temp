"""The safety check and the proof line, through the real engine with the AI and speech faked."""

from __future__ import annotations

import pytest

from app.pipeline.safety import check, speak_address, spoken_warning


def kinds(text: str, lang: str = "en") -> list[tuple[str, str]]:
    return [(f["kind"], f["level"]) for f in check(text, lang)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Pay the exam fee Rs. 500 to the UPI ID asha.college@ybl.", [("upi", "caution")]),
        ("Please pay Rs. 200 on PhonePe to 9876543210 today.", [("phone", "caution")]),
        ("Reply with your OTP to complete the registration.", [("secret", "caution")]),
        ("Share your PIN with the clerk.", [("secret", "caution")]),
        ("अपना ओटीपी बताएं", [("secret", "caution")]),
        ("ओटीपी शेअर करा", [("secret", "caution")]),
        ("Pay the fine within 24 hours or admission is cancelled.", [("urgent", "caution")]),
        ("Fill the form at bit.ly/exam-form-26 today", [("short_link", "caution")]),
        ("Apply at https://college.ac.in.pay-now.xyz/form", [("lookalike", "caution")]),
        ("Register at examfee.xyz/pay", [("link", "info")]),
    ],
)
def test_red_flags_are_found(text, expected):
    assert kinds(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Send your form to office@college.edu.in before Friday.",  # an email is not a UPI ID
        "Mail exam@gmail.com for queries.",
        "Fee Rs. 500 payable at the office counter. For queries call 9876543210.",  # a contact number is not a payment number
        "Apply at https://examportal.unipune.ac.in/forms",  # an official domain
        "Last date for exam form submission: 27/11/2026. Hall tickets on 02.12.2026 at 10.30 am in Room 14.",
        "Pay within 3 days at the office.",  # not a rush
        "Do not share your password with anyone.",  # advice, not a request
        "Never share your OTP or PIN with anyone, even college staff.",
        "OTP कधीही कोणाला सांगू नका.",
        "अपना ओटीपी कभी न बताएं।",
    ],
)
def test_ordinary_notices_are_not_flagged(text):
    assert [k for k in kinds(text) if k[1] == "caution"] == []


def test_the_warning_is_spoken_in_the_students_language_and_never_says_scam():
    flags = check("Pay to the UPI ID asha@ybl", "hi")
    assert "UPI आईडी" in flags[0]["text"] and "पक्की कर लें" in flags[0]["text"]
    for lang in ("en", "hi", "mr"):
        for f in check("Pay to the UPI ID asha@ybl. Reply with your OTP.", lang):
            assert "scam" not in f["text"].lower() and "fake" not in f["text"].lower()
    assert spoken_warning(check("see https://some-site.xyz/x"), "en") == ""  # a quiet finding is shown, not spoken


def test_an_address_is_spoken_so_a_voice_can_read_it():
    assert speak_address("asha@ybl") == "asha at ybl" and speak_address("pay-now.xyz") == "pay-now dot xyz"


def test_scanned_table_html_is_read_as_text():
    assert ("upi", "caution") in kinds("<td>Pay to</td><td>asha@ybl</td>")


# ---------------------------------------------------------------- through the engine


async def test_the_reading_carries_a_safety_check_and_the_proof_line(client, engine_for_safety):
    st, tok = engine_for_safety
    from .conftest import notice_image

    r = await client.post(f"/api/me/read?t={tok}", files={"file": ("n.jpg", notice_image(), "image/jpeg")}, data={"lang": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["kind"] for f in body["safety"]] == ["upi"]
    assert body["spoken_text"].startswith("Safety check. This notice asks you to pay to the UPI ID asha dot college at ybl")
    first = body["actions"][0]
    assert first["status"] == "verified" and first["evidence"].startswith("Last date for exam form submission")
    assert body["actions"][1]["evidence"].startswith("Hall tickets will be distributed")
    unverified = next(a for a in body["actions"] if a["status"] != "verified") if any(a["status"] != "verified" for a in body["actions"]) else None
    assert unverified is None or unverified["evidence"] is None  # a date that was not confirmed shows no proof


async def test_an_invented_date_has_no_proof_line(client, engine_for_safety):
    st, tok = engine_for_safety
    from datetime import timedelta

    from .conftest import notice_image

    st.extraction["actions"][0]["due_date"] = (st.due + timedelta(days=4)).isoformat()
    body = (await client.post(f"/api/me/read?t={tok}", files={"file": ("n.jpg", notice_image(), "image/jpeg")}, data={"lang": "en"})).json()
    assert body["actions"][0]["status"] != "verified" and body["actions"][0]["evidence"] is None
