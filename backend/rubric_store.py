"""Tiny SQLite-backed rubric library."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_DB = Path(__file__).parent / "data" / "rubrics.db"


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS rubrics (
          id      INTEGER PRIMARY KEY AUTOINCREMENT,
          name    TEXT NOT NULL,
          grade   INTEGER NOT NULL,
          subject TEXT NOT NULL,
          chapter TEXT,
          rubric  TEXT NOT NULL,
          created INTEGER NOT NULL
        )
    """)
    return c


def list_rubrics() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, name, grade, subject, chapter, created FROM rubrics ORDER BY created DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_rubric(rid: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM rubrics WHERE id = ?", (rid,)).fetchone()
    return dict(row) if row else None


def save_rubric(name: str, grade: int, subject: str, chapter: str, rubric: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO rubrics(name, grade, subject, chapter, rubric, created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, grade, subject, chapter or "", rubric, int(time.time())),
        )
        return cur.lastrowid


def delete_rubric(rid: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM rubrics WHERE id = ?", (rid,))
