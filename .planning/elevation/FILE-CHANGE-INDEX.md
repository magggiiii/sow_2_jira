# FILE-CHANGE-INDEX

Per-file index of every file touched by the elevation plan. Derived verbatim from the 9 cluster scout specs (model_boundary, orchestration, agents, web_api, jira, domain_persistence, harness_core, infra_deploy, testing_evals). Open this to see, per file, exactly what to do.

Change-id prefixes: `mode-*` (model boundary), `orch-*`/`cov-*` (orchestration), `agen-*` (agents), `web_-*` (web/API/CLI), `jira-*` (Jira), `doma-*` (domain/persistence), `harn-*` (harness core), `infr-*` (infra/deploy), `test-*` (testing/evals).

---

## 1. EXISTING FILES

| File | Disposition | Change-ids | What happens |
|------|-------------|------------|--------------|
| `.github_token` | delete | infr-2 | Rotate the leaked PAT and delete the on-disk file (not git-tracked; no history scrub needed); ensure `.gitignore`/`.dockerignore` cover it. |
| `Makefile` | modify | infr-24, test-5 | Add `worker`/`migrate`/`lock`/`lint`/`audit`/`test`/`coverage` targets; update `verify` import smoke list (add sqlalchemy/asyncpg/arq/redis/boto3/authlib/langfuse/instructor, drop OTel/traceloop); bind `${PORT:-8000}` locally. |
| `audit/logger.py` | modify | doma-9, infr-8 | Rewrite from shared SQLite (`data/audit.db`) to async/sync Postgres `audit_log` via the engine; add required `user_id` param to `log()`/`get_run_logs()`; tz-aware timestamps; accept `run_id` in `__init__` (fixes `run_eval_dataset.py:31`). |
| `config/settings.py` | modify | infr-7, infr-23, doma-3, doma-14 | Delegate `encrypt_secret`/`decrypt_secret` to `config.crypto`; drop keyfile-write fallback; mark single-tenant settings legacy-migration-only; remove `_ensure_docker_host`, ollama/zai from `PROVIDER_REGISTRY` and `build_litellm_model` local branches. |
| `main.py` | modify | web_-18, doma-9(caller) | Drop `ensure_ollama_model`, the `LLMMode.LOCAL` branch, and z.ai/Bifrost/Ollama wizard copy; stop writing `JIRA_PROJECT_KEY` to env; thread user/audit through CLI startup. |
| `models/eval_schemas.py` | modify | doma-10 | Regenerate `GoldenTicket.acceptance_criteria` from canonical `AcceptanceCriterion` (or add a subset contract test); modernize `list[]`/`X|None` typing. |
| `models/schemas.py` | modify | mode-1, doma-4, doma-5, doma-6, doma-7, doma-8, doma-11, doma-13, harn-4, harn-6, harn-7, jira-2, jira-3 | Add `LLMResult`; add `JiraPushResult.hierarchy_degraded`/`idempotent_skip`; add `JiraCredentials`; bound `ManagedTask.confidence [0,1]`; MERGED-implies-`merged_from` validator; `SourceRef` page-order validator; `extra='forbid'` on LLM-facing models; promote `decision`/`kind`/`verified_by` to normalizing enums; full-UUID `run_id`/`AcceptanceCriterion.id`; `utcnow()` helper + tz-aware defaults; secret-redaction on `ProviderConfig`/persisted `RunConfig`; becomes a re-export shim for `core.domain.*`; add `RunSpec`/`PipelineFlags`/`RunLimits`/`resume_from`/tuning knobs. |
| `pageindex/utils.py` | modify | infr-3b | Swap `import PyPDF2` → `import pypdf`; replace all `PyPDF2.PdfReader` calls; change `pdf_parser` default/branch from `"PyPDF2"` to `"pypdf"`. |
| `pipeline/agents/classifier.py` | modify | agen-12, agen-15, agen-16, cov-1(adj), doma-2(caller), harn-7(caller) | Hand `ClassificationResult` as response_model + sized max_tokens; replace silent-swallow `_default_mixed` return with typed DEGRADED status (still fail-safe `should_extract=True`); consume enums; import centralized schemas. |
| `pipeline/agents/coverage_check.py` | modify | cov-1, agen-12, agen-19, harn-19(caller), doma-12(re-export) | Move `MissedItem`/`SectionCoverageReport` to `models/`; tz-aware `checked_at`; `extra='forbid'` on `MissedItem`; add `CoverageResponse` response_model + typed StageResult status; flagging gate moves to CoverageGate. |
| `pipeline/agents/critic.py` | modify | agen-10, agen-11, agen-12, harn-19(caller), doma-12(re-export) | Remove `LIKELY_DUPLICATE` (enum, prompts, `_apply`); gate ALL flagging on `flag_confidence_floor` (default 0.5) so conf=0.00 never flags; add `CritiqueResponse` response_model; typed StageResult status; delete hand-parse `_parse_critique`. |
| `pipeline/agents/deduplication.py` | modify | agen-3, agen-4, agen-5, agen-6, agen-7, agen-8, agen-9, agen-19, orch-3(method), doma-2/7(caller) | Batch candidate pairs (~25/call) with sized max_tokens + `DedupDecisionBatch` response_model; merge content on `keep_first`/`keep_second` (call `_merge_tasks`); delete dead `if/pass` block; fix inverted `merged_from`; add `assert_work_done` DEGRADED; pgvector-backed embeddings scoped by `(user_id, project_key)`; add `dedup_against(existing,new)` incremental method; `DedupAction` enum; tz-aware utcnow. |
| `pipeline/agents/extraction.py` | modify | agen-1, agen-2, agen-15, agen-16, mode-2(caller) | Add `ExtractionResponse` response_model + section-sized max_tokens; replace the three silent `return []` paths with typed `AgentResult{status}`; collapse dict/list sniffing once Instructor lands. |
| `pipeline/agents/gap_recovery.py` | modify | agen-14, agen-12, orch-3(caller), mode-2(caller) | Honor caller's `min_text_length` (defense-in-depth ≥100 guard); add `RecoveredTasksResponse` response_model; surface recovered-count + per-node failures via typed result; align `[:16000]` with `max_section_chars`. |
| `pipeline/agents/state.py` | modify | doma-9(caller), agen-19, harn-? | Thread `user_id` into `audit.log`; switch naive `datetime.utcnow()` (`:240-241`) to tz-aware helper; (note) `_merge` list-merge semantics flagged for consistency. |
| `pipeline/coverage.py` | modify | orch-2 | Fix `CoverageTracker.get_gaps` to actually apply `min_text_length` (filter uncovered nodes by `len(text/summary) >= min_text_length`); update docstring. |
| `pipeline/evals/judges.py` | modify | mode-13, doma-10(caller), test-10, infr-19 | Remove `langchain_openai`/`langchain` imports; reimplement `HierarchicalJudge` on `LLMClient.complete_json` with `EvaluationScores` response_model; drop Bifrost/Ollama defaults + hardcoded admin key. (W0 minimal: lazy/guard import to unblock collection.) |
| `pipeline/indexer.py` | modify | orch-8(caller), harn-12(adapter), infr-3b(caller) | Reads/writes route through ObjectStore seam; consumes `stop_event` via `build_tree`; formalized behind `DocumentIndexPort`. |
| `pipeline/llm_client.py` | modify | mode-2, mode-3, mode-4, mode-7, mode-8, mode-9, mode-10, mode-11, harn-10(impl), infr-15b | Instructor structured output + per-call `response_model`; per-call `max_tokens` (kill hard 4096); reject `finish_reason=length`; configure litellm globals once (`configure_litellm_once`); per-call `completion_cost`; drop OTel/Argus/SYNC_ENABLED → Langfuse; delete ZAI/Ollama headers + 3600s unbounded local loop; typed litellm exception classification. |
| `pipeline/llm_router.py` | modify | mode-5, mode-6, infr-23, doma-14(caller) | Inject per-run `ProviderConfig` (no global `SettingsManager`/`os.environ` reads); delete `LLMMode.LOCAL`/Bifrost/ZAI branches and `_ensure_docker_host` import; reduce `configure_litellm_for_mode` to a pure resolver/shim. |
| `pipeline/observability.py` | modify | mode-9(coord), infr-15 | Rewrite to single loguru stdout-JSON + secret redaction; delete traceloop/OTel/Argus/Bifrost/`INSTANCE_ID`/`SYNC_ENABLED`/`init_argus`/file sinks; keep `trace_span`/`run_logger` as no-op shims; remove `sync_telemetry`. |
| `pipeline/orchestrator.py` | modify | orch-1, orch-2(caller), orch-3, orch-8, orch-9, orch-10, orch-11, orch-12, orch-13, cov-1(caller), infr-10, infr-15c, mode-5(caller) | Move coverage flagging post-dedup/report-level/confidence-gated; scoped dedup of recovered tasks (no full re-dedup); route disk writes through ObjectStore; DB upserts for run/tasks/coverage; build `RunHealthReport`/DEGRADED; move tuning knobs/agent construction to RunConfig/container; per-node result records; Redis-polled cancel; remove `sync_telemetry`/direct `tracer.start_as_current_span` spans; thread `user_id`. |
| `pipeline/parser.py` | modify | infr-15c | Keep `trace_span` as no-op shim; drop any `sync_telemetry`/OTel references. |
| `pipeline/telemetry.py` | modify | mode-12 | Replace single-field denylist (`section_title`) with an allowlist of permitted event fields; add `cost_usd`; route through centralized loguru. |
| `integrations/jira_client.py` | modify | jira-3, jira-5, jira-6, jira-7, jira-8, jira-10, jira-2(constructs), mode-?(adapter), infr-15c | Accept `JiraCredentials` (no `os.environ` creds); idempotency skip on existing `jira_issue_key`; 429/Retry-After + 5xx backoff + bounded concurrency; container-failure `hierarchy_degraded` instead of silent flatten; fix inverted dependency-link direction (per-kind map); integrate `PushGate`; keep `trace_span` no-op / remove direct tracer span at `:394`. |
| `integrations/jira_mcp_client.py` | delete | jira-9 | Delete the unwired MCP Jira client (token-in-shell, dead code; only consumer is `test_jira_mcp.py`). |
| `ui/server.py` | modify | web_-1, web_-2, web_-3, web_-4, web_-5, web_-6, web_-7, web_-8, web_-9, web_-10, web_-11, web_-12, web_-13, web_-14, web_-15, web_-16, web_-19, jira-1, jira-4, infr-15c, infr-16, doma-13(caller) | Import `JiraClient` (fix `/api/push` NameError); bind `$PORT`/`0.0.0.0`; FastAPI lifespan + configure litellm once; Google OAuth (@calibraint) login/callback/logout/me; opaque sha256 sessions; `current_user` dependency; `Depends(current_user)` + `WHERE user_id` + ownership-404 on all data routes; per-user `user_credentials` settings (remove env writes incl. `JIRA_PROJECT_KEY` at `:649`, env_defaults at `:221-224`); thread per-run creds; harden `/api/upload` (UUID key, %PDF, 413, R2); delete `active_runs`/`active_orchestrators`/`MODEL_CACHE`; enqueue to arq; Redis-backed status/cancel; lock CORS + CSRF; per-user concurrency cap; full-UUID `run_id`. |
| `ui/app.js` | modify | web_-17 | Data-driven step count (`status.total_steps`); add `credentials:'include'` + `X-CSRF-Token` on fetches; 401→login redirect; Sign-in-with-Google bootstrap via `/api/auth/me`; use upload `object_key`. |
| `requirements.txt` | modify | mode-14, infr-3, test-6 | Pin all deps `==` + lockfile; swap `PyPDF2`→`pypdf`; remove OTel/traceloop; add instructor, json-repair, langfuse, sqlalchemy[asyncio], asyncpg, alembic, boto3, arq, redis, authlib, itsdangerous, pgvector, pip-audit, ruff, pytest-cov, respx. |
| `.gitignore` | modify | infr-1(coord) | Ensure `.github_token`/secrets covered; note `#.planning/` is commented (tracked) — reconcile so secrets/bulk dirs don't bake into the image. |
| `Dockerfile` | modify | infr-4 | `$PORT` bind + `SOW_ROLE` entrypoint branch (web/worker/cron); bake `all-MiniLM-L6-v2`; `--graceful-timeout 30`; healthcheck `/healthz`. |
| `scripts/install/install.sh` | modify | infr-22 | Stop copying Argus compose files; remove hardcoded `ARGUS_HQ_URL`/`ARGUS_BACKBONE_TOKEN` (rotate the baked secret) and `BIFROST_*`/`OLLAMA_*`; keep installer functional without Argus or deprecate for hosted model. |
| `scripts/verify-telemetry.py` | delete | infr-18 | Delete (imports a non-existent `resolve_observability_endpoint`; verifies the removed Argus/Bifrost path). |
| `scripts/run_eval_dataset.py` | modify | doma-9(caller), test-12(caller) | Fixed by `AuditLogger(run_id)` accepting `run_id`; wire the ported judge; drop live-Langfuse hardcoded keys for the harness path. |
| `infra/user/docker-compose.user.yml` | delete | infr-22 | Delete the Argus collector / Bifrost / Loki / Tempo user stack from the deploy path. |
| `infra/admin/docker-compose.admin.yml` | delete | infr-22 | Delete the Argus HQ admin stack (Bifrost, OTel collector, self-hosted Langfuse, evaluator). |
| `test_jira_api.py` (root) | move | infr-21, test-3 | Move/rename to `scripts/check_jira_api.py` (live-credential smoke, never asserts). |
| `test_jira_mcp.py` (root) | move/delete | infr-21, test-3, jira-9 | Move to `scripts/check_jira_mcp.py` then delete in W5 (exercises the deleted MCP client). |
| `test_discovery.py` (root) | move | infr-21, test-3 | Migrate genuine unit tests into `tests/test_discovery.py`; gate/rewrite once `MODEL_CACHE`/discovery endpoint are deleted (W2). |
| `test_settings.py` (root) | move | infr-21, test-3 | Migrate genuine unit tests into `tests/test_settings.py`; retarget encryption test at `config/crypto.py`. |
| `tests/test_hierarchical_judge.py` | modify | test-1, test-11 | W0: `pytest.importorskip("langchain_openai")` to unblock collection. W3: rewrite to mock `LLMClient.complete_json` returning `EvaluationScores` (drop langchain `.invoke` shape + importorskip). |
| `tests/test_coverage_check.py` | modify | test-8, agen-12(impact), cov-1(import) | Add `test_well_covered_section_yields_zero_missed_items` + low-confidence-not-surfaced tests; import paths via shim; rework to typed-result contract. |
| `tests/test_critic.py` | modify | test-9, agen-10/11/12(impact) | Add conf=0.0→no-flag, confidence-floor-gated, and ignore-`likely_duplicate` tests; update boundary cases (0.4 no-flag / 0.5 flag); keep MISSING_AC auto-fix un-floored. |
| `tests/test_dedup.py` | modify | agen-3/4/5/6/7/9(impact), orch-3 | Cover batching call_count, merge-on-keep content absorption, no-op dead-code removal, `merged_from` fix, zero-merge DEGRADED, enum decisions, scoped `dedup_against`. |
| `tests/test_extraction.py` | modify | agen-1/2(impact) | Long-section no-truncation case; `ExtractionResponse` rejects unknown keys; forced-error→`status=FAILED` (not silent `[]`). |
| `tests/test_classifier.py` | modify | agen-12(impact), doma-? | Forced-error→DEGRADED MIXED with `should_extract=True`; confirm centralized confidence bound (clamp vs reject conflict at `~:198`). |
| `tests/test_cross_run_index.py` | modify | agen-13(impact) | Rework off disk `.npz` onto fake/in-memory pgvector store; assert `(user_id, project_key)` isolation; preserve dormant-by-default contract. |
| `tests/test_hierarchy_preservation.py` | modify | jira-2/3(impact) | Update `JiraClient` construction (lines 289 & 369) to `JiraCredentials`; construct `JiraPushResult` with new fields. |
| `tests/test_structured_acceptance.py` | modify | jira-3(impact), jira-8/test-15, doma-7 | Update `JiraClient` construction (`:214`); flip the inward/outward link-direction assertion (`:299-301`) to corrected direction; update `verified_by`/`kind` enum assertions. |
| `tests/test_routing.py` | delete | test-16, mode-6/infr-23(coord) | Delete entirely once `_ensure_docker_host` is removed (whole file tests it; also fix env-mutation hygiene before deletion). |
| `tests/test_phase2_runtime_reliability.py` | modify/delete | test-16, mode-10/11(impact) | Delete `test_local_remote_isolation` + `LLMMode.LOCAL`/ollama branches; keep/rewrite remote 429/backoff + retryable-classification (status_code branch) tests as bounded-behavior. |
| `tests/test_litellm_model_routing.py` | modify | test-16, infr-23 | Remove ollama assertions (`:60-61`, incl. `hf.co` case); keep `build_litellm_model` non-ollama cases. |
| `tests/test_phase11_evals.py` | modify | test-12 | Delete/reimplement the three bare-`pass` stubs and the hasattr-only test against the real EvalHarness. |
| `pyproject.toml` | create-or-modify | test-2 | (No file today.) Add pytest config (`testpaths`, `--strict-markers`, `asyncio_mode`, markers) + ruff config (py311, `select=[F,I]`). |

