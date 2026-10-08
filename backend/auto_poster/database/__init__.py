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
    """
    ALTER TABLE posts ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
    CREATE TABLE scheduled_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        scheduled_at TEXT NOT NULL,          -- UTC, ISO 8601
        text TEXT NOT NULL,
        image_ids TEXT NOT NULL,             -- JSON list
        alt_texts TEXT NOT NULL,             -- JSON object
        platforms TEXT NOT NULL,             -- JSON list
        options TEXT NOT NULL,               -- JSON object
        enabled INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL,                -- scheduled, sending, sent, partial, failed, missed
        attempt INTEGER NOT NULL DEFAULT 0,  -- bumped by "send now"/reschedule so request IDs stay unique
        history_post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL,
        note TEXT
    );
    CREATE INDEX idx_scheduled_due ON scheduled_posts(status, enabled, scheduled_at);
    """,
    """
    -- Structured posts: mode, title, hashtags, link and per-platform hand edits (JSON).
    ALTER TABLE scheduled_posts ADD COLUMN compose TEXT NOT NULL DEFAULT '{}';
    -- The exact text each platform received.
    ALTER TABLE post_results ADD COLUMN sent_text TEXT;
    """,
    """
    -- The full post as written (PostRequest JSON), so it can be posted again.
    ALTER TABLE posts ADD COLUMN compose TEXT;
    CREATE TABLE drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        data TEXT NOT NULL                  -- PostRequest JSON
    );
    CREATE TABLE hashtag_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        tags TEXT NOT NULL,                 -- JSON list, without '#'
        created_at TEXT NOT NULL
    );
    CREATE TABLE reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        history_post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL,
        label TEXT NOT NULL,                -- short preview of the post
        request TEXT NOT NULL,              -- PostRequest JSON to prefill the repost
        next_at TEXT NOT NULL,              -- UTC, ISO 8601
        repeat TEXT NOT NULL,               -- once, weekly, every_2_weeks, monthly
        status TEXT NOT NULL,               -- active, stopped
        notified_at TEXT                    -- when the current due date was announced
    );
    """,
    """
    ALTER TABLE post_results ADD COLUMN deleted_at TEXT;  -- set by "Delete everywhere"
    """,
    """
    -- Latest totals per published post and platform (no history of numbers is kept).
    CREATE TABLE post_stats (
        post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        platform TEXT NOT NULL,
        data TEXT,                          -- PostStats JSON, last good value
        error TEXT,                         -- last problem fetching, in plain English
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (post_id, platform)
    );
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
