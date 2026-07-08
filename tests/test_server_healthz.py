# tests/test_server_healthz.py
"""SERVER-A backend lane — container/Render readiness.

Covers:
  4.2d  GET /healthz → 200 (lightweight, no DB dependency) AND "/healthz" is in
        the uvicorn access-log polling filter so it doesn't spam logs.
  4.2b  the __main__ block binds host 0.0.0.0 and port int($PORT, 8000) so the
        container / Render can route to it (asserted by reading the source; we
        do NOT actually bind a socket).
"""

from __future__ import annotations

import inspect
import logging

from fastapi.testclient import TestClient


def test_healthz_returns_200():
    import ui.server as srv
    client = TestClient(srv.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    # Lightweight liveness payload — some ok/status marker.
    assert body.get("status") in ("ok", "healthy") or body.get("ok") is True


def test_healthz_is_in_access_log_filter():
    """The startup PollingFilter must drop /healthz lines like it drops
    /api/status, so container liveness probes don't flood the access log."""
    import ui.server as srv

    # Run startup so the filter is installed on uvicorn.access.
    srv.startup_event()

    access_logger = logging.getLogger("uvicorn.access")
    filters = access_logger.filters
    assert filters, "startup should install at least one access-log filter"

    def _rec(msg: str) -> logging.LogRecord:
        return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, msg, None, None)

    # A /healthz line is filtered out (returns False → suppressed).
    healthz_rec = _rec('127.0.0.1 - "GET /healthz HTTP/1.1" 200')
    assert any(f.filter(healthz_rec) is False for f in filters), \
        "/healthz should be suppressed by the access-log filter"

    # A normal request line still passes (not over-filtered).
    normal_rec = _rec('127.0.0.1 - "GET /api/tasks HTTP/1.1" 200')
    assert all(f.filter(normal_rec) is not False for f in filters)


def test_main_block_binds_all_interfaces_and_port_env():
    import ui.server as srv
    src = inspect.getsource(srv)
    # __main__ must bind 0.0.0.0 (container-reachable) and read $PORT.
    assert '0.0.0.0' in src
    assert 'PORT' in src
    assert 'int(os.environ.get("PORT"' in src or "int(os.environ.get('PORT'" in src
