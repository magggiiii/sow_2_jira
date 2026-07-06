# NEXT-SESSION PLAN — max-parallel, multi-workflow (WAVE 1.6 + WAVE 2 + WAVE 4 offline)

**Generated:** 2026-07-05 (end of the WAVE 6 + WAVE 1-offline session) by a 7-agent planning workflow (6 parallel track scopers → synthesis), verified against live code.
**Branch:** `elevation/wave-0` (10 unpushed commits ahead of remote `063cf5f`). **Green baseline:** pytest 667/2, vitest 76/76, docker build OK.
**Method (proven this session):** 2+ concurrent **disjoint-file** streams (backend pytest ∥ frontend vitest/browser) · background agents for big/independent work · **one reusable adversarial-review Workflow (default-reject) gating EVERY phase** · per-phase cadence **TDD → review → commit → UPGRADES.html → `graphify update .`** · **explicit-path `git add`** (never `-A`) so concurrent agents don't cross-stage · when phases share a file → **one comprehensive agent**, never parallel-with-merge.

---

## 0. Ground truth (verified against live code — do not re-derive)

- Ports already exist in `core/ports.py`: `ObjectStore`, `RunRepository`, `TaskRepository`, `CredentialRepository` (additive-done — **do not re-add**). `config/crypto.py`, `core/domain/ids.make_run_id`, `pipeline/db.py` (7-table ORM), `integrations/object_store.py` all shipped.
- **Keystone blocker:** `pipeline/db.py` uses `postgresql.INET` (`sessions.ip`), `JSONB`, and `gen_random_uuid()` server default → `Base.metadata.create_all(sqlite)` **fails** (`CompileError: can't render INET`). `aiosqlite`/`greenlet` are **not** in the venv. ⇒ **W2-S0 (SQLite-compat shim) is the hard gate for ALL offline DB/repo/GC/migrator tests.** The plan's earlier "test against SQLite" note does NOT hold against the real ORM without W2-S0.
- `PipelineOrchestrator.__init__` (`orchestrator.py:73`) has `cancel_check`/`stop_event` but **no `object_store` kwarg** → 1.6a seam is net-new. `app/container.build_container` already builds a `LocalObjectStore` → thread it in.
- **FE keystone:** `push_results` is built at `ui/server.py:727` but **discarded before `save_data` at :737** — that's why per-task chips are impossible today. BE-1 = persist it.
- **Latent bug to fix (cheap, offline):** `audit/logger.py:14` `__init__` takes no args, but `scripts/run_eval_dataset.py:31` calls `AuditLogger(run_id)` → add a tolerant `run_id` kwarg (back-compat) **without** the PG rewrite.
- `observability.py` is imported by **11 modules** → its rewrite is a solo gate; keep no-op `tracer`/`sync_telemetry`/`run_logger`/`trace_span` shims so importers never `NameError`.
- Tooling present: `vitest` (`npm test`), `render` CLI (`render blueprints validate` works offline), `langfuse` installed but **missing from requirements.txt**.
- **Net-new files (zero contention — fan out freely):** `core/domain/repositories.py`, `integrations/repositories.py`, `core/queue_port.py`, `integrations/queue/*`, `core/progress_port.py`, `integrations/progress/*`, `pipeline/jobs.py`, `auth/*`, `render.yaml`, `docker-entrypoint.sh`, `scripts/gc_sessions.py`, `scripts/migrate_fs_to_pg.py`, all new `tests/*`.
- **Security out-of-band:** rotate the committed `ARGUS_BACKBONE_TOKEN` leaked at `scripts/install/install.sh:18`.

---

## 1. Streams (disjoint file scopes)

| Stream | Harness | File scope | Offline? |
|---|---|---|---|
| **STREAM-DB** | pytest + SQLite-in-mem | `pipeline/db.py` (only in W2-S0 gate), `core/domain/repositories.py`✚, `integrations/repositories.py`✚, `scripts/migrate_fs_to_pg.py`✚, `requirements.txt` (aiosqlite/greenlet, merge-gated) | ✅ after W2-S0 |
| **STREAM-SEAM-A** | pytest (fakes) | `core/queue_port.py`✚, `integrations/queue/*`✚, `core/progress_port.py`✚, `integrations/progress/*`✚, `pipeline/jobs.py`✚, `models/schemas.py` (RunKind, merge-gated) | ✅ (real arq/redis walled) |
| **STREAM-AUTH** | pytest (TestClient) | `auth/*`✚ (tokens/domain_gate/store/deps/oauth/routes), `core/ports.py` (Session/User Protocols, additive) | ✅ (real Google OIDC walled) |
| **STREAM-OBS** | pytest | `pipeline/observability.py` (SOLO rewrite), `pipeline/telemetry.py`, tracer-block removals across 6 importers, `requirements.txt` (OTel strip, final gate) | ✅ (live Langfuse walled) |
| **STREAM-FE** | vitest+jsdom → browser | `ui/src/{render,main,modals,api}.js`, `ui/index.html` | ✅ (browser-smoke walled) |
| **STREAM-DEPLOY** | render CLI / TestClient / docker | `render.yaml`✚, `docker-entrypoint.sh`✚, `scripts/gc_sessions.py`✚, `Dockerfile` (web branch), `ui/server.py` (/healthz — serialized w/ server lane), `infra/**` (delete) | ✅ (real Render deploy walled) |

