---
name: suchana-setu-backend
description: Where the Saathi Campus / Suchana Setu backend code lives, its stack, features and key design decisions (updated 21 Nov 2026)
metadata:
  type: project
  modified: 2026-11-21T10:06:53.299Z
---

Backend built 21 Nov 2026 in the build thread. Code: /mnt/project-files/code/suchana-setu/ (git archive of HEAD; local git has 6 commits). Not yet on GitHub (no repo attached); user to create one. backend/.env exists locally with generated secrets + VAPID keys, API key lines blank; never copied to project files.

- Product face is **Saathi Campus** (locked 21 Nov) on the Suchana Setu engine. Six features, all with API + WhatsApp: Clash Radar + Right Now (NOW), Verbal Deadline Catcher (CATCH, read-back, YES/NO; dates resolved in code, never by LLM), Board Snap & Lecture Memory (photo captioned BOARD; Gemini OCR first to save Sarvam credit), Buzz Codes (own RFC 8291 Web Push, Android Chrome only), Campus Walkthrough routes (volunteer records, second person approves, QR at /api/public/routes/{id}/qr.png, page /r/{id}), Exam Eve Pack (night-before reminder with checklist + route link).
- Old features (forward checker, NAAC log, generic Q&A) still in code but out of UI/pitch.
- Stack: FastAPI + SQLAlchemy async (SQLite dev, Postgres prod), DB job queue (leases, backoff, dead jobs), SSE events, Jinja accessible page at /n/{id}.
- Providers: OCR Sarvam -> Gemini; extraction Gemini -> rules; TTS Sarvam bulbul:v3 -> Google -> text; STT Sarvam -> Gemini; WhatsApp Cloud API only. Sarvam cap SARVAM_BUDGET_PAISE=5500.
- 70 pytest tests pass (fakes for external APIs only). Keys still needed: SARVAM_API_KEY, GEMINI_API_KEY, WhatsApp token/phone id/app secret. Run python -m scripts.check_providers after adding keys.
- Known gaps: tests use fixed Nov/Dec 2026 dates (will need updating after 27 Nov); no DB migrations (create_all); single-process event bus.
- Frontend built 21 Nov (frontend/, Next.js 15.5, plain CSS): / landing, /me?t= student app, /r/{id} walkthrough player, /routes?c=, /desk staff desk, /desk/routes/{id} recorder. Proxies /api /media /n to backend. Design: tactile-paving yellow #ffc928 on ink #15184a, Atkinson Hyperlegible Next. `npm run test:a11y` (axe + keyboard, needs empty DB) passes; manual TalkBack/NVDA checklist in frontend/README.md is still for the team to do. Screens in /mnt/project-files/screens/.
- Related: [[hackathon-problem-5-3-research-v2]].
