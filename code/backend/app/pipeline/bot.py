"""The WhatsApp bot: what happens when a student messages us.

Commands work in English, Marathi and Hindi. Anything that is not a command is
treated as a question and answered only from published notices.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import sessionmaker, utcnow
from ..errors import AppError, PermanentError
from ..models import (
    College,
    PersonalDeadline,
    Delivery,
    DeliveryStatus,
    ForwardCheck,
    InboundMessage,
    Job,
    Notice,
    NoticeAction,
    NoticeSource,
    NoticeStatus,
    Student,
    Verification,
)
from ..providers import whatsapp
from ..providers.base import ProviderError
from ..providers.llm import gemini_answer
from ..providers.media import mp3_to_voice_note, prepare_document, sniff_mime
from ..providers.ocr import run_ocr
from ..providers.stt import transcribe
from ..providers.tts import speak_uncached, synthesize
from ..security import decrypt_phone, sign_student_token
from ..storage import get_storage, sha256_bytes
from . import campus, linking, reader
from .dates import speak_date
from .jobs import enqueue
from .notify import notice_link, t
from .personal import resolve_phrase

log = logging.getLogger("suchana.bot")

CMD = {
    "help": {"help", "hi", "hello", "menu", "मदत", "मदद", "नमस्कार", "नमस्ते"},
    "deadlines": {"deadlines", "deadline", "dates", "due", "मुदत", "तारखा", "तारीख", "तारीखें"},
    "inbox": {"inbox", "link", "notices", "सूचना", "सूचनाएं"},
    "stop": {"stop", "unsubscribe", "बंद", "थांबा", "रोको"},
    "start": {"start", "subscribe", "सुरू", "शुरू"},
    "now": {"now", "right now", "आता", "अभी", "अब"},
    "catch": {"catch", "remember", "note", "लक्षात ठेवा", "याद रखो", "नोंद"},
    "yes": {"yes", "y", "ok", "okay", "yeah", "हो", "होय", "हां", "हाँ", "ठीक"},
    "no": {"no", "n", "cancel", "नाही", "नहीं", "नको", "रद्द"},
    "route": {"route", "routes", "map", "मार्ग", "रास्ता", "नकाशा"},
    "timetable": {"timetable", "time table", "वेळापत्रक", "समय सारणी"},
    "language": {"language", "languages", "भाषा"},
    "history": {"history", "इतिहास"},
    "disconnect": {"disconnect", "unlink", "डिस्कनेक्ट"},
    "delete": {"delete", "erase", "हटवा", "हटाएं", "मिटाओ"},
    "delete_confirm": {"delete everything", "delete my data", "erase everything", "सर्व हटवा", "सब मिटाओ"},
    "again": {"again", "repeat", "audio", "play", "पुन्हा", "फिर से", "सुनाओ"},
}
# The QR's pre-filled message is "Hi Samyukta, my link code is K7M2QX4P". A typed "link K7M2QX4P" works too. An ordinary sentence
# that merely contains the word "link" does not match.
_CODE = r"([A-HJ-NP-Z2-9]{8})"
LINK_RE = re.compile(rf"(?:\blink code(?: is)?[\s:#-]{{1,3}}{_CODE}\b|^\s*/?link[\s:#-]{{1,3}}{_CODE}\s*$)", re.IGNORECASE)
SAVE_RE = re.compile(r"^save\s*(\d{1,2})?$", re.IGNORECASE)
BATCH_RE = re.compile(r"^(?:batch|बॅच|बैच)\s+(.{1,30})$", re.IGNORECASE)
BOARD_WORDS = ("board", "snap", "फळा", "फलक", "बोर्ड", "ब्लैकबोर्ड", "lecture", "notes")
LANG_WORDS = {"mr": {"marathi", "मराठी", "lang mr"}, "hi": {"hindi", "हिंदी", "lang hi"}, "en": {"english", "इंग्रजी", "lang en"}}
JOIN_RE = re.compile(r"^(?:join|जॉईन|जॉइन)\s+([a-z0-9\-]{2,16})$", re.IGNORECASE)


L = {
    "welcome": {
        "en": "Welcome to Samyukta. Send me a photo or PDF of a college notice and I will read it aloud and check every date against it. "
        "I keep your phone number (encrypted) and your language. I keep the last notice for one hour so you can ask about it. "
        "Send DELETE to erase everything. Send HELP for commands.",
        "hi": "समयुक्ता में आपका स्वागत है। कॉलेज की सूचना की फ़ोटो या PDF भेजें, मैं उसे पढ़कर सुनाऊँगा और हर तारीख को सूचना से मिलाऊँगा। "
        "मैं आपका फ़ोन नंबर (एन्क्रिप्टेड) और भाषा रखता हूँ। आख़िरी सूचना एक घंटे तक रखता हूँ ताकि आप उसके बारे में पूछ सकें। "
        "सब मिटाने के लिए DELETE भेजें। कमांड के लिए HELP भेजें।",
        "mr": "समयुक्तामध्ये स्वागत. महाविद्यालयाच्या सूचनेचा फोटो किंवा PDF पाठवा, मी ती वाचून दाखवीन आणि प्रत्येक तारीख सूचनेशी पडताळीन. "
        "मी तुमचा फोन नंबर (एन्क्रिप्टेड) आणि भाषा ठेवतो. शेवटची सूचना एक तास ठेवतो, म्हणजे तुम्ही त्याबद्दल विचारू शकता. "
        "सर्व पुसण्यासाठी DELETE पाठवा. कमांडसाठी HELP पाठवा.",
    },
    "menu": {
        "en": "Send a photo or PDF of a notice. Then ask me about it by text or voice note. Commands: DEADLINES, AGAIN, SAVE 1, LANGUAGE, LINK, HISTORY, DISCONNECT, DELETE, STOP.",
        "hi": "सूचना की फ़ोटो या PDF भेजें। फिर उसके बारे में लिखकर या वॉइस नोट से पूछें। कमांड: DEADLINES, AGAIN, SAVE 1, LANGUAGE, LINK, HISTORY, DISCONNECT, DELETE, STOP.",
        "mr": "सूचनेचा फोटो किंवा PDF पाठवा. मग त्याबद्दल लिहून किंवा व्हॉइस नोटने विचारा. कमांड: DEADLINES, AGAIN, SAVE 1, LANGUAGE, LINK, HISTORY, DISCONNECT, DELETE, STOP.",
    },
    "got_it": {
        "en": "Got it. Reading this notice now. It takes about half a minute.",
        "hi": "ठीक है। मैं यह सूचना पढ़ रहा हूँ, इसमें आधा मिनट लगेगा।",
        "mr": "ठीक आहे. मी ही सूचना वाचत आहे, अर्धा मिनिट लागेल.",
    },
    "dates_head": {"en": "Dates I checked against the notice:", "hi": "सूचना से मिलाई गई तारीखें:", "mr": "सूचनेशी पडताळलेल्या तारखा:"},
    "unconfirmed": {
        "en": "date not confirmed, please check with the college office",
        "hi": "तारीख की पुष्टि नहीं हुई, कृपया कॉलेज कार्यालय से पूछें",
        "mr": "तारखेची खात्री झाली नाही, कृपया महाविद्यालयाच्या कार्यालयात विचारा",
    },
    "next": {
        "en": "Ask me anything about this notice, by text or voice note. Reply SAVE 1 to get reminders for the first date. Reply AGAIN to hear it once more.",
        "hi": "इस सूचना के बारे में कुछ भी पूछें, लिखकर या वॉइस नोट से। पहली तारीख का रिमाइंडर पाने के लिए SAVE 1 भेजें। फिर से सुनने के लिए AGAIN भेजें।",
        "mr": "या सूचनेबद्दल काहीही विचारा, लिहून किंवा व्हॉइस नोटने. पहिल्या तारखेची आठवण हवी असल्यास SAVE 1 पाठवा. पुन्हा ऐकण्यासाठी AGAIN पाठवा.",
    },
    "safety": {"en": "Safety check:", "hi": "सुरक्षा जाँच:", "mr": "सुरक्षा तपासणी:"},
    "from_notice": {"en": "From the notice:", "hi": "सूचना में:", "mr": "सूचनेत:"},
    "ask_first": {
        "en": "Send me a photo or PDF of a notice first. Then you can ask me about it.",
        "hi": "पहले मुझे सूचना की फ़ोटो या PDF भेजें। फिर आप उसके बारे में पूछ सकते हैं।",
        "mr": "आधी मला सूचनेचा फोटो किंवा PDF पाठवा. मग तुम्ही त्याबद्दल विचारू शकता.",
    },
}


def lt(key: str, lang: str) -> str:
    return L[key].get(lang, L[key]["en"])


def classify(text: str) -> tuple[str, str | None]:
    clean = re.sub(r"\s+", " ", text.strip().lower()).lstrip("/")  # "/help" and "help" are the same
    m = LINK_RE.search(text)
    if m:
        return "link", (m.group(1) or m.group(2)).upper()
    m = SAVE_RE.match(clean)
    if m:
        return "save", m.group(1)
    m = JOIN_RE.match(clean)
    if m:
        return "join", m.group(1).upper()
    m = BATCH_RE.match(clean)
    if m:
        return "batch", m.group(1).strip().upper()
    for lang, words in LANG_WORDS.items():
        if clean in words:
            return "lang", lang
    for cmd, words in CMD.items():
        if clean in words:
            return cmd, None
    return "question", None


async def reply(phone: str, text: str) -> None:
    await whatsapp.send_text(phone, text)


async def upcoming_actions(s: AsyncSession, student: Student, days: int = 45) -> list[tuple[NoticeAction, Notice]]:
    if not student.college_id:
        return []
    from .steps import today_local

    today = today_local()
    rows = (
        await s.execute(
            select(NoticeAction, Notice)
            .join(Notice, Notice.id == NoticeAction.notice_id)
            .where(
                Notice.college_id == student.college_id,
                Notice.status == NoticeStatus.published,
                NoticeAction.due_date.is_not(None),
                NoticeAction.due_date >= today,
                NoticeAction.due_date <= today + timedelta(days=days),
            )
            .order_by(NoticeAction.due_date)
            .limit(10)
        )
    ).all()
    tags = set(student.audience_tags or [])
    return [(a, n) for a, n in rows if not n.audience_tags or not tags or tags & set(n.audience_tags)]


async def saved_deadlines_text(s: AsyncSession, student: Student) -> str:
    from .steps import today_local

    rows = (
        await s.scalars(
            select(PersonalDeadline)
            .where(PersonalDeadline.student_id == student.id, PersonalDeadline.status == "confirmed", PersonalDeadline.due_date >= today_local())
            .order_by(PersonalDeadline.due_date)
            .limit(10)
        )
    ).all()
    if not rows:
        return ""
    return "Your saved deadlines:\n" + "\n".join(f"{i}. {speak_date(r.due_date)}: {r.label}" for i, r in enumerate(rows, 1))


def deadlines_text(items: list[tuple[NoticeAction, Notice]]) -> str:
    if not items:
        return "No upcoming deadlines in your college's published notices."
    lines = ["Upcoming:"]
    for i, (a, n) in enumerate(items, 1):
        check = " Please confirm with the office." if a.verification == Verification.needs_check else ""
        lines.append(f"{i}. {speak_date(a.due_date)}: {a.label} ({n.title}).{check}")
    return "\n".join(lines)


_ANSWERS: dict[tuple, str] = {}


async def answer_question(s: AsyncSession, student: Student, question: str) -> str:
    lang = student.preferred_lang or "en"
    recent = await linking.recall_read(s, student)
    if recent:  # the notice they just sent: answered from that notice only, and every date and number is checked against it
        res = await reader.ask_notice(question, recent["text"], lang)
        return res["answer"] + (f"\n\n{lt('from_notice', lang)} {res['evidence'][0]}" if res["evidence"] else "")
    mem = await campus.answer_from_memory(s, student, question)
    if mem:
        return mem["text"] + "\n\n(From your saved board photos.)"
    items = await upcoming_actions(s, student, days=120)
    notices = (
        await s.scalars(
            select(Notice)
            .where(Notice.college_id == student.college_id, Notice.status == NoticeStatus.published)
            .order_by(Notice.published_at.desc())
            .limit(6)
        )
    ).all() if student.college_id else []
    if not notices:
        if not student.college_id:
            return lt("ask_first", lang)
        return "I could not find any published notices for your college yet. Send JOIN followed by your college code to connect."
    # The same question about the same notices is answered once; a new or changed notice changes the key.
    cache_key = (student.college_id, student.preferred_lang, re.sub(r"\W+", " ", question.lower()).strip(), tuple(n.id for n in notices), tuple(n.version for n in notices))
    if cache_key in _ANSWERS:
        return _ANSWERS[cache_key]
    context_lines = []
    for n in notices:
        context_lines.append(f"[{n.title}] issued by {n.issuer or 'college'}; summary: {(n.summaries or {}).get('en', '')}")
    for a, n in items:
        context_lines.append(f"- {a.label}: {a.due_date.isoformat()}{' ' + a.due_time if a.due_time else ''} ({n.title}; {'verified' if a.verification != 'needs_check' else 'unconfirmed'})")
    try:
        answer = await gemini_answer(question, "\n".join(context_lines), student.preferred_lang)
        if len(_ANSWERS) > 300:
            _ANSWERS.clear()
        _ANSWERS[cache_key] = answer
        return answer
    except ProviderError as exc:
        log.warning("Q&A unavailable, falling back to deadline list: %s", exc)
        return "I can't answer questions right now. " + deadlines_text(items[:5])


async def resend_deferred(s: AsyncSession, student: Student) -> int:
    """The 24h window just opened: send anything we had to hold back."""
    held = (await s.scalars(select(Delivery).where(Delivery.student_id == student.id, Delivery.status == DeliveryStatus.deferred_window))).all()
    for d in held:
        d.status = DeliveryStatus.queued
        await enqueue(s, "deliver", {"delivery_id": d.id}, dedupe_key=f"deliver:{d.id}:retry:{int(utcnow().timestamp())}", notice_id=d.notice_id)
    return len(held)


async def forward_count_today(s: AsyncSession, student: Student) -> int:
    since = utcnow() - timedelta(hours=24)
    return int(
        await s.scalar(select(func.count(ForwardCheck.id)).where(ForwardCheck.student_id == student.id, ForwardCheck.created_at >= since))
        or 0
    )


from .steps import handler


@handler("handle_inbound")
async def handle_inbound(job: Job) -> None:
    get_settings()
    async with sessionmaker()() as s:
        msg = await s.get(InboundMessage, job.payload["inbound_id"])
        if msg is None or msg.status == "done":
            return
        student = await s.get(Student, msg.student_id) if msg.student_id else None
        if student is None:
            raise PermanentError("inbound message has no student")
        phone = decrypt_phone(student.phone_enc)
        student.last_inbound_at = msg.received_at
        resent = await resend_deferred(s, student)
        await s.commit()
        await whatsapp.mark_read(job.payload.get("wa_message_id", ""))
        lang = student.preferred_lang or "en"

        if not student.college_id and await first_contact(s, student, phone, msg, lang):
            msg.status = "done"
            await s.commit()
            return

        # If a reply fails, the job retries the whole message. Every branch is safe to
        # repeat: join/lang/stop are idempotent and OCR results are cached.
        if msg.msg_type == "text" and msg.text:
            await handle_text(s, student, phone, msg.text, lang, resent)
        elif msg.msg_type == "image" and msg.text and any(w in msg.text.lower() for w in BOARD_WORDS):
            await handle_snap(s, student, phone, msg)
        elif msg.msg_type in ("image", "document"):
            await handle_forward(s, student, phone, msg)
        elif msg.msg_type == "audio":
            try:
                data, mime = await whatsapp.download_media(msg.media_id)
                question = await transcribe(data, mime)
            except ProviderError:
                await reply(phone, "Sorry, I could not understand the voice message. Please try again or type your question.")
            else:
                if student.catch_until and student.catch_until > utcnow():
                    pd, readback = await campus.create_catch(s, student, question, "voice")
                    await reply(phone, f'I heard: "{question}"\n\n{readback}')
                    msg.status = "done"
                    await s.commit()
                    return
                answer = await answer_question(s, student, question)
                await reply(phone, f'You asked: "{question}"\n\n{answer}')
                try:
                    if not student.college_id:
                        # Student mode: spoken from memory, never cached or written to disk (the answer is about their own notice).
                        await send_voice_bytes(phone, await speak_uncached(answer, lang))
                    else:
                        audio = await synthesize(answer, lang)
                        from ..models import Rendition
                        from .steps import send_voice

                        await send_voice(phone, Rendition(lang=lang, text=answer, audio_key=audio.audio_key, voice_note_key=audio.voice_note_key))
                except ProviderError as exc:
                    log.info("spoken answer skipped: %s", exc)
        else:
            await reply(phone, t("menu", lang))
        msg.status = "done"
        await s.commit()


def _has_code(text: str, code: str) -> bool:
    """The access code as a whole word. A short code must not match inside an ordinary word."""
    return bool(code) and re.search(rf"(?<!\w){re.escape(code)}(?!\w)", text, re.IGNORECASE) is not None


async def first_contact(s: AsyncSession, student: Student, phone: str, msg: InboundMessage, lang: str) -> bool:
    """A student with no college is a student-mode user. Before their first answer they get the privacy notice (and must
    give the access code if the deployment has one). Returns True if this message was used up by that step."""
    if student.consent_at:
        return False
    code = get_settings().demo_access_code
    text = msg.text or ""
    kind = classify(text)[0] if msg.msg_type == "text" else ""
    if kind == "join":  # joining a college has its own welcome and consent
        return False
    if code and kind != "link" and not _has_code(text, code):
        await reply(phone, "To start, send the access code the Samyukta team gave you.")
        return True
    student.consent_at = utcnow()
    await s.commit()
    await reply(phone, lt("welcome", lang))
    # A greeting or the access code itself is fully answered by the welcome; anything else (a photo, a question, a link code) is still handled.
    return kind in ("help", "start") or bool(code and _has_code(text, code) and kind != "link")


async def handle_text(s: AsyncSession, student: Student, phone: str, text: str, lang: str, resent: int) -> None:
    kind, arg = classify(text)
    if kind == "link":
        await reply(phone, await linking.claim_link(s, student, arg))
    elif kind == "disconnect":
        done = await linking.disconnect(s, student)
        await reply(phone, "Disconnected. I have forgotten this phone number. Your saved deadlines stay in your Samyukta account on the website." if done else "This number was not connected to a website account.")
    elif kind == "delete":
        await reply(phone, "This erases your phone number, saved deadlines, reminders and everything else I hold about you. To confirm, send DELETE EVERYTHING.")
    elif kind == "delete_confirm":
        await linking.erase_student(s, student)
        await reply(phone, "Everything is erased. You can message me again any time to start fresh.")
    elif kind == "history":
        recent = await linking.recall_read(s, student)
        saved = await saved_deadlines_text(s, student)
        head = (
            "I do not keep a history of your notices. That is on purpose. "
            + (f"I am keeping only the last one, \"{recent.get('title') or 'your notice'}\", for one hour so you can ask about it." if recent else "Right now I am keeping no notice.")
        )
        await reply(phone, head + (f"\n\n{saved}" if saved else ""))
    elif kind == "language":
        await reply(phone, "Send ENGLISH, HINDI or MARATHI. / हिंदी के लिए HINDI भेजें। / मराठीसाठी MARATHI पाठवा.")
    elif kind == "again":
        await handle_again(s, student, phone, lang)
    elif kind == "save":
        await handle_save(s, student, phone, arg, lang)
    elif kind == "join":
        college = await s.scalar(select(College).where(College.code == arg))
        if not college:
            await reply(phone, f"No college found with code {arg}. Please check the code with your college office.")
            return
        student.college_id = college.id
        student.consent_at = student.consent_at or utcnow()
        student.opted_out_at = None
        await s.commit()
        await reply(
            phone,
            f"You are connected to {college.name}. You will get its official notices here as voice notes and accessible pages, "
            "with reminders before deadlines. We store your phone number (encrypted) and your language choice only. "
            "Notices are read by AI services (Sarvam, Google Gemini) and checked by college staff before sending.\n\n" + t("menu", lang),
        )
    elif kind == "lang":
        student.preferred_lang = arg
        await s.commit()
        await reply(phone, {"mr": "भाषा मराठी केली.", "hi": "भाषा हिंदी कर दी गई.", "en": "Language set to English."}[arg] + "\n" + t("menu", arg))
    elif kind == "stop":
        student.opted_out_at = utcnow()
        await s.commit()
        await reply(phone, "You will not get notices or reminders any more. Send START to turn them back on.")
    elif kind == "start":
        student.opted_out_at = None
        await s.commit()
        await reply(phone, "Notices and reminders are on again.")
    elif kind == "deadlines":
        items = await upcoming_actions(s, student)
        saved = await saved_deadlines_text(s, student)
        if not items and saved:
            await reply(phone, saved)
        else:
            await reply(phone, deadlines_text(items) + (f"\n\n{saved}" if saved else ""))
    elif kind == "inbox":
        base = get_settings().frontend_base_url.rstrip("/")
        await reply(phone, f"Your notices: {base}/me?t={sign_student_token(student.id)}")
    elif kind == "now":
        await reply(phone, (await campus.now_summary(s, student))["text"])
    elif kind == "timetable":
        await handle_batch(s, student, phone, None)
    elif kind == "batch":
        await handle_batch(s, student, phone, arg)
    elif kind == "catch":
        student.catch_until = utcnow() + timedelta(minutes=5)
        await s.commit()
        await reply(phone, "Ready. Send a voice note or type what the teacher said, for example: assignment due next Friday.")
    elif kind in ("yes", "no"):
        await handle_yes_no(s, student, phone, kind)
    elif kind == "route":
        base = get_settings().frontend_base_url.rstrip("/")
        await reply(phone, f"Campus walkthroughs for your college: {base}/routes?c={student.college_id}" if student.college_id else "Send JOIN followed by your college code first.")
    elif kind == "help":
        if not student.college_id:
            await reply(phone, lt("menu", lang))
        else:
            await reply(phone, ("Sent the notices you missed above.\n\n" if resent else "") + t("menu", lang))
    else:
        armed = student.catch_until and student.catch_until > utcnow()
        pending = await campus.latest_pending(s, student.id)
        if armed:
            pd, readback = await campus.create_catch(s, student, text, "text")
            await reply(phone, readback)
        elif pending is not None and (pending.needs_date or (len(text) < 60 and resolve_phrase(text, campus.local_now().date()).due)):
            await reply(phone, await campus.fix_date(s, pending, text) + " Reply YES to save or NO to cancel.")
        else:
            await reply(phone, await answer_question(s, student, text))


async def handle_yes_no(s: AsyncSession, student: Student, phone: str, kind: str) -> None:
    pending = await campus.latest_pending(s, student.id)
    if pending is None:
        await reply(phone, "Nothing is waiting for a yes or no. Send CATCH to save a deadline, or HELP.")
        return
    if kind == "no":
        pending.status = "cancelled"
        await s.commit()
        await reply(phone, "Cancelled. Send CATCH to try again.")
        return
    try:
        made = await campus.confirm_catch(s, student, pending)
    except AppError as exc:
        await reply(phone, str(exc))
        return
    await reply(phone, campus.confirmation_text(pending, len(made)))


async def handle_batch(s: AsyncSession, student: Student, phone: str, batch: str | None) -> None:
    try:
        n, _ = await campus.import_timetable(s, student, batch)
    except AppError as exc:
        await reply(phone, str(exc) + (" Reply BATCH followed by your batch name." if exc.code == "batch_required" else ""))
        return
    await reply(phone, f"Your timetable is saved: {n} classes. Send NOW any time to hear what is on. I will warn you only when a new notice clashes with your classes.")


async def handle_snap(s: AsyncSession, student: Student, phone: str, msg: InboundMessage) -> None:
    try:
        data, _mime = await whatsapp.download_media(msg.media_id)
    except ProviderError as exc:
        if exc.retryable:
            raise
        await reply(phone, "I could not open that photo. Please send it again.")
        return
    if sniff_mime(data) not in ("image/jpeg", "image/png", "image/webp"):
        await reply(phone, "Please send the board as a photo.")
        return
    sn = await campus.create_snap(s, student, data)
    await reply(phone, f"Saved{' under ' + sn.subject if sn.subject else ''}. I am reading it now. Later you can ask me about it by voice or text.")


async def send_voice_bytes(phone: str, mp3: bytes) -> None:
    """Send audio held in memory as a WhatsApp voice note (OGG/Opus), or as an MP3 if ffmpeg is missing. Nothing is written to disk."""
    ogg = await mp3_to_voice_note(mp3)
    data, mime, name = (ogg, "audio/ogg", "notice.ogg") if ogg else (mp3, "audio/mpeg", "notice.mp3")
    await whatsapp.send_audio(phone, await whatsapp.upload_media(data, mime, name))


def compose_read(res: dict, lang: str) -> str:
    """The text twin of the voice note: the title, the spoken answer, and each date marked checked or not."""
    lines = [res.get("title") or "", "", res["spoken_text"]]
    acts = [a for a in res.get("actions", []) if a.get("due_date")]
    if acts:
        lines += ["", lt("dates_head", lang)]
        for i, a in enumerate(acts[:8], 1):
            if a["status"] == "verified":
                when = a["date_spoken"] + (f", {a['due_time']}" if a.get("due_time") else "")
                lines.append(f"{i}. {a['label']}: {when}.")
            else:
                lines.append(f"{i}. {a['label']}: {lt('unconfirmed', lang)}.")
    quiet = [f["text"] for f in res.get("safety", []) if f["level"] != "caution"]  # cautions are already the first sentence of the reading
    if quiet:
        lines += ["", lt("safety", lang)] + [f"- {q}" for q in quiet[:3]]
    lines += ["", lt("next", lang)]
    return "\n".join(lines).strip()[:4000]


async def handle_personal_read(s: AsyncSession, student: Student, phone: str, msg: InboundMessage) -> None:
    """Student mode: the same engine as the website. Reads the notice, checks every date, replies with text and a voice
    note. Nothing about the notice is kept except a sealed copy for one hour, so the student can ask about it."""
    s_ = get_settings()
    lang = student.preferred_lang or "en"
    since = utcnow() - timedelta(hours=24)
    used = await s.scalar(
        select(func.count(InboundMessage.id)).where(
            InboundMessage.student_id == student.id, InboundMessage.msg_type.in_(("image", "document")), InboundMessage.received_at >= since
        )
    )
    if int(used or 0) > s_.student_daily_forward_limit:
        await reply(phone, "You have sent many notices today. Please try again tomorrow.")
        return
    try:
        data, _mime = await whatsapp.download_media(msg.media_id)
    except ProviderError as exc:
        if exc.retryable:
            raise
        await reply(phone, "I could not open that file. Please send the photo or PDF again.")
        return
    await reply(phone, lt("got_it", lang))
    earlier = await linking.recall_read(s, student)  # the notice from the last hour, if any: a correction may follow it
    try:
        res = await reader.read_notice(data, lang)
    except AppError as exc:
        # Written for people ("This PDF is password protected..."), so it is sent as it is. No retry: it would only repeat the same message.
        await reply(phone, exc.message if exc.status_code < 500 else "I could not read this notice just now. Please send it again in a minute.")
        return
    text = res.pop("_ocr_text", "")
    keep = [{"label": a["label"], "due_date": a["due_date"], "due_time": a.get("due_time"), "status": a["status"]} for a in res["actions"] if a.get("due_date")]
    changes = reader.diff_actions(earlier["actions"], keep) if earlier and res["is_notice"] else []
    if changes:  # first: the most important thing for someone who may be about to act on the old date
        await reply(phone, reader.change_text(changes, lang))
    await reply(phone, compose_read(res, lang))
    if res.get("audio_b64"):
        import base64

        try:
            await send_voice_bytes(phone, base64.b64decode(res["audio_b64"]))
        except ProviderError as exc:
            log.info("voice note skipped: %s", exc)  # the text already went out; a missing voice note must not repeat it
    if res["is_notice"] and text:
        await linking.remember_read(s, student, {"text": text[:30000], "title": res.get("title"), "spoken": res["spoken_text"], "lang": lang, "actions": keep})


async def handle_again(s: AsyncSession, student: Student, phone: str, lang: str) -> None:
    recent = await linking.recall_read(s, student)
    if not recent:
        await reply(phone, lt("ask_first", lang))
        return
    await reply(phone, recent["spoken"])
    try:
        await send_voice_bytes(phone, await speak_uncached(recent["spoken"], lang))
    except ProviderError as exc:
        log.info("voice note skipped: %s", exc)


async def handle_save(s: AsyncSession, student: Student, phone: str, which: str | None, lang: str) -> None:
    """Smart Deadline Guardian: only a date that was checked against the notice can become a reminder, and only after the student says YES."""
    from datetime import date as _date

    from .dates import speak_date_lang
    from .steps import today_local

    recent = await linking.recall_read(s, student)
    acts = [a for a in (recent or {}).get("actions", []) if a.get("due_date")]
    if not acts:
        await reply(phone, "I have no dates from a notice to save. Send me a notice first.")
        return
    i = int(which or 1) - 1
    if not 0 <= i < len(acts):
        await reply(phone, f"Please choose a number from 1 to {len(acts)}.")
        return
    a = acts[i]
    if a["status"] != "verified":
        await reply(phone, "I will not save that date because I could not confirm it against the notice. Please check it with the college office.")
        return
    due = _date.fromisoformat(a["due_date"])
    if due < today_local():
        await reply(phone, "That date has already passed.")
        return
    await s.execute(
        PersonalDeadline.__table__.update().where(PersonalDeadline.student_id == student.id, PersonalDeadline.status == "pending").values(status="cancelled")
    )
    s.add(PersonalDeadline(student_id=student.id, label=a["label"][:300], due_date=due, due_time=a.get("due_time"), transcript="From a notice I read", source="notice", status="pending", needs_date=False))
    await s.commit()
    when = speak_date_lang(due, lang) + (f", {a['due_time']}" if a.get("due_time") else "")
    await reply(phone, f"Save a reminder for: {a['label']}, {when}? Reply YES to save, or NO to cancel.")


async def handle_forward(s: AsyncSession, student: Student, phone: str, msg: InboundMessage) -> None:
    if not student.college_id:
        await handle_personal_read(s, student, phone, msg)
        return
    s_ = get_settings()
    if await forward_count_today(s, student) >= s_.student_daily_forward_limit:
        await reply(phone, "You have checked many notices today. Please try again tomorrow.")
        return
    try:
        data, _mime = await whatsapp.download_media(msg.media_id)
    except ProviderError as exc:
        if exc.retryable:
            raise
        await reply(phone, "I could not open that file. Please send the photo or PDF again.")
        return
    await reply(phone, "Got it. Reading this notice now, it takes about half a minute.")
    if sniff_mime(data) is None:
        await reply(phone, "I can read PDFs and photos only. Please send the notice as a photo or PDF.")
        return
    try:
        doc = prepare_document(data, max_pages=s_.max_pdf_pages, max_side=s_.max_image_side_px)
    except PermanentError as exc:
        await reply(phone, str(exc))
        return
    try:
        ocr = await run_ocr(doc, "mr")
    except ProviderError:
        await reply(phone, "Sorry, I could not read this notice right now. I'll keep trying and message you.")
        raise

    from .steps import match_text, send_voice

    result, score, matched, latest = await match_text(s, ocr.text, student.college_id)
    s.add(
        ForwardCheck(
            channel="whatsapp",
            student_id=student.id,
            college_id=student.college_id,
            source_sha256=sha256_bytes(doc.data),
            result=result,
            similarity=score,
            matched_notice_id=matched.id if matched else None,
            latest_notice_id=latest.id if latest else None,
        )
    )
    await s.commit()

    lang = student.preferred_lang or "en"
    token = sign_student_token(student.id)
    if result in ("official", "superseded") and latest is not None:
        from .steps import load_notice

        latest_full = await load_notice(s, latest.id)
        if result == "official":
            head = f"This is an official, current notice: {latest_full.title}."
        else:
            head = (
                f"Careful: this is an OLD version. It was replaced"
                f"{' on ' + speak_date(latest.published_at.date()) if latest.published_at else ''}.\n"
            )
            if latest_full.changes_from_previous:
                head += "What changed:\n" + "\n".join(f"- {c}" for c in latest_full.changes_from_previous[:5])
        rendition = next((r for r in latest_full.renditions if r.lang == lang and r.audio_key), None) or next(
            (r for r in latest_full.renditions if r.audio_key), None
        )
        try:
            await send_voice(phone, rendition)
        except ProviderError as exc:
            log.warning("voice for forward check failed: %s", exc)
        await reply(phone, f"{head}\n\nLatest version: {notice_link(latest_full, token)}")
        return

    # Not an official notice we know: read it for them (unverified) and queue it for a volunteer.
    key = await get_storage().put(f"forwards/{sha256_bytes(doc.data)}{doc.ext}", doc.data)
    n = Notice(
        college_id=student.college_id,
        source=NoticeSource.forward,
        status=NoticeStatus.queued,
        source_file_key=key,
        source_mime=doc.mime,
        source_sha256=sha256_bytes(doc.data),
        page_count=doc.pages,
        ocr_text=ocr.text,
        ocr_engine=ocr.engine,
        forwarded_by_student_id=student.id,
    )
    s.add(n)
    await s.flush()
    await enqueue(s, "process_notice", {"notice_id": n.id}, dedupe_key=f"process:{n.id}", notice_id=n.id)
    await s.commit()
