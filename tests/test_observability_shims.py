"""
Tests for pipeline.observability shims (TASK 4.1a).

These verify the module imports and functions correctly even when
OpenTelemetry / Traceloop are UNINSTALLED (the R4 removal target),
while preserving the exact public surface its live importers use.

Gates covered:
  A. OTel-uninstalled import works (subprocess with sys.modules poisoned).
  B. Static: no top-level opentelemetry/traceloop imports.
  C. user_id + run_id present in serialized JSON audit sink.
  D. Secret redaction in emitted sink output.
  E. Public surface importable + F821 clean (pyflakes or ast/compile).
"""

import os
import re
import sys
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OBS_PATH = REPO_ROOT / "pipeline" / "observability.py"
PY = str(REPO_ROOT / "venv" / "bin" / "python")


# ─── GATE A: OTel-uninstalled import ─────────────────────────────────────────

def test_gate_a_import_without_otel():
    """Poison opentelemetry/traceloop in sys.modules (forces ImportError) and
    confirm observability still imports, tracer/metrics/SYNC_ENABLED all work."""
    code = (
        "import sys\n"
        "sys.modules['opentelemetry'] = None\n"
        "sys.modules['traceloop'] = None\n"
        "import pipeline.observability as o\n"
        "o.llm_token_usage.add(1)\n"
        "o.llm_token_usage.add(5, {'k': 'v'})\n"
        "o.llm_operation_duration.record(0.5)\n"
        "o.llm_operation_duration.record(0.5, {'k': 'v'})\n"
        "with o.tracer.start_as_current_span('x') as s:\n"
        "    s.set_attribute('k', 'v')\n"
        "with o.tracer.start_as_current_span('y'):\n"
        "    pass\n"
        "o.sync_telemetry()\n"
        "print('OK', o.SYNC_ENABLED)\n"
    )
    proc = subprocess.run(
        [PY, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"exit={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "OK" in proc.stdout, f"stdout missing OK:\n{proc.stdout}\n{proc.stderr}"


def test_gate_a_trace_span_decorator_without_otel():
    """trace_span still works as a pass-through decorator with OTel absent."""
    code = (
        "import sys\n"
        "sys.modules['opentelemetry'] = None\n"
        "sys.modules['traceloop'] = None\n"
        "import pipeline.observability as o\n"
        "@o.trace_span('op', agent='a', run_id='r1')\n"
        "def f(x):\n"
        "    return x * 2\n"
        "print('RESULT', f(21))\n"
    )
    proc = subprocess.run(
        [PY, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"exit={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "RESULT 42" in proc.stdout, f"{proc.stdout}\n{proc.stderr}"


# ─── GATE B: Static — no top-level OTel imports ──────────────────────────────

def test_gate_b_no_toplevel_otel_imports():
    """No line at column 0 (module top level) may import opentelemetry/traceloop."""
    text = OBS_PATH.read_text()
    offenders = re.findall(
        r"^(?:import|from)\s+(?:opentelemetry|traceloop)\b.*$",
        text,
        flags=re.MULTILINE,
    )
    assert offenders == [], f"top-level OTel imports found: {offenders}"


# ─── GATE C: user_id + run_id in serialized JSON ─────────────────────────────

def test_gate_c_user_id_and_run_id_in_json(tmp_path):
    """A serialized JSON sink must carry both user_id and run_id in record.extra."""
    import pipeline.observability as o

    json_sink = tmp_path / "audit_test.jsonl"
    handler_id = o.logger.add(str(json_sink), serialize=True, format="{message}")
    try:
        with o.run_logger("run-abc", user_id="user-xyz"):
            o.logger.bind(agent="tester").info("hello audit")
    finally:
        o.logger.remove(handler_id)

    lines = [ln for ln in json_sink.read_text().splitlines() if ln.strip()]
    assert lines, "no JSON lines emitted"
    rec = None
    for ln in lines:
        obj = json.loads(ln)
        if obj["record"]["message"] == "hello audit":
            rec = obj
            break
    assert rec is not None, f"target log line not found; got: {lines}"
    extra = rec["record"]["extra"]
    assert extra.get("run_id") == "run-abc", f"run_id missing/wrong: {extra}"
    assert extra.get("user_id") == "user-xyz", f"user_id missing/wrong: {extra}"


def test_gate_c_user_id_defaults_to_none():
    """user_id defaults to 'none' in the base context when unset."""
    import pipeline.observability as o
    # loguru stores configured extras on the core; check via a captured record
    captured = {}

    def sink(msg):
        captured.update(msg.record["extra"])

    handler_id = o.logger.add(sink, format="{message}")
    try:
        o.logger.info("ctx check")
    finally:
        o.logger.remove(handler_id)
    assert captured.get("user_id") == "none", f"default user_id not 'none': {captured}"


# ─── GATE D: Secret redaction ────────────────────────────────────────────────

def test_gate_d_redaction_in_output(tmp_path):
    """A logged secret must appear as ***REDACTED*** and never in raw form."""
    import pipeline.observability as o

    sink_file = tmp_path / "redact.log"
    handler_id = o.logger.add(str(sink_file), format="{message}")
    try:
        o.logger.info("connecting with api_key=sk-ABCDEF1234567890 now")
    finally:
        o.logger.remove(handler_id)

    out = sink_file.read_text()
    assert "***REDACTED***" in out, f"no redaction marker in output:\n{out}"
    assert "sk-ABCDEF1234567890" not in out, f"raw secret leaked:\n{out}"


def test_gate_d_redaction_in_json_extra(tmp_path):
    """Redaction also applies to values threaded through the JSON audit sink."""
    import pipeline.observability as o

    json_sink = tmp_path / "redact.jsonl"
    handler_id = o.logger.add(str(json_sink), serialize=True, format="{message}")
    try:
        o.logger.info("token=supersecretvalue1234567890 authorized")
    finally:
        o.logger.remove(handler_id)

    raw = json_sink.read_text()
    assert "supersecretvalue1234567890" not in raw, f"raw secret leaked in json:\n{raw}"
    assert "***REDACTED***" in raw, f"no redaction marker in json:\n{raw}"


# ─── GATE D+: Redaction correctness (under/over-redaction regressions) ───────

@pytest.mark.parametrize(
    "line, secret",
    [
        # The literal env-var names used across this repo (jira_client.py,
        # ui/server.py, llm_router.py, CLAUDE.md). Under-redaction of these is
        # the most likely real-world credential leak shape.
        ("JIRA_API_TOKEN=ATATT3xFfGF0abcdefghijklmnop", "ATATT3xFfGF0abcdefghijklmnop"),
        ("LITELLM_API_KEY=sk-abcdefghijklmnopqrstuvwx", "sk-abcdefghijklmnopqrstuvwx"),
        ("JIRA_API_TOKEN: ATATT3xShortish123", "ATATT3xShortish123"),
        ("client_secret=abc123def456", "abc123def456"),
    ],
)
def test_redact_underscore_prefixed_keys(line, secret):
    """UPPER_SNAKE / underscore-prefixed key names (API_TOKEN, API_KEY) must
    redact their values — the '_' before token/key must not block the match."""
    import pipeline.observability as o
    out = o._redact_text(line)
    assert secret not in out, f"secret leaked: {out!r}"
    assert o.REDACTED in out, f"no redaction marker: {out!r}"


@pytest.mark.parametrize(
    "prose",
    [
        "auth failed for user bob",
        "token bucket algorithm applied",
        "password reset email sent",
        "secret santa gift exchange",
        "authorization header missing entirely",
        "bearer of bad news arrived today",
    ],
)
def test_no_over_redaction_of_prose(prose):
    """A sensitive-sounding word followed by a plain space (no : or =) must NOT
    redact the following ordinary word — legitimate log lines must survive."""
    import pipeline.observability as o
    out = o._redact_text(prose)
    assert out == prose, f"prose over-redacted: {out!r} (was {prose!r})"


@pytest.mark.parametrize(
    "line",
    [
        "session 550e8400-e29b-41d4-a716-446655440000 started",
        "run_id 550e8400-e29b-41d4-a716-446655440000 completed",
        "processing run 123e4567-e89b-12d3-a456-426614174000 ok",
    ],
)
def test_no_over_redaction_of_uuid_ids(line):
    """UUID run_id/session_id (the primary debugging correlation key) must
    survive redaction verbatim — must not be swallowed after a trigger word or
    treated as a high-entropy blob."""
    import pipeline.observability as o
    out = o._redact_text(line)
    assert out == line, f"UUID correlation key over-redacted: {out!r} (was {line!r})"


def test_redaction_still_catches_kv_forms():
    """Regression guard: the original delimiter-based kv redaction still works
    for the lowercase key=value / key: value forms."""
    import pipeline.observability as o
    assert o.REDACTED in o._redact_text("api_key=sk-ABCDEF1234567890")
    assert "sk-ABCDEF1234567890" not in o._redact_text("api_key=sk-ABCDEF1234567890")
    assert o.REDACTED in o._redact_text("token: supersecretvalue1234567890")
    assert "supersecretvalue1234567890" not in o._redact_text("token: supersecretvalue1234567890")


# ─── GATE E: Surface + F821 ──────────────────────────────────────────────────

def test_gate_e_public_surface_importable():
    """All required public symbols must import (with OTel installed)."""
    from pipeline.observability import (  # noqa: F401
        logger,
        tracer,
        trace_span,
        sync_telemetry,
        run_logger,
        add_run_file_logger,
        llm_token_usage,
        llm_operation_duration,
        INSTANCE_ID,
        SYNC_ENABLED,
        init_argus,
        DEFAULT_JOB_NAME,
        meter,
    )
    assert callable(trace_span)
    assert callable(sync_telemetry)
    assert callable(run_logger)
    assert callable(add_run_file_logger)
    assert callable(init_argus)
    assert isinstance(INSTANCE_ID, str)
    assert isinstance(DEFAULT_JOB_NAME, str)
    assert isinstance(SYNC_ENABLED, bool)


def test_gate_e_static_clean():
    """pyflakes if present, else ast.parse + compile — no syntax/undefined errors."""
    text = OBS_PATH.read_text()
    # Always must parse + compile.
    import ast
    tree = ast.parse(text, filename=str(OBS_PATH))
    compile(tree, str(OBS_PATH), "exec")

    # Prefer pyflakes if available for F821 undefined-name detection.
    proc = subprocess.run(
        [PY, "-m", "pyflakes", str(OBS_PATH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 and "No module named" not in (proc.stderr or ""):
        # pyflakes ran and found problems
        raise AssertionError(f"pyflakes issues:\n{proc.stdout}\n{proc.stderr}")
