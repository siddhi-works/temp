# One container: FastAPI backend (internal port 8001) + Next.js frontend (public $PORT, proxies /api, /media to the backend).
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV BACKEND_URL=http://127.0.0.1:8001 NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web /usr/local/bin/node /usr/local/bin/node
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
WORKDIR /app/frontend
COPY --from=web /web ./
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
ENV PYTHONUNBUFFERED=1 STORAGE_DIR=/tmp/saathi-storage NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000
CMD ["bash", "/app/start.sh"]
