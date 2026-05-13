"""
SQLite-backed history of past comparison and validation runs.
Database file lives at <project_root>/history.db.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

_DB_PATH = Path(__file__).parent.parent / "history.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    job_id      TEXT PRIMARY KEY,
    run_type    TEXT NOT NULL,
    file1       TEXT NOT NULL,
    file2       TEXT NOT NULL,
    started_at  REAL NOT NULL,
    duration_s  REAL NOT NULL,
    status      TEXT NOT NULL,
    summary     TEXT NOT NULL
);
"""


class HistoryManager:
    def __init__(self, db_path: Optional[Path] = None):
        self._db = db_path or _DB_PATH
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(_CREATE_SQL)

    def save_run(
        self,
        job_id: str,
        run_type: str,
        file1: str,
        file2: str,
        duration_s: float,
        status: str,
        summary: dict,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (job_id, run_type, file1, file2, started_at, duration_s, status, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    run_type,
                    file1,
                    file2,
                    time.time(),
                    round(duration_s, 2),
                    status,
                    json.dumps(summary),
                ),
            )

    def get_runs(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_run(self, job_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None

    def delete_run(self, job_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM runs WHERE job_id = ?", (job_id,))
        return cur.rowcount > 0

    def clear(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM runs")


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["summary"] = json.loads(d["summary"])
    return d
