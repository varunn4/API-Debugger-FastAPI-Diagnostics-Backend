import sqlite3
import logging
import time
from datetime import datetime

from app.config import DEBUGGER_DB_PATH, DB_DEDUP_MINUTES

logger = logging.getLogger(__name__)

# Store db in a predictable place so Docker volume mounts work cleanly.
DB_PATH = DEBUGGER_DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS debug_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint    TEXT,
                method      TEXT,
                status_code INTEGER,
                issue_type  TEXT,
                root_cause  TEXT,
                confidence  REAL,
                created_at  TEXT
            )
        """)
        conn.commit()


def save_debug_result(req, result, retries: int = 3):
    """
    Persist debug results to SQLite.

    Includes:
    - Retry on transient sqlite OperationalErrors
    - Deduplication for identical failures seen in the last few minutes
    """
    last_exc = None
    for attempt in range(retries):
        try:
            _save_debug_result_once(req, result)
            return
        except sqlite3.OperationalError as e:
            last_exc = e
            if attempt == retries - 1:
                logger.error(
                    f"DB save failed after {retries} attempts: endpoint={req.endpoint} "
                    f"method={req.method} status_code={req.status_code} issue_type={result.issue_type}. "
                    f"Error: {e}"
                )
            time.sleep(0.1 * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def _save_debug_result_once(req, result):
    # Dedup: skip insert if identical failure occurred within the last N minutes.
    with get_conn() as conn:
        dup = conn.execute(
            """
            SELECT id
            FROM debug_logs
            WHERE endpoint=? AND method=? AND status_code=? AND issue_type=?
              AND datetime(created_at) > datetime('now', ?)
            LIMIT 1
            """,
            (
                req.endpoint,
                req.method,
                req.status_code,
                result.issue_type,
                f"-{DB_DEDUP_MINUTES} minutes",
            ),
        ).fetchone()

        if dup:
            return

        conn.execute(
            """INSERT INTO debug_logs
               (endpoint, method, status_code, issue_type, root_cause, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                req.endpoint,
                req.method,
                req.status_code,
                result.issue_type,
                result.root_cause,
                result.confidence_score,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()


def recent_logs(limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM debug_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
