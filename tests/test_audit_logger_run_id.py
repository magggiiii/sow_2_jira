"""Back-compat regression for AuditLogger(run_id).

``scripts/run_eval_dataset.py:31`` constructs ``AuditLogger(run_id)`` positionally,
but the class ``__init__`` historically took no args. This locks in a tolerant
``run_id`` kwarg WITHOUT the Postgres rewrite (WALLED 1.6d) — the SQLite backend
and the ``.log()`` signature stay exactly as they were.
"""

from audit.logger import AuditLogger


def test_audit_logger_accepts_positional_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(AuditLogger, "DB_PATH", tmp_path / "audit.db")
    a = AuditLogger("run-123")
    try:
        assert a.run_id == "run-123"
        # .log() contract is unchanged and still writes to SQLite.
        a.log(run_id="run-123", agent="extraction", action="start", detail="ok")
        rows = a.get_run_logs("run-123")
        assert len(rows) == 1
        assert rows[0]["agent"] == "extraction"
    finally:
        a.close()


def test_audit_logger_still_constructs_with_no_args(tmp_path, monkeypatch):
    monkeypatch.setattr(AuditLogger, "DB_PATH", tmp_path / "audit.db")
    a = AuditLogger()
    try:
        assert a.run_id is None
    finally:
        a.close()
