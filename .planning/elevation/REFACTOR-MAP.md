# REFACTOR-MAP — Current → Target Internal Architecture

**Scope:** the *internal software architecture* refactor that makes SOW-to-Jira stable, production-grade, and easy to extend. This is the structural companion to `ELEVATION-PLAN.md` (which sequences the platform migration: Postgres, R2, Arq, Render, Langfuse) and `AUDIT.md` (which catalogued the root-cause bugs). Nothing here is a big-bang rewrite — every step is a strangler-fig move that keeps `pytest tests/` green and both surfaces (`main.py`, `ui/server.py`) running.

**Target shape in one line:** a pure inward-only `core/` (domain + ports + a Stage runner), feature-grouped `features/`, `adapters/` behind Protocols, one `app/container.py` composition root, and thin `api/` `worker/` `cli/` surfaces — wired so the LLM/Jira/disk/env coupling that makes the engine untestable today is replaced by mockable seams.

**Six design inputs folded in here:** `topology`, `ports`, `pipeline` (Stage framework), `agents_llm` (Instructor), `domain_data` (SQLModel + repositories), `crosscutting` (Settings/errors/observability/tests). Where two designs name the same target slightly differently, this map picks one canonical name and notes it.

---

## 1. Current → Target file/module map

Legend for **Action**: **MOVE** = relocated mostly as-is · **SPLIT** = one file becomes several · **WRAP** = kept but only reachable behind a new adapter/port · **DELETE** = dropped, not ported · **NEW** = no current equivalent.

