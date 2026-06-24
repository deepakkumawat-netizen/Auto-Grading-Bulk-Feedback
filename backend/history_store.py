"""SQLite-backed history of grading sessions.

Each row = one /api/grade/bulk run. Stores rubric, file count, class average,
the full results JSON, plus a quick summary for the sidebar.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_DB = Path(__file__).parent / "data" / "history.db"


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
          id        INTEGER PRIMARY KEY AUTOINCREMENT,
          created   INTEGER NOT NULL,
          title     TEXT NOT NULL,
          summary   TEXT NOT NULL,
          rubric    TEXT,
          payload   TEXT NOT NULL
        )
    """)
    return c


def save_history(title: str, summary: str, rubric: str, payload: dict[str, Any]) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO history(created, title, summary, rubric, payload) VALUES (?, ?, ?, ?, ?)",
            (int(time.time()), title, summary, rubric or "", json.dumps(payload, ensure_ascii=False)),
        )
        return cur.lastrowid


def list_history(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, created, title, summary FROM history ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_history(hid: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM history WHERE id = ?", (hid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["payload"] = json.loads(d.get("payload") or "{}")
    except Exception:
        d["payload"] = {}
    return d


def delete_history(hid: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM history WHERE id = ?", (hid,))


def clear_history() -> None:
    with _conn() as c:
        c.execute("DELETE FROM history")
