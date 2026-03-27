# audit/logger.py

import sqlite3
import datetime
from pathlib import Path
from typing import Optional


class AuditLogger:
    """SQLite-backed audit logger. Thread-safe for single-process use."""

    DB_PATH = Path("data/audit.db")

    def __init__(self):
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.DB_PATH), check_same_thread=False)
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                agent TEXT NOT NULL,
                node_id TEXT,
                action TEXT NOT NULL,
                task_id TEXT,
                detail TEXT,
                llm_tokens_used INTEGER DEFAULT 0,
                llm_model TEXT DEFAULT ''
            )
        """)
        self._conn.commit()

    def log(
        self,
        run_id: str,
        agent: str,
        action: str,
        detail: str,
        node_id: Optional[str] = None,
        task_id: Optional[str] = None,
        llm_tokens_used: int = 0,
        llm_model: str = "",
    ):
        self._conn.execute(
            """INSERT INTO audit_log
               (run_id, timestamp, agent, node_id, action, task_id, detail,
                llm_tokens_used, llm_model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                datetime.datetime.utcnow().isoformat(),
                agent,
                node_id,
                action,
                task_id,
                detail,
                llm_tokens_used,
                llm_model,
            ),
        )
        self._conn.commit()

    def get_run_logs(self, run_id: str) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM audit_log WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self):
        self._conn.close()