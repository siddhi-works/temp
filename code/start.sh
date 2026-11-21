#!/usr/bin/env bash
# Start the backend and the frontend; if either one exits, stop the container so the host restarts it.
set -u
cd /app/backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 &
cd /app/frontend
node node_modules/next/dist/bin/next start -p "${PORT:-3000}" -H 0.0.0.0 &
wait -n
exit 1
