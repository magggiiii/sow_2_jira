# Phase 2: Runtime Logging Reliability - Research

**Researched:** 2026-04-05  
**Domain:** PageIndex/LiteLLM runtime reliability (timeouts, retries, cancellation, degradation)  
**Confidence:** HIGH

## User Constraints

No `CONTEXT.md` exists for this phase directory (`.planning/phases/02-runtime-logging-reliability`), so there are no additional locked decisions/discretion/deferred items beyond the objective in this request.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PIPE-01 | Each pipeline run logs active provider/model/base exactly once at run start. | Keep current run-summary behavior; add retry-budget/circuit-breaker logs under PageIndex so failures remain single-run scoped. |
| PIPE-02 | Cancellation cleanly stops retries/background processing. | Replace unbounded retry semantics with event-aware stop conditions and bounded total retry budget per stage. |
| PIPE-03 | PageIndex logging path never crashes when logger callbacks are missing. | Preserve logger-default guards and standardize `status_callback` optional checks in all retry paths. |

## Project Constraints (from CLAUDE.md)

- Runtime must remain Python 3.11 + FastAPI + LiteLLM compatible.
- Deployment remains Docker-first and reproducible via compose.
- Persisted credentials must stay encrypted at rest.
- Existing extraction and Jira push flows must not regress.
- Logs/traces must remain debuggable from terminal and Grafana/Bifrost.

## Summary

The production hang pattern is explained by stacked retry loops in PageIndex LLM stages. `pageindex/utils.py` retries each LiteLLM call multiple times with long per-attempt timeouts, and `pageindex/page_index.py` (`toc_transformer`) adds an outer retry/continue loop. In failure mode (Ollama timeout), this multiplies into very long wall-clock hangs before a hard exception.

Cancellation is partially wired (`stop_event` passed end-to-end), but current retry behavior still allows long wait windows before loop exit. In `pipeline/llm_client.py`, Tenacity is configured with `stop_never` and retries on broad `Exception`, which is risky for cancellation containment and can create effectively unbounded retry behavior.

Primary corrective strategy: enforce explicit retry budgets (attempt + total elapsed), make retries cancellation-aware (including backoff sleep), and degrade gracefully from TOC-heavy PageIndex paths to a no-TOC strategy before hard-failing the run.

**Primary recommendation:** Implement a unified `RetryBudget` + `CancellationAwareRetry` policy in `pageindex/utils.py` and apply it to all TOC calls; cap total PageIndex LLM time per run and fallback to `process_no_toc` when TOC transform fails.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| LiteLLM | `>=1.82.0` (repo floor), local env shows `1.82.6` | Unified provider calls (`completion`, `acompletion`, timeout control) | Already integrated across pipeline and supports timeout/retry params. |
| Tenacity | existing dependency (local env `8.5.0`) | Retry policy composition, stop conditions, exponential backoff | Better than hand-rolled retry loops; supports event-based stop controls. |
| `threading.Event` | stdlib | Cross-layer cancellation signal (`ui` -> orchestrator -> indexer/pageindex) | Already the project’s cancellation primitive. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `asyncio` (Python 3.11) | stdlib | Async timeout/cancel boundaries in PageIndex async stages | Wrap expensive async operations and enforce per-stage deadlines. |
| `rich` | existing dependency | User-facing progress/status lines | Emit concise retry/degradation logs without spam. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Tenacity-driven bounded retry | Fully custom retry loops everywhere | Higher bug risk (sleep cancellation, jitter, stop conditions, observability duplication). |
| Graceful fallback to no-TOC mode | Immediate hard failure on TOC transform failure | Hard fail is simpler but causes avoidable run loss when extraction can continue. |

**Installation:**
```bash
pip install -r requirements.txt
```

**Version verification (local env check used for this research):**
```bash
pip3 show litellm tenacity fastapi httpx pydantic
```

## Architecture Patterns

### Recommended Project Structure
```text
pipeline/
├── llm_client.py           # Agent-level LLM calls (non-PageIndex)
├── indexer.py              # PageIndex wrapper + run-level fallback boundaries
└── orchestrator.py         # Run lifecycle + cancellation ownership

pageindex/
├── utils.py                # Shared LLM call policy (timeouts/retries/budget)
└── page_index.py           # TOC pipeline with mode fallback
```

### Pattern 1: Retry Budget Object (Required)
**What:** Add a run-scoped retry budget object carrying `deadline_ts`, `max_attempts`, and `stop_event`.  
**When to use:** Every `llm_completion` / `llm_acompletion` call in PageIndex TOC and summary generation paths.  
**Example:**
```python
# Source: project code + Tenacity API docs
@dataclass
class RetryBudget:
    max_attempts: int
    max_elapsed_s: float
    per_attempt_timeout_s: float
    stop_event: threading.Event | None = None
    started_at: float = field(default_factory=time.monotonic)

    def expired(self) -> bool:
        return (time.monotonic() - self.started_at) >= self.max_elapsed_s
```

