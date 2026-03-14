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
                discord_login TEXT NOT NULL,
                discord_display_name TEXT NOT NULL,
                registered_name TEXT NOT NULL,
                final_nickname TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                restored_count INTEGER NOT NULL DEFAULT 0,
                last_name_change_at TEXT
            );

            CREATE TABLE IF NOT EXISTS name_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                old_registered_name TEXT,
                new_registered_name TEXT NOT NULL,
                old_final_nickname TEXT,
                new_final_nickname TEXT NOT NULL,
                source TEXT NOT NULL,
                changed_by INTEGER,
                changed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_name_history_user_id ON name_history(user_id);
            """
        )
        conn.commit()
    migrate_legacy_schema()


def migrate_legacy_schema() -> None:
    with closing(get_connection()) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(registrations)").fetchall()}
        if cols and "discord_login" not in cols:
            conn.execute("ALTER TABLE registrations RENAME TO registrations_old")
            conn.executescript(
                """
                CREATE TABLE registrations (
                    user_id INTEGER PRIMARY KEY,
                    discord_login TEXT NOT NULL,
                    discord_display_name TEXT NOT NULL,
                    registered_name TEXT NOT NULL,
                    final_nickname TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    restored_count INTEGER NOT NULL DEFAULT 0,
                    last_name_change_at TEXT
                );
                """
            )
            old_rows = conn.execute("SELECT * FROM registrations_old").fetchall()
            for row in old_rows:
                username = row[1] if len(row) > 1 else f"user{row[0]}"
                normalized_name = row[2] if len(row) > 2 else username
                registered_at = row[3] if len(row) > 3 else utc_now_iso()
                updated_at = row[4] if len(row) > 4 else utc_now_iso()
                restored_count = row[5] if len(row) > 5 else 0
                last_change = row[6] if len(row) > 6 else updated_at
                conn.execute(
                    """
                    INSERT INTO registrations (
                        user_id, discord_login, discord_display_name, registered_name,
                        final_nickname, registered_at, updated_at, restored_count, last_name_change_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row[0],
                        username,
                        username,
                        normalized_name,
                        username,
                        registered_at,
                        updated_at,
                        restored_count,
                        last_change,
                    ),
                )
            conn.execute("DROP TABLE registrations_old")
        hist_cols = {row[1] for row in conn.execute("PRAGMA table_info(name_history)").fetchall()}
        if hist_cols and "old_registered_name" not in hist_cols:
            conn.execute("ALTER TABLE name_history RENAME TO name_history_old")
            conn.executescript(
                """
                CREATE TABLE name_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    old_registered_name TEXT,
                    new_registered_name TEXT NOT NULL,
                    old_final_nickname TEXT,
                    new_final_nickname TEXT NOT NULL,
                    source TEXT NOT NULL,
                    changed_by INTEGER,
                    changed_at TEXT NOT NULL
                );
                """
            )
            old_rows = conn.execute("SELECT * FROM name_history_old").fetchall()
            for row in old_rows:
                conn.execute(
                    """
                    INSERT INTO name_history (
                        id, user_id, old_registered_name, new_registered_name,
                        old_final_nickname, new_final_nickname, source, changed_by, changed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row[0], row[1], row[2], row[3], row[2], row[3], row[4], row[5], row[6]
                    ),
                )
            conn.execute("DROP TABLE name_history_old")
        conn.commit()


def get_user(user_id: int) -> dict[str, Any] | None:
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM registrations WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute("SELECT * FROM registrations ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def delete_user(user_id: int) -> bool:
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM registrations WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


def upsert_user(*, user_id: int, discord_login: str, discord_display_name: str, registered_name: str, final_nickname: str) -> None:
    now = utc_now_iso()
    with closing(get_connection()) as conn:
        exists = conn.execute("SELECT 1 FROM registrations WHERE user_id = ?", (user_id,)).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE registrations
                SET discord_login = ?, discord_display_name = ?, registered_name = ?,
                    final_nickname = ?, updated_at = ?, last_name_change_at = ?
                WHERE user_id = ?
                """,
                (discord_login, discord_display_name, registered_name, final_nickname, now, now, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO registrations (
                    user_id, discord_login, discord_display_name, registered_name,
                    final_nickname, registered_at, updated_at, last_name_change_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, discord_login, discord_display_name, registered_name, final_nickname, now, now, now),
            )
        conn.commit()


def increment_restore_count(user_id: int) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            "UPDATE registrations SET restored_count = restored_count + 1, updated_at = ? WHERE user_id = ?",
            (utc_now_iso(), user_id),
        )
        conn.commit()


def add_name_history(
    *,
    user_id: int,
    old_registered_name: str | None,
    new_registered_name: str,
    old_final_nickname: str | None,
    new_final_nickname: str,
    source: str,
    changed_by: int | None,
) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            """
            INSERT INTO name_history (
                user_id, old_registered_name, new_registered_name,
                old_final_nickname, new_final_nickname, source, changed_by, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                old_registered_name,
                new_registered_name,
                old_final_nickname,
                new_final_nickname,
                source,
                changed_by,
                utc_now_iso(),
            ),
        )
        conn.commit()


def get_name_history(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM name_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
