"""Safety check for a notice: money and link red flags, found by code (no AI).

Fake "fee" and "form" notices spread in college WhatsApp groups. A blind student cannot see that a payment goes to a
personal UPI ID or that a link is a shortener. This reads the scan text and says what it finds, in plain words:

- It never calls a notice "fake" or "a scam". Real notices also ask for fees and contain links. It states a fact
  ("this asks you to pay to the UPI ID x at ybl") and what to do ("confirm it with the college office before paying").
- A caution is spoken with the reading. Quieter findings (an ordinary link) are shown but not spoken.
- Official-looking domains (.ac.in, .edu.in, .gov.in, .nic.in) are not flagged as links, but only when the
  whole host ends with them, so "college.ac.in.pay-now.xyz" is still flagged.
"""

from __future__ import annotations

import re

from .dates import DEVANAGARI_DIGITS

# A UPI ID is name@handle with NO dot after the handle ("asha@ybl"). An email always has one ("office@college.edu.in").
UPI_RE = re.compile(r"(?<![\w.@-])([a-z0-9][a-z0-9._-]{1,40}@[a-z]{2,20})\b(?![\w@-])(?!\.[a-z0-9])", re.I)
# A mobile number counts as a payment target only when a payment word comes right before it. A "call 98... for queries" line does not.
PAY_TO_PHONE_RE = re.compile(r"\b(?:pay(?:ment)?|send|transfer|gpay|google\s*pay|phonepe|paytm)\b[^\n]{0,45}?(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)", re.I)
NOT_PAYMENT_RE = re.compile(r"\b(?:call|contact|helpline|enquir|inquir|queries|query|whatsapp)\b", re.I)
SHORT_HOSTS = r"(?:bit\.ly|tinyurl\.com|t\.co|cutt\.ly|rb\.gy|shorturl\.at|is\.gd|wa\.me|t\.me|goo\.gl|ow\.ly|tiny\.cc)"
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s)>\]\"']+"
    r"|\b" + SHORT_HOSTS + r"/[^\s)>\]\"']+"
    r"|\b[a-z0-9-]{2,}\.(?:xyz|top|click|link|live|site|online|icu|buzz|shop|cc|tk|ml|ga|cf)\b[^\s)>\]\"']*",
    re.I,
)
SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "cutt.ly", "rb.gy", "shorturl.at", "is.gd", "wa.me", "t.me", "goo.gl", "ow.ly", "tiny.cc")
OFFICIAL_SUFFIXES = (".ac.in", ".edu.in", ".gov.in", ".nic.in", ".edu")
SECRET_RE = re.compile(
    r"(?:share|send|tell|give|provide|enter|submit|forward|reply)[^.\n]{0,40}?\b(otp|cvv|pin|password|passcode)\b"
    r"|\b(otp|cvv|pin|password|passcode)\b[^.\n]{0,25}?(?:share|send|tell|give|provide|enter|required|needed)",
    re.I,
)
# Hindi and Marathi: no \b here, because Python does not treat Devanagari vowel signs as word characters.
SECRET_DEV_RE = re.compile(
    r"(?:ओटीपी|पिन|पासवर्ड)[^.\n।]{0,25}?(?:बताएं|बताएँ|बताओ|बताइए|भेजें|भेजो|साझा|दें|सांगा|पाठवा|शेअर|द्या|द्यावा)"
    r"|(?:बताएं|बताएँ|बताओ|भेजें|साझा|सांगा|पाठवा|शेअर|द्या)[^.\n।]{0,25}?(?:ओटीपी|पिन|पासवर्ड)"
)
# "Never share your OTP" is advice, not a request. A negation just before or inside the match means no flag.
NEGATION_RE = re.compile(r"\b(?:do\s*not|don'?t|dont|never|not|no\s+one|nobody|without)\b|कभी\s*(?:न|नहीं)|मत\b|नका|कधीही|कधी\s*नाही|नये|न\s+करें|नहीं\s+देना|नाही", re.I)
URGENT_PAY_RE = re.compile(r"(?:pay|fee|fine|penalty|deposit)[^.\n]{0,60}?(?:within|before|in)\s+(\d{1,2})\s*(?:hours?|hrs?|minutes?|mins?)", re.I)

