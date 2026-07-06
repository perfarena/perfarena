# syntax=docker/dockerfile:1.7
#
# PerfArena — single-container image (frontend + backend).
#
# The Vite SPA is built in a Node stage and copied into a Python runtime
# stage; one Uvicorn process serves /api/* AND the built React app at the
# root, so a single `docker run -p 8000:8000 perfarena` ships the whole
# product. SQLite data lives on the /data volume so DB files survive
# container restarts.
#
# Build:
#   docker build -t perfarena .
# Run:
#   docker run -p 8000:8000 -v perfarena-data:/data perfarena
# Visit:
#   http://localhost:8000

# ============================================================================
# Stage 1 — build the frontend
# ============================================================================
FROM node:20-alpine AS frontend
WORKDIR /build

# Cache npm install separately from the source code so editing TSX doesn't
# bust the install layer.
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY webapp/frontend/. ./

# Default to same-origin /api in production; override at build time with
# --build-arg VITE_API_URL=https://api.example.com to point elsewhere.
ARG VITE_API_URL=""
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build


# ============================================================================
# Stage 2 — Python runtime
# ============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # SQLite lands on the /data volume so it persists across container
    # restarts / re-creates.
    ARENA_DB=/data/arena.db \
    # Tells main.py to mount the built SPA at root.
    ARENA_SERVE_FRONTEND_DIR=/app/web \
    # Per-(IP, endpoint) rate-limit cap for unauthenticated reads — 60
    # GETs / IP / endpoint / minute.
    ARENA_IP_RATE_LIMIT_ENABLED=1 \
    ARENA_IP_RATE_LIMIT_PER_MIN=60

# Minimal system deps. `tini` gives us proper PID-1 signal handling so
# Ctrl-C / docker stop terminates Uvicorn cleanly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer cache survives source-only edits.
COPY webapp/backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Backend source.
COPY webapp/backend/ /app/
COPY leetbot.py /leetbot.py

# LeetCode submission CLI — lives at the repo root in the source tree,
# but the runtime expects to subprocess it from /app/leetbot.py (and
# app.dispatch._resolve_leetbot() looks here first inside the container).
COPY leetbot.py /app/leetbot.py

# Reference data (anchors, baselines, discrimination, sampled problems).
# Path matches the one app/* scripts default to (repo-root / data).
COPY data /app/data

# Built frontend (Vite dist/).
COPY --from=frontend /build/dist /app/web

# Persistent volume for the SQLite database.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# tini → uvicorn so SIGTERM propagates and a single CTRL-C exits cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
