FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libmupdf-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# Frontend build (UI.2a): compile the Vite bundle into ui/dist
FROM node:20-slim AS uibuilder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY vite.config.js ./
COPY ui/ ./ui/
RUN npm run build

FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/magggiiii/sow_2_jira"
LABEL org.opencontainers.image.description="SOW-to-Jira Portable Extraction Engine."

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libmupdf-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY . .
# Overlay the built frontend (ui/dist is gitignored, so absent from the context above)
COPY --from=uibuilder /app/ui/dist ./ui/dist

# Role-aware entrypoint (web/worker dispatch); make it executable regardless of
# the host's checked-in mode bits.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

RUN useradd -m -u 1000 sow \
    && mkdir -p /app/data \
    && chown -R sow:sow /app

USER sow

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT', '8000') + '/api/status').read()"

# Default (no SOW_ROLE) stays the web server on 0.0.0.0:${PORT:-8000}, matching
# prior behaviour; the entrypoint branches on SOW_ROLE for worker roles.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
