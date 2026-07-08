# Handoff — Offline deploy-ready layer BUILT (WAVE 2 offline half + config layer)

**Generated:** 2026-07-08 (session PART 2)
**Branch:** `elevation/wave-0` (HEAD `c32b527`; **21 commits ahead** of last-pushed `9929ad3`)
**Baseline in:** `HANDOFF_2026-07-08.md` (the input handoff). This is its follow-up.
**Status:** The offline, no-live-infra deploy-ready layer is BUILT + verified green. Nothing pushed.

## 1. What shipped this session (10 tracks, 12 commits, all TDD → green → committed)
| # | Track | Commit |
|---|---|---|
| 1 | Wave-4: remove dead OpenTelemetry residue from `llm_client` (decision 5) | `cbb8f6f` |
| 2 | STEP 2.1: pure env-free `resolve_provider_config` in `llm_router` | `8e7e919` |
| 3 | STEP 2.2: `configure_litellm_once()` idempotent guard | `aa8d48d` |
| 4 | **Config layer**: `config/settings.py::Settings` (APP_ENV flip) + `build_container` `select_object_store`/`select_queue` + `.queue` port + `.env.local.example` + `.gitignore` + `scripts/dev-local-up.sh` | `c6e16ed` |
| 5 | Decision 3: flip `use_pipeline_runner` default → True (full suite re-verified) | `785d6c2` |
| 6 | STEP 2.4: `JiraCredentials` model + injection into `JiraClient` (env/shared) | `37ab6b2` |
| 7 | STEP 2.5: sync `DbSessionStore` over `pipeline.db` (`auth/db_store.py`) | `a7d0e1f` |
| 8 | Deps pin + relock (incl. **missing** `pydantic-settings`; + arq/redis/authlib/itsdangerous/boto3/pgvector/psycopg2-binary) | `c3e8cc7` |
| 9 | STEP 2.3: arq `pipeline/worker.py` + `ArqQueue` adapter | `b3a56a3` |
| 10 | `S3ObjectStore` adapter → makes `select_object_store` production branch real | `64a4d04` |
| — | Adversarial-review fix-forward (2 low test-quality nits) | `c32b527` |

**Verification:** suite **941 pass / 0 fail / 0 err / 2 skip**; `uvx ruff check .` clean; `make verify` OK.
**Adversarial review** (5-lens Workflow → verify): 0 production defects; 1 security finding (JiraCredentials plain-str) correctly REJECTED after tracing all usages; 2 low test-quality fixes applied.

## 2. The 6 decisions (all resolved → recommended option)
1. `AUTH_MODE=local` (seeded dev-login). 2. queue = `arq`. 3. flip `use_pipeline_runner=True` (done, suite green). 4. UI revamp AFTER backend (React Phase 0 deferred). 5. delete dead OTel (done). 6. **push deferred to session end** (gated — see §5).

## 3. Deferred (walled / friction — NOT done, with reasons)
- **Track 3 audit→SQLAlchemy (STEP 1.6d):** `tests/test_audit_logger.py` LOCKS the sqlite backend shape (PRAGMA / `sqlite3.IntegrityError`), and `pipeline.db.AuditLog` has `user_id NOT NULL` + `run_id` UUID-FK — a blind rewrite breaks the contract and needs the multi-user context. `audit.logger.AuditLogger` already satisfies `core.ports.AuditSink` (it's the local adapter). RIGHT approach next: add a DB-backed `AuditSink` adapter selected by `build_container`, DON'T rewrite the sqlite one. Non-trivial (sync port over async DB + tenancy).
- **STEP 2.6 IDOR + STEP 2.5 OIDC route bodies + STEP 2.3 web enqueue:** all live in the 1211-line `ui/server.py` and need authlib + Google OAuth + Redis running (service-walled). This is the "production integration half." `current_user`/`DbSessionStore`/`ArqQueue`/`resolve_provider_config` seams are all built and unit-tested — the remaining work is wiring them into the async server + the atomic route-by-route `Depends(current_user)` + ownership-404 sweep.
- **React "Drafting Table" Phase 0:** large independent frontend track (per decision 4, after backend). Plan: `.planning/ui-rework/UI-REWORK-PLAN.md §4`.

## 4. Immediate next steps
1. **Push** (gated — get explicit go-ahead): 21 commits `9929ad3..c32b527` to BOTH remotes (GitHub `magggiiii`; GitLab `git@calib.dev` needs VPN — verify `git ls-remote`).
2. **UPGRADES.html bar bump** + **`graphify update .`** (deferred housekeeping).
3. Then the live-walled server integration (2.6/OIDC/enqueue) once infra is provisioned, or Track 3 audit-adapter, or React Phase 0.

## 5. Still open (human / out-of-band)
- Rotate `ARGUS_BACKBONE_TOKEN` (in pushed history). · Install `supabase` CLI (`brew install supabase/tap/supabase`) for local stack. · Provision the service-walled stack (Supabase hosted, Render, Redis, R2/Storage, Google OAuth, live LLM/Jira) — the go-live gate.

## 6. Gotchas (carried forward)
- Clear `__pycache__` before trusting green; `PYTHONDONTWRITEBYTECODE=1`. rtk eats pytest/git-diff summaries → use `--junit-xml` + parse. A Workflow result with a `<failures>` block = VACUOUS → verify directly (this session's review returned a real `<result>`, verified).
- `pipeline/llm_router.py` + `pipeline/observability.py` are in ruff `extend-exclude` (LOCKED-adjacent); `resolve_provider_config` was the intended STEP-2.1 touch.
- The `npm test` `cd ui` persists the Bash cwd — use absolute paths afterward.
