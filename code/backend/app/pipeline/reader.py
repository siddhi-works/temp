"""The one engine that reads a notice. The website and the WhatsApp bot both call `read_notice`.

Photo or PDF in, a spoken and written answer out. The AI proposes; the code in `dates.py` and `profile.py`
checks every date, time, amount and quoted line against the scan before anything is spoken. Nothing is stored:
OCR is run with cache=False and the audio is returned as bytes, so a caller decides what (if anything) to keep.

Raises AppError with a message written for the student, so any channel can show or speak it as it is.
"""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import date

from ..config import get_settings
from ..errors import AppError, PermanentError
from ..providers.base import ProviderError
from ..providers.llm import gemini_answer, gemini_extract
from ..providers.media import prepare_document, sniff_mime
from ..providers.ocr import pdf_text_layer, run_ocr
from ..providers.tts import speak_uncached
from .dates import DEVANAGARI_DIGITS, find_dates, first_title_line, rule_based_actions, speak_date_lang, verify_action
from . import safety
from .profile import rule_based_personal, verify_personal

CAUTION = {
    "en": "Some dates could not be checked against the scan. Please confirm them with the college office.",
    "hi": "कुछ तारीखों की स्कैन से पुष्टि नहीं हो सकी। कृपया कॉलेज कार्यालय से पूछ लें।",
    "mr": "काही तारखांची स्कॅनशी खात्री करता आली नाही. कृपया महाविद्यालयाच्या कार्यालयात विचारा.",
}
NOT_NOTICE = {
    "en": "This does not look like a college notice. Please try a clearer photo.",
    "hi": "यह कॉलेज की सूचना जैसी नहीं लगती। कृपया साफ़ फ़ोटो भेजें।",
    "mr": "ही महाविद्यालयाची सूचना वाटत नाही. कृपया स्पष्ट फोटो पाठवा.",
}


FALLBACK_INTRO = {
    "en": "I could not summarise this notice, so here is the start of its text.",
    "hi": "मैं इस सूचना का सार नहीं बना सका, इसलिए इसका शुरुआती पाठ पढ़ रहा हूँ।",
    "mr": "मला या सूचनेचा सारांश करता आला नाही, म्हणून तिचा सुरुवातीचा मजकूर वाचत आहे.",
}
PARTIAL = {
    "en": "This file has {total} pages. I read only the first {n}.",
    "hi": "इस फ़ाइल में {total} पन्ने हैं। मैंने सिर्फ़ पहले {n} पढ़े हैं।",
    "mr": "या फाईलमध्ये {total} पाने आहेत. मी फक्त पहिली {n} वाचली आहेत.",
}


PERSONAL_HEAD = {"en": "For you:", "hi": "आपके लिए:", "mr": "तुमच्यासाठी:"}
PERSONAL_NONE = {
    "en": "I could not find anything in this notice that is specifically for you.",
    "hi": "मुझे इस सूचना में आपके लिए खास कुछ नहीं मिला।",
    "mr": "मला या सूचनेत तुमच्यासाठी खास काही सापडले नाही.",
}
PERSONAL_CHECK = {
    "en": "Please double-check this on the notice itself.",
    "hi": "कृपया सूचना में खुद भी जाँच लें।",
    "mr": "कृपया सूचनेत स्वतः तपासून घ्या.",
}
PERSONAL_RULE = {
    "en": "I found a line with your details:",
    "hi": "मुझे आपके विवरण वाली यह पंक्ति मिली:",
    "mr": "मला तुमचे तपशील असलेली ही ओळ सापडली:",
}


def _evidence(quote: str | None) -> str | None:
    q = _plain_text(quote or "")
    return q[:240] or None


def _plain_text(text: str) -> str:
    """Scanned tables arrive as HTML. Turn cells into comma-separated speech and drop markup, so nothing reads '<td>' aloud."""
    t = re.sub(r"</t[dh]\s*>", ", ", text, flags=re.I)
    t = re.sub(r"</tr\s*>|<br\s*/?>", ". ", t, flags=re.I)
    t = re.sub(r"<[^>]*>?", " ", t)  # tags, including ones whose closing '>' was lost
    t = re.sub(r"[#*|_`~]+", " ", t)
    t = re.sub(r"(,\s*){2,}", ", ", t)
    t = re.sub(r"(\.\s*){2,}", ". ", t)
    return re.sub(r"\s+", " ", t).strip()