### Pattern 2: Cancellation-Aware Backoff Sleep
**What:** During retry backoff, sleep in short slices and exit immediately if `stop_event` is set.  
**When to use:** All retry loops in `pageindex/utils.py` and `pipeline/llm_client.py`.  
**Example:**
```python
def sleep_with_cancel(total_s: float, stop_event: threading.Event | None) -> None:
    end = time.monotonic() + total_s
    while time.monotonic() < end:
        if stop_event and stop_event.is_set():
            raise RuntimeError("Cancelled during backoff")
        time.sleep(0.2)
```

### Pattern 3: Degrade TOC Path Before Failing Run
**What:** If `toc_transformer` exceeds budget or max attempts, fallback to `process_no_toc` in the same run.  
**When to use:** `meta_processor()` when TOC modes fail due to timeout/connection errors.  
**Example:**
```python
try:
    toc_with_page_number = process_toc_with_page_numbers(...)
except (TimeoutError, RuntimeError) as e:
    logger.warning(f"TOC mode degraded: {e}; falling back to process_no_toc")
    toc_with_page_number = process_no_toc(...)
```

### Anti-Patterns to Avoid
- **Nested uncoordinated retries:** Per-call retries plus outer transformation retries without shared budget cause runaway duration.
- **`stop_never` with broad `Exception` retries:** Can undermine cancellation and keep loops alive indefinitely.
- **Blocking sleeps without cancel checks:** Adds user-visible cancel lag.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry policy | ad-hoc `while True` + sleep | Tenacity stop/wait strategy + explicit budget checks | Prevents infinite loops and centralizes retry behavior. |
| Cancellation during backoff | blind `time.sleep()` | event-aware sliced sleep | Enables near-immediate cancellation. |
| TOC hard-failure control | throw raw `Exception` after many nested attempts | typed exceptions + fallback mode in `meta_processor` | Keeps extraction alive under partial PageIndex failure. |

**Key insight:** Reliability here is a control-plane problem (retry/cancel budget), not just a timeout value problem.

## Common Pitfalls

### Pitfall 1: Multiplicative Retry Explosion
**What goes wrong:** One timeout at LLM call layer is retried repeatedly, then retried again by TOC transformation logic.  
**Why it happens:** Retry policy is local to functions; no shared elapsed-time budget.  
**How to avoid:** Introduce stage-level deadline + per-call attempt cap + single owner of retry policy.  
**Warning signs:** Logs show repeated `Timeout after 60s` then `max retries reached` many times before final exception.

### Pitfall 2: Cancellation Not Taking Effect Quickly
**What goes wrong:** User presses cancel but system waits through long timeout/backoff windows.  
**Why it happens:** Cancellation is checked before attempt, not during sleep/wait paths.  
**How to avoid:** Check `stop_event` before attempt, after exception, and during backoff sleep slices.  
**Warning signs:** `/api/cancel/{run_id}` returns success but run logs continue for minutes.

### Pitfall 3: Hard-Fail Instead of Degrade
**What goes wrong:** TOC transform failure aborts full pipeline even when non-TOC extraction path is viable.  
**Why it happens:** `toc_transformer` raises terminal exception with no fallback switch.  
**How to avoid:** Catch typed retry-budget/timeout exceptions and route to `process_no_toc`.

## Code Examples

Verified patterns from official sources + project fit:

### Event-Aware Tenacity Stop (for `pipeline/llm_client.py`)
```python
from tenacity import retry, stop_any, stop_after_attempt, stop_after_delay, stop_when_event_set

@retry(
    stop=stop_any(
        stop_after_attempt(3),
        stop_after_delay(120),
        stop_when_event_set(stop_event),
    ),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _do_call():
    ...
```

