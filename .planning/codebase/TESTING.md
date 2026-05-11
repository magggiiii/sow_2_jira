# Testing Patterns

**Analysis Date:** 2026-05-11

## Test Framework

**Runner:**
- `pytest >= 8.0.0` (declared in `requirements.txt`).
- Async tests use `pytest-asyncio >= 0.23.0` (declared in `requirements.txt`).
- Config: none. No `pytest.ini`, `pyproject.toml`, `setup.cfg`, or `conftest.py` exists at the repository root, so pytest runs with all default settings (discovery from the current working directory, default `testpaths`, no markers registered, no asyncio mode declared).

**Assertion Library:**
- Built-in `assert` statements from pytest. No third-party assertion library is used.

**Mocking utilities:**
- `unittest.mock` from the standard library — `MagicMock`, `AsyncMock`, `patch` (see `test_discovery.py`, `tests/test_hierarchical_judge.py`).
- `monkeypatch` pytest fixture for environment variables and module-level attribute substitution (see `tests/test_phase2_runtime_reliability.py`).
- Lightweight stub classes (`DummyAuditLogger`, `DummyHTTPError`, `MockResponse`) defined inline within the test file rather than shared fixtures.

**Run Commands:**
```bash
venv/bin/pytest tests/                                    # Run the automated suite
venv/bin/pytest tests/test_routing.py                     # Run one file
venv/bin/pytest tests/test_routing.py::test_ensure_docker_host_with_localhost  # Run one test
venv/bin/pytest tests/ -k "retry"                         # Filter by name
venv/bin/pytest tests/ -v                                 # Verbose output
```

There is no `make test` target in `Makefile`; invoke pytest directly via the project virtualenv at `venv/bin/pytest`.

## Test File Organization

**Location:**
- Automated pytest suite lives under `tests/` at the repository root.
- `tests/` does not contain a `conftest.py` or `__init__.py`; each test file is self-contained.

**The two categories of `test_*.py` in this repo (important distinction):**

1. **`tests/` — automated pytest suite (run in CI / locally with `pytest tests/`):**
   - `tests/test_routing.py` — pure unit tests over `config.settings._ensure_docker_host`.
   - `tests/test_phase2_runtime_reliability.py` — unit tests over `pipeline.llm_client` retry/cancellation behavior, using `monkeypatch` and `SimpleNamespace` stubs.
   - `tests/test_phase11_evals.py` — schema + import sanity tests for `models.eval_schemas` and `scripts.run_eval_dataset`; live-environment tests are stubbed out with `pass`.
   - `tests/test_hierarchical_judge.py` — unit tests over `pipeline.evals.judges.HierarchicalJudge` with `MagicMock`ed LLM.

2. **Repository-root `test_*.py` — standalone manual sanity scripts (NOT picked up by `pytest tests/`):**
   - `test_jira_api.py` — connects to the live Jira REST API; requires `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` env vars.
   - `test_jira_mcp.py` — exercises the MCP-based Jira client; spawns `npx @atlassian/mcp-remote`.
   - `test_discovery.py` — async tests over `ui.server.get_provider_models` model-discovery endpoint.
   - `test_settings.py` — tests over `config.settings.SettingsManager` (encryption, corruption handling, model name building).
   - These files use `pytest` syntax but are *not* invoked when running `pytest tests/`. Some have an `if __name__ == "__main__":` entry point and can be run directly with `python test_jira_api.py`. Treat them as manual scripts that require live credentials; do not assume they pass in CI.

**Where to add new tests:** Put new automated tests under `tests/`. Reserve repo-root `test_*.py` for one-off manual scripts that require live external systems.

**Naming:**
- File name: `test_<area>.py` (e.g. `test_routing.py`, `test_phase2_runtime_reliability.py`).
- Test function name: `test_<behavior_under_test>` in `snake_case` (e.g. `test_ensure_docker_host_with_localhost`, `test_remote_retry_after_header_takes_precedence`, `test_judge_parsing`).

