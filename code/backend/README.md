# Suchana Setu backend

The college publishes a notice once. Every blind student gets it on WhatsApp as a
voice note in Marathi, Hindi or English plus a page their screen reader can
navigate, with dates checked against the scan and approved by a person, and
reminders before each deadline. Anyone can check whether a forwarded photo is the
official, latest version.

## Run it

```bash
cd backend
pip install -r requirements-dev.txt     # ffmpeg must be installed for voice notes
cp .env.example .env                    # keys are optional for local work
uvicorn app.main:app --reload           # API on http://localhost:8000, docs at /docs
python -m pytest                        # 49 tests, ~8 s, no keys or network needed
python -m scripts.check_providers       # after adding keys: one tiny real call per provider
```

First-time setup (creates the college and the admin sign-in token, shown once):

```bash
curl -X POST localhost:8000/api/setup/bootstrap -H 'content-type: application/json' \
  -d '{"college_code":"MODERN","college_name":"Modern College, Pune","admin_name":"Exam Section Head","admin_office":"Exam Section"}'
```

WhatsApp: in the Meta app, set the webhook URL to `https://<public-host>/webhooks/whatsapp`
with verify token `WHATSAPP_VERIFY_TOKEN`, subscribe to `messages`. For local work, expose
port 8000 with `cloudflared tunnel --url http://localhost:8000`.

## How a notice flows

```
Desk upload ──► process_notice ──────────────────────────► needs_review ──(staff approve)──► publish_notice ──► deliver × students
                 1. check file (type, size, pages, rotate)                                     voice notes        voice note + text
                 2. OCR: Sarvam ─► Gemini (cached by hash)                                     (cached)           24h window or template
                 3. understand: Gemini JSON ─► rule-based fallback                             reminders          read receipts
                 4. verify every date against the scan                                         old version superseded
WhatsApp in ──► handle_inbound ─► JOIN / LANG / DEADLINES / INBOX / STOP / question (text or voice) / forwarded photo check
```

## What makes it reliable

| Failure | What happens |
|---|---|
| Sarvam down or out of credit | OCR falls back to Gemini; voice falls back to Google TTS; then text-only. Notice is still published. |
| Sarvam credit (₹70) | Hard cap (`SARVAM_BUDGET_PAISE`, default ₹55). Cost is reserved before each call, atomically. Every OCR and audio result is cached by content hash, so rehearsals are free. |
| Gemini down | Two retries, then a rule-based extractor finds every date; all flagged for human review. |
| Provider keeps failing | Circuit breaker opens for 60 s so the fallback is used instantly instead of waiting on timeouts. |
| Server crash mid-job | Jobs live in the database with a lease; another worker picks them up. Paid steps are checkpointed, so OCR is never paid twice. |
| Job keeps failing | Exponential backoff (5 s to 20 min), then `dead`. The notice shows a plain error and a Retry button (`POST /api/notices/{id}/retry`). |
| AI invents or misreads a date | Date must be found in the scan, in the quoted line, with a consistent weekday, plausible, Indian day/month order; time and amount must also appear. Otherwise it is flagged and approval is blocked until a person confirms it. |
| Student hasn't messaged in 24 h | Uses an approved template if configured; otherwise holds the message and sends it automatically the next time the student writes. |
| Meta retries a webhook | Duplicate message ids are dropped. Statuses arriving out of order never downgrade "read". |
| Forged webhook | `X-Hub-Signature-256` verified with the app secret (enforced in prod). |
| Bad uploads | Type sniffed from bytes, size and page limits, corrupt or encrypted PDFs and tiny images rejected before any paid call. |
| Old notice forwarded around | "Check a forward" matches the photo to the series and replies with what changed. |

## Privacy

Phone numbers are encrypted at rest (Fernet) and looked up by HMAC; they never appear
in job payloads or logs. Students join by sending `JOIN <code>` and get a consent
message naming the AI services; `STOP` opts out. Photos uploaded to the public check
are deleted after matching. Source scans are visible to staff only.

## API (all JSON errors look like `{"error": {"code", "message", "request_id"}}`)

| Area | Endpoints |
|---|---|
| Setup and people | `POST /api/setup/bootstrap`, `GET /api/me`, `POST/GET /api/staff`, `DELETE /api/staff/{id}`, `POST/GET /api/students` |
| Desk | `POST /api/notices` (multipart: file, language_hint, audience_tags, supersedes_id), `GET /api/notices`, `GET /api/notices/{id}`, `GET /api/notices/{id}/source`, `PATCH /api/notices/{id}`, `POST /api/notices/{id}/approve`, `/reject`, `/retry`, `GET /api/notices/{id}/jobs` |
| Review | `GET /api/review-queue` (staff and Rotaract verifiers) |
| Student | `GET /api/me/inbox?t=`, `PATCH /api/me/preferences?t=` (signed link from WhatsApp, no password) |
| Public | `GET /n/{public_id}` (accessible HTML, no JS needed), `GET /api/public/notices/{public_id}`, `.../calendar.ics`, `POST /api/public/check`, `GET /api/public/check/{id}`, `GET /media/audio/...` |
| Impact | `GET /api/impact`, `GET /api/impact/log.csv` (NAAC 7.1.7 evidence), `GET /api/impact/usage` (Sarvam spend, circuit states) |
| Live | `GET /api/events?token=` (Server-Sent Events: notice stages, deliveries, metrics) |
| System | `GET /health`, `GET /ready`, `GET/POST /webhooks/whatsapp` |

## Known limits and next steps

- Tables are created on startup. Add Alembic migrations before the schema changes on a live pilot.
- Event bus and budget lock are per process. Run one API process (the default), or move them to Postgres `LISTEN/NOTIFY` and a row lock.
- WhatsApp media is re-uploaded per delivery; cache media ids per audio file at larger scale.
- Handwritten Devanagari is the weakest OCR case for every vendor: it is always flagged for review.
- Maths in question papers (MathML) is not handled yet.


## Speed and API spend

- Gemini calls run with hidden "thinking" off, short prompts and output caps. Small jobs (deadline label, short answers,
  board-memory answers) use `GEMINI_LIGHT_MODEL`; if that name is rejected the main model is used.
- A notice's extraction no longer returns the whole notice again (`body_markdown`): the page is built from the OCR text.
  The photo is sent to Gemini only when the OCR text is short or contains `[unclear]`.
- PDFs with a clean text layer are read for free (`PDF_TEXT_LAYER=true`); scans, odd fonts and mixed files still go to OCR.
- Voice notes are made only for English plus the languages the college's students use (`PUBLISH_LANGS=en`). Audio, OCR and
  answers are cached, so repeats cost nothing.
- Publishing to 301 students with timetables: 6,380 -> 2,372 SQL statements, 6.2 s -> 2.8 s
  (`pytest tests/bench_publish.py -s`).