---

## 2. NEW FILES

| File | Purpose | Change-ids | Key symbols introduced |
|------|---------|------------|------------------------|
| `core/__init__.py` (+ `core/domain/__init__.py`, `core/ports/__init__.py`, `core/pipeline/__init__.py`, `core/guardrails/__init__.py`) | Inward-only hexagon package root (stdlib + pydantic only). | harn-1 | package skeleton, dependency-rule docstring |
| `core/pipeline/stage.py` | Stage abstraction + single exception boundary (degrade-don't-crash). | harn-2, orch-4 | `StageStatus`, `StageResult`, `Stage` (Protocol), `BaseStage` |
| `core/pipeline/context.py` | Mutable per-run state carrier + wired Ports bundle. | harn-3, orch-5 | `PipelineContext`, `Ports`, `RunContext` |
| `core/pipeline/runner.py` | PEV state machine over a Stage list with resume + worst-status rollup. | harn-9, orch-6, orch-7 | `PipelineRunner`, `RunResult`, `worst_status()` |
| `core/pipeline/registry.py` | `StageName → Stage` factory map for strangler assembly. | harn-(missed, required W0) | `StageRegistry` |
| `core/pipeline/health.py` | Per-run health/cost aggregation. | harn-24, orch-10 | `RunHealthReport`, `StageHealth`, `CostMeter` |
| `core/domain/models.py` (+ `core/domain/extraction.py`) | Transport/LLM-facing domain models (moved from schemas). | harn-4, harn-6 | `ManagedTask`, `SourceRef`, `AcceptanceCriterion`, `RunSpec`, `PipelineFlags`, `RunLimits`, `JiraPushResult`; `RawTask`, `TaskDependency`, `DedupDecision` |
| `core/domain/ids.py` (+ `core/ports/tenant.py`) | Identity value types + tenant context. | harn-5 | `UserId`, `OrgId`, `RunId`, `make_run_id()`, `TenantContext` |
| `core/domain/enums.py` | Normalizing closed-set enums. | harn-7, doma-2 | `_NormalizedEnum`, `DedupAction`, `DependencyKind`, `VerifiedBy`, `RunStatus`, `StageName`, `StageStatus`, `CredentialKind`, `AuditAction` (+ moved `TaskStatus`/`TaskFlag`/`JiraHierarchy`) |
| `core/errors.py` | Typed error taxonomy across port seams. | harn-8 | `SowError`, `DomainError`, `ExtractionEmptyError`, `AdapterError`, `LLMTransportError`, `LLMValidationError`, `LLMAuthError`, `JiraPushError`, `RepositoryError` |
| `core/ports/llm.py` | LLM provider port (pydantic-in/pydantic-out). | harn-10 | `LLMProvider` (Protocol), `LLMResult` |
| `core/ports/jira.py` | Jira gateway port. | harn-11 | `JiraGateway` (Protocol) |
| `core/ports/indexer.py` | Document indexing port. | harn-12 | `DocumentIndexPort` (Protocol) |
| `core/ports/embeddings.py` | Embedding/candidate-pair port (numpy stays out of core). | harn-13 | `EmbeddingIndexPort` (Protocol) |
| `core/ports/object_store.py` | Object store with server-controlled key contract. | harn-14, orch-8 | `ObjectStore` (Protocol), `key_for()` |
| `core/ports/observability.py` (+ `core/ports/audit.py`, `core/ports/clock.py`) | Observability/audit/clock ports. | harn-15 | `ObservabilityPort`, `AuditSink`, `Clock` |
| `core/ports/repositories.py` | Tenant-scoped repository ports + bundle (resumability backbone). | harn-16 | `RunRepository`, `StageResultRepository`, `TaskRepository`, `CredentialRepository`, `CoverageRepository`, `AuditRepository`, `RepositoryBundle` |
| `core/guardrails/gate.py` | Deterministic config-driven gates. | harn-19, agen-11(impl), jira-10(impl) | `GateConfig`, `CriticGate`, `CoverageGate`, `PushGate`, `CoverageVerdict` |
| `core/guardrails/verify.py` | No-LLM computational verifiers. | harn-19a, agen-7(impl) | `VerifyVerdict`, `assert_work_done()`, `assert_finish_complete()`, `assert_nonempty_when_expected()` |
| `pipeline/specs/base.py` | Frozen, versioned declarative cognition contract. | harn-17, agen-15 | `AgentSpec[I,O]`, `ModelPolicy`, `AgentStatus`, `AgentResult` |
| `pipeline/specs/runner.py` (or `features/agents/base.py`) | Single model-call path wrapper; shrinks agents to one-liners. | harn-18, agen-16 | `AgentRunner`/`BaseAgent` |
| `pipeline/specs/{extraction,classifier,dedup,critic,coverage,gap_recovery}.py` | Per-agent SPEC instances (prompt + response_model + policy + gate). | harn-15..17, agen-15 | `EXTRACTION_SPEC`, `CLASSIFIER_SPEC`, `DEDUP_SPEC`, `CRITIC_SPEC`, `COVERAGE_SPEC`, `GAP_RECOVERY_SPEC` |
| `app/container.py` | Composition root wiring ports → delegating adapters + runner. | harn-20 | `Container`, `build_container()` |
| `app/pipelines.py` | PEV-ordered stage list (coverage/critic moved post-dedup). | harn-21, orch-6 | `default_stage_order(flags, ports)` |
| `prompts/registry.py` (+ `prompts/*.v1.txt`) | Filesystem-backed versioned prompt loader. | harn-25 | `PromptRegistry`, `PromptTemplate` |
| `models/tables.py` | SQLModel persistence tables. | doma-1 | `User`, `Session`, `Credential`/`UserCredential`, `Run`, `Task`, `StageResult`, `CoverageReport`, `AuditEntry` (+ `Org` open) |
| `models/enums.py` | Closed-set enums (flat-layout home; see also `core/domain/enums.py`). | doma-2 | `_NormalizedEnum`, `DedupAction`, `DependencyKind`, `VerifiedBy`, `RunStatus`, `StageName`, `StageStatus`, `CredentialKind`, `AuditAction` |
| `models/intelligence_schemas.py` | Centralized persisted intelligence schemas. | doma-12, cov-1, agen-18 | `MissedItem`, `SectionCoverageReport`, `TaskCritique`, `CritiqueReport`, `CritiqueIssue`, `ClassificationResult`, `SectionType`, `EvaluationScores` |
| `models/time.py` (or helper in config) | Centralized tz-aware time helper. | doma-11, agen-19 | `utcnow()` |
| `config/crypto.py` | MultiFernet secret helper via `APP_ENC_KEY` (+ `APP_ENC_KEY_OLD`). | doma-3, infr-7 | `get_multifernet()`, `encrypt_secret()`/`encrypt()`, `decrypt_secret()`/`decrypt()` |
| `pipeline/db.py` | Async engine + session factory + URL normalizer + ORM models. | infr-6 | `normalize_db_url()`, `engine`, `async_session`, `get_session()`, `Base`, `User`/`Session`/`UserCredential`/`Run`/`Task`/`CoverageReport`/`AuditLog` |
| `integrations/object_store.py` | R2 (boto3) object store with user-prefixed keys. | infr-9, orch-8(adapter) | `put_artifact()`, `get_artifact()`, `presign_get()`, `delete_run_prefix()`, `stream_upload`; `LocalObjectStore`/`R2ObjectStore` |
| `pipeline/jobs.py` | Arq job bodies + idempotency/heartbeat/concurrency helpers. | infr-13, infr-14 | `run_pipeline_job()`, `run_push_job()`, `requeue_or_fail_stale_runs()`, `assert_user_concurrency_ok()` |
| `pipeline/worker.py` | Arq worker settings/process entrypoint. | infr-13 | `WorkerSettings` |
| `alembic/` (`alembic.ini`, `alembic/env.py`, `alembic/versions/0001_baseline.py`) | DB migrations baseline for all tables/indexes/extensions. | infr-12 | baseline migration (`alembic upgrade head`) |
| `scripts/gc_sessions.py` | Nightly cron: purge expired sessions/artifacts/orphan rows. | infr-17 | `main()` (run via `python -m scripts.gc_sessions`) |
| `render.yaml` | 4-service Render Blueprint + Postgres + Key Value + env group. | infr-5 | services web/worker/cron, `sow-postgres`, `sow-redis`, `envVarGroups: sow-shared` |
| `.dockerignore` | Stop secrets/bulk dirs baking into the image. | infr-1 | exclude `.env`, `.github_token`, `data/`, `.git/`, `venv/`, `infra/`, etc. |
| `.github/workflows/ci.yml` | CI: pytest `--cov` floor + ruff F821 import-lint + pip-audit. | infr-20, test-4 | `on:[push,pull_request]`, ruff/pytest/pip-audit steps |
| `.importlinter` | Enforce core's inward-only dependency rule in CI. | harn-22 | forbidden + layered import contracts |
| `tests/fakes/__init__.py` (+ files) | Port fakes for tests above the agent line. | harn-23 | `FakeLLMProvider`, `InMemoryBundle`, `FakeObservability`, `FakeJiraGateway`, `FakeIndexer`, `FakeEmbeddingIndex`, `FakeObjectStore` |
| `tests/pipeline/test_stage.py` | StageResult/BaseStage degrade-don't-crash tests. | harn-2 | stage boundary tests |
| `tests/pipeline/test_context.py` | PipelineContext round-trip/record tests. | harn-3, orch-5 | context tests |
| `tests/pipeline/test_runner.py` | First test above the agent layer: sequencing/skip/resume/worst-status. | harn-9, orch-4 | runner tests |
| `tests/pipeline/test_pipelines.py` | Assert PEV order (coverage post-dedup) + flag toggles. | harn-21 | stage-order tests |
| `tests/contract/test_*_port.py` + `tests/contract/test_fakes_satisfy_ports.py` | Protocol contract tests (isinstance against ports). | harn-10..16, harn-23 | port/fake contract tests |
| `tests/unit/test_{domain_models,ids,runspec,enums,errors,agent_spec,agent_runner,gates,verify,health,prompts}.py` | Unit tests for new core types/gates/verifiers/health/prompts. | harn-4..8,17,18,19,19a,24,25 | core unit tests |
| `tests/test_tables.py` | SQLModel table/constraint round-trip tests. | doma-1 | table tests |
| `tests/test_enums.py` | Normalizing-enum coercion tests. | doma-2 | enum coercion tests |
| `tests/test_crypto.py` | MultiFernet round-trip + rotation tests. | doma-3, test-17 | crypto tests |
| `tests/test_eval_schema_contract.py` | GoldenTicket ⊆ RawTask field-subset contract. | doma-10 | eval schema contract test |
| `tests/test_audit.py` / `tests/test_audit_logger.py` | Postgres audit writes + user_id isolation. | doma-9, infr-8, test-17 | audit tests |
| `tests/test_llm_truncation.py` | Regression: finish_reason=length re-ask/raise, payload-sized max_tokens. | test-7, mode-2/3/4 | truncation regression tests |
| `tests/test_orchestrator_integration.py` | Stubbed-LLM orchestrator run: checkpoint shape, post-dedup coverage, DEGRADED. | test-13, orch-1/3/10 | orchestrator integration tests |
| `tests/test_api_routes.py` | TestClient: 401 unauth + cross-user IDOR 404 + OAuth domain gate + push smoke. | test-14, test-18, web-* | route/auth tests |
| `tests/test_jira_client.py` | Mocked-SDK Jira: parent fallback, error mapping, link direction, idempotency, 429. | test-15, jira-5/6/7/8 | jira client tests |
| `tests/test_object_store.py` | R2 key-layout (user-scoped) tests. | test-17, infr-9 | object store tests |
| `tests/eval/test_eval_bands.py` (+ `bands.yaml`, `cassette_103node.json`) | EvalHarness golden cassette CI gate (incomplete_rate ≤0.40, merges ≥1, zero conf-flags, no DEGRADED). | test-12, harn-26 | `EvalHarness`, bands gate |
| `docker-entrypoint.sh` | `SOW_ROLE` dispatch (web/worker/cron) for the image. | infr-4 | role-branching entrypoint |
| `scripts/check_{jira_api,jira_mcp,discovery,settings}.py` | Relocated root smoke scripts (targets of the moves above). | infr-21, test-3 | standalone check scripts |
| `dev-requirements.txt` (optional) | Separable dev/test tooling so prod image stays lean. | test-6 | ruff, pytest-cov, pip-audit, respx |

---

## 3. DELETIONS (dead code / dropped features removed)

- `integrations/jira_mcp_client.py` — unwired MCP Jira client (token-in-shell). [jira-9]
- `.github_token` — committed/leaked PAT; rotate + delete on disk. [infr-2]
- `scripts/verify-telemetry.py` — imports a non-existent symbol; verifies removed Argus path. [infr-18]
- `infra/user/*` and `infra/admin/*` — Argus/Bifrost/Tempo/Loki/self-hosted-Langfuse stack out of the deploy path. [infr-22]
- `tests/test_routing.py` — entirely tests the removed `_ensure_docker_host`. [test-16]
- `deduplication.py:368-370` — dead `if TaskFlag.POTENTIAL_DUPLICATE not in ...: pass` no-op. [agen-5]
- `LIKELY_DUPLICATE` — removed from `CritiqueIssue` enum, critic prompts, and `_apply`. [agen-10]
- `task_b.merged_from = [task_a.id]` (dedup merge branch) — backwards/dead lineage write. [agen-6]
- `_apply_settings_to_env_legacy` + `os.environ['LITELLM_*'/'JIRA_*'/'JIRA_PROJECT_KEY']` writes — process-global credential leaks. [web_-8, web_-9, jira-4, web_-18]
- `active_runs` / `active_orchestrators` / `MODEL_CACHE` — process-global dicts (broken under multi-worker). [web_-11]
- `BackgroundTasks` / `threading.Thread` pipeline+push execution in `ui/server.py` — replaced by arq. [web_-12]
- LLM router `LLMMode.LOCAL`/Bifrost/ZAI branches, `_ensure_docker_host`, ollama/zai registry entries. [mode-6, infr-23]
- llm_client unbounded local-Ollama 3600s retry loop + ZAI/Ollama `extra_headers`; per-instance OTel callbacks. [mode-9, mode-10]
- OTel/traceloop/Argus/Bifrost observability symbols (`init_argus`, `INSTANCE_ID`, `SYNC_ENABLED`, file sinks, `sync_telemetry`). [infr-15, infr-15b, infr-15c]
- `langchain_openai`/`langchain.prompts` from `pipeline/evals/judges.py`. [mode-13, test-10]
- Hard-coded `max_tokens=4096` and the regex-scrape JSON path in `complete_json` (last-resort fallback only). [mode-2, mode-3]
- `data/.keyfile` keyfile-write fallback (hosted mode has no writable disk). [doma-3, infr-7]
- Three bare-`pass` stubs in `tests/test_phase11_evals.py`; `test_local_remote_isolation`. [test-12, test-16]

## 4. NEW DEPENDENCIES (added to requirements)

Runtime / platform:
- `instructor` — structured LLM output over litellm. [mode-2, mode-14]
- `json-repair` — final JSON-parse fallback. [mode-2, mode-14]
- `langfuse` — LLM tracing callback (replaces OTel/Argus). [mode-9, mode-14]
- `pypdf` — replaces deprecated `PyPDF2`. [infr-3, infr-3b]
- `sqlalchemy[asyncio]`, `asyncpg`, `alembic` — Postgres data layer + migrations. [infr-3, infr-6, infr-12]
- `boto3` — R2/S3 object store. [infr-3, infr-9]
- `arq`, `redis` — background worker queue + progress/cancel. [infr-3, infr-13, infr-14]
- `authlib`, `itsdangerous` — Google OAuth + session signing. [infr-3, web_-4]
- `pgvector` — cross-run embedding store. [infr-3, agen-8, agen-13]

Removed: `opentelemetry-api/sdk/exporter-otlp/instrumentation-fastapi/instrumentation-logging`, `traceloop-sdk`, `PyPDF2`. [infr-3]

Dev / CI:
- `ruff` — lint (incl. F821 import-lint gate). [test-6, infr-20]
- `pytest-cov` — coverage floor. [test-6, infr-20]
- `pip-audit` — dependency CVE audit. [test-6, infr-20]
- `respx` — httpx mock transport for route tests. [test-6]
- `import-linter` — enforce core's inward-only dependency contract. [harn-22]

Cross-cutting: pin all deps `==` and commit a lockfile (uv/pip-tools). [mode-14, infr-3]
