# SOW-to-Jira — Harness-Engineered Re-Architecture

> Re-framing of the prior elevation work (`AUDIT.md`, `ELEVATION-PLAN.md`, `ARCHITECTURE.md`, `RENDER-MIGRATION.md`) through a single axis: **cleanly separate the CORE AI SYSTEM (model + prompts + the decision we ask it to make) from the HARNESS (the deterministic engineering shell that makes it reliable)**, so the AI half can evolve (swap models / rewrite prompts) without destabilizing the engineering half, and the harness supplies the maturity the model cannot.

## Thesis

The three headline failures of the first real run (103-node SOW → 509 tasks) are **harness gaps, not model gaps**:
- **100% of tickets flagged INCOMPLETE** — coverage verdict applied per-section, pre-dedup, with no confidence gate.
- **0 dedup merges on 100+ candidate pairs** — response truncated past the hard-coded `max_tokens=4096`, regex scrape failed, the agent's broad `except` silently returned the tasks unchanged.
- **Critic confidence=0.00 on 100% of flagged tasks** — flag paths fire with no confidence floor.

Fix the harness and the same model produces reliable output.

---

## 1. The Three Layers

### Core Model / Cognition — *the AI we ask to decide*
The non-deterministic half: a base model + a single well-scoped decision per agent. Owns **semantics only** — never I/O, never retries, never state, never whether its own verdict is trusted. Free to swap and re-prompt.

The six agents:
- `extraction` — which `RawTask`s live in a section
- `classifier` — actionable vs non-actionable section + confidence
- `deduplication` (LLM merge call) — which candidate pairs are true duplicates
- `critic` — per-task issues + suggested rewrites + a confidence
- `coverage_check` — what actionable deliverables were missed
- `gap_recovery` — tasks for structurally-uncovered nodes

The inline prompt strings (`EXTRACTION_SYSTEM_PROMPT`, `CRITIC_PROMPT_TEMPLATE`, `COVERAGE_PROMPT_TEMPLATE`, etc.) are cognition assets, not harness.

### Scaffolding — *pre-runtime assembly, versioned + tested independently*
Everything assembled **before** a run that shapes the conversation with the model: the system prompts, the per-agent Pydantic `response_model` that IS the boundary contract, the prompt-construction/truncation logic, and the controlled vocabularies (enums, AC shape). Today scattered as inline f-strings and ad-hoc dicts, untested in isolation. As a discipline, lifted out so a prompt or schema change is a reviewable, independently-testable diff that never touches runtime.

### Harness — *runtime orchestration, validation, gating, recovery, state*
The deterministic control shell. Owns: structured-output enforcement at the model boundary, computational verification, confidence gating before any verdict mutates a task, token/cost budgeting, per-stage health as typed results (OK|DEGRADED|FAILED), durable per-stage state (resumability), and the state-machine that sequences stages.

Today's harness lives almost entirely in two thin/broken seams:
- `pipeline/llm_client.py complete_json()` — hard-codes `max_tokens=4096` and regex-scrapes JSON; **the single thinnest, most-broken seam.**
- `pipeline/orchestrator.py run()` — a 270-line linear best-effort pass holding all state in memory, persisting only a final `pipeline_output.json`.

---

## 2. Harness-Lens Maturity Scorecard

| Area | Layer | Maturity | One-liner |
|------|-------|----------|-----------|
| Scaffolding (prompts, routing, response contracts) | boundary | **poc** | Response models exist but are never handed to the model call — the contract is fictional. |
| Structured-output enforcement & computational verification | boundary | **poc** | The thinnest seam: regex scrape, hard-coded 4096, silent-swallow — all three headline bugs trace here. |
| Control loop & orchestration | harness | **poc** | 270-line linear in-memory pass; no typed StageResult, no resumability, no gates between stages. |
| Guardrails, gating & error recovery | harness | **poc** | No confidence gate, no work-was-done check, no truncation check, no action guardrail on Jira push. |
| Evals, observability & cost control | harness | **poc** | Metrics emit to a NoOp meter; telemetry off by default; no cost tracking; eval harness gates nothing. |
| State, durability & isolation | harness | **poc** | Runs die with the worker; provider/creds are process-global (cross-tenant bleed). |

