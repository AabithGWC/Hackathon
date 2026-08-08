"""
Generic SQLite-backed run store, reusable across all agent playbooks.
"""
import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent_runs.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY,
            playbook_name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def save_run(run_id: str, playbook_name: str, timestamp: str, status: str, result: dict):
    init_db()
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO agent_runs (run_id, playbook_name, timestamp, status, result_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, playbook_name, timestamp, status, json.dumps(result)),
    )
    conn.commit()
    conn.close()


def get_last_run(playbook_name: str):
    """Return the most recent completed run's result dict for this playbook, or None."""
    init_db()
    conn = _connect()
    row = conn.execute(
        "SELECT result_json FROM agent_runs WHERE playbook_name = ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (playbook_name,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return json.loads(row["result_json"])


def get_run(run_id: str):
    init_db()
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "playbook_name": row["playbook_name"],
        "timestamp": row["timestamp"],
        "status": row["status"],
        "result": json.loads(row["result_json"]),
    }


def list_runs(playbook_name: str = None):
    """Return list of {run_id, playbook_name, timestamp, status} ordered newest first."""
    init_db()
    conn = _connect()
    if playbook_name:
        rows = conn.execute(
            "SELECT run_id, playbook_name, timestamp, status FROM agent_runs "
            "WHERE playbook_name = ? ORDER BY timestamp DESC",
            (playbook_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT run_id, playbook_name, timestamp, status FROM agent_runs "
            "ORDER BY timestamp DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
