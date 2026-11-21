# Handoff: where the project stands and how to carry on

Written for a teammate who has not seen the project. Read this first, then `README.md`, then `docs/STUDY_GUIDE.md`. No secrets are in this file: it names them and says where they live.

## 1. What this is, in three sentences

Samyukta reads a photo or PDF of a college notice aloud in English, Hindi or Marathi for blind, low-vision and dyslexic students, and checks every date against the scan before saying it. A student can also save their class on their own phone to hear only what applies to them. Nothing about a read is stored. Team MolecuLens, Naya Bharat 2047 hackathon, problem 5.3.

## 2. Where everything is

| Thing | Where | Used for |
|---|---|---|
| Code | GitHub `siddhi-works/temp`, branch `main` | Source; Render deploys from it |
| Live app | https://temp-6vp3.onrender.com/me | The demo. Free hosting: sleeps when idle |
| Hosting | Render, web service `temp`, root directory `code`, Docker | Runs API and website in one container |
| Database | Neon (Postgres) | Anonymous sessions and saved deadlines |
| Keep-awake | UptimeRobot, monitor on `/health`, every 5 minutes | Stops the free server sleeping |
| AI services | Sarvam AI (scan reading, voice); Google AI Studio (Gemini), 3 keys from 3 accounts | The product's AI |

## 3. State today

**Working and tested:** reading notices in 3 languages; verified dates; personal "For you" answers; the deadline recorder; four colour themes and three text sizes; interface in 3 languages; security headers; model, key and provider failover. 116 backend tests pass, lint and types are clean, and the accessibility test (axe including AAA rules) passes.

**Not done:**
- A consented test with a real blind or low-vision student. This is the most important gap.
- A contact email on the About page (needs a real address from the team).
- Official Rotaract logo, if the organisers allow it (do not use an unofficial one).
- A real run of the new CI workflow (check the GitHub Actions tab).

## 4. Secrets: names only

Real values live in two places: **Render, Environment tab**, and the local file `code/backend/.env` (never committed). Never paste a key into a file that is committed; GitHub already blocked one such push.

| Name | What it is |
|---|---|
| `ENV` | `prod` on Render, `dev` locally |
| `DATABASE_URL` | Neon connection string. **If this is missing, the app loses its data on every restart** |
| `SECRET_KEY`, `PHONE_ENCRYPTION_KEY` | Signing and encryption keys. Production refuses to start without them |
| `SARVAM_API_KEY`, `SARVAM_BUDGET_PAISE` | Scan reading and voice; spend cap |
| `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, `GEMINI_MODEL` | Understanding. Extra keys absorb quota limits; use different Google accounts |
| `PUBLIC_BASE_URL`, `FRONTEND_BASE_URL`, `CORS_ORIGINS` | The Render URL |
| `COLLEGE_MODE` | Leave unset: the college side stays off. `true` brings back the staff desk and publishing |
| `SETUP_TOKEN` | Only matters when college mode is on: locks first-time desk setup |
| `DEMO_ACCESS_CODE` | Optional code students must enter to start |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT` | Vibration alerts. Set the subject to `mailto:` and a real email |
| `MAX_PDF_PAGES`, `MAX_TEXT_PDF_PAGES` | Scanned page cap (10) and text-PDF page cap (40) |

## 5. Run it, change it, ship it

```bash
# backend (port 8001: port 8000 was taken by another app on the original laptop)
cd code/backend && python -m venv .venv && .venv/Scripts/activate
pip install -r requirements-dev.txt && cp .env.example .env     # fill in the keys
uvicorn app.main:app --port 8001

# frontend
cd code/frontend && npm ci && echo BACKEND_URL=http://localhost:8001 > .env.local
npm run build && npm start                                     # http://localhost:3000/me

# checks before you push
cd code/backend && pytest -q && ruff check --select F app tests scripts
cd code/frontend && npx tsc --noEmit --noUnusedLocals && node scripts/a11y-student.mjs
```

Pushing to `main` redeploys Render in about three minutes. `docs/DEPLOYMENT.md` has the Render and Neon steps; `docs/SUBMISSION.md` covers copying the code to the organisers' repository.

## 6. The college side is switched off

Samyukta is a **student-facing product**: a student opens a link, taps Start, and reads notices. There is no admin or staff login in the product.

The college side still exists in the code, for a future where a college wants to publish official notices with a human check (staff desk, campus walkthroughs, WhatsApp). It is behind one switch, **off by default**:

- API: `COLLEGE_MODE=true` mounts those endpoints. Off, they do not exist (a test proves it), and that includes first-time admin setup, so there is no admin account to take over.
- Website: build with `NEXT_PUBLIC_COLLEGE_MODE=true`. Off, `/desk`, `/routes` and `/r/...` return "not found".

To turn it on later: set both switches, redeploy, then open `/desk` and follow the first-time setup (needs `SETUP_TOKEN`). To show judges a signed-in desk safely, use a private link `/desk#token=<token>`: the part after `#` is never sent to the server or logged.

## 7. Things we learned the hard way

- **Gemini's free quota ran out** during testing. Daily limits reset around midnight US Pacific (about 1:30 PM in India). Keep the three keys and do not run many test reads before a demo.
- **A first deploy lost its data** because `DATABASE_URL` was not set to Neon.
- **Windows:** a storage path check once failed because it assumed `/`; `tzdata` and `ffmpeg` must be installed for timezones and voice.
- **Rate limits** are per student for reads and generous per address for sign-up, so a classroom on one Wi-Fi is not blocked.
- **The old WhatsApp path is switched off.** The code is there, and needs Meta business verification to use.
- **The old student screens** (timetable, board photos, notices) are hidden. They only make sense with a college.

## 8. Next steps, in order

1. Get a consented session with a blind or low-vision student and write down what happened.
2. Add a real contact email (About page, `NEXT_PUBLIC_CONTACT_EMAIL`).
3. Record a backup demo video (a hackathon rule) and prepare the slide that names every AI tool.
4. Move beyond one instance: shared queue, migrations, object storage (`docs/ARCHITECTURE.md`, section 6).

## 9. Demo-day checklist

- [ ] UptimeRobot is green, and you opened the app once before going on.
- [ ] Phone charged, volume up, app installed from Chrome, TalkBack tested.
- [ ] Three current notices ready (English, Marathi, plus Hindi voice on any of them).
- [ ] "My details" filled in with your own real class, on the phone you present with.
- [ ] Backup video on the laptop.
- [ ] You can answer everything in `docs/JUDGES_QA.md`.