**Structure:**
```
tests/
├── test_routing.py
├── test_phase2_runtime_reliability.py
├── test_phase11_evals.py
└── test_hierarchical_judge.py
```

No subdirectories for unit vs. integration; the suite is flat.

## Test Structure

**Suite Organization:**
- Each test is a top-level `def test_...` function. No `class Test...` containers are used.
- Test files are flat collections of related functions; grouping is implicit through file naming.

```python
# tests/test_phase2_runtime_reliability.py
def test_remote_retry_after_header_takes_precedence():
    err = DummyHTTPError(
        "rate limited",
        status_code=429,
        headers={"Retry-After": "17", "x-ratelimit-reset": "9999999999"},
    )
    hint = llm_mod.extract_retry_hint(err)
    assert hint is not None
    assert hint.source == "retry-after-header"
    assert hint.wait_seconds == 17.0
    assert llm_mod.compute_wait_seconds(hint, attempt=3, max_wait_s=300) == 17.0
```

**Patterns:**
- Arrange/Act/Assert is followed informally — most tests are 5-15 lines, no shared setup.
- Build small stub classes inline at the top of the test file when a dependency must be impersonated (e.g. `DummyAuditLogger` and `DummyHTTPError` in `tests/test_phase2_runtime_reliability.py`).
- Provide a small builder helper when several tests need the same constructed object (e.g. `_build_client(...)` in `tests/test_phase2_runtime_reliability.py`).
- Tests print confirmation strings on success (`print("✓ Judge parsing verified")`) in some files. This is a holdover style; do not rely on print output for assertions.

**Setup / Teardown:**
- No `setUp` / `tearDown` or pytest fixtures defined in this repo.
- Use `monkeypatch` for environment and attribute patching with automatic teardown:
  ```python
  monkeypatch.setenv("LLM_REMOTE_MAX_ATTEMPTS", "2")
  monkeypatch.setattr(llm_mod, "_sleep_with_cancel", lambda total_s, stop_event: None)
  ```
- Use `tmp_path` (pytest builtin) for tests that need a filesystem sandbox (see `test_settings.py::test_corrupted_settings_throws_error`).

## Mocking

**Framework:** `unittest.mock` (stdlib) + pytest's `monkeypatch`.

**Patterns:**

*MagicMock on instance attributes* (from `tests/test_hierarchical_judge.py`):
```python
judge = HierarchicalJudge()
judge.llm = MagicMock()
judge.llm.invoke.return_value = MockResponse(
    '```json {"alignment": 1.0, "recall": 0.8, ...} ```'
)
scores = judge.evaluate("Source", {"expected": "truth"}, [{"actual": "test"}])
```

*AsyncMock + patch context manager* (from `test_discovery.py`):
```python
with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
    mock_get.return_value = mock_response
    result = await get_provider_models(provider_id, req)
```

*monkeypatch for module-level attributes and env vars* (from `tests/test_phase2_runtime_reliability.py`):
```python
monkeypatch.setattr(llm_mod.random, "uniform", lambda a, b: 0.0)
monkeypatch.setattr(llm_mod.litellm, "completion", lambda **kwargs: raise_rate_limit())
monkeypatch.setenv("LLM_REMOTE_MAX_ATTEMPTS", "2")
```

*Inline stub classes* — preferred over `MagicMock` when the shape of the collaborator is simple and stable:
```python
class DummyAuditLogger:
    def log(self, **kwargs):
        return None
```

**What to Mock:**
- External LLM SDKs (`litellm.completion`, `judge.llm.invoke`) — never make real network calls in unit tests.
- HTTP clients (`httpx.AsyncClient.get`) — patch at the boundary.
- Timing primitives (`random.uniform`, `time.sleep`) and the project's own `_sleep_with_cancel` helper — keep tests deterministic and fast.
- Audit logger and other side-effect sinks — use minimal `DummyAuditLogger`-style stubs.