---

## 2. Rounds (what runs concurrently + which workflows to launch)

### R0 — SOLO GATES (two parallel solos on different files)
- **W2-S0** SQLite-compat enabler on `pipeline/db.py`: `@compiles(INET/JSONB/UUID, 'sqlite')` shims + app-side id/timestamp fallbacks + add `aiosqlite`/`greenlet`. Unblocks every offline DB test.
- **4.1a** rewrite `pipeline/observability.py` (keep no-op shims so 11 importers never break).
- **Workflows:** 2 background impl agents (different files → concurrent) + **1 adversarial-review Workflow per phase** (2 total). Gate: `create_all` succeeds on `sqlite:///:memory:` (7 tables, app-supplied ids, JSON round-trip); `import pipeline.observability` with OTel uninstalled + JSON log line w/ `user_id`/`run_id` + secret redacted.

### R1 — MAX FAN-OUT (5 concurrent streams, all NET-NEW files → zero contention)
- STREAM-DB: `W2-S1 RunRepo → W2-S2 TaskRepo` (sequential, share `repositories.py`) ∥ `W2-S3 CredentialRepo`.
- STREAM-SEAM-A: `2.3-a queue+FakeQueue` ∥ `2.3-b progress+FakeProgressStore` → `2.3-c jobs.py` ∥ `2.3-e RunKind`.
- STREAM-AUTH: `2.5a tokens/domain-gate → 2.5b store-port+fake → 2.5c current_user`.
- STREAM-DEPLOY: `4.2c gc_sessions` ∥ `4.2a render.yaml` ∥ `4.1e delete verify-telemetry`.
- STREAM-OBS-tail: `4.1b tracer-block removal` ∥ `4.1c telemetry allowlist` (after R0's 4.1a).
- **Workflows:** 5 impl fan-out agents (internally-sequential chains stay single-agent). **Adversarial-review Workflow fires per phase as it finishes** (not batched). Merge-gate the 3 additive-shared files (`requirements.txt`, `core/ports.py`, `models/schemas.py`) — one owner each, others rebase.

### R2 — HOT-SHARED-FILE serial lanes (single comprehensive agent per file, two lanes concurrent)
- **LANE-ORCH** `pipeline/orchestrator.py`: `1.6a object_store kwarg → 1.6b route 6 write + 2 read sites through the store → 1.6c DB write-through (uses integrations/repositories.py) → 2.3-d cancel through indexer/llm`. **Must NOT touch `_stage_setup`'s `configure_litellm_for_mode`/`current_provider_config` (LOCKED-adjacent).**
- **LANE-SERVER** `ui/server.py`: `BE-1 persist push_results → BE-2 expose on /api/tasks → BE-3 POST /api/jira/test → 2.6a IDOR route-scoping → 2.6b upload hardening → 2.6c CORS+CSRF → 4.2d /healthz → 4.2b __main__ 0.0.0.0:$PORT + 4.1b server sync_telemetry-import removal`. **2.6a must NOT remove `os.environ` JIRA_* writes (LOCKED 2.4).**
- **Workflows:** 2 comprehensive background impl agents + **adversarial-review Workflow per phase inside each lane** (review 1.6b before 1.6c; 2.6a before 2.6b; …).

### R3 — Frontend vertical slices ∥ deploy tail (after their backends land in R2)
- STREAM-FE: `FE-1 per-task push chips` (render.js+main.js — **one agent**) after BE-1/BE-2; `FE-2 Test-Connection` (modals.js+api.js+index.html) + `2.6d auth wiring` (api.js CSRF/credentials + 401→redirect) after BE-3/2.6c → rebuild `ui/dist`.
- DEPLOY-TAIL: `4.2b Dockerfile web branch` (after /healthz) ; `4.3a delete infra/` ; `4.3b install.sh ARGUS_* scrub` (leave OLLAMA_* to the LOCKED 5.1 owner).
- **Workflows:** 2 impl agents (FE vertical-slice; deploy-tail) + adversarial-review per phase.

### R4 — Final gates + offline-authored walled transforms + full verify
- `4.1f strip OTel from requirements.txt` (SOLO, LAST — after every OTel importer is clean) ∥ `4.1d langfuse fail-open wrapper` (llm_client; add langfuse to requirements — scope to ONLY langfuse, not LITELLM/creds).
- `1.6e migrate_fs_to_pg.py` authored **offline behind repo-fakes** (FS-read + task_count/coverage reproduction + settings re-encrypt under APP_ENC_KEY; PG landing gated behind the 1.6c repo seam).
- **Verify gauntlet:** full `pytest` ∥ `npm test` ∥ `render blueprints validate` ∥ **fresh-venv-without-OTel import smoke** → checkpoint/HANDOFF.

---

## 3. Conflict map (the rules that keep parallelism safe)

- **Hot files → single comprehensive agent, sequential internally:** `pipeline/orchestrator.py` (LANE-ORCH), `ui/server.py` (LANE-SERVER), `pipeline/db.py` (W2-S0 solo), `pipeline/observability.py` (4.1a solo), `render.js`+`main.js` (FE-1), `api.js`+`index.html` (FE-2).
- **`pipeline/llm_client.py` is edited by 4.1b (R1) + 2.3-d (R2) + 4.1d (R4)** and is LOCKED-adjacent to `llm_router` → **sequenced across rounds** (one round edits it at a time); each editor scopes to ONLY its symbol set.
- **Merge-gated additive-only (one owner, others rebase):** `requirements.txt` (aiosqlite/greenlet R0; langfuse+OTel-strip R4 — land dep changes on their own explicit-path commit), `core/ports.py` (Session/User Protocols, R1 AUTH), `models/schemas.py` (RunKind, R1 SEAM-A).
- **Git-index rule:** every phase commits with EXPLICIT paths so two background agents finishing together never cross-stage.

---

## 4. Verification (per-phase, review MUST see each proof pass)

Backend green: `venv/bin/pytest tests/` (targeted during dev, full in R4, `--junit-xml`+parse — rtk eats the summary). Frontend green: `npm test`. Key proofs: W2-S0 create_all on sqlite; repos → `isinstance` + tenant isolation (user B can't read user A) + `Task.id==ManagedTask.id` + credential ciphertext≠plaintext (monkeypatch `APP_ENC_KEY` like `test_crypto.py`); 1.6b → monkeypatch `builtins.open` to RAISE on `data/sessions` + assert all 6 writes/2 reads go through the store (LocalObjectStore round-trip = byte-identical parity); 1.6c → repo-fake, NO plaintext `api_key`; 2.3-d → `cancel_check` flip → indexer bails + in-flight llm raises (reuse `test_orchestrator_cancel.py`); observability → OTel-uninstalled import + JSON line + `grep sync_telemetry` empty + pyflakes F821 clean; 4.1c allowlist must be a **superset** of every live emit key (don't silently drop `run.*`/`step.completed`); BE-1/2 → mixed success/fail `JiraPushResult` persisted + on `/api/tasks`; BE-3 → 401→user_fixable/429→transient (read-only); auth → 401 no/forged/expired + domain-gate 403; IDOR → user-B→**404** on every data route; upload → traversal neutralized/non-PDF 400/oversize 413; CSRF → 403 on mismatch; `/healthz` 200 (add to access-log filter at `server.py:83`); gc → SQLite+tmp store removes expired/leaves recent/2nd-run no-op; render.yaml validate; FE → text-labelled `.sev-chip` (WCAG 1.4.1) + aggregate fallback + Test button + CSRF fetch wrapper + 401→redirect; **4.1f → fresh venv WITHOUT otel/traceloop imports clean (definitive de-OTel proof)**; Dockerfile web → `docker run -e SOW_ROLE=web -e PORT=10000` → /healthz 200.

---

## 5. Walled / Deferred / Locked

**WALLED (needs a live service — park, no round):** 1.6c full DB-row + INET/JSONB assertions & 1.6d audit→Postgres (create_all fails on SQLite; 1.6d also breaks `test_core_ports.py:149` + hits 184 `.log()` sites → keep audit on SQLite this session, only fix the latent `AuditLogger(run_id)` kwarg); 2.3 real arq/redis worker + route rewrite; 2.5d Google OAuth token exchange (authlib/itsdangerous not installed); 2.6d live sign-in smoke; 4.1d live Langfuse trace; 4.2a real Render deploy; 4.2b worker-role entrypoint; 4.3b real installer run; VERIFY-1 browser-smoke (make ui + real Jira valid+invalid creds + playwright/chrome-devtools MCP).

**LOCKED (never touch):** `pipeline/llm_router.py` + `configure_litellm_for_mode`; `BIFROST_*`; `data/.keyfile`; `os.environ` credential-WRITE paths (JIRA_*/LITELLM_* — owned by deferred 2.4); STEP 5.1 Ollama/Bifrost removal (4.3b scrubs ARGUS_* ONLY). Any edit adjacent to these (2.3-d/4.1b in llm_client; 1.6b/1.6c in orchestrator `_stage_setup`) scopes to only its own symbols and is flagged for careful review.

---

## 6. Text DAG

```
R0 (parallel solos, different files):
  [SOLO-DB] W2-S0 db.py SQLite shims + aiosqlite/greenlet  ∥  [SOLO-OBS] 4.1a observability rewrite+shims

R0 → R1 (5-way fan-out, all NET-NEW files):
  W2-S0 → [DB] (W2-S1 RunRepo → W2-S2 TaskRepo) ∥ W2-S3 CredentialRepo
        → [SEAM-A] (2.3-a queue ∥ 2.3-b progress) → 2.3-c jobs.py ∥ 2.3-e RunKind
        → [AUTH]   2.5a tokens/gate → 2.5b store+fake → 2.5c current_user
        → [DEPLOY] 4.2c gc_sessions ∥ 4.2a render.yaml ∥ 4.1e del verify-telemetry
  4.1a  → [OBS-tail] 4.1b tracer removal ∥ 4.1c allowlist

R1 → R2 (two parallel HOT-FILE lanes, single agent each, sequential inside):
  [LANE-ORCH orchestrator.py] 1.6a → 1.6b → 1.6c(← repos) → 2.3-d(← 2.3-b)
  [LANE-SERVER server.py]     BE-1 → BE-2 → BE-3 ; 2.5c → 2.6a → 2.6b → 2.6c ; 4.2d → 4.2b ; +4.1b

R2 → R3 (frontend ∥ deploy-tail, disjoint):
  BE-1,BE-2 → [FE] FE-1 (render.js+main.js, one agent)
  BE-3,2.6c → [FE] FE-2 (modals.js+api.js+index.html) + 2.6d → rebuild ui/dist
  4.2d → [DEPLOY-TAIL] 4.2b Dockerfile web ; 4.1a → 4.3a del infra/ → 4.3b install.sh ARGUS_* scrub

R3 → R4 (final gates + walled-transform authoring + full verify):
  (all OTel importers clean) → 4.1f strip OTel from requirements [SOLO last]
  4.1b → 4.1d langfuse fail-open (llm_client)
  1.6c repo seam → 1.6e migrate_fs_to_pg.py (offline behind repo-fakes)
  ALL → VERIFY: pytest ∥ npm test ∥ render validate ∥ fresh-venv-OTel-strip → checkpoint
```

---

## 7. Estimated concurrency & workflow count

- Peak parallelism: **R1 = 5 concurrent impl streams**, each with its own per-phase review workflow (≈5 impl agents + ~5 review workflows live).
- R0 = 2 solos; R2 = 2 hot-file lanes; R3 = 2; R4 = 2 + verify.
- Every phase = 1 adversarial-review Workflow (default-reject). Expect the review to catch real defects (it did every UI round this session) — budget for a fix+re-review cycle on the high-risk phases (1.6b/1.6c orchestrator routing, 2.6a IDOR, observability rewrite, FE-1).
- **Start each session by re-confirming the green baseline** (pytest + npm test) and re-scoping R0 against live code before fanning out.

---

## 8. FINAL WAVE — Argus decommission (added 2026-07-06; R0→R4 above are DONE + committed on `elevation/wave-0`, unpushed)

**Goal:** fully retire the self-hosted **Argus observability fleet** (per-instance OTel edge collectors + admin HQ deck: Grafana/Loki/Tempo/Langfuse-self-hosted) now that hosted SaaS uses **Langfuse Cloud** directly. OTel is already dropped (R4 `4.1f`); this wave removes the fleet's *infra, config, env identity, and installer/doc footprint*, and closes out the leaked-token remediation. Same method: disjoint-file streams · concurrent workflows · per-phase **TDD → adversarial-review (default-reject) → explicit-path commit → UPGRADES.html → `graphify update .`**.

### Ground truth (verified 2026-07-06)
- Fleet dirs: `infra/user/` (`docker-compose.user.yml` + `config/user/argus-collector-edge.yaml`) and `infra/admin/` (`docker-compose.admin.yml` + `evaluator/` + Bifrost). `infra/` is referenced by `scripts/install/install.sh` (local bring-up) + `README.md` + `CLAUDE.md`/`AGENTS.md`.
- Code hooks: `pipeline/observability.py` — `ARGUS_SYNC_ENABLED`, `ARGUS_COLLECTOR_URL`, `resolve_collector_endpoint`, `_use_local_collector`, `INSTANCE_ID`(=`SOW_INSTANCE_ID`), `argus.instance_id` resource attr; `pipeline/llm_client.py` imports `INSTANCE_ID` (uses it in span attrs); `tests/test_observability_shims.py` asserts on `SYNC_ENABLED`. Langfuse-cloud path (`LANGFUSE_*`) already exists and is the keeper.
- ⚠️ **Bifrost is LOCKED (STEP 5.1).** `config/admin/bifrost.admin.yaml` + the Bifrost service inside `infra/admin` are the LLM *gateway*, NOT observability. This wave must NOT change Bifrost routing / `BIFROST_*` / `llm_router` / `configure_litellm_for_mode`. If deleting `infra/admin` wholesale would remove the Bifrost deployment, split it: delete only the Argus-observability services, or get explicit go-ahead to retire Bifrost too.

### Streams (disjoint scopes)
| Stream | Scope | Offline? |
|---|---|---|
| **AR-OBS** (hot, 1 agent) | `pipeline/observability.py` — strip ARGUS collector paths + `INSTANCE_ID`/`argus.instance_id` fleet identity; keep Langfuse-cloud + the no-op/de-OTel shims intact. `pipeline/llm_client.py` (LOCKED-adjacent: remove ONLY the `INSTANCE_ID` import/usage, touch nothing else). `tests/test_observability_shims.py`. | ✅ |
| **AR-INSTALL-DOCS** | `scripts/install/install.sh` (drop ARGUS_* + the `infra/` bring-up), `README.md`, `CLAUDE.md`/`AGENTS.md`/`GEMINI.md` (remove infra/Argus references). Leave `OLLAMA_*` (LOCKED 5.1). | ✅ |
| **AR-INFRA-DELETE** | delete `infra/user/**` + `config/user/argus-collector-edge.yaml` (pure Argus observability). `infra/admin/**` + `config/admin/bifrost.admin.yaml` → **gated on the Bifrost decision** (default: keep, flag for 5.1). | ✅ |
| **AR-SECRET** (walled/out-of-band) | rotate `ARGUS_BACKBONE_TOKEN` in the Argus backend (human); decide git-history purge (filter-repo/BFG → rewrites already-pushed history → needs explicit go-ahead + coordinated force-push to both remotes). | ⛔ |

### Rounds
- **AR-R0 (2 concurrent, disjoint):** AR-OBS (single comprehensive agent — hot file) ∥ AR-INFRA-DELETE (delete `infra/user` + argus-collector-edge.yaml). Gate: `import pipeline.observability` clean with OTel blocked + Langfuse path intact; no `INSTANCE_ID`/`ARGUS_COLLECTOR_URL` symbol left; `llm_client` still imports (grep proves only INSTANCE_ID removed); full suite green.
- **AR-R1:** AR-INSTALL-DOCS (after AR-INFRA-DELETE so doc/installer references match reality). Gate: `sh -n install.sh`; no `infra/` or `ARGUS_*` literal remains; `OLLAMA_*` untouched (diff); docs mirror-synced.
- **AR-R2 (decisions + walled):** confirm Bifrost scope with the user → optionally delete `infra/admin`/`bifrost.admin.yaml`; then AR-SECRET (rotation + history purge) on explicit go-ahead. **Verify gauntlet:** full pytest ∥ npm test ∥ OTel-absent smoke ∥ `git grep` token = 0 tracked ∥ (if history purged) `git log -S<token> --all` = 0.

### Conflict / LOCKED
- Hot file: `pipeline/observability.py` = single agent. `pipeline/llm_client.py` LOCKED-adjacent — scope to ONLY `INSTANCE_ID`. **LOCKED (never touch):** `BIFROST_*`, `bifrost.admin.yaml` routing, `llm_router`/`configure_litellm_for_mode`, `data/.keyfile`, `os.environ` credential writes, `OLLAMA_*` / STEP 5.1.
- **WALLED:** live Langfuse-cloud trace verification; live installer run; actual git-history rewrite + force-push (disruptive; explicit go-ahead only); token rotation (human/backend).
