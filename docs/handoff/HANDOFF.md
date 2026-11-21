# Saathi Campus: session handoff (21 Nov 2026, evening)

## What this is
Product for Naya Bharat 2047 hackathon, problem 5.3 ("A Notice the Screen Reader Can't Read"). Face: **Saathi Campus**, built on the Suchana Setu engine. Judges are Rotary and Rotaract heads. Rules: no fake data or users, name every source and AI tool, test with a real screen reader, keep a backup demo recording.

## State: built and tested, not yet run with live Sarvam or WhatsApp
- Backend (FastAPI, `backend/`) and frontend (Next.js, `frontend/`) are done. 80 backend tests pass; automated accessibility run (axe WCAG 2.2 AA, keyboard, focus) passes on every screen in light and dark.
- Six features: Right Now + Clash Radar, Verbal Deadline Catcher, Board Snap + Lecture Memory, Buzz Codes (Android Chrome vibration), Campus Walkthrough routes (QR), Exam Eve Pack. Old features (forward checker, NAAC log) stay in code, out of UI and pitch.
- Gemini verified with the user's real key: `gemini-2.5-flash` works (extraction in Marathi, labels). Findings the fakes had hidden: `gemini-flash-latest` does not exist for this key; the light model names tried were rejected, so `GEMINI_LIGHT_MODEL` is empty (main model used); the client must use API v1beta for the thinking setting (done).
- NOT verified: Sarvam (the cloud sandbox blocks its host; user must run `python -m scripts.check_providers` on their laptop), WhatsApp (no token/phone id/app secret yet), real TalkBack/NVDA test, Postgres/Supabase (SQLite used), Web Push to a real phone.

## Where things are
- Code: `/mnt/project-files/code/suchana-setu/` (backend, frontend; local git has ~10 commits, no GitHub repo yet). Zip of everything: `/mnt/project-files/handoff/saathi-campus-handoff.zip`.
- User guide (what only they can do): https://claude.ai/artifact/2eraMcq6VnQyehvRrv1Hvq (note: volunteer tokens are now made in the desk's Team tab).
- Screens: `/mnt/project-files/screens/`. Frontend README has the manual screen reader checklist.
- Keys: the user pasted Sarvam and Gemini keys into the project copy of `backend/.env`. That file must never be committed (gitignored). Consider rotating keys after the hackathon.

## Run it
```
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -m scripts.check_providers
uvicorn app.main:app --port 8000
cd ../frontend && cp .env.example .env.local && npm install && npm run build && npm start
```
Open http://localhost:3000/desk, do first-time setup, save the admin token (shown once).

## Still needed from the user
1. Run `check_providers` on the laptop; send output (Sarvam unverified).
2. Optional WhatsApp: token, phone number ID, app secret, verify token, public URL (cloudflared). Demo works on the laptop without it.
3. Empty GitHub repo + connect GitHub so code can be pushed.
4. Real screen reader test with a blind or low-vision person (consent), backup demo video, slide naming all AI tools and sources.

## Next steps for the next session
- After the Sarvam check: fix anything it shows (TTS model/speaker, OCR job flow, STT), then run one real notice end to end.
- Push to GitHub once a repo exists.
- Known gaps: tests use fixed Nov/Dec 2026 dates (break after 27 Nov); no DB migrations; in-process event bus and budget lock (single process only); storage is local disk (use a persistent disk if hosted).

## Decisions worth remembering
Dates are verified in code against the scan (weekday, invented dates); the LLM never resolves relative dates. Caught deadlines need a spoken read-back and a confirm. Routes need a second person to approve. Sarvam spend is capped at Rs 55 of Rs 70. Speed/spend work: thinking off, no duplicate body output, image sent to Gemini only when OCR is poor, free PDF text-layer reading, voice only in languages students use.
