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

FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/magggiiii/sow_2_jira"
LABEL org.opencontainers.image.description="SOW-to-Jira Portable Extraction Engine. This v1.1.1 patch resolves the legacy Loki telemetry crash and routes events to the new Argus JSON audit log."

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libmupdf-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY . .

RUN useradd -m -u 1000 sow \
    && mkdir -p /app/data \
    && chown -R sow:sow /app

USER sow

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/status').read()"

CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:8000", "ui.server:app"]
