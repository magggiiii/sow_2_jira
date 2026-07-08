"""Schema coverage for the AuditLogger SQLite backend.

These tests lock in the on-disk shape of the ``audit_log`` table: table
creation on construction, the exact 9-column layout, NOT NULL enforcement,
column DEFAULTs, tz-aware UTC timestamps, and ORDER BY id ordering.

The DB is fully isolated per test by monkeypatching ``AuditLogger.DB_PATH``
onto a ``tmp_path`` file, so nothing ever touches the real ``data/audit.db``
(other test streams run concurrently against it).

run_id back-compat construction cases live in
``tests/test_audit_logger_run_id.py`` and are intentionally NOT duplicated here.
"""

import datetime
import sqlite3

import pytest

from audit.logger import AuditLogger


@pytest.fixture
def audit(tmp_path, monkeypatch):
    """An AuditLogger backed by a throwaway DB under tmp_path."""
    monkeypatch.setattr(AuditLogger, "DB_PATH", tmp_path / "audit.db")
    logger = AuditLogger()
    try:
        yield logger
    finally:
        logger.close()


# (1) Constructing AuditLogger creates the DB file + the audit_log table.
def test_construction_creates_db_and_table(tmp_path, monkeypatch):
    db_path = tmp_path / "nested" / "audit.db"
    monkeypatch.setattr(AuditLogger, "DB_PATH", db_path)

    logger = AuditLogger()
    try:
        # Parent dir + DB file materialize on construction.
        assert db_path.exists()

        cur = logger._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        )
        assert cur.fetchone() is not None
    finally:
        logger.close()


# (2) PRAGMA table_info shows the expected 9 columns (name + type + order).
def test_table_has_expected_nine_columns(audit):
    cur = audit._conn.execute("PRAGMA table_info(audit_log)")
    info = cur.fetchall()  # (cid, name, type, notnull, dflt_value, pk)

    names = [row[1] for row in info]
    assert names == [
        "id",
        "run_id",
        "timestamp",
        "agent",
        "node_id",
        "action",
        "task_id",
        "detail",
        "llm_tokens_used",
        "llm_model",
    ]
    assert len(names) == 9 + 1  # 9 data columns + the id primary key

    types = {row[1]: row[2] for row in info}
    assert types["id"] == "INTEGER"
    assert types["run_id"] == "TEXT"
    assert types["timestamp"] == "TEXT"
    assert types["llm_tokens_used"] == "INTEGER"
    assert types["llm_model"] == "TEXT"


# (3) NOT NULL constraints behave: required cols reject NULL, nullable cols accept it.
def test_not_null_constraints(audit):
    cur = audit._conn.execute("PRAGMA table_info(audit_log)")
    notnull = {row[1]: bool(row[3]) for row in cur.fetchall()}

    # Required columns.
    assert notnull["run_id"] is True
    assert notnull["timestamp"] is True
    assert notnull["agent"] is True
    assert notnull["action"] is True
    # Optional columns.
    assert notnull["node_id"] is False
    assert notnull["task_id"] is False
    assert notnull["detail"] is False

    # A required column set to NULL must be rejected by SQLite.
    with pytest.raises(sqlite3.IntegrityError):
        audit._conn.execute(
            """INSERT INTO audit_log
               (run_id, timestamp, agent, action)
               VALUES (?, ?, ?, ?)""",
            ("run-x", None, "extraction", "start"),
        )

    # Nullable columns accept NULL without error.
    audit._conn.execute(
        """INSERT INTO audit_log
           (run_id, timestamp, agent, action, node_id, task_id, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            "run-x",
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "extraction",
            "start",
            None,
            None,
            None,
        ),
    )
    audit._conn.commit()


# (4) Column DEFAULTs apply when omitted from the INSERT.
def test_column_defaults(audit):
    audit.log(
        run_id="run-defaults",
        agent="extraction",
        action="start",
        detail="no token/model given",
    )
    rows = audit.get_run_logs("run-defaults")
    assert len(rows) == 1
    assert rows[0]["llm_tokens_used"] == 0
    assert rows[0]["llm_model"] == ""

    # Confirm the DEFAULT clause is what the schema advertises, independent of .log().
    cur = audit._conn.execute("PRAGMA table_info(audit_log)")
    defaults = {row[1]: row[4] for row in cur.fetchall()}
    assert defaults["llm_tokens_used"] == "0"
    assert defaults["llm_model"] == "''"


# (5) Timestamps are tz-aware UTC ISO-8601 strings.
def test_timestamps_are_tz_aware_utc(audit):
    audit.log(
        run_id="run-ts",
        agent="extraction",
        action="start",
        detail="check ts",
    )
    rows = audit.get_run_logs("run-ts")
    assert len(rows) == 1

    parsed = datetime.datetime.fromisoformat(rows[0]["timestamp"])
    assert parsed.tzinfo is not None  # tz-aware
    assert parsed.utcoffset() == datetime.timedelta(0)  # UTC


# (6) Rows come back ordered by id (insertion order), not arbitrary.
def test_rows_returned_order_by_id(audit):
    for i in range(5):
        audit.log(
            run_id="run-order",
            agent="extraction",
            action=f"step-{i}",
            detail=str(i),
        )
    rows = audit.get_run_logs("run-order")
    assert len(rows) == 5

    ids = [row["id"] for row in rows]
    assert ids == sorted(ids)
    assert [row["action"] for row in rows] == [
        "step-0",
        "step-1",
        "step-2",
        "step-3",
        "step-4",
    ]