TEXT = {
    "upi": {
        "en": "This notice asks you to pay to the UPI ID {x}. Before you pay, confirm this ID with the college office.",
        "hi": "इस सूचना में UPI आईडी {x} पर पैसे भेजने को कहा गया है। पैसे भेजने से पहले कॉलेज कार्यालय से यह आईडी पक्की कर लें।",
        "mr": "या सूचनेत UPI आयडी {x} वर पैसे पाठवायला सांगितले आहे. पैसे पाठवण्यापूर्वी महाविद्यालयाच्या कार्यालयात हा आयडी खात्री करून घ्या.",
    },
    "phone": {
        "en": "This notice asks you to pay to a mobile number, {x}. Colleges usually take fees at the office or through their own account. Confirm this number with the office before paying.",
        "hi": "इस सूचना में मोबाइल नंबर {x} पर पैसे भेजने को कहा गया है। कॉलेज आम तौर पर फीस कार्यालय में या अपने खाते से लेते हैं। पैसे भेजने से पहले कार्यालय से यह नंबर पक्का कर लें।",
        "mr": "या सूचनेत मोबाइल नंबर {x} वर पैसे पाठवायला सांगितले आहे. महाविद्यालये सहसा शुल्क कार्यालयात किंवा स्वतःच्या खात्यात घेतात. पैसे पाठवण्यापूर्वी कार्यालयात हा नंबर खात्री करा.",
    },
    "secret": {
        "en": "This notice asks for an OTP, PIN or password. Never share these. A real college does not ask for them in a notice.",
        "hi": "इस सूचना में OTP, PIN या पासवर्ड माँगा गया है। इन्हें कभी साझा न करें। असली कॉलेज सूचना में इन्हें नहीं माँगता।",
        "mr": "या सूचनेत OTP, PIN किंवा पासवर्ड मागितला आहे. हे कधीही देऊ नका. खरे महाविद्यालय सूचनेत हे मागत नाही.",
    },
    "urgent": {
        "en": "This notice gives only {x} hours to pay. Confirm the amount and the deadline with the college office before paying.",
        "hi": "इस सूचना में पैसे भेजने के लिए सिर्फ़ {x} घंटे दिए गए हैं। पैसे भेजने से पहले राशि और समय सीमा कार्यालय से पक्की कर लें।",
        "mr": "या सूचनेत पैसे भरण्यासाठी फक्त {x} तास दिले आहेत. पैसे भरण्यापूर्वी रक्कम आणि मुदत कार्यालयात खात्री करा.",
    },
    "lookalike": {
        "en": "This notice has a link, {x}, that looks like a college address but is not one. Do not enter any details.",
        "hi": "इस सूचना में लिंक {x} है जो कॉलेज के पते जैसा दिखता है पर है नहीं। कोई जानकारी न भरें।",
        "mr": "या सूचनेत लिंक {x} आहे, जी महाविद्यालयाच्या पत्त्यासारखी दिसते पण तशी नाही. कोणतीही माहिती भरू नका.",
    },
    "short_link": {
        "en": "This notice has a shortened link, {x}. A short link hides where it goes. Do not enter any details until the college office confirms it.",
        "hi": "इस सूचना में छोटा किया हुआ लिंक {x} है। छोटा लिंक छिपा देता है कि वह कहाँ जाता है। कार्यालय से पुष्टि होने तक कोई जानकारी न भरें।",
        "mr": "या सूचनेत छोटी केलेली लिंक {x} आहे. छोटी लिंक ती कुठे जाते ते लपवते. कार्यालयाने खात्री करेपर्यंत कोणतीही माहिती भरू नका.",
    },
    "link": {
        "en": "This notice has a link to {x}. Check with the college office that it is theirs before you enter any details.",
        "hi": "इस सूचना में {x} का लिंक है। कोई जानकारी भरने से पहले कार्यालय से पूछ लें कि यह उन्हीं का है।",
        "mr": "या सूचनेत {x} ची लिंक आहे. कोणतीही माहिती भरण्यापूर्वी ती त्यांचीच आहे का ते कार्यालयात विचारा.",
    },
}
HEAD = {"en": "Safety check.", "hi": "सुरक्षा जाँच।", "mr": "सुरक्षा तपासणी."}
CAUTION_KINDS = {"upi", "phone", "secret", "urgent", "short_link", "lookalike"}


