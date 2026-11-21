# Deploy Saathi Campus (Render + Neon, free)

One Docker container runs the backend and the website. Data lives in Neon Postgres so restarts lose nothing.

## 1. Neon (database)
1. neon.com -> new project -> copy the **connection string** (starts with `postgresql://`).

## 2. GitHub
Push this folder to a **private** GitHub repo. `.env` files are git-ignored; never add them.

## 3. Render (app)
New -> Web Service -> pick the repo -> Runtime **Docker**.
- Root Directory: `code`
- Dockerfile Path: `./Dockerfile`
- Health Check Path: `/health`
- Instance type: Free

Environment variables (Render dashboard -> Environment). Copy the values from your local `backend/.env`, except where noted:

| Name | Value |
|---|---|
| `ENV` | `prod` |
| `DATABASE_URL` | the Neon string |
| `SECRET_KEY`, `PHONE_ENCRYPTION_KEY` | copy from local `.env` (keep them the same if you reuse data) |
| `SARVAM_API_KEY`, `SARVAM_BUDGET_PAISE`, `SARVAM_TTS_MODEL` | from local `.env` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | from local `.env` (`gemini-2.5-flash`) |
| `GEMINI_LIGHT_MODEL` | leave empty |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT` | from local `.env`; set the subject to `mailto:` + a real email |
| `PUBLIC_BASE_URL`, `FRONTEND_BASE_URL` | your Render URL, e.g. `https://saathi.onrender.com` |
| `CORS_ORIGINS` | same Render URL |
| `SETUP_TOKEN` | any long random string (needed once for first-time setup) |
| `DEMO_ACCESS_CODE` | a short code you give testers (optional) |

## 4. Keep it awake
UptimeRobot -> new HTTP monitor -> `https://<your-render-url>/health` every 5 minutes.

## 5. First-time setup (once)
Right after the first deploy, create the volunteer/admin account yourself (it needs `SETUP_TOKEN`).
