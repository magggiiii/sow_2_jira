# tests/test_server_no_otel.py
"""STREAM A — ui.server must import and run without OpenTelemetry installed.

observability.py is already de-OTel'd (lazy imports guarded by try/except).
server.py was the LAST top-level OTel importer via
`from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor` and an
unconditional `FastAPIInstrumentor.instrument_app(app)` call.

These tests lock in:
  1. No top-level opentelemetry/traceloop import survives in ui/server.py.
  2. Importing ui.server in a subprocess where `opentelemetry`/`traceloop` are
     forced to raise ImportError still succeeds (instrumentation is skipped).
  3. The app object is still constructed and serves a route (no regression) when
     imported without OTel.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_no_toplevel_otel_import_in_server_source():
    """Static guard: ui/server.py must not have a module-level
    `import opentelemetry...` / `from opentelemetry...` (or traceloop) line."""
    src = (REPO_ROOT / "ui" / "server.py").read_text()
    offending = [
        line
        for line in src.splitlines()
        if re.match(r"^(import|from)\s+(opentelemetry|traceloop)\b", line)
    ]
    assert not offending, (
        "ui/server.py still has top-level OTel imports: " + repr(offending)
    )


def test_server_imports_without_opentelemetry_installed():
    """Force opentelemetry + traceloop to be unimportable, then import
    ui.server in a fresh subprocess. It must succeed and print OK."""
    code = (
        "import sys; "
        "sys.modules['opentelemetry']=None; "
        "sys.modules['traceloop']=None; "
        "import ui.server; "
        "print('OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, (
        "ui.server failed to import without OTel:\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "OK" in proc.stdout


def test_app_still_serves_when_otel_absent():
    """Sanity: with OTel actually absent from this process's module table, the
    app object still exists and a lightweight route responds."""
    # Simulate absence within this interpreter for the duration of the check.
    saved = {k: sys.modules.get(k) for k in ("opentelemetry", "traceloop")}
    sys.modules["opentelemetry"] = None
    sys.modules["traceloop"] = None
    try:
        # Drop any cached ui.server so the import re-runs under the shim.
        for mod in list(sys.modules):
            if mod == "ui.server" or mod.startswith("ui.server."):
                del sys.modules[mod]
        from fastapi.testclient import TestClient

        import ui.server as srv

        client = TestClient(srv.app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        for mod in list(sys.modules):
            if mod == "ui.server" or mod.startswith("ui.server."):
                del sys.modules[mod]


def test_instrumentation_is_lazy_and_gated_in_source():
    """The FastAPI instrumentation must be behind observability.SYNC_ENABLED and
    a try/except ImportError guard, not an unconditional top-level call."""
    import ui.server as srv
    src = inspect.getsource(srv)
    assert "SYNC_ENABLED" in src, "instrumentation should be gated on SYNC_ENABLED"
    assert "ImportError" in src, "instrumentation import should be ImportError-guarded"
    assert (
        "instrument_app" in src
    ), "instrumentation should still call instrument_app when available"
