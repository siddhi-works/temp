import base64
import json
import struct
from datetime import date, datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.pipeline import buzz
from app.pipeline.personal import (
    Slot,
    catch_readback,
    clash_text,
    exam_eve_text,
    find_clashes,
    is_yes,
    resolve_phrase,
    right_now,
    right_now_text,
    speak_time,
)

SLOTS = [
    Slot(1, "10:00", "11:00", "Physics", "Room 12"),  # Tuesday
    Slot(1, "11:15", "12:15", "Chemistry", "Room 4"),
    Slot(3, "09:00", "10:00", "Maths", "Room 2"),
]
SAT = date(2026, 11, 21)  # Saturday


def test_speak_time_is_unambiguous():
    assert speak_time("14:05") == "2:05 PM" and speak_time("00:00") == "12 AM" and speak_time("12:30") == "12:30 PM"


def test_right_now_in_class_free_and_next_day():
    tue_1030 = datetime(2026, 11, 24, 10, 30)
    assert right_now_text(SLOTS, tue_1030) == (
        "Right now: Physics in Room 12, until 11 AM, 30 minutes left. Next: Chemistry at 11:15 AM in Room 4, in 45 minutes."
    )
    tue_late = datetime(2026, 11, 24, 17, 0)
    assert "No more classes today. Next: Maths Thursday at 9 AM in Room 2." in right_now_text(SLOTS, tue_late)
    wed = datetime(2026, 11, 25, 12, 0)
    assert right_now(SLOTS, wed)["next_day"][0] == 1
    assert "don't have your timetable" in right_now_text([], wed)


def test_clash_detection_overlap_edges():
    tue = date(2026, 12, 1)
    assert [c.subject for c in find_clashes(SLOTS, tue, "10:30", 60)] == ["Physics", "Chemistry"]
    assert find_clashes(SLOTS, tue, "11:00", 15) == []  # touching, not overlapping
    assert find_clashes(SLOTS, tue, "09:00", 60) == []
    assert find_clashes(SLOTS, date(2026, 12, 2), "10:30", 60) == []  # Wednesday: no classes
    text = clash_text("Hall ticket collection", tue, "10:30", find_clashes(SLOTS, tue, "10:30", 60))
    assert text.startswith("Clash: Hall ticket collection, Tuesday, 1 December 2026 at 10:30 AM, overlaps your Physics class")


def test_catcher_resolves_relative_dates_in_code():
    assert resolve_phrase("assignment due tomorrow", SAT).due == date(2026, 11, 22)
    assert resolve_phrase("परवा सबमिशन आहे", SAT).due == date(2026, 11, 23)
    assert resolve_phrase("उद्या टेस्ट", SAT).due == date(2026, 11, 22)
    assert resolve_phrase("submit on Friday", SAT).due == date(2026, 11, 27)
    assert resolve_phrase("शुक्रवारी जमा करा", SAT).due == date(2026, 11, 27)
    assert resolve_phrase("submit in 3 days", SAT).due == date(2026, 11, 24)
    assert resolve_phrase("last date 28/11/2026 at 4 pm", SAT).due == date(2026, 11, 28)
    assert resolve_phrase("last date 28/11/2026 at 4 pm", SAT).time == "16:00"
    mon = date(2026, 11, 23)
    nf = resolve_phrase("next Friday", mon)
    assert nf.due == date(2026, 12, 4) and nf.ambiguous  # next week's Friday, flagged for read-back
    assert resolve_phrase("next Friday", SAT).due == date(2026, 11, 27)
    none = resolve_phrase("bring your notebooks", SAT)
    assert none.due is None
    assert "could not tell the date" in catch_readback("Bring notebooks", none)
    rb = catch_readback("Physics assignment", nf)
    assert "Friday, 4 December 2026" in rb and "check this date" in rb and rb.endswith("say the date again.")
    assert is_yes("YES") and is_yes("होय") and not is_yes("maybe")


def test_exam_eve_text_languages():
    t = exam_eve_text("Physics exam", date(2026, 12, 2), "10:00", "Room 14", ["hall ticket", "ID"], "https://x/r/abc")
    assert t.startswith("Tomorrow: Physics exam, Wednesday, 2 December 2026 at 10 AM. Place: Room 14.") and "https://x/r/abc" in t
    assert "उद्या परीक्षा" in exam_eve_text("भौतिकशास्त्र", date(2026, 12, 2), None, None, ["x"], None, "mr")


def test_buzz_patterns_validate_overrides():
    assert buzz.pattern_for("exam", None) == [450, 150, 450, 150, 450]
    assert buzz.pattern_for("exam", {"exam": [100, 100]}) == [100, 100]
    assert buzz.pattern_for("exam", {"exam": [5000]}) == [450, 150, 450, 150, 450]  # unsafe value ignored
    assert len({tuple(v["pattern"]) for v in buzz.BUZZ_TYPES.values()}) == len(buzz.BUZZ_TYPES)  # all distinct


def test_web_push_encryption_round_trips_per_rfc8291():
    """Play the browser's role: decrypt what we encrypted, using only the subscription secrets."""
    from app.providers.webpush import b64e, encrypt_payload, generate_vapid_keys

    ua_private = ec.generate_private_key(ec.SECP256R1())
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    msg = json.dumps({"title": "Exam tomorrow", "vibrate": [450, 150, 450]}, ensure_ascii=False).encode()
    body = encrypt_payload(msg, b64e(ua_public), b64e(auth))

    salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    as_public, ciphertext = body[21 : 21 + idlen], body[21 + idlen :]
    assert rs == 4096 and idlen == 65
    ecdh = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_public + as_public).derive(ecdh)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02") and json.loads(plain[:-1])["vibrate"] == [450, 150, 450]
    priv, pub = generate_vapid_keys()
    assert len(base64.urlsafe_b64decode(pub + "==")) == 65


def test_vapid_jwt_verifies(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    from app.config import get_settings
    from app.providers.webpush import b64d, generate_vapid_keys, vapid_headers

    priv, pub = generate_vapid_keys()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", priv)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", pub)
    get_settings.cache_clear()
    h = vapid_headers("https://fcm.googleapis.com/fcm/send/abc")["Authorization"]
    t = h.split("t=")[1].split(",")[0]
    head, claims, sig = t.split(".")
    assert json.loads(b64d(claims))["aud"] == "https://fcm.googleapis.com"
    raw = b64d(sig)
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(pub))
    key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