**What NOT to Mock:**
- Pydantic schemas in `models/schemas.py` — instantiate them with real data.
- Pure helpers under test (e.g. `_ensure_docker_host`, `extract_retry_hint`, `compute_wait_seconds`, `is_retryable_remote_error`, `build_litellm_model`) — call them directly.
- `monkeypatch` itself — let it manage its own teardown.

## Fixtures and Factories

**Test Data:**
- Built inline using Pydantic model constructors and `SimpleNamespace` for SDK response shapes.

```python
# tests/test_phase2_runtime_reliability.py
def _fake_response(content="ok", finish_reason="stop", prompt_tokens=4, completion_tokens=3):
    usage = SimpleNamespace(
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)
```

```python
# tests/test_phase11_evals.py
ticket = GoldenTicket(
    title="Test Ticket",
    short_description="This is a test description",
    acceptance_criteria=["AC 1", "AC 2"],
)
```

**Location:**
- No dedicated fixtures directory. Test data lives inline in each test file. There is no `conftest.py`.

## Coverage

**Requirements:** None enforced. No coverage configuration, no minimum thresholds, no CI gate.

**Tooling:** `coverage` / `pytest-cov` are not declared in `requirements.txt`. The `.gitignore` does include `.coverage` and `htmlcov/` as placeholders, so coverage runs can be added without polluting git, but they are not part of the standard workflow today.

**Adding coverage locally (if needed):**
```bash
venv/bin/pip install coverage
venv/bin/coverage run -m pytest tests/
venv/bin/coverage report -m
venv/bin/coverage html        # writes to htmlcov/
```

## Test Types

**Unit Tests (the bulk of the suite):**
- Pure-function tests in `tests/test_routing.py` and parts of `tests/test_phase2_runtime_reliability.py`.
- Class-level behavior tests with mocked collaborators in `tests/test_hierarchical_judge.py`.
- Scope: a single function or method; collaborators are stubbed or mocked.

**Integration / Live-Environment Tests:**
- The repo-root manual scripts (`test_jira_api.py`, `test_jira_mcp.py`) exercise real external systems and require live env vars.
- `tests/test_phase11_evals.py` reserves slots for live Langfuse / Bifrost tests but currently leaves them as `pass` stubs — they are not implemented.

**E2E Tests:**
- Not used. No browser/Playwright/Selenium suite. The frontend (`ui/index.html`, `ui/app.js`) is not exercised by automated tests.

## Common Patterns

**Async testing** (requires `pytest-asyncio`):
```python
# test_discovery.py, test_jira_mcp.py
@pytest.mark.asyncio
async def test_get_provider_models_caching():
    MODEL_CACHE.clear()
    # ... await calls under test
```
Note: there is no `asyncio_mode = auto` setting; you MUST decorate each async test with `@pytest.mark.asyncio`.

**Error testing** — assert that a domain exception is raised with the expected message fragment:
```python
# tests/test_phase2_runtime_reliability.py
with pytest.raises(RuntimeError, match="retry budget exhausted"):
    client.complete("prompt", agent_name="test")

# test_settings.py
with pytest.raises(RuntimeError, match="Corrupted settings.json"):
    manager.load()
```

**Path setup for tests that need the project root on `sys.path`:**
```python
# tests/test_phase2_runtime_reliability.py
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
```
This is required because there is no `conftest.py` at the repo root to manage path. Use the same pattern when adding new tests that import first-party modules and may be run from outside the project root.

**Environment hygiene:** prefer `monkeypatch.setenv(...)` over `os.environ[...] = ...`. `tests/test_routing.py` uses direct `os.environ` mutations — this is a legacy style and leaks state between tests; do not propagate it.

**Cache reset for module-level state:**
```python
# test_discovery.py
MODEL_CACHE.clear()  # required before exercising cached endpoints
```
Module-level dicts/caches in `ui/server.py` survive between tests in the same process. Always reset them in the test body when relevant.

---

*Testing analysis: 2026-05-11*
