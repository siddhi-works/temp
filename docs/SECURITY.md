# Security and privacy

What we protect, from whom, how, and how we know it works. Every control marked **(tested)** has an automated test in `code/backend/tests/`.

## 1. What we protect

1. **Students' trust in what they hear.** A wrong deadline is the worst failure.
2. **Students' details** (class, roll number) and, in seating lists, **other students' names**.
3. **Our API keys and budget.**
4. **Availability** during a live demo.

## 2. Threat model

| Threat | Control | Proof |
|---|---|---|
| AI invents or garbles a date | Every date, weekday, amount, time and quote is re-checked in code; failures are spoken with a warning | `test_dates`, `test_flow`, `test_profile` |
| AI claims a seat or row that belongs to someone else | The quoted line must name the student's own roll number, name or class; numbers must appear in that line | `test_profile` |
| A malicious notice tries to give the AI orders (prompt injection) | Scan text is wrapped as untrusted data; output must match a schema; the student's details are stripped of markup and length-capped; nothing the AI returns is trusted without verification | `test_profile` (sanitising), verification tests |
| Forged or stolen student token | Signed (HMAC) with expiry; tampered tokens rejected; no passwords to steal | **(tested)** `test_a_tampered_token_is_rejected` |
| One student reading another's data | Every record is checked against the token's student; a wrong owner gets the same "not found" as a missing record | **(tested)** `test_a_student_cannot_confirm_another_students_deadline` |
| Stranger takes over first-time admin setup | The whole college side is **not mounted** by default (no setup endpoint exists); when switched on, setup needs a secret token | **(tested)** both ways |
| Stranger drains the AI budget | Access code on sign-up, per-student read limit, generous per-client sign-up limit, hard spend cap | **(tested)** access code and rate limits |
| Rate limits unfairly block a classroom on one Wi-Fi address | Reads are limited per student, not per address | **(tested)** |
| Huge, empty or disguised files | 15 MB limit, empty check, type from the first bytes (an `.exe` renamed `.pdf` is refused), page caps, locked PDFs refused | **(tested)** |
| Data left behind | Student reads bypass the OCR and audio caches; audio is returned inline; the profile is never written. On WhatsApp the scan text is kept encrypted for one hour only, and the file and audio are never kept | **(tested)** `test_personal_read_speaks_the_verified_seat_and_stores_nothing`, `test_nothing_about_the_notice_is_stored_except_a_sealed_copy_for_one_hour` |
| Someone photographs the WhatsApp QR and links their own number to a student's account | The QR holds a one-time code only; linking also needs a 4-digit code that WhatsApp sends to the claiming phone and the signed-in website session types in. Five wrong tries cancel it; the website shows the last four digits of the claimant | **(tested)** `test_someone_who_photographs_the_qr_cannot_link_their_number`, `test_five_wrong_codes_cancel_the_link` |
| A forged or old WhatsApp webhook | Meta's X-Hub-Signature-256 is checked on the raw body; production refuses to start without the app secret; duplicates are dropped by a unique message id | `test_webhook_duplicates_are_ignored`, `test_security` |
| AI answers a question with a date or number that is not in the notice | Every date, weekday and number in the answer is checked against the scan; failures are marked unverified; if the AI is down the closest scan lines are quoted | **(tested)** `test_grounding_check_rules` |
| Someone floods the WhatsApp number to spend the AI budget | A first message needs the access code when one is set; a daily limit per student; the existing spend cap | **(tested)** `test_the_access_code_gates_a_new_whatsapp_student`, `test_the_daily_limit_stops_a_flood` |
| Personal data cached by browsers or proxies | `Cache-Control: no-store` on personal responses | **(tested)** |
| Clickjacking, content sniffing, referrer leaks, third-party scripts | Strict Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, HSTS, microphone limited to this site | **(tested)** headers; CSP checked in a real browser |
| Leaked secrets | Environment variables only; `.env` ignored by git and Docker; production refuses to start with placeholder secrets | **(tested)** |
| Internals leaked in errors | One error shape (`code`, `message`); no traces or SQL in responses | **(tested)** |
| A provider outage or quota end | Circuit breakers, key and model failover, rule-based fallback | `test_failures` |

## 3. What is stored (data inventory)

| Data | Student read path | Saved deadline | Staff desk path |
|---|---|---|---|
| The notice file | Never stored | n/a | Stored (the college's own notice) |
| Scanned text | Never stored | n/a | Cached by file hash |
| Spoken audio | Never stored | n/a | Cached |
| Student's details (class, roll no.) | Browser only; sent with the request, not written | n/a | n/a |
| Identity | Random id and language, no name, no phone. A student who links WhatsApp also has their phone number, encrypted, until they disconnect or send DELETE | Same | Staff names; tokens hashed |
| A notice read on WhatsApp | Encrypted copy of the scan text for one hour (so the student can ask about it), then erased; erased at once by DELETE or by the next notice | n/a | n/a |
| Link codes | Only hashes of the QR code and the 4-digit code, valid 10 minutes | n/a | n/a |
| Logs | Method, path, status, time, request id (no bodies, no details) | Same | Same |

The student only creates a stored record when they **choose to save a deadline**, and only for that deadline. The About page says this in all three languages.

## 4. Secrets

- Keys live in environment variables on the host. They are not in the repository, the Docker image or the logs.
- Local `.env` files are git-ignored and Docker-ignored.
- **Incident, handled:** during development a Gemini key was pasted into `.env.example`. GitHub's push protection blocked the push before it left the laptop. We moved the key to `.env`, blanked the example and amended the unpushed commit. Lesson kept: examples hold names only, and the setup guide says so.
- Production (`ENV=prod`) refuses to start if `SECRET_KEY` or `PHONE_ENCRYPTION_KEY` is missing.

## 5. Known gaps (we would rather say so)

- The Content-Security-Policy allows inline scripts, because Next.js inlines a bootstrap script. A nonce-based policy is the next hardening step.
- Anonymous sign-up is intentional (no accounts), so abuse control rests on rate limits, an optional access code and the spend cap, not on identity.
- Rate limits live in memory, so they reset on restart and are per instance.
- No third-party penetration test has been done.
- India's Digital Personal Data Protection Act applies to personal data we process. Our design avoids storing any for reads, but we have not had legal review.
