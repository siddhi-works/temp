# Study guide: understand the code in an afternoon

For the team. Read this once, open the files in the order below, and you can explain any part to a judge.

## The 30-second explanation

> A blind student shares a scanned notice. We read the scan, ask an AI to pull out the dates, and then **check every date in code against the scan** before speaking it. If the student saved their class on their phone, we also tell them what applies to *them*. Nothing about the notice is stored.

## Four ideas to be able to explain

1. **Trust boundary.** The AI proposes, our code verifies. That is the product's integrity.
2. **Privacy by omission.** We avoid risk by not keeping data, not by guarding it.
3. **Failover.** Every outside service has a plan B and a plan C.
4. **Accessibility as engineering.** Contrast is computed, behaviour is tested, and the student controls the look.

## Reading order (backend)

| # | File | Read it for |
|---|---|---|
| 1 | `app/main.py` | How the app starts, security headers, request ids |
| 2 | `app/config.py` | Every setting in one place; production checks |
| 3 | `app/routers/me.py` | **`_read_notice`**: the whole student flow in one function |
| 4 | `app/pipeline/dates.py` | **`verify_action`**: the trust boundary. Find dates, weekdays, quotes |
| 5 | `app/pipeline/profile.py` | Personal insight: `focus_text`, `verify_personal` |
| 5b | `app/pipeline/reader.py`, `app/pipeline/safety.py` | The one reading engine the website and WhatsApp both call, and the fake-notice safety check |
| 6 | `app/providers/ocr.py` | Reading scans: text layer, then Sarvam, then Gemini |
| 7 | `app/providers/llm.py` | The AI call: schema, prompts, key and model failover |
| 8 | `app/providers/tts.py` | Speech chain, and `speak_uncached` (the privacy path) |
| 9 | `app/providers/base.py` | Circuit breakers, retries, the spend cap |
| 10 | `app/security.py` | Tokens, hashing, encryption |
| 11 | `tests/test_profile.py`, `tests/test_security.py` | The guarantees, as executable examples |

## Reading order (frontend)

| # | File | Read it for |
|---|---|---|
| 1 | `app/me/StudentApp.tsx` | Screen router (hash routes) and the session |
| 2 | `app/me/ReadScreen.tsx` | The main screen: upload, player, "For you" |
| 3 | `app/me/Shell.tsx` | The frame every screen uses (focus, title, language buttons) |
| 4 | `app/me/me.css` | The four palettes and the spacing rules |
| 5 | `lib/i18n.ts` | Translations and friendly errors |
| 6 | `lib/display.ts` | Display choices saved on the phone |
| 7 | `next.config.mjs` | Security headers and the proxy |
| 8 | `scripts/a11y-student.mjs` | The accessibility test |

## Terms to know

- **OCR**: turning a picture of text into text.
- **Text layer**: a PDF that already contains real text (free to read).
- **Schema-enforced JSON**: the AI must answer in a fixed shape; anything else is rejected.
- **Circuit breaker**: after repeated failures a service is skipped for a while.
- **PWA**: a website that installs like an app.
- **CSP**: a browser rule that lists exactly which sources the page may load.
- **axe**: a tool that finds accessibility problems automatically.

## A 3-minute demo

1. *(20 s)* The problem: show a scanned notice. "A screen reader reads this as nothing."
2. *(60 s)* Open the app on the phone. Tap **Start**. Choose the notice. Listen. Point out the date and the warning on anything it could not verify.
3. *(40 s)* Settings: add class and division. Read a seating or schedule PDF: **For you**.
4. *(30 s)* Switch to Marathi: the interface, the voice, the date words all change.
5. *(30 s)* "Nothing is stored." Show the About page.

If the internet or an AI quota fails mid-demo, say so: the fallback quoting the scan is a feature.

## Questions to answer before you go on stage

Read `docs/JUDGES_QA.md` aloud once. For each answer, be able to point at the file that proves it.