def identifiers(text: str) -> set[str]:
    """Payment IDs, link hosts and mobile numbers in `text`, lower-case. Used to check an answer adds none the notice lacks."""
    t = re.sub(r"<[^>]*>?", " ", text.translate(DEVANAGARI_DIGITS))
    out = {m.group(1).lower() for m in UPI_RE.finditer(t)}
    for m in URL_RE.finditer(t):
        host = _host(m.group(0).rstrip(".,;"))
        if host and not host.replace(".", "").isdigit():
            out.add(host)
    out |= set(re.findall(r"(?<!\d)[6-9]\d{9}(?!\d)", t))
    return out


def speak_address(x: str) -> str:
    """A screen reader and a voice read "@" badly. "asha@ybl" is spoken "asha at ybl"."""
    return x.replace("@", " at ").replace(".", " dot ")


def _host(url: str) -> str:
    u = re.sub(r"^[a-z]+://", "", url.strip().lower())
    return re.split(r"[/?#:]", u, maxsplit=1)[0].strip(".")


def check(text: str, lang: str = "en") -> list[dict]:
    """Findings: [{"kind", "level": "caution" | "info", "text": localized sentence, "evidence": the words in the notice}]."""
    lang = lang if lang in HEAD else "en"
    t = re.sub(r"<[^>]*>?", " ", text.translate(DEVANAGARI_DIGITS))  # scanned tables arrive as HTML
    t = re.sub(r"[ \t]+", " ", t)
    found: list[dict] = []
    seen: set[tuple] = set()

    def add(kind: str, x: str, evidence: str, spoken: str | None = None) -> None:
        key = (kind, x.lower())
        if key in seen:
            return
        seen.add(key)
        found.append(
            {"kind": kind, "level": "caution" if kind in CAUTION_KINDS else "info", "text": TEXT[kind][lang].format(x=spoken or x), "evidence": evidence.strip()[:120]}
        )

    for m in list(SECRET_RE.finditer(t)) + list(SECRET_DEV_RE.finditer(t)):
        around = t[max(0, m.start() - 25) : m.end() + 25]
        if NEGATION_RE.search(around):
            continue
        add("secret", "otp", m.group(0))
    for m in UPI_RE.finditer(t):
        add("upi", m.group(1), m.group(0), speak_address(m.group(1)))
    for m in PAY_TO_PHONE_RE.finditer(t):
        if NOT_PAYMENT_RE.search(m.group(0)):
            continue
        add("phone", m.group(1), m.group(0)[-60:], " ".join(m.group(1)))  # digits one by one, so they are read clearly
    for m in URGENT_PAY_RE.finditer(t):
        hrs = int(m.group(1))
        unit = m.group(0).lower()
        if hrs <= 48 and ("hour" in unit or "hr" in unit):
            add("urgent", str(hrs), m.group(0))
    for m in URL_RE.finditer(t):
        raw = m.group(0).rstrip(".,;")
        host = _host(raw)
        if not host or host.replace(".", "").isdigit():
            continue
        if any(host == s or host.endswith("." + s) for s in SHORTENERS):
            add("short_link", host, raw, speak_address(host))
        elif any(host.endswith(sfx) for sfx in OFFICIAL_SUFFIXES):
            continue
        elif any(sfx + "." in host + "." and not host.endswith(sfx) for sfx in OFFICIAL_SUFFIXES):
            add("lookalike", host, raw, speak_address(host))  # "college.ac.in.pay-now.xyz": an official name inside a different domain
        else:
            add("link", host, raw, speak_address(host))
    found.sort(key=lambda f: f["level"] != "caution")  # cautions first
    return found[:5]


def spoken_warning(flags: list[dict], lang: str) -> str:
    """The sentence added to the voice reading: only cautions, at most two, so a normal notice stays short."""
    lang = lang if lang in HEAD else "en"
    cautions = [f["text"] for f in flags if f["level"] == "caution"][:2]
    return f"{HEAD[lang]} " + " ".join(cautions) if cautions else ""