def _has_text_layer(reader) -> bool:
    """Sample first, middle and last page: a PDF exported from Word or PowerPoint has real text on every page, a scan has none."""
    n = len(reader.pages)
    try:
        for i in sorted({0, n // 2, n - 1}):
            t = reader.pages[i].extract_text() or ""
            if len(t.strip()) < 150 or "(cid:" in t:
                return False
    except Exception:  # noqa: BLE001
        return False
    return True


def _first_pages(data: bytes, cfg, force_scan: bool = False) -> tuple[bytes, tuple[int, int] | None, int]:
    """Smarter page limit. Scanned pages are paid OCR, so they are capped (MAX_PDF_PAGES). PDFs that already contain
    text are read for free, so they may be longer (MAX_TEXT_PDF_PAGES). Returns (pdf, (pages read, pages in file) if cut, limit)."""
    import io

    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise AppError("This PDF is password protected, so I cannot read it. Please send an unlocked copy.", code="pdf_locked", status_code=415)
        total = len(reader.pages)
        limit = cfg.max_pdf_pages
        if not force_scan and total > limit and _has_text_layer(reader):
            limit = cfg.max_text_pdf_pages
        if total <= limit:
            return data, None, limit
        writer = PdfWriter()
        for page in reader.pages[:limit]:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue(), (limit, total), limit
    except AppError:
        raise
    except Exception:  # noqa: BLE001  (damaged: let prepare_document report it)
        return data, None, cfg.max_pdf_pages


def _prepare(raw: bytes, cfg):
    """Returns (document, truncation info, free text layer or None). Cost and time stay bounded for any upload."""
    data, truncated, limit = raw, None, cfg.max_pdf_pages
    if sniff_mime(raw) == "application/pdf":
        data, truncated, limit = _first_pages(raw, cfg)
    doc = prepare_document(data, max_pages=limit, max_side=cfg.max_image_side_px)
    layer = None
    if limit > cfg.max_pdf_pages:
        layer = pdf_text_layer(doc)
        if layer is None:  # looked like text but is not clean: treat it as a scan so cost stays capped
            data, truncated, limit = _first_pages(raw, cfg, force_scan=True)
            doc = prepare_document(data, max_pages=limit, max_side=cfg.max_image_side_px)
    return doc, truncated, layer


_READ_SLOTS = asyncio.Semaphore(2)  # the free host has little memory: at most two notices are processed at once, others wait their turn



async def _read_notice(data: bytes, lang: str, prof=None) -> dict:
    from .steps import today_local  # imported late: steps imports the bot, which imports this module

    cfg = get_settings()
    try:
        doc, truncated, layer = _prepare(data, cfg)
        ocr = layer or await run_ocr(doc, "mr", cache=False)  # cache=False: the notice text is never saved
    except AppError:
        raise
    except ProviderError as exc:
        raise AppError("I could not read that file just now. Please try again in a minute.", code="read_failed", status_code=502) from exc
    except PermanentError as exc:  # already written for people, e.g. "This file is not a PDF or photo"
        raise AppError(str(exc), code=getattr(exc, "code", "unsupported_file"), status_code=415) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppError("I could not read that file. Please try a clearer photo or PDF.", code="read_failed", status_code=502) from exc

    ref = today_local()
    image = doc.data if doc.mime.startswith("image/") else None
    # Tables and date sheets: let the vision model see the page itself (photos always; short PDFs up to 4 pages).
    has_table = "<table" in ocr.text.lower()
    visual = image if image is not None else (doc.data if doc.mime == "application/pdf" and doc.pages <= 4 and has_table else None)
    if len(ocr.text.strip()) < 10 and image is None:
        raise AppError("I could not find any text in that file. It may be blank or too faint. Please try a clearer scan.", code="no_text", status_code=422)
    ex = None
    try:
        # Hard time limit: the host cuts requests near 100 seconds, so a slow AI answer must fall back to the plain text.
        ex = await asyncio.wait_for(
            gemini_extract(ocr.text, image=visual, image_mime=doc.mime if visual else None, today=ref.isoformat(), profile=prof, lang=lang, force_image=has_table),
            timeout=40,
        )
        title, is_notice, legibility = ex.title, ex.is_notice, ex.legibility
        summary = getattr(ex.summaries, lang) or ex.summaries.en
        raw_actions = [a.model_dump() for a in ex.actions]
    except Exception as exc:  # noqa: BLE001  (Gemini busy, or its answer was cut off on a long document)
        if not ocr.text.strip():
            raise AppError("I could not read any text in that file. Please try a clearer photo or PDF.", code="read_failed", status_code=502) from exc
        # Fallback: the scan was read, so speak the start of the text itself rather than failing.
        title, is_notice, legibility = first_title_line(ocr.text), True, "partial"
        summary = FALLBACK_INTRO[lang] + " " + _plain_text(ocr.text)[:600]
        raw_actions = rule_based_actions(ocr.text, ref)

    actions, needs_check = [], False
    for a in raw_actions:
        v = verify_action(due_date=a.get("due_date"), due_time=a.get("due_time"), amount=a.get("amount"), source_quote=a.get("source_quote"), ocr_text=ocr.text, ref=ref)
        needs_check = needs_check or v.status != "verified"
        actions.append(
            {
                "kind": a.get("kind"),
                "label": a.get("label"),
                "due_date": a.get("due_date"),
                "date_spoken": speak_date_lang(date.fromisoformat(a["due_date"]), lang) if a.get("due_date") and v.status == "verified" else None,
                "due_time": a.get("due_time"),
                "venue": a.get("venue"),
                "status": v.status,
                "reasons": v.reasons,
                # The line of the scan that backs this date. verify_action has already found it in the scan, so it is safe to show.
                "evidence": _evidence(a.get("source_quote")) if v.status == "verified" else None,
            }
        )

    # The spoken summary is AI text too: any date in it must exist in the scan, and any weekday must match its date.
    scan_dates = {f.value for f in find_dates(ocr.text, ref)} | {f.alt_value for f in find_dates(ocr.text, ref) if f.alt_value}
    for f in find_dates(summary, ref):
        if f.value not in scan_dates or (f.weekday_written is not None and f.weekday_written != f.value.weekday()):
            needs_check = True

    # Personal insight: only claims that are really in the scan AND really about this student are spoken.
    personal, personal_spoken = None, ""
    if prof is not None and is_notice:
        ai = getattr(ex, "personal", None)
        matches = verify_personal(ai.matches, ocr.text, prof) if ai is not None else []
        by_rule = False
        if not matches:
            matches, by_rule = rule_based_personal(ocr.text, prof), True
        if not matches:
            personal_spoken = PERSONAL_NONE[lang]
        elif by_rule:
            personal_spoken = f"{PERSONAL_RULE[lang]} " + " ".join(m.quote[:220] + "." for m in matches[:2]) + f" {PERSONAL_CHECK[lang]}"
        else:
            personal_spoken = f"{PERSONAL_HEAD[lang]} " + " ".join(f"{m.what}: {m.detail}." for m in matches[:3]) + f" {PERSONAL_CHECK[lang]}"
        personal = {
            "found": bool(matches),
            "matches": [{"what": m.what, "detail": m.detail, "quote": m.quote, "basis": m.basis} for m in matches],
        }

    if not is_notice:
        spoken = NOT_NOTICE[lang]
    else:
        spoken = f"{personal_spoken} {summary}".strip()
        if needs_check or legibility != "good":
            spoken = f"{spoken} {CAUTION[lang]}"
    flags = safety.check(ocr.text, lang) if is_notice else []
    if flags:  # money and link red flags are spoken before anything else matters (only the serious ones)
        warning = safety.spoken_warning(flags, lang)
        spoken = f"{warning} {spoken}".strip() if warning else spoken
    if truncated:
        spoken = f"{spoken} {PARTIAL[lang].format(n=truncated[0], total=truncated[1])}"
    # Spoken here, returned inside the answer and never stored: personal details must not end up in a cache or a file.
    mp3 = await speak_uncached(spoken, lang)
    return {
        "title": title,
        "is_notice": is_notice,
        "lang": lang,
        "spoken_text": spoken,
        "legibility": legibility,
        "actions": actions,
        "needs_check": needs_check,
        "safety": flags,
        "personal": personal,
        "audio_b64": base64.b64encode(mp3).decode() if mp3 else None,
        "audio_mime": "audio/mpeg",
        "audio_url": None,
        "pages_read": truncated[0] if truncated else getattr(ocr, "pages", None),
        "_ocr_text": ocr.text,  # internal: callers seal or discard this, it is never sent to a client as it is
    }


async def read_notice(data: bytes, lang: str = "en", prof=None) -> dict:
    """Read one notice. At most two run at once (the free host has little memory); others wait their turn."""
    lang = lang if lang in CAUTION else "en"
    async with _READ_SLOTS:
        return await _read_notice(data, lang, prof)


# --------------------------------------------------------------------------- Ask your notice

ASK_CAUTION = {
    "en": "I could not match every date or number in this answer to the notice, so please check it on the notice itself.",
    "hi": "मैं इस उत्तर की हर तारीख या संख्या को सूचना से मिला नहीं पाया, इसलिए कृपया सूचना में खुद जाँच लें।",
    "mr": "मला या उत्तरातील प्रत्येक तारीख किंवा संख्या सूचनेशी जुळवता आली नाही, म्हणून कृपया सूचनेत स्वतः तपासा.",
}
ASK_CLOSEST = {
    "en": "I cannot answer that right now. These are the closest lines in the notice:",
    "hi": "मैं अभी इसका उत्तर नहीं दे सकता। सूचना की सबसे करीबी पंक्तियाँ ये हैं:",
    "mr": "मी आत्ता याचे उत्तर देऊ शकत नाही. सूचनेतील सर्वात जवळच्या ओळी या आहेत:",
}
ASK_NOTHING = {
    "en": "I could not find that in the notice. Please ask the college office.",
    "hi": "मुझे यह सूचना में नहीं मिला। कृपया कॉलेज कार्यालय से पूछें।",
    "mr": "हे मला सूचनेत सापडले नाही. कृपया महाविद्यालयाच्या कार्यालयात विचारा.",
}
_STOP = {"the", "and", "for", "when", "what", "which", "who", "how", "are", "was", "is", "my", "me", "do", "does", "have", "has", "need", "there", "any", "this", "that", "with", "from", "about", "your", "you"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w']+", text.lower().translate(DEVANAGARI_DIGITS)) if len(w) >= 3 and w not in _STOP}


def best_lines(notice_text: str, query: str, n: int = 2) -> list[str]:
    """The lines of the scan that share the most words with the query. Pure code: used as evidence and as the fallback answer."""
    want = _words(query)
    if not want:
        return []
    scored = []
    # Scanned tables arrive as HTML: put every row on its own line, then drop the markup.
    rows = re.sub(r"</tr\s*>|<br\s*/?>", "\n", notice_text, flags=re.I)
    for line in rows.split("\n"):
        line = re.sub(r"</t[dh]\s*>", " | ", line, flags=re.I)  # cells stay separated, so "1234 | Room 12 | 4" survives
        line = re.sub(r"\s+", " ", re.sub(r"<[^>]*>?", " ", line)).strip(" |")
        if len(line) < 6:
            continue
        score = len(want & _words(line))
        if score:
            scored.append((score, -len(line), line[:240]))
    scored.sort(reverse=True)
    return [x[2] for x in scored[:n]]


def is_grounded(answer: str, notice_text: str, ref: date) -> bool:
    """True only if every date, weekday and number the answer states is really in the notice."""
    scan = notice_text.translate(DEVANAGARI_DIGITS)
    scan_dates = {f.value for f in find_dates(notice_text, ref)} | {f.alt_value for f in find_dates(notice_text, ref) if f.alt_value}
    for f in find_dates(answer, ref):
        if f.value not in scan_dates or (f.weekday_written is not None and f.weekday_written != f.value.weekday()):
            return False
    scan_numbers = set(re.findall(r"\d+", scan))
    # Every number counts, even a single digit: "bench 4" must not become "bench 7".
    known = {x.lstrip("0") or "0" for x in scan_numbers}
    return all((n.lstrip("0") or "0") in known for n in re.findall(r"\d+", answer.translate(DEVANAGARI_DIGITS)))


async def ask_notice(question: str, notice_text: str, lang: str = "en") -> dict:
    """Answer a question about ONE notice, from that notice only. Returns {"answer", "verified", "evidence", "source"}.
    The AI proposes an answer; code checks that every date and number in it is in the scan. If the AI is down, the
    closest lines of the scan are quoted instead, so the student always gets something true."""
    from .steps import today_local

    lang = lang if lang in ASK_CAUTION else "en"
    question = re.sub(r"\s+", " ", question or "").strip()[:300]
    if len(question) < 3:
        raise AppError("I did not catch your question. Please ask again.", code="empty_question")
    ref = today_local()
    try:
        answer = _plain_text(await asyncio.wait_for(gemini_answer(question, notice_text[:12000], lang), timeout=40))
        if not answer:
            raise ValueError("empty answer")
        if safety.identifiers(answer) - safety.identifiers(notice_text):
            # A hostile notice can try to make the AI repeat a payment ID or link of its own. The answer may only name ones the notice has.
            raise ValueError("answer names a payment ID, link or number that is not in the notice")
    except Exception:  # noqa: BLE001  (Gemini busy, out of quota, or silent)
        lines = best_lines(notice_text, question)
        text = f"{ASK_CLOSEST[lang]} " + " ".join(f"{ln}." if not ln.endswith((".", "।")) else ln for ln in lines) if lines else ASK_NOTHING[lang]
        return {"answer": text, "verified": False, "evidence": lines, "source": "scan"}
    ok = is_grounded(answer, notice_text, ref)
    if not ok:
        answer = f"{answer} {ASK_CAUTION[lang]}"
    return {"answer": answer, "verified": ok, "evidence": best_lines(notice_text, f"{question} {answer}", 1), "source": "ai"}


# --------------------------------------------------------------------------- Notice change detector

CHANGED_HEAD = {
    "en": "This looks like an update to the notice you sent earlier. What changed:",
    "hi": "यह आपकी भेजी हुई पिछली सूचना का अपडेट लगता है। क्या बदला:",
    "mr": "ही तुम्ही आधी पाठवलेल्या सूचनेचे अपडेट वाटते. काय बदलले:",
}
CHANGED_LINE = {
    "en": "{label}: it was {old}, now {new}.",
    "hi": "{label}: पहले {old} थी, अब {new} है।",
    "mr": "{label}: आधी {old} होती, आता {new} आहे.",
}
CHANGED_TAIL = {
    "en": "Both notices were checked against their scans. Please confirm with the college office which one is current.",
    "hi": "दोनों सूचनाएँ अपने स्कैन से जाँची गई हैं। कृपया कॉलेज कार्यालय से पूछें कि कौन सी ताज़ा है।",
    "mr": "दोन्ही सूचना त्यांच्या स्कॅनशी तपासल्या आहेत. कृपया महाविद्यालयाच्या कार्यालयात विचारा की कोणती ताजी आहे.",
}


def diff_actions(old: list[dict], new: list[dict]) -> list[dict]:
    """What changed between two reads of (versions of) a notice. Pure code, no AI.
    A change is claimed only when BOTH sides have a date that was verified against its own scan and the two lines are about the
    same thing (their words overlap). Anything unmatched or unverified claims nothing, which is the safe direction."""
    usable = [a for a in old if a.get("due_date") and a.get("status") == "verified"]
    taken: set[int] = set()
    out = []
    for n in new:
        if not n.get("due_date") or n.get("status") != "verified":
            continue
        nw = _words(n["label"])
        best, best_i, best_score = None, -1, 0.0
        for i, o in enumerate(usable):
            if i in taken:
                continue
            ow = _words(o["label"])
            score = len(nw & ow) / max(1, len(nw | ow))
            if score > best_score:
                best, best_i, best_score = o, i, score
        if best is None or best_score < 0.4:
            continue
        taken.add(best_i)
        if (best["due_date"], best.get("due_time") or None) != (n["due_date"], n.get("due_time") or None):
            out.append({"label": n["label"], "old": (best["due_date"], best.get("due_time")), "new": (n["due_date"], n.get("due_time"))})
    return out


def change_text(changes: list[dict], lang: str) -> str:
    """The message that tells the student what changed, with each date written out in their language."""
    from .dates import speak_date_lang

    lang = lang if lang in CHANGED_HEAD else "en"

    def when(pair) -> str:
        iso, hhmm = pair
        return speak_date_lang(date.fromisoformat(iso), lang) + (f", {hhmm}" if hhmm else "")

    lines = [CHANGED_HEAD[lang]] + [f"- {CHANGED_LINE[lang].format(label=c['label'], old=when(c['old']), new=when(c['new']))}" for c in changes]
    return "\n".join(lines + [CHANGED_TAIL[lang]])
