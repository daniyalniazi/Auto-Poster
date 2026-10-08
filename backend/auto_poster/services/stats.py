"""Post stats: current totals (likes, shares, comments, views) for posts made by Auto Poster.

Only totals are kept, never history: Auto Poster isn't always running, so trends would be
unreliable. Totals are fetched when the Stats page is opened (at most every 15 minutes per
post); comments are fetched only when the user asks to see them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from auto_poster import database
from auto_poster.connectors.base import Connector, safe_comments, safe_stats
from auto_poster.connectors.registry import CONNECTORS, get_connector
from auto_poster.models import Comment, PostStats
from auto_poster.services import history
from auto_poster.services.settings import load_config

REFRESH_AFTER = timedelta(minutes=15)
RECENT_POSTS = 50
CONCURRENCY = 4
SUMMARY_DAYS = 30


class PlatformStats(BaseModel):
    platform: str
    post_url: str | None
    deleted: bool
    stats: PostStats | None
    error: str | None


class StatsPost(BaseModel):
    history_id: int
    created_at: str
    text: str
    results: list[PlatformStats]


class Totals(BaseModel):
    days: int
    posts: int
    likes: int
    shares: int
    replies: int
    views: int | None  # None when no platform reported views


class StatsOverview(BaseModel):
    platforms: dict[str, dict]  # id -> {name, supported, reason}
    totals: Totals
    posts: list[StatsPost]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(post_id: int, platform: str) -> tuple[PostStats | None, str | None]:
    with database.session() as conn:
        row = conn.execute("SELECT data, error FROM post_stats WHERE post_id = ? AND platform = ?",
                           (post_id, platform)).fetchone()
    if row is None:
        return None, None
    return (PostStats.model_validate_json(row["data"]) if row["data"] else None), row["error"]


def _save(post_id: int, platform: str, stats: PostStats | None, error: str | None) -> None:
    with database.session() as conn:
        if stats is None:  # keep the last good numbers, just record the problem
            conn.execute(
                "INSERT INTO post_stats (post_id, platform, data, error, fetched_at) VALUES (?, ?, NULL, ?, ?) "
                "ON CONFLICT(post_id, platform) DO UPDATE SET error = excluded.error, fetched_at = excluded.fetched_at",
                (post_id, platform, error, history.now_iso()),
            )
        else:
            conn.execute(
                "INSERT INTO post_stats (post_id, platform, data, error, fetched_at) VALUES (?, ?, ?, NULL, ?) "
                "ON CONFLICT(post_id, platform) DO UPDATE SET data = excluded.data, error = NULL, "
                "fetched_at = excluded.fetched_at",
                (post_id, platform, stats.model_dump_json(), history.now_iso()),
            )


def _fetched_at(post_id: int, platform: str) -> datetime | None:
    with database.session() as conn:
        row = conn.execute("SELECT fetched_at FROM post_stats WHERE post_id = ? AND platform = ?",
                           (post_id, platform)).fetchone()
    return datetime.fromisoformat(row["fetched_at"]) if row else None


def platform_info() -> dict[str, dict]:
    return {c.id: {"name": c.display_name, "supported": c.supports_stats, "reason": c.stats_unavailable_reason}
            for c in CONNECTORS.values()}


def _targets():
    """(history entry, result) pairs worth showing stats for."""
    for entry in history.list_entries(limit=RECENT_POSTS):
        for result in entry.results:
            if result.success and result.post_id:
                yield entry, result


async def refresh(force: bool = False) -> None:
    """Fetch current totals for recent posts (skipping ones checked in the last 15 minutes)."""
    configs: dict[str, tuple[Connector, dict]] = {}
    jobs = []
    for entry, result in _targets():
        connector = get_connector(result.platform)
        if connector is None or not connector.supports_stats or result.deleted_at:
            continue
        fetched = _fetched_at(entry.id, result.platform)
        if not force and fetched and _now() - fetched < REFRESH_AFTER:
            continue
        if connector.id not in configs:
            configs[connector.id] = (connector, load_config(connector))
        jobs.append((entry.id, result.platform, result.post_id))

    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(history_id: int, platform: str, remote_id: str) -> None:
        connector, config = configs[platform]
        if not connector.is_configured(config):
            _save(history_id, platform, None, f"{connector.display_name} is not connected.")
            return
        async with limit:
            stats, error = await safe_stats(connector, remote_id, config)
        _save(history_id, platform, stats, error.message if error else None)

    await asyncio.gather(*(one(*job) for job in jobs))


def overview() -> StatsOverview:
    info = platform_info()
    posts: dict[int, StatsPost] = {}
    cutoff = _now() - timedelta(days=SUMMARY_DAYS)
    totals = {"likes": 0, "shares": 0, "replies": 0, "views": None}
    counted_posts: set[int] = set()
    for entry, result in _targets():
        supported = info.get(result.platform, {}).get("supported", False)
        stats, error = _load(entry.id, result.platform) if supported else (None, None)
        post = posts.setdefault(entry.id, StatsPost(history_id=entry.id, created_at=entry.created_at,
                                                    text=entry.text, results=[]))
        post.results.append(PlatformStats(platform=result.platform, post_url=result.post_url,
                                          deleted=bool(result.deleted_at), stats=stats, error=error))
        if stats and datetime.fromisoformat(entry.created_at) >= cutoff:
            counted_posts.add(entry.id)
            totals["likes"] += stats.likes or 0
            totals["shares"] += stats.shares or 0
            totals["replies"] += stats.replies or 0
            if stats.views is not None:
                totals["views"] = (totals["views"] or 0) + stats.views
    return StatsOverview(platforms=info, posts=list(posts.values()),
                         totals=Totals(days=SUMMARY_DAYS, posts=len(counted_posts), **totals))


async def comments(history_id: int, platform: str) -> tuple[list[Comment], str | None]:
    """Fetch the latest comments for one post on one platform, live."""
    entry = history.get_entry(history_id)
    connector = get_connector(platform)
    result = next((r for r in entry.results if r.platform == platform and r.success), None) if entry else None
    if connector is None or result is None or not result.post_id:
        return [], "That post isn't available."
    if not connector.supports_stats:
        return [], connector.stats_unavailable_reason
    config = load_config(connector)
    if not connector.is_configured(config):
        return [], f"{connector.display_name} is not connected."
    found, error = await safe_comments(connector, result.post_id, config)
    return found, (error.message if error else None)

