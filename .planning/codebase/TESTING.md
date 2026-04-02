# Testing Patterns

**Analysis Date:** 2026-03-30

## Test Framework

**Runner:**
- Framework: Not detected (no `pytest`, `unittest` suite wiring, or test runner config files such as `pytest.ini`/`tox.ini` in repository root).
- Config: Not detected.

**Assertion Library:**
- Not detected as a formal test assertion library.
- Existing verification scripts rely on manual runtime checks and printed outcomes in `test_jira_api.py` and `test_jira_mcp.py`.

**Run Commands:**
```bash
python test_jira_api.py          # Manual Jira API connectivity check
python test_jira_mcp.py          # Manual MCP integration check (async)
make verify                      # Dependency import smoke check from `Makefile`
```

## Test File Organization

**Location:**
- Pattern: Root-level standalone scripts, not co-located with modules.
- Current files: `test_jira_api.py`, `test_jira_mcp.py`.

**Naming:**
- Pattern: `test_*.py` prefix at root.

**Structure:**
```text
project-root/
├── test_jira_api.py
└── test_jira_mcp.py
```

## Test Structure

**Suite Organization:**
```python
# `test_jira_api.py`
def test_api():
    load_dotenv()
    try:
        jira = JIRA(server=server, basic_auth=(email, token))
        ...
    except Exception as e:
        print(f"API test FAILED: {e}")

if __name__ == "__main__":
    test_api()
```

**Patterns:**
- Setup pattern: Load environment and instantiate real clients directly (`test_jira_api.py`, `test_jira_mcp.py`).
- Teardown pattern: Not detected.
- Assertion pattern: Print success/failure messages instead of formal assertions (`assert` not used in test files).

## Mocking

**Framework:** Not detected.

**Patterns:**
```python
# No mock/patch usage detected in `test_jira_api.py` or `test_jira_mcp.py`.
# Tests call external systems directly (Jira server, MCP process).
```

**What to Mock:**
- Current codebase does not define a mocking standard.
- For future automated tests, mock external boundaries currently called directly:
1. `jira.JIRA` in `integrations/jira_client.py` and `test_jira_api.py`.
2. Remote LLM calls in `pipeline/llm_client.py`.
3. External HTTP model discovery in `ui/server.py` (`requests.get`).

**What NOT to Mock:**
- Current scripts do not define this explicitly.
- Preserve real serialization and schema validation behavior from `models/schemas.py` in unit-level tests.

## Fixtures and Factories

**Test Data:**
```python
# No shared fixtures/factories detected.
# Inline data and real environment variables are used directly in:
# - `test_jira_api.py`
# - `test_jira_mcp.py`
```

**Location:**
- Not detected (`tests/fixtures`, factory modules, and reusable builders are absent).

## Coverage

**Requirements:** None enforced (no coverage tooling or thresholds detected).

**View Coverage:**
```bash
# Not applicable: no coverage command/config detected in repository.
```

## Test Types

**Unit Tests:**
- Not detected as a maintained pattern.

**Integration Tests:**
- Present as manual scripts that exercise real external dependencies:
1. Jira API auth/project access in `test_jira_api.py`.
2. MCP client async initialization path in `test_jira_mcp.py`.

**E2E Tests:**
- Not detected.

## Common Patterns

**Async Testing:**
```python
# `test_jira_mcp.py`
async def test_mcp():
    ...

if __name__ == "__main__":
    asyncio.run(test_mcp())
```

**Error Testing:**
```python
# Manual try/except logging pattern used in both scripts
try:
    ...
except Exception as e:
    print(f"... FAILED: {e}")
```

---

*Testing analysis: 2026-03-30*