---

## 3. Key Findings (each tied to the violated harness principle)

### Boundary

1. **Response models exist but are never handed to the model call — the boundary contract is fictional.** *(critical)* `complete_json()` returns an untyped dict regex-scraped from free text; the Pydantic models parse the scrape after the fact inside skip-on-error logic. *Principle: structured output is the contract, not a parse step.* **Fix:** make `complete_json` take a `response_model` enforced via Instructor over LiteLLM; the only thing crossing the seam is a valid object or a typed FAILED.

2. **Hard-coded `max_tokens=4096` silently truncates large payloads.** *(critical)* The dedup call serializes all candidate pairs into one prompt; over 509 tasks the response exceeds 4096, `finish_reason=length` is ignored, the scrape fails, 0 merges read as success. *Principle: size the token budget to the payload; never hard-code it.* **Fix:** size/escalate output budget per call; treat `finish_reason=length` as a first-class failure; batch dedup pairs.

3. **Every agent swallows LLM/parse failures and returns inputs unchanged.** *(critical)* `critic.py:200-208`, `coverage_check.py:180-188`, `deduplication.py:336-347` all return inputs on any error; the run continues green. *Principle: failure is data, never a swallowed exception.* **Fix:** each stage returns a typed `StageResult(OK|DEGRADED|FAILED, reason)`; ban the `except: return tasks` pattern.

### Harness — guardrails & verification

4. **No confidence gate: coverage stamps 100% INCOMPLETE ignoring `checker_confidence`.** *(critical)* `orchestrator.py:284-288` flags every task in a section whenever `missed_items` is non-empty, per-section, pre-dedup, never reading `checker_confidence`. *Principle: no model verdict mutates a task below its confidence gate.* **Fix:** gate INCOMPLETE on `checker_confidence >= floor`, applied once post-dedup at batch level.

5. **Critic flags fire at confidence 0.00.** *(critical)* `_apply` gates auto-fixes on confidence but the flag paths are ungated; a `conf=0.00` critique still flags. *Principle: no model verdict mutates a task below its confidence gate; inferential verifiers must themselves be gated.* **Fix:** apply a confidence floor symmetric across fix and flag paths.

6. **No work-was-done sanity check.** *(high)* Dedup over 100+ candidates yielding 0 merges reads as success. *Principle: computational verification — did this stage actually do work.* **Fix:** `assert_work_done(before, after)` → DEGRADED when `before > N and before == after`.

7. **No action guardrail on Jira push.** *(high)* The push path selects only `TaskStatus.APPROVED`; flagged or DEGRADED-run tasks can be pushed. The flags become Jira labels, never block. *Principle: tool/action guardrails before an irreversible side effect.* **Fix:** `PushGate` blocks tasks carrying blocking flags or from a DEGRADED/FAILED run without explicit override.

### Harness — control plane & state

8. **`run()` is a 270-line linear pass with no typed per-stage result.** *(critical)* A degraded stage is indistinguishable from a healthy one. *Principle: per-stage health as typed StageResult; failure is data.* **Fix:** a `Stage` protocol + `StageRunner` that owns the one exception boundary, timing, verify, gate, persist.

9. **No PEV ordering — verify stages run before dedup and mutate inline.** *(high)* Critic and coverage (the LLM-as-judge verifiers) run per-node before dedup and apply ungated verdicts immediately. *Principle: plan → execute → verify; only gated verdicts mutate state.* **Fix:** node loop = plan/extract only; dedup = execute; critic+coverage = post-dedup gated batch verify.

10. **All run state in memory; only a terminal checkpoint — no resumability.** *(high)* A crash at node 80/103 loses everything. *Principle: stages are deterministic and resumable.* **Fix:** persist each StageResult to `data/sessions/<run_id>/stages/`; resume from the last good stage.

