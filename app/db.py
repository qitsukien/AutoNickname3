from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from .config import DB_PATH
from .utils import utc_now_iso


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_connection()) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS registrations (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                restored_count INTEGER NOT NULL DEFAULT 0,
                last_name_change_at TEXT
            );

            CREATE TABLE IF NOT EXISTS name_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                old_name TEXT,
                new_name TEXT NOT NULL,
                source TEXT NOT NULL,
                changed_by INTEGER,
                changed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_name_history_user_id ON name_history(user_id);
            """
        )
        conn.commit()


def get_user(user_id: int) -> dict[str, Any] | None:
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM registrations WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def save_user(user_id: int, username: str, normalized_name: str) -> None:
    now = utc_now_iso()
    with closing(get_connection()) as conn:
        exists = conn.execute("SELECT 1 FROM registrations WHERE user_id = ?", (user_id,)).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE registrations
                SET username = ?, normalized_name = ?, updated_at = ?, last_name_change_at = ?
                WHERE user_id = ?
                """,
                (username, normalized_name, now, now, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO registrations (
                    user_id, username, normalized_name, registered_at, updated_at, last_name_change_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, normalized_name, now, now, now),
            )
        conn.commit()


def delete_user(user_id: int) -> bool:
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM registrations WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


def list_users() -> list[dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute("SELECT * FROM registrations ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def add_name_history(user_id: int, old_name: str | None, new_name: str, source: str, changed_by: int | None) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            """
            INSERT INTO name_history (user_id, old_name, new_name, source, changed_by, changed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, old_name, new_name, source, changed_by, utc_now_iso()),
        )
        conn.commit()


def get_name_history(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT * FROM name_history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def increment_restore_count(user_id: int) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            "UPDATE registrations SET restored_count = restored_count + 1, updated_at = ? WHERE user_id = ?",
            (utc_now_iso(), user_id),
        )
        conn.commit()
