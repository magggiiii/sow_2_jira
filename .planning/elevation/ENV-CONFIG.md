# Dual-environment config — `APP_ENV` flip (local ↔ production)

Status: LOCKED 2026-07-08 (user-approved). This is the authoritative config/deployment spec for the offline Wave-1/2/4 build. One flip, `APP_ENV=local|production`, resolved through `config/settings.py` (pydantic-settings) → `app/container.build_container(settings)` picks the adapter per port (`core/ports.py`). No deep branching; only the composition root chooses adapters.

## Dependency matrix

| Dependency | `APP_ENV=local` | `APP_ENV=production` | Env var(s) |
|---|---|---|---|
| Hosting | uvicorn (`make ui`) | Render (gunicorn + uvicorn worker) | `APP_ENV` |
| Postgres | Supabase **local stack** (`supabase start`, Docker) `postgresql://…@127.0.0.1:54322/postgres` | Supabase **hosted project** | `DATABASE_URL` |
| Object storage | Supabase **local** Storage S3 `http://127.0.0.1:54321/storage/v1/s3` | Supabase **hosted** Storage S3 `https://<project>.supabase.co/storage/v1/s3` | `S3_ENDPOINT` `S3_ACCESS_KEY` `S3_SECRET` `S3_REGION` `S3_BUCKET` |
| Queue / worker | Docker `redis:7` + arq worker (fallback: in-process queue adapter, no Redis) | Render Key Value (Redis) + arq worker service | `REDIS_URL` (unset → in-process) |
| Encryption (Fernet) | dev key in `.env.local` (`Fernet.generate_key()`) | real secret in Render secret store | `APP_ENC_KEY` (+ `APP_ENC_KEY_OLD` for rotation) |
| Auth | dev-login (seeded users) — or auth-off single user | Google OAuth, restricted to @calibraint.com | `AUTH_MODE=local|oauth` `GOOGLE_CLIENT_ID` `GOOGLE_CLIENT_SECRET` `AUTH_CALLBACK_URL` |
| LLM gateway (Bifrost) | `http://65.1.177.144:8080/` | same | `BIFROST_BASE_URL` (both) |
| Observability (Langfuse) | env creds | env creds | `LANGFUSE_PUBLIC_KEY` `LANGFUSE_SECRET_KEY` `LANGFUSE_HOST` (both) |
| Jira | env creds (single shared account) | env creds | `JIRA_SERVER` `JIRA_EMAIL` `JIRA_API_TOKEN` `JIRA_PROJECT_KEY` (both) |

## Supabase for both — verified

- **Postgres:** local = `supabase start` (Docker; Postgres 17 on `:54322`, pgvector + pgcrypto available); prod = hosted Supabase project. Same technology, `DATABASE_URL` flips. **Alembic stays the single schema authority** (do NOT adopt Supabase's migration CLI) — `alembic upgrade head` runs against each `DATABASE_URL` independently.
- **Object storage — MinIO DROPPED.** Verified from Supabase CLI source: the local stack exposes the S3 protocol (`S3_PROTOCOL_ACCESS_KEY_ID`/`_SECRET`, storage-api 1.43.3, `s3Protocol.enabled`) on the API gateway `:54321` at `/storage/v1/s3`. One boto3 adapter serves both; **use path-style addressing** (`config=Config(s3={"addressing_style":"path"})`) + a `region_name` (`S3_REGION`, e.g. `local` for the stack, the project region for hosted). Local keys come from `supabase status`; prod keys from Dashboard → Storage → S3 Connection.

## Data separation (dev/test never touches prod)

Separation is at the **instance** level, never a flag/column inside one DB:
1. **Separate databases** — local = your machine's Docker volume (ephemeral) or a `…-dev` hosted project; prod = the hosted prod project. Different hosts + different credentials ⇒ test data physically cannot reach prod.
2. **Alembic per-DB** — same migration files, applied to each `DATABASE_URL` separately.
3. **Per-user tenancy within each** — `user_id` scoping (Wave 1/2) isolates users inside an environment (orthogonal to env separation).
4. **Distinct `APP_ENC_KEY` per env** — backstop: prod ciphertext won't decrypt under the dev key.
5. **Guard rail** — seed/migrate/destructive scripts assert their target matches `APP_ENV`; the app refuses to run seeds when `APP_ENV=production`.

## Local stack bring-up (dev)

`supabase start` (Postgres + Storage/S3 + Studio) + a `redis:7` container for arq (or omit Redis → in-process queue). `.env.local` holds: `APP_ENV=local`, the `supabase status` DATABASE_URL/S3 values, a generated dev `APP_ENC_KEY`, `AUTH_MODE=local`, `BIFROST_BASE_URL`, and the shared `LANGFUSE_*`/`JIRA_*` creds. Prereq: `supabase` CLI (`brew install supabase/tap/supabase`); Docker already present.

## Production (Render)

`render.yaml` (already 4-service from WAVE 7) wires web + worker + cron + envVarGroups. Operator provides in the Render dashboard/secret store: `DATABASE_URL` (Supabase hosted), `REDIS_URL` (Render Key Value), `S3_*` (Supabase hosted S3), `APP_ENC_KEY`, `GOOGLE_CLIENT_ID/SECRET`, `LANGFUSE_*`, `JIRA_*`, `BIFROST_BASE_URL`, `APP_ALLOWED_ORIGINS` (the Render web origin).

## Open sub-decision

- **Local auth mode:** default `AUTH_MODE=local` = a tiny seeded dev-login (lets you exercise per-user tenancy without Google). Set `AUTH_MODE=off` for a single fixed dev user if you don't need multi-user locally.

## Notes / risks

- **Bifrost over plain HTTP** to a public IP (`65.1.177.144:8080`) means prod LLM traffic is unencrypted — recommend TLS/private-network before real production use. Config-only for now (no code change to the LOCKED `llm_router`/`BIFROST_*`).
- **Jira env creds = one shared Jira account** for all users (not per-user) — this deliberately defers the per-user credential model (old STEP 2.4). Fine for a single-team tool.