| Current file/module | Action | Target location / shape | Notes |
|---|---|---|---|
| `pipeline/orchestrator.py` (`PipelineOrchestrator.run`, ~270-line method) | SPLIT | `core/pipeline/runner.py` (`PipelineRunner.run` loop + checkpointing) **+** `app/pipelines.py` (`default_stage_order`, feature flags) **+** per-stage `_execute` in `features/*/stage.py` | The linear method is deleted; sequencing leaves the orchestrator entirely. The 6 in-method accumulators (`open_tasks`, `all_closed_tasks`, `section_coverage_reports`, `coverage`, `deduplicated`, …) become fields on `PipelineContext`. |
| `pipeline/orchestrator.py` `__init__` agent construction + `os.getenv` toggles | MOVE | `app/container.py` `build_container()` + typed `Settings` flags | Scattered `os.getenv('SOW_*_ENABLED')` become validated `RunLimits`/flag fields. |
| `pipeline/orchestrator.py` `_build_or_load_tree` + node-index persistence | WRAP | `features/indexing/stage.py` (`IndexStage`) calling `DocumentIndexPort` | Disk writes move behind `ObjectStore` / `RunRepository`. |
| `pipeline/llm_client.py` (`complete_json` regex-scrape, `max_tokens=4096`) | SPLIT + DELETE | `adapters/llm/instructor_litellm.py` (`InstructorLLM` impl of `StructuredLLM`/`LLMProvider`) **+** `adapters/llm/retry.py` (the *good* retry/Retry-After/cancel logic, moved verbatim) | `complete_json` regex path and the hard-coded 4096 are **deleted** — this is the #1 root-cause bug. `max_tokens` becomes per-agent. The retry machinery is preserved inside the adapter, not rewritten. |
| `pipeline/llm_router.py` (`configure_litellm_for_mode`, reads `settings.json`/`os.environ`, Bifrost/Ollama branches) | SPLIT + DELETE | `adapters/llm/router.py` (`resolve_provider(LLMSettings, UserCredentials)`) | `current_provider_config` ContextVar deleted; config flows as a constructor arg. Ollama/Bifrost branches dropped per locked target. |
| `pipeline/agents/extraction.py` | SPLIT | `features/extraction/stage.py` + `dto.py` + `prompts/extraction.v1.txt` | Class becomes `BaseAgent`/`Stage` subclass; LLM via `ctx.llm`; broad try/except → typed `StageResult`/`AgentResult`. 180-line inline prompt extracted to a versioned file. |
| `pipeline/agents/state.py` | MOVE | `features/state/stage.py` | Pure logic, no LLM — moves cleanly, becomes a `Stage`. |
| `pipeline/agents/deduplication.py` | SPLIT | `features/dedup/stage.py` (+`dto.py`) **+** `adapters/embeddings/minilm_npz.py` | MiniLM/sklearn/`.npz` I/O extracted into the `EmbeddingIndex` adapter; only merge-decision logic stays in the stage. |
| `pipeline/coverage.py` + `pipeline/agents/coverage_check.py` | MOVE | `features/coverage/stage.py` (+`dto.py`); `CoverageTracker` becomes a local helper inside it | Two files consolidate into one feature folder. |
| `pipeline/agents/classifier.py` | MOVE | `features/classifier/stage.py` (+`dto.py`) | `_default_mixed` becomes `_empty` returned with status `DEGRADED` so "MIXED because the call failed" is distinguishable from a real MIXED. |
| `pipeline/agents/critic.py` | MOVE | `features/critic/stage.py` (+`dto.py`) | `_parse_critique` hand-parse deleted (Instructor validates `CritiqueResponse`); `_apply` auto-fix kept as `_to_domain`. |
| `pipeline/agents/gap_recovery.py` | MOVE | `features/gap_recovery/stage.py` | Per-node loop structure preserved; each call is one `BaseAgent.run`. |
| `pipeline/agents/cross_run_index.py` (`ProjectEmbeddingIndex`, `data/project_indices/*.npz`) | SPLIT | `features/cross_run/stage.py` **+** the `.npz`/embedding store side moves into `adapters/embeddings/minilm_npz.py` | Currently dormant; re-enabled as a registry stage later. |
| `integrations/jira_client.py` (REST, `os.environ[JIRA_*]`) | WRAP | `adapters/jira/rest_gateway.py` (`RestJiraGateway` impl of `JiraGateway`) | Creds **constructor-injected**, never `os.environ`. Hierarchy/dependency push logic kept; node-index reads come from `ObjectStore`. |
| `integrations/jira_mcp_client.py` (dead npx shell-out) | DELETE | — | Not ported. (Optionally revived later as a 2nd adapter behind the same `JiraGateway` port — but not now.) |
| `pipeline/jira_push` orchestration currently inside `ui/server.py` | MOVE | `features/jira_push/service.py` (+`dto.py`) | The REST orchestration that calls `JiraGateway`. |
| `ui/server.py` (719 LOC: routes + `run_pipeline_task` + `active_runs`/`active_orchestrators` + `_load_settings`/`_save_settings` + `os.environ` writes) | SPLIT | `api/server.py` (routes + DTO mapping only) + `api/deps.py` + `api/schemas.py` **+** `worker/arq_worker.py` (the BackgroundTask execution) **+** `app/container.py` (wiring) **+** repository adapters (replace the in-memory dicts) | Single `@app.exception_handler(SowError)` replaces scattered `HTTPException` raises. `os.environ` writes for `LITELLM_*`/`JIRA_*` deleted. |
| `ui/index.html`, `ui/app.js`, `ui/styles.css` | MOVE | `api/static/` (served as today) | No structural change; FastAPI static mount preserved. |
| `main.py` | MOVE | `cli/main.py` | Builds the container, calls `runner.run()`. |
| `models/schemas.py` (240 LOC) | SPLIT | `core/domain/models.py` (transport: `RawTask`, `ManagedTask`, `AcceptanceCriterion`, `TaskDependency`, `SourceRef`, `DedupDecision`, `JiraPushResult`) + `core/domain/enums.py` + `core/domain/ids.py` (**NEW** `UserId`/`OrgId`/`RunSpec`) + `persistence/tables.py` (SQLModel rows) | Closed-set bare-`str` fields (`DedupDecision.decision`, `TaskDependency.kind`, `AcceptanceCriterion.verified_by`, Jira issue-type strings, run/stage status) become normalizing enums. `current_provider_config` ContextVar deleted. |
| `audit/logger.py` (raw SQLite at `data/audit.db`) | WRAP → later DELETE | `adapters/audit/sqlite_sink.py` (impl of `AuditSink`) now; `adapters/audit/pg_sink.py` in Wave 1 | Kept behind the seam first; SQLite→Postgres swap is an adapter change only. |
| `config/settings.py` (`SettingsManager`, `data/settings.json` Fernet, `data/.keyfile`) | SPLIT | `app/settings.py` (`AppSettings(BaseSettings)`, infra config, `get_settings()` lru_cache) **+** `CredentialRepository` (per-user secrets) | Three-way config tangle (`os.getenv` + `app_config` + `settings.json`) collapses into one frozen typed object + a creds repo. Fernet key from `SOW_FERNET_KEY`. |
| `config/sow_config.json` (issue-type defaults, extraction/index caps) | MOVE | `app/settings.py` `RunLimits` fields | Stops being json.load'd inline in `run_pipeline_task`. |
| `pipeline/observability.py` (`init_argus()` import-time, `SYNC_ENABLED` global, Tempo/Loki fan-out) | SPLIT + WRAP | `core/ports/observability.py` (`ObservabilityPort`) **+** `adapters/observability/langfuse.py` + `adapters/observability/stdout.py` | Argus/Tempo/Loki fan-out + `INSTANCE_ID` dropped (single Render instance). Import-time side effect removed; adapters built in the composition root. |
| `pipeline/telemetry.py` | MOVE | folded into the observability adapter | `telemetry.emit('step.completed', …)` derived once from `StageResult.metrics` in the runner. |
| `pipeline/parser.py` (OpenDataLoader path) | WRAP | behind `DocumentIndexPort` adapter alongside PageIndex | Optional parser path stays an adapter detail. |
| `pageindex/` (vendored) | KEEP untouched | only `adapters/indexer/pageindex.py` imports it | Treated as third-party; no further abstraction. |
| **NEW** | NEW | `core/ports/*.py` (`llm`, `jira`, `indexer`, `embeddings`, `run_repo`, `object_store`, `audit`, `clock`, `observability`) | The single seam absent today. |
| **NEW** | NEW | `core/pipeline/stage.py` (`Stage` Protocol, `StageResult`, `StageStatus`), `context.py` (`RunContext`/`PipelineContext`), `registry.py` | Uniform stage contract. |
| **NEW** | NEW | `core/errors.py` (`SowError` taxonomy: `DomainError`, `AdapterError(retryable)`, `LLMTransportError`/`LLMValidationError`/`LLMAuthError`/`JiraPushError`/`RepositoryError`) | Replaces string-matching + silent-swallow. |
| **NEW** | NEW | `persistence/tables.py`, `persistence/session.py`, `persistence/repositories/{sqlmodel,memory}/`, `persistence/factory.py`, `persistence/migrations/` (Alembic) | SQLModel rows + repository adapters + in-memory fakes + composition root. |
| **NEW** | NEW | `pipeline/prompts/registry.py` + `prompts/*.v1.txt` | Versioned prompt assets. |
| **NEW** | NEW | `tests/fakes/` (`FakeLLMProvider`, `FakeRunRepository`, `FakeJiraGateway`, `FakeObservability`, `InMemoryBundle`), `tests/pipeline/`, `tests/contract/`, `tests/eval/`, CI workflow | The missing test layers above the agent line. |

