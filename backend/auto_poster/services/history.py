"""Post history stored in the local database."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel

from auto_poster import database
from auto_poster.models import Post, PostResult


class HistoryEntry(BaseModel):
    id: int
    created_at: str  # ISO 8601, UTC; the UI shows it in local time
    text: str
    image_count: int
    platforms: list[str]
    status: str  # "publishing", "success", "partial", "failed"
    results: list[PostResult]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_post(request_id: str, post: Post, platforms: list[str]) -> int:
    with database.session() as conn:
        cursor = conn.execute(
            "INSERT INTO posts (request_id, created_at, text, image_count, platforms, status) "
            "VALUES (?, ?, ?, ?, ?, 'publishing')",
            (request_id, now_iso(), post.text, len(post.images), json.dumps(platforms)),
        )
        return cursor.lastrowid


def overall_status(results: list[PostResult]) -> str:
    if not results:
        return "failed"
    successes = sum(r.success for r in results)
    if successes == len(results):
        return "success"
    return "partial" if successes else "failed"


def save_results(post_id: int, results: list[PostResult]) -> None:
    with database.session() as conn:
        for r in results:
            conn.execute(
                "INSERT INTO post_results (post_id, platform, success, remote_id, url, message, error_code, "
                "technical_details, retry_after, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (post_id, r.platform, int(r.success), r.post_id, r.post_url, r.message, r.error_code,
                 r.technical_details, r.retry_after, now_iso()),
            )
        conn.execute("UPDATE posts SET status = ? WHERE id = ?", (overall_status(results), post_id))


def _results(conn, post_id: int) -> list[PostResult]:
    rows = conn.execute("SELECT * FROM post_results WHERE post_id = ? ORDER BY id", (post_id,)).fetchall()
    return [
        PostResult(
            platform=row["platform"],
            success=bool(row["success"]),
            post_id=row["remote_id"],
            post_url=row["url"],
            message=row["message"],
            error_code=row["error_code"],
            technical_details=row["technical_details"],
            retry_after=row["retry_after"],
        )
        for row in rows
    ]


def _entry(conn, row) -> HistoryEntry:
    return HistoryEntry(
        id=row["id"],
        created_at=row["created_at"],
        text=row["text"],
        image_count=row["image_count"],
        platforms=json.loads(row["platforms"]),
        status=row["status"],
        results=_results(conn, row["id"]),
    )


def find_by_request_id(request_id: str) -> HistoryEntry | None:
    with database.session() as conn:
        row = conn.execute("SELECT * FROM posts WHERE request_id = ?", (request_id,)).fetchone()
        return _entry(conn, row) if row else None


def list_entries(limit: int = 50, offset: int = 0) -> list[HistoryEntry]:
    with database.session() as conn:
        rows = conn.execute(
            "SELECT * FROM posts ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [_entry(conn, row) for row in rows]


def mark_interrupted() -> None:
    """Posts left 'publishing' when the app was closed mid-publish."""
    with database.session() as conn:
        conn.execute("UPDATE posts SET status = 'interrupted' WHERE status = 'publishing'")