11. **Provider/model/creds from process-global settings + `os.environ` — cross-tenant bleed.** *(critical)* One credential and one model for the whole process; concurrent runs can push to the wrong Jira or call the wrong model. *Principle: environment is injected per run, not read from process globals.* **Fix:** resolve a validated `ProviderConfig` once at a composition root and inject it into the run and every client.

### Harness — evals, observability & cost

12. **The measurement half is absent or disconnected.** *(high)* OTel metrics record into a NoOp meter (no `MeterProvider` in app code); all telemetry is gated behind `SYNC_ENABLED` which defaults OFF; no cost tracking anywhere; the eval harness is fully out-of-band (gates nothing, not in CI). *Principle: cost and health are first-class run outputs; verify both ways and gate on both.* **Fix:** construct a `MeterProvider`; decouple local health recording from remote sync; add a `CostMeter` (`litellm.completion_cost`) rolled into a per-run `RunHealthReport`; wire a golden-set eval into CI with quality bands.

---

## 4. The Boundary Contract

A typed Pydantic `response_model` per agent call **plus** a harness wrapper that validates, confidence-gates, and converts to a `StageResult` before any downstream code trusts the value.

- extraction → `list[RawTask]`
- classifier → `SectionClassification{type, confidence}`
- dedup → a list of merge decisions over candidate pairs
- critic → `TaskCritique{issues, suggested_title, suggested_acceptance_criteria, confidence, reason}`
- coverage → `SectionCoverageReport{missed_items[], checker_confidence}`
- gap_recovery → a recovered-tasks model

Today the contract is fictional: the model returns free text, `complete_json()` regex-scrapes it, and only then do the Pydantic models parse the scrape inside a swallowing try/except. The re-architecture hands the `response_model` to the model call itself (Instructor/JSON-mode), so the **only** thing that can cross the seam is a schema-valid object or a typed failure. The wrapper then enforces the half the schema cannot: a **confidence floor**, a **work-was-done** check, and a **finish_reason** check. Every `confidence` float is part of the contract and is gated at the seam, never applied verbatim.

---

## 5. The Elevated Harness-Engineered Architecture

### 5.1 The seam: `AgentSpec` (scaffolding) + `AgentRunner` (harness)
Split the fused agent into two named artifacts with one typed contract between them.

- **`AgentSpec[I, O]`** — a frozen, versioned, declarative cognition object: system prompt, prompt-builder, `response_model` (the contract), `ModelPolicy` (tier/temperature/budget), and the `gate` thresholds. Lifted into `pipeline/specs/` so a prompt/schema change is a reviewable diff with fixture tests.
- **`AgentRunner`** — one deterministic wrapper, instantiated per run from the composition root with the run's `ProviderConfig`. `run(spec, input)` is the single path every model call takes: build prompt → call model in structured-output mode → size `max_tokens` to payload → reject `finish_reason=length` → reprompt on schema error → run `spec.gate` → record cost/health → return `StageResult[O]`. Never returns inputs-unchanged, never raises into the agent.

The six agent classes shrink to one-liners: `self.runner.run(CRITIC_SPEC, CriticInput(...))`.

```python
StageResult[O](BaseModel):
    status: Literal['OK','DEGRADED','FAILED']
    output: O | None; reason: str; agent: str; spec_version: str
    tokens_in: int; tokens_out: int; cost_usd: float
    latency_ms: int; finish_reason: str
```

### 5.2 The control plane: a typed PEV pipeline runner
Replace the linear `run()` with a `Stage` protocol + `StageRunner` + `PipelineRunner` state machine. The `StageRunner` — not the agent — owns the single exception boundary, timing, the work-was-done check, durable persistence, and the predecessor-gate.

Stage list encodes the **Plan-Execute-Verify** sandwich:
- **PLAN:** Classify (cheap model gate) → Extract (candidate tasks)
- **EXECUTE:** State (deterministic lifecycle) → Dedup (vector + LLM merge)
- **VERIFY:** Coverage (post-dedup, batch) → Critic → GapRecovery
- **PERSIST:** Checkpoint

