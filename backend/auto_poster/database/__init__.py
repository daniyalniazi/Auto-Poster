"""Local SQLite database. Holds settings and history, never secrets."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from auto_poster.config import database_path

# Each entry upgrades the schema by one version. Append new entries; never edit old ones.
MIGRATIONS = [
    """
    CREATE TABLE platform_settings (
        platform TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (platform, key)
    );
    CREATE TABLE posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        text TEXT NOT NULL,
        image_count INTEGER NOT NULL DEFAULT 0,
        platforms TEXT NOT NULL,
        status TEXT NOT NULL
    );
    CREATE TABLE post_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        platform TEXT NOT NULL,
        success INTEGER NOT NULL,
        remote_id TEXT,
        url TEXT,
        message TEXT NOT NULL,
        error_code TEXT,
        technical_details TEXT,
        retry_after INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_post_results_post ON post_results(post_id);
    """,
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or database_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def migrate() -> None:
    with session() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {index}")