> **Canonical naming note.** The `ports` design and the Stage `pipeline` design both define a "run state carrier" and a "result"; this map uses **`RunContext`** for the wired-ports carrier and **`PipelineContext`** for the mutable run-state carrier, and **`StageResult`/`StageStatus`** as the single result type (the `AgentResult`/`AgentStatus` from `agents_llm` is the *agent-internal* return that a Stage rolls up into a `StageResult`). Pick these names once in step S2 below and don't re-litigate.

---

## 2. Introduce-seams-first ordering (stays green at every step)

The governing rule: **never move an implementation and change its callers in the same step.** First create the seam (Protocol + adapter that wraps the *existing* code unchanged), flip callers to the seam, *then* refactor behind it. Each phase below ends with `pytest tests/` green.

1. **S0 — Safety net.** Turn the red-on-checkout test command green, add a CI workflow, add a coverage gate placeholder (≥0% to start, ratchets up). This is the precondition for everything else; without CI, "stays green" is unverifiable.
2. **S1 — Ports package, zero behavior change.** Add `core/ports/*.py` (Protocols only) + `core/domain/` (move `models/schemas.py` content, keep a re-export shim at the old path). Nothing imports the ports yet. App still runs on old code paths.
3. **S2 — Stage contract + runner, parallel to the orchestrator.** Add `core/pipeline/stage.py`, `context.py`, `runner.py`, `registry.py` and `tests/pipeline/test_runner.py` driven by *fake* stages. The runner is unit-tested in isolation; the live app still uses `PipelineOrchestrator.run`. This is the first-ever test above the agent layer.
4. **S3 — Composition root, wrapping existing concretes.** Add `app/container.py` + `app/settings.py`. `build_container` constructs *thin adapters that delegate to today's `LLMClient`, `JiraClient`, `DocumentIndexer`, local-disk repo* — i.e. `FsRunRepository`, `LocalObjectStore`, `SettingsCredentialRepository` (reads today's Fernet file). Behavior identical; the wiring just exists now.
5. **S4 — Flip surfaces to the container.** `main.py`/`ui/server.py` build the container and call into it instead of `new`-ing the orchestrator directly. Still running old internals, now through the seam. Both surfaces converge on one entry point.
6. **S5 — Wrap externals behind ports (adapters delegate, don't rewrite).** `adapters/llm/instructor_litellm.py` *initially* just calls the old `complete_json`; `adapters/jira/rest_gateway.py` wraps `JiraClient` with injected creds; `adapters/embeddings/minilm_npz.py` wraps the dedup embedding code; `adapters/audit/sqlite_sink.py` wraps `AuditLogger`. Callers now depend only on Protocols. Contract tests assert each adapter satisfies its Protocol.
7. **S6 — Extract stages one at a time.** Move each agent into `features/<x>/stage.py` behind the `Stage` contract, replacing its broad try/except with a typed `StageResult`. Order by independence: `state` (pure, no LLM) → `indexing` → `coverage` → `classifier` → `critic` → `dedup` → `gap_recovery` → `extraction` (largest). After each, the runner can execute that stage from the registry while the rest stay on the orchestrator via an adapter stage — strangler-style.
8. **S7 — Retire the orchestrator.** Once all stages are in the registry, `app/pipelines.py` supplies the full ordered list and `PipelineRunner.run` replaces `PipelineOrchestrator.run`. Delete the orchestrator.
9. **S8 — Swap `complete_json` for real Instructor.** *Now* that everything is behind `StructuredLLM`, replace the delegating-to-`complete_json` body of the LLM adapter with `instructor.from_litellm` + per-agent `response_model` + per-agent `max_tokens`. This is the single change that kills the truncation/regex bug class — and because it's behind the port, every stage benefits at once and it's contract-tested in isolation. Delete `complete_json` and the 4096 constant.
10. **S9 — Error taxonomy + observability port land.** Replace `_is_non_retryable_llm_error`/`_extract_status_code` with typed classification in the LLM adapter; add the single edge `@app.exception_handler(SowError)`; swap the observability concrete for `ObservabilityPort` + Langfuse/Stdout adapters.

After S9 the engine is structurally done on *local* infra. Steps that require Postgres/R2/Arq are the platform swaps below and dovetail into the elevation waves.

---

## 3. Strangler-fig steps as small PRs

Each bullet is intended to be one reviewable PR that lands green.

- **PR-1:** Green the default test command + add CI workflow + `.dockerignore`. *(S0)*
- **PR-2:** `core/ports/` Protocols + `core/domain/` models with old-path re-export shim. No callers changed. *(S1)*
- **PR-3:** `core/pipeline/` (`stage`, `context`, `runner`, `registry`) + `tests/pipeline/test_runner.py` with fake stages. *(S2)*
- **PR-4:** `app/settings.py` `AppSettings` + `get_settings()`; migrate one config read at a time (start with the 4096→`llm_max_tokens` field, still unused). *(S3a)*
- **PR-5:** `app/container.py` `build_container` wrapping existing concretes via `Fs`/`Local`/`SettingsCredential` adapters. *(S3b)*
- **PR-6:** Flip `ui/server.py` to build + use the container (behavior identical). *(S4a)*
- **PR-7:** Flip `cli/main.py` likewise; delete `os.environ` writes for `LITELLM_*`/`JIRA_*`. *(S4b)*
- **PR-8:** `adapters/llm/instructor_litellm.py` delegating to `complete_json` + `adapters/llm/retry.py` (moved) + contract test. *(S5a)*
- **PR-9:** `adapters/jira/rest_gateway.py` (creds injected) + contract test; delete `jira_mcp_client.py`. *(S5b)*
- **PR-10:** `adapters/embeddings/minilm_npz.py` + `adapters/audit/sqlite_sink.py` behind ports. *(S5c)*
- **PR-11..18:** one PR per stage extraction (`state`, `indexing`, `coverage`, `classifier`, `critic`, `dedup`, `gap_recovery`, `extraction`), each porting its agent test to `FakeLLMProvider` and adding a `StageResult` health assertion. *(S6)*
- **PR-19:** `app/pipelines.py` full ordering + delete `PipelineOrchestrator`. *(S7)*
- **PR-20:** Instructor wiring (real `response_model`, per-agent `max_tokens`) + delete `complete_json`/4096 + LLM eval cassette tests. *(S8)*
- **PR-21:** `core/errors.py` taxonomy + edge exception handler + typed LLM-error classification. *(S9a)*
- **PR-22:** `ObservabilityPort` + Langfuse/Stdout adapters; drop Argus fan-out. *(S9b)*
- **PR-23+ (platform):** SQLModel `tables.py` + Alembic + `persistence/repositories/` + `InMemoryBundle`; swap `FsRunRepository`→`PgRunRepository`, `LocalObjectStore`→`R2`, `SqliteAuditSink`→`PgAuditSink`, `SettingsCredentialRepository`→`PgCredentialRepository`; `worker/arq_worker.py` replaces the BackgroundTask. *(maps to elevation waves)*

---

## 4. Dovetail with ELEVATION-PLAN.md waves

The internal refactor is **front-loaded** so the platform waves swap *adapters*, not rewrite logic. Mapping:

| Refactor step / PR | Elevation Wave | Rationale |
|---|---|---|
| PR-1 (CI, green tests, `.dockerignore`) | **Wave 0 — Hardening** | Wave 0 is "no behavior change + safety net"; the CI gate is exactly that and is the precondition the other waves depend on. |
| PR-2..PR-7 (ports, runner, container, surface flips) | **Wave 0** | Pure seam introduction, no behavior change. Lands the inward-only structure before any data move so later waves are adapter swaps. |
| PR-8..PR-10 (LLM/Jira/embeddings/audit adapters, delegating) | **Wave 0** | Still no behavior change — just moves I/O behind ports. Sets up Wave 1/2/4 to swap implementations. |
| PR-23 *partial*: SQLModel `tables.py`, Alembic, `Pg*Repository`, `R2ObjectStore`, `PgCredentialRepository`, Fernet-per-user | **Wave 1 — Data + secrets** | This is the `FsRunRepository`→`PgRunRepository` / `LocalObjectStore`→`R2` / `SettingsCredentialRepository`→`PgCredentialRepository` swap. The ports already exist (Wave 0), so Wave 1 is implementing them, not re-plumbing callers. `org_id`/`user_id` columns + `TenantContext`. |
| PR-23 *partial*: `worker/arq_worker.py`, `Run.status`/`current_stage` replacing `active_runs`/`active_orchestrators` dicts, auth wiring into `RunContext` | **Wave 2 — Auth + worker** | The container is built per-job in the Arq worker exactly as it's built per-request in FastAPI. Resumability (skip persisted stages) becomes real once `RunRepository` is Postgres-backed. |
| PR-11..PR-20 (stage extraction, typed `StageResult` health, **Instructor swap**, eval cassettes) | **Wave 3 — Intelligence quality** | Wave 3 *is* the "100% INCOMPLETE / 0 merges / critic conf=0.00" fix. The Instructor swap (PR-20) is the structural fix for truncation; per-stage `DEGRADED` health surfaces the zero-merge/all-flagged failure modes instead of swallowing them. 3.1–3.6 can run at the agent level in parallel with Waves 1/2; 3.7 (cross-run/pgvector) needs Wave 1. |
| PR-21..PR-22 (error taxonomy, `ObservabilityPort`, Langfuse/Stdout, drop Argus) | **Wave 4 — Render deploy + observability cutover** | Langfuse Cloud adapter + dropping the Argus/Tempo/Loki fan-out is the hosted-observability cutover. The single edge exception handler gives clean HTTP error mapping for the deployed API. |
| `jira_mcp_client.py` delete, Jira robustness, dead-code cleanup | **Wave 5 — Cleanup & polish** | Post-launch hardening; the dead MCP client is dropped (or revived behind `JiraGateway` only if needed). |

**Key dovetail property:** because Wave 0 lands all the ports + the container, Waves 1, 2, and 4 each become *"write the production adapter for a port that already exists and flip one line in `build_container`"* — no caller churn, which is what keeps the platform migration low-risk.

---

## 5. Risk notes on the large moves

- **Splitting `PipelineOrchestrator.run` (S6–S7).** *Highest behavioral risk* — it holds run state in 6 accumulators and ordering matters (gap-recovery re-runs dedup). *Mitigation:* extract stages one at a time behind the registry while the rest still run on the orchestrator (strangler); keep a golden end-to-end test (fake LLM) asserting task count/dedup/flags before and after each extraction; `PipelineContext` is serialized so a diff of the carrier before/after a stage is reviewable. Don't delete the orchestrator until every stage is in the registry and the golden test matches.
- **Swapping `complete_json` for Instructor (S8 / PR-20).** *Highest "silent change in LLM output shape" risk.* The regex path tolerated junk that strict validation will reject — some prompts may now legitimately fail where they used to limp through. *Mitigation:* do it only *after* the port + contract tests exist; ship behind the adapter so it's reversible by one constructor swap; land the LLM eval cassettes (PR-20) in the same PR so the "100% INCOMPLETE" regression is caught; keep Instructor `max_retries` (schema reprompt) and `retry.py` (transient 5xx) as two distinct layers.
- **Credentials: `os.environ`/`settings.json` → injected → Postgres (S5, then Wave 1).** *Security + multi-user correctness risk* (process-global creds leak across users today). *Mitigation:* land the `SettingsCredentialRepository` shim first (same disk file, new seam) so the *injection* change is validated before the *storage* change; never write plaintext — `secret_enc` Fernet only; per-request decrypt into `RunContext`, never back into `os.environ`. Rotate any secrets that were ever in env during the transition.
- **In-memory `active_runs`/`active_orchestrators` → DB-backed `Run` rows (Wave 2).** *Risk:* status/cancel semantics change from in-process to DB reads; a cancel must reach the Arq worker. *Mitigation:* keep status/cancel API contract identical; back it with `Run.status`/`current_stage` columns + an Arq abort signal; test resume-skip (a re-run skips persisted `StageResult` rows) before relying on it for crash recovery.
- **Local disk (`data/sessions/*`, `*.npz`, `audit.db`) → R2 + Postgres (Wave 1).** *Risk:* path-shaped assumptions baked into Jira/dedup/orchestrator. *Mitigation:* the `ObjectStore` key convention `{user_id}/{run_id}/...` + `LocalObjectStore` shim (Wave 0) proves the seam on current infra; the R2 adapter is then a drop-in. Migrate existing `audit.db`/JSON once via Alembic data migration, then delete — don't dual-write.
- **Observability rip-out (Argus fan-out → Langfuse, Wave 4).** *Risk:* import-time `init_argus()` side effect means removing it can break module load order. *Mitigation:* introduce `ObservabilityPort` + `StdoutObservability` as the default *first* (kills the import-time global), and only then add the Langfuse adapter; the core never imports otel/loguru directly, so the cutover touches one adapter.
- **Enum promotion of bare-`str` fields (domain split).** *Risk:* legacy checkpoints / dirty LLM output (`"MERGE"` vs `"merge"`) fail to load. *Mitigation:* the `_NormalizedEnum._missing_` coercion (lowercase/strip/alias) handles dirty input; table-driven unit tests pin the coercion before the enums replace the `str` fields anywhere.

---

*Summary of intent:* land the seams (ports + Stage runner + composition root) with zero behavior change in Wave 0, wrap every external behind a delegating adapter, then strangle the orchestrator into stages, flip `complete_json` to Instructor behind the now-stable LLM port, and finally let Waves 1/2/4 swap local adapters for Postgres/R2/Arq/Langfuse one constructor line at a time.
