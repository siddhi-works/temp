# Deployment (Render and Neon, free tiers)

One Docker container runs the API and the website. The database lives in Neon so a restart loses nothing.

## 1. Neon (database)
1. Create a project at neon.com and copy the **connection string** (starts with `postgresql://`).

## 2. GitHub
Push the repository. `.env` files are git-ignored; never add them.

## 3. Render (the app)
New, Web Service, pick the repository, runtime **Docker**.

| Setting | Value |
|---|---|
| Root Directory | `code` |
| Dockerfile Path | `./Dockerfile` |
| Health Check Path | `/health` |
| Instance type | Free |

Environment variables (Render, Environment tab). Copy the values from your local `code/backend/.env`:

| Name | Notes |
|---|---|
| `ENV` | `prod` |
| `DATABASE_URL` | the Neon string. **Required**: without it the data disappears on every restart |
| `SECRET_KEY`, `PHONE_ENCRYPTION_KEY` | same values as local |
| `SARVAM_API_KEY`, `SARVAM_BUDGET_PAISE`, `SARVAM_TTS_MODEL` | scan reading, voice, spend cap |
| `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, `GEMINI_MODEL` | `gemini-2.5-flash`. Use different Google accounts for the extra keys |
| `GEMINI_LIGHT_MODEL` | leave empty |
| `GEMINI_FALLBACK_MODELS` | optional; default `gemini-3.1-flash-lite` |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT` | vibration alerts; subject is `mailto:` plus a real email |
| `PUBLIC_BASE_URL`, `FRONTEND_BASE_URL`, `CORS_ORIGINS` | your Render URL, no trailing slash |
| `COLLEGE_MODE` | leave unset. The college side (staff desk, publishing, walkthroughs, WhatsApp) stays off |
| `SETUP_TOKEN` | only if you turn college mode on: long random string for first-time desk setup |
| `DEMO_ACCESS_CODE` | optional code students must enter to start |
| `MAX_PDF_PAGES`, `MAX_TEXT_PDF_PAGES` | optional; defaults 10 and 40 |

## 4. Keep it awake
UptimeRobot: new HTTP monitor on `https://<your-render-url>/health`, every 5 minutes.

## 5. The college side (optional, off by default)
Saathi is student-facing, so nothing here is needed. To bring the staff desk back, set `COLLEGE_MODE=true` on Render, build with `NEXT_PUBLIC_COLLEGE_MODE=true` (a Docker build argument), and follow `docs/HANDOFF.md`, section 6.

## 6. After a change
Pushing to `main` redeploys in about three minutes. Check `/health`, `/me` and `/about`.
