"""Words students hear and read. Short, screen-reader friendly, no emojis
(TalkBack reads every emoji aloud by name), dates always spoken in full."""

from __future__ import annotations

from datetime import date

from ..config import get_settings
from ..models import Notice, NoticeAction, Verification
from .dates import MONTH_NAMES, speak_date

T = {
    "official": {
        "en": "Official notice from {college}.",
        "mr": "{college} कडून अधिकृत सूचना.",
        "hi": "{college} की आधिकारिक सूचना.",
    },
    "todo": {"en": "What you need to do:", "mr": "तुम्हाला काय करायचे आहे:", "hi": "आपको क्या करना है:"},
    "approved": {"en": "Checked and approved by {who} on {when}.", "mr": "{who} यांनी {when} रोजी तपासून मंजूर केले.", "hi": "{who} ने {when} को जाँचकर मंज़ूर किया."},
    "page": {"en": "Full accessible notice: {link}", "mr": "संपूर्ण सूचना: {link}", "hi": "पूरी सूचना: {link}"},
    "check_needed": {
        "en": "Please confirm this date with the office.",
        "mr": "ही तारीख कार्यालयाकडून खात्री करून घ्या.",
        "hi": "कृपया यह तारीख कार्यालय से पक्की कर लें.",
    },
    "menu": {
        "en": "Reply NOW for what is on, CATCH to save a deadline the teacher said, DEADLINES for dates, INBOX for your notices. Send a photo captioned BOARD to save it. "
        "Ask a question by text or voice. LANG MR for Marathi, LANG HI for Hindi. STOP to unsubscribe.",
        "mr": "आत्ता काय आहे यासाठी NOW, शिक्षकांनी सांगितलेली मुदत जतन करण्यासाठी CATCH, येणाऱ्या तारखांसाठी DEADLINES पाठवा, तुमच्या सूचनांसाठी INBOX. कोणत्याही सूचनेचा फोटो पाठवून तपासा. "
        "प्रश्न लिहून किंवा बोलून विचारा. English साठी LANG EN, हिंदीसाठी LANG HI. बंद करण्यासाठी STOP.",
        "hi": "अभी क्या है यह जानने के लिए NOW, टीचर की बताई तारीख सहेजने के लिए CATCH, आने वाली तारीखों के लिए DEADLINES भेजें, अपनी सूचनाओं के लिए INBOX. किसी भी सूचना की फोटो भेजकर जाँचें. "
        "सवाल लिखकर या बोलकर पूछें. English के लिए LANG EN, मराठी के लिए LANG MR. बंद करने के लिए STOP.",
    },
}


def t(key: str, lang: str, **kw) -> str:
    table = T[key]
    return table.get(lang, table["en"]).format(**kw)


def short_date(d: date) -> str:
    return f"{d.day} {MONTH_NAMES[d.month - 1]}"


def action_line(a: NoticeAction, lang: str) -> str:
    parts = [a.label]
    if a.due_date:
        when = speak_date(a.due_date)
        if a.due_time:
            when += f" at {a.due_time}"
        parts.append(when)
    if a.venue:
        parts.append(a.venue)
    if a.amount:
        parts.append(a.amount)
    line = ", ".join(parts) + "."
    if a.verification == Verification.needs_check:
        line += " " + t("check_needed", lang)
    return line


def notice_link(notice: Notice, token: str | None = None) -> str:
    base = get_settings().frontend_base_url.rstrip("/")
    return f"{base}/n/{notice.public_id}" + (f"?t={token}" if token else "")


def compose_notice_message(notice: Notice, college_name: str, lang: str, approver: str | None, token: str | None) -> str:
    lines = [t("official", lang, college=college_name), "", notice.title or "Notice"]
    if notice.changes_from_previous:
        lines += ["", "What changed:"] + [f"- {c}" for c in notice.changes_from_previous[:5]]
    acts = [a for a in notice.actions if a.due_date or a.venue or a.amount]
    if acts:
        lines += ["", t("todo", lang)] + [f"{i}. {action_line(a, lang)}" for i, a in enumerate(acts[:6], 1)]
    if approver and notice.approved_at:
        lines += ["", t("approved", lang, who=approver, when=short_date(notice.approved_at.date()))]
    lines += ["", t("page", lang, link=notice_link(notice, token))]
    return "\n".join(lines)


def compose_reminder(action: NoticeAction, notice: Notice, days_left: int, lang: str, token: str | None) -> str:
    if days_left <= 0:
        lead = {"en": "Today:", "mr": "आज:", "hi": "आज:"}.get(lang, "Today:")
    elif days_left == 1:
        lead = {"en": "Tomorrow:", "mr": "उद्या:", "hi": "कल:"}.get(lang, "Tomorrow:")
    else:
        lead = {"en": f"In {days_left} days:", "mr": f"{days_left} दिवसांनी:", "hi": f"{days_left} दिन में:"}.get(lang, "")
    return f"Reminder. {lead} {action_line(action, lang)}\nFrom: {notice.title}\n{notice_link(notice, token)}"
