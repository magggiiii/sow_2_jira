#!/usr/bin/env bash
# scripts/dev-local-up.sh — bring up the local dev stack for APP_ENV=local.
#
# Starts the Supabase local stack (Postgres 17 + Storage/S3 + Studio) and a
# redis:7 container for the arq worker, then prints the values you paste into
# `.env.local`. Idempotent: safe to re-run.
#
# Spec: .planning/elevation/ENV-CONFIG.md
# Prereqs: Docker (running) + the Supabase CLI (`brew install supabase/tap/supabase`).
set -euo pipefail

# Guard rail: never run the local bring-up against a production env.
if [ "${APP_ENV:-local}" = "production" ]; then
  echo "Refusing to run: APP_ENV=production. This script is for local dev only." >&2
  exit 1
fi

if ! command -v supabase >/dev/null 2>&1; then
  echo "supabase CLI not found. Install it:  brew install supabase/tap/supabase" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker does not appear to be running. Start Docker Desktop and retry." >&2
  exit 1
fi

echo "▶ Starting Supabase local stack (postgres :54322, storage/s3 :54321, studio)…"
supabase start

echo "▶ Starting redis:7 (for the arq worker) as container 'sow-redis'…"
if [ "$(docker ps -aq -f name=^sow-redis$)" ]; then
  docker start sow-redis >/dev/null
else
  docker run -d --name sow-redis -p 6379:6379 redis:7 >/dev/null
fi

echo
echo "✅ Local stack is up. Populate .env.local from:"
echo "   • supabase status   → DATABASE_URL + S3_PROTOCOL_ACCESS_KEY_ID / _SECRET"
echo "   • REDIS_URL=redis://127.0.0.1:6379/0"
echo "   • APP_ENC_KEY: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
echo
echo "Then:  alembic upgrade head   (apply schema)   &&   make ui   (run the web app)"
echo "Tear down with:  supabase stop   &&   docker stop sow-redis"