`RunResult.status = worst(stage statuses)`. A predecessor that FAILED → dependents SKIPPED (recorded, not silent). PEV re-ordering alone fixes two of three headline bugs: coverage no longer flags 509 per-section duplicates as INCOMPLETE, and verify verdicts land only after the corpus is shaped. Each `StageResult` is persisted, enabling `RunConfig.resume_from`.

### 5.3 Guardrails & verification band: `core/guardrails/`
A thin, deterministic, fully-tested band every agent output crosses **after** the schema check and **before** any mutation.
- **`verify.py`** (computational, always-on, no LLM): `assert_work_done`, `assert_finish_complete`, `assert_nonempty_when_expected` → typed `VerifyVerdict`.
- **`gate.py`** (output + action guardrails): `CriticGate.admit_flags` drops flags below the floor (kills conf=0.00 flagging); `CoverageGate.apply` is run-wide, post-dedup, report-level INCOMPLETE gated on `checker_confidence` (fixes 100% INCOMPLETE); `PushGate.assert_pushable` blocks Jira push for flagged or DEGRADED-run tasks.

Floors are **config**, not code, so retuning is a settings change and is unit-tested with no model in the loop.

### 5.4 Eval, observability & cost harness
Four deterministic capabilities, all in the harness, none in the agents:
1. **Record/replay cassettes + eval bands in CI** — intercept at the shared `complete()` seam; an `EvalHarness` replays a frozen 103-node fixture and asserts quality bands (`incomplete_rate <= 0.40`, `merge_count >= 1`, `zero_conf_flags == 0`, `no DEGRADED`) — the three production failures turned into red CI. Bands live in `tests/eval/bands.yaml`.
2. **Per-run `RunHealthReport`** — per-stage `StageHealth`, derived `run_status`, headline metrics, persisted to `health.json` and surfaced as a DEGRADED badge in the UI instead of a green "Pipeline Complete".
3. **Tracing via a `TracingPort`** — one Langfuse adapter injected from the composition root, keyed by `user_id`/`run_id`.
4. **Per-call cost tracking** — `CostMeter` via `litellm.completion_cost`, accumulated into the run report; the Denial-of-Wallet node cap gains a `max_run_cost_usd` companion.

`complete()` returns a typed `LLMResult{content, finish_reason, tokens, usd_cost}` instead of a bare string, so finish_reason and cost cross the seam.

---

## 6. Top Structural Shifts

1. **Make the boundary contract real.** Hand the Pydantic `response_model` to the model call (Instructor/JSON-mode), delete the regex scrape, delete the after-the-fact swallowing parse. The only thing crossing the seam is a valid object or a typed FAILED.
2. **Replace silent-swallow with typed StageResult.** Ban `except: return inputs` across all six agents; one exception boundary in `StageRunner`; failure becomes visible run state.
3. **Move every confidence gate to the harness apply-site.** Symmetric across critic fix/flag; coverage INCOMPLETE gated on `checker_confidence`, post-dedup, batch-level.
4. **Inject environment per run and make stages durable/resumable.** `ProviderConfig` from a composition root (no `os.environ`); per-stage persistence; PEV state machine instead of a 270-line linear pass.

---

## 7. Minimum-Viable Harness (build FIRST)

1. **Structured output at the seam** — `complete_json` takes a `response_model`, enforced via Instructor; delete the regex scrape.
2. **Kill the 4096 truncation** — size `max_tokens` to payload; treat `finish_reason=length` as a typed failure; batch dedup pairs.
3. **Typed `StageResult` + ban silent-swallow** — every agent returns OK|DEGRADED|FAILED with a reason.
4. **The two confidence gates** — `CriticGate.admit_flags` (no conf=0.00 flags) and `CoverageGate.apply` (gated on `checker_confidence`, post-dedup batch).
5. **Work-was-done check** — dedup over 100+ candidates merging 0 → DEGRADED.
6. **Per-run health report** — surface OK|DEGRADED|FAILED + cost so a degraded run is visibly distinct from a clean one.

These six close all three headline failures without touching a single prompt.