### Bounded PageIndex Completion Wrapper (for `pageindex/utils.py`)
```python
def llm_completion(..., retry_budget: RetryBudget | None = None):
    budget = retry_budget or RetryBudget(max_attempts=3, max_elapsed_s=180, per_attempt_timeout_s=45)
    attempt = 0
    while attempt < budget.max_attempts and not budget.expired():
        ...
    raise RuntimeError("PageIndex LLM budget exhausted")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Open-ended retry semantics (`stop_never`, broad exception retries, nested loops) | Budgeted retries + cancellation-aware stop + graceful fallback | Recommended now (Phase 2 reliability remediation) | Prevents multi-minute hangs and improves cancel responsiveness. |

**Deprecated/outdated:**
- Unbounded retries in reliability-critical stages.
- Hard exception after TOC retries without fallback path.

## Open Questions

1. **Fallback quality bar for degraded TOC mode**
   - What we know: `process_no_toc` path exists and can proceed without TOC transformation.
   - What's unclear: Minimum acceptable extraction quality before auto-aborting.
   - Recommendation: Add a guardrail threshold (`min_nodes_extracted`) and log fallback cause in run summary.

2. **Target timeout profile per provider/model size**
   - What we know: Current behavior uses long local timeout windows.
   - What's unclear: Best timeout for your actual Ollama model + hardware.
   - Recommendation: Start with conservative defaults below; tune via run telemetry percentile.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11 | Runtime constraint + asyncio behavior assumptions | ✗ (host has 3.9.6) | 3.9.6 | Run/test inside Docker image (`python:3.11-slim`) |
| Docker | Compose-based local stack | ✓ | 29.3.1 | — |
| Ollama CLI/service | Local model inference path | ✗ (CLI crashes on this host) | — | Use Docker Ollama overlay service or non-local provider for verification |
| pytest | Validation architecture | ✓ | 8.4.2 | — |

**Missing dependencies with no fallback:**
- None (Docker fallback exists for Python 3.11 runtime).

**Missing dependencies with fallback:**
- Host Ollama CLI instability; use Dockerized Ollama service path for this phase validation.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.4.2 (+ pytest-asyncio) |
| Config file | none detected |
| Quick run command | `python3 -m pytest tests/test_routing.py test_discovery.py -q` |
| Full suite command | `python3 -m pytest -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PIPE-01 | Single provider/model/base summary per run | integration/log assertions | `python3 -m pytest tests/test_phase2_runtime_reliability.py::test_run_summary_once -q` | ❌ Wave 0 |
| PIPE-02 | Cancellation terminates retry loops quickly | integration (cancel + simulated timeout) | `python3 -m pytest tests/test_phase2_runtime_reliability.py::test_cancel_stops_pageindex_retries -q` | ❌ Wave 0 |
| PIPE-03 | Missing logger/status callbacks do not crash PageIndex | unit | `python3 -m pytest tests/test_phase2_runtime_reliability.py::test_pageindex_logger_none_safe -q` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_phase2_runtime_reliability.py -q`
- **Per wave merge:** `python3 -m pytest -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_phase2_runtime_reliability.py` — timeout/retry budget + cancellation behavior for PageIndex and LLMClient.
- [ ] Deterministic retry fixture/mocks (LiteLLM timeout simulation).
- [ ] Add log-capture assertions for PIPE-01 single summary emission.

## Prioritized Implementation Guidance

1. **P0: Stop unbounded retry semantics in `pipeline/llm_client.py`**
   - Replace `stop_never` with `stop_any(stop_after_attempt, stop_after_delay, stop_when_event_set)`.
   - Remove broad retry on all `Exception`; retry only transient classes (`Timeout`, `APIConnectionError`, rate limit/server errors).
   - Treat cancellation exception as non-retryable.

2. **P0: Enforce PageIndex stage budget in `pageindex/utils.py` and `pageindex/page_index.py`**
   - Add env-driven knobs:
     - `PAGEINDEX_LLM_TIMEOUT_S` (default `45`)
     - `PAGEINDEX_LLM_MAX_ATTEMPTS` (default `2`)
     - `PAGEINDEX_STAGE_BUDGET_S` (default `180`)
   - Pass one shared budget object into TOC-related calls so nested retries consume the same budget.

3. **P0: Graceful degradation path in `meta_processor`**
   - On TOC transform budget exhaustion, fallback to `process_no_toc` once.
   - Only hard-fail if fallback path also fails or cancellation is requested.
   - Emit explicit telemetry event: `pageindex.degraded` with reason and attempt/time counters.

4. **P1: Make backoff cancellation-aware**
   - Replace monolithic sleep with event-aware sliced waits (<=200ms slices).
   - Ensure cancel can break between retries, not only before call start.

5. **P1: Fix current syntax defect before reliability rollout**
   - `pageindex/page_index.py` currently has a syntax error at `find_toc_pages` line 362 (merged statement).
   - This must be corrected first; otherwise runtime reliability changes cannot be validated.

6. **P2: Add deterministic tests for PIPE-01/02/03**
   - Mock LiteLLM timeout errors and assert elapsed wall-time stays within budget.
   - Assert cancel path exits within bounded threshold.
   - Assert logger/callback optionality remains safe.

## Sources

### Primary (HIGH confidence)
- LiteLLM input params doc (timeout + `num_retries` support): https://docs.litellm.ai/docs/completion/input
- Tenacity API (stop conditions incl. `stop_after_attempt`, `stop_after_delay`, `stop_when_event_set`): https://tenacity.readthedocs.io/en/latest/api.html
- Python 3.11 asyncio task/timeout behavior (`wait_for` cancellation semantics): https://docs.python.org/3.11/library/asyncio-task.html
- Local code evidence:
  - `pageindex/utils.py` retry/timeout behavior
  - `pageindex/page_index.py` TOC retry loops + terminal exception
  - `pipeline/llm_client.py` Tenacity config
  - `pipeline/indexer.py` PageIndex integration
  - `ui/server.py` cancel endpoint and active orchestrator signaling

### Secondary (MEDIUM confidence)
- None required.

### Tertiary (LOW confidence)
- None required.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all recommendations align with existing stack and official docs.
- Architecture: HIGH - based on direct code-path inspection in this repo.
- Pitfalls: HIGH - reproduced from observed logs and current retry topology.

**Research date:** 2026-04-05  
**Valid until:** 2026-05-05
