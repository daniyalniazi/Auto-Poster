"""Scheduled posts. Runs inside the app, so Auto Poster must be open at the scheduled time.

Duplicate protection:
  * A post is "claimed" with an atomic UPDATE (status scheduled -> sending) before sending.
  * It is published with request ID "scheduled-<id>-<attempt>", which history stores with a
    UNIQUE constraint, so the same attempt can never be published twice.
  * If the app closes while a post is "sending", it is NOT retried on the next start.
    It is marked failed with an explanation, because some platforms may already have it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from auto_poster import database
from auto_poster.models import ImageInfo, PostRequest
from auto_poster.services import history, media, publisher

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15
# If the app was closed at the scheduled time, posts up to this late are still sent when it
# starts again. Older ones are marked "missed" so nothing surprising goes out days later.
LATE_GRACE = timedelta(minutes=60)

EDITABLE = ("scheduled", "missed", "failed", "partial")


class ScheduledPost(BaseModel):
    id: int
    created_at: str
    updated_at: str
    scheduled_at: str
    mode: str
    title: str
    text: str
    hashtags: str
    link: str
    overrides: dict[str, str]
    images: list[ImageInfo]
    alt_texts: dict[str, str]
    platforms: list[str]
    options: dict[str, dict[str, str]]
    enabled: bool
    status: str
    note: str | None
    history_post_id: int | None


class ScheduleError(ValueError):
    pass


def to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ScheduleError("The scheduled time must include a time zone.")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _image_infos(image_ids: list[str]) -> list[ImageInfo]:
    infos = []
    for image_id in image_ids:
        image = media.load(image_id)
        if image:
            infos.append(ImageInfo(id=image.id, filename=image.filename, format=image.format,
                                   size_bytes=image.size_bytes, width=image.width, height=image.height))
    return infos


COMPOSE_FIELDS = ("mode", "title", "hashtags", "link", "overrides")


def _compose_json(request: PostRequest) -> str:
    return json.dumps({name: getattr(request, name) for name in COMPOSE_FIELDS})


def _compose(row) -> dict:
    data = json.loads(row["compose"] or "{}")
    return {"mode": "quick", "title": "", "hashtags": "", "link": "", "overrides": {}, **data}


def _from_row(row) -> ScheduledPost:
    return ScheduledPost(
        **_compose(row),
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        scheduled_at=row["scheduled_at"],
        text=row["text"],
        images=_image_infos(json.loads(row["image_ids"])),
        alt_texts=json.loads(row["alt_texts"]),
        platforms=json.loads(row["platforms"]),
        options=json.loads(row["options"]),
        enabled=bool(row["enabled"]),
        status=row["status"],
        note=row["note"],
        history_post_id=row["history_post_id"],
    )


def _request_from_row(row) -> PostRequest:
    return PostRequest(
        **_compose(row),
        text=row["text"],
        platforms=json.loads(row["platforms"]),
        image_ids=json.loads(row["image_ids"]),
        alt_texts=json.loads(row["alt_texts"]),
        options=json.loads(row["options"]),
    )


def _check(request: PostRequest, when: datetime) -> None:
    to_utc_iso(when)  # rejects times without a time zone
    if when < _now() - timedelta(minutes=1):
        raise ScheduleError("The scheduled time is in the past. Choose a time in the future.")
    problems = publisher.validate(request)
    if publisher.has_errors(problems):
        messages = [p.message for items in problems.values() for p in items if p.level == "error"]
        raise ScheduleError("Fix these problems before scheduling: " + " ".join(messages))


def get(post_id: int) -> ScheduledPost | None:
    with database.session() as conn:
        row = conn.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone()
    return _from_row(row) if row else None


def list_all() -> list[ScheduledPost]:
    with database.session() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts ORDER BY "
            "CASE WHEN status IN ('scheduled', 'sending', 'missed') THEN 0 ELSE 1 END, "
            "CASE WHEN status IN ('scheduled', 'sending', 'missed') THEN scheduled_at END ASC, "
            "scheduled_at DESC"
        ).fetchall()
    return [_from_row(r) for r in rows]


def create(request: PostRequest, when: datetime) -> ScheduledPost:
    _check(request, when)
    now = history.now_iso()
    with database.session() as conn:
        cursor = conn.execute(
            "INSERT INTO scheduled_posts (created_at, updated_at, scheduled_at, text, image_ids, alt_texts, "
            "platforms, options, compose, enabled, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'scheduled')",
            (now, now, to_utc_iso(when), request.text, json.dumps(request.image_ids),
             json.dumps(request.alt_texts), json.dumps(request.platforms), json.dumps(request.options),
             _compose_json(request)),
        )
        post_id = cursor.lastrowid
    return get(post_id)


def update(post_id: int, request: PostRequest, when: datetime) -> ScheduledPost:
    existing = get(post_id)
    if existing is None:
        raise ScheduleError("That scheduled post no longer exists.")
    if existing.status not in EDITABLE:
        raise ScheduleError("This post is being sent or was already sent, so it can't be changed.")
    _check(request, when)
    with database.session() as conn:
        updated = conn.execute(
            "UPDATE scheduled_posts SET updated_at = ?, scheduled_at = ?, text = ?, image_ids = ?, alt_texts = ?, "
            "platforms = ?, options = ?, compose = ?, status = 'scheduled', attempt = attempt + 1, note = NULL "
            f"WHERE id = ? AND status IN ({','.join('?' * len(EDITABLE))})",
            (history.now_iso(), to_utc_iso(when), request.text, json.dumps(request.image_ids),
             json.dumps(request.alt_texts), json.dumps(request.platforms), json.dumps(request.options),
             _compose_json(request), post_id, *EDITABLE),
        ).rowcount
    if not updated:
        raise ScheduleError("This post started sending while you were editing it.")
    _delete_unused_images({i.id for i in existing.images} - set(request.image_ids))
    return get(post_id)


def set_enabled(post_id: int, enabled: bool) -> ScheduledPost | None:
    with database.session() as conn:
        conn.execute("UPDATE scheduled_posts SET enabled = ?, updated_at = ? WHERE id = ?",
                     (int(enabled), history.now_iso(), post_id))
    return get(post_id)


def delete(post_id: int) -> None:
    existing = get(post_id)
    if existing is None:
        return
    if existing.status == "sending":
        raise ScheduleError("This post is being sent right now. Try again in a moment.")
    with database.session() as conn:
        conn.execute("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))
    _delete_unused_images({i.id for i in existing.images})


def referenced_image_ids() -> set[str]:
    with database.session() as conn:
        rows = conn.execute("SELECT image_ids FROM scheduled_posts WHERE status != 'sent'").fetchall()
    return {image_id for row in rows for image_id in json.loads(row["image_ids"])}


def _delete_unused_images(image_ids: set[str]) -> None:
    still_used = referenced_image_ids()
    for image_id in image_ids - still_used:
        media.delete(image_id)


# ---- sending ---------------------------------------------------------------------------


async def send(post_id: int, allowed_from: tuple[str, ...] = ("scheduled",)) -> ScheduledPost | None:
    """Claim and publish one scheduled post. Returns None if someone else already claimed it."""
    with database.session() as conn:
        if allowed_from != ("scheduled",):
            # "Send now" on a missed post: start a fresh attempt with its own request ID.
            conn.execute(
                f"UPDATE scheduled_posts SET attempt = attempt + 1 WHERE id = ? "
                f"AND status IN ({','.join('?' * len(allowed_from))})",
                (post_id, *allowed_from),
            )
        claimed = conn.execute(
            f"UPDATE scheduled_posts SET status = 'sending', updated_at = ? WHERE id = ? "
            f"AND status IN ({','.join('?' * len(allowed_from))})",
            (history.now_iso(), post_id, *allowed_from),
        ).rowcount
        row = conn.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone()
    if not claimed:
        return None

    request = _request_from_row(row)
    request_id = f"scheduled-{post_id}-{row['attempt']}"
    try:
        history_id, results = await publisher.publish_now(
            request, request_id, source="scheduled", delete_media=False
        )
        status = history.overall_status(results)
        status = "sent" if status == "success" else status
        failed = [r for r in results if not r.success]
        note = None if not failed else f"{len(failed)} of {len(results)} platforms failed. See History for details."
    except Exception:
        log.exception("Scheduled post %s crashed while sending", post_id)
        history_id, status, note = None, "failed", "Something unexpected went wrong while sending."

    with database.session() as conn:
        conn.execute(
            "UPDATE scheduled_posts SET status = ?, note = ?, history_post_id = ?, updated_at = ? WHERE id = ?",
            (status, note, history_id, history.now_iso(), post_id),
        )
    if status == "sent":
        _delete_unused_images(set(request.image_ids))
    return get(post_id)


def mark_missed(now: datetime | None = None) -> None:
    cutoff = to_utc_iso((now or _now()) - LATE_GRACE)
    with database.session() as conn:
        conn.execute(
            "UPDATE scheduled_posts SET status = 'missed', note = ? "
            "WHERE status = 'scheduled' AND enabled = 1 AND scheduled_at < ?",
            ("Auto Poster wasn't running at the scheduled time, so this was not sent. "
             "Send it now or choose a new time.", cutoff),
        )


def recover_after_restart() -> None:
    """Called at startup, before the scheduler loop begins."""
    with database.session() as conn:
        conn.execute(
            "UPDATE scheduled_posts SET status = 'failed', note = ? WHERE status = 'sending'",
            ("Auto Poster was closed while this post was being sent. It may already be on some "
             "platforms. Check them before sending it again.",),
        )
    mark_missed()


async def run_due(now: datetime | None = None) -> int:
    """Send every enabled post whose time has come. Returns how many were attempted."""
    now = now or _now()
    mark_missed(now)
    with database.session() as conn:
        rows = conn.execute(
            "SELECT id FROM scheduled_posts WHERE status = 'scheduled' AND enabled = 1 AND scheduled_at <= ? "
            "ORDER BY scheduled_at",
            (to_utc_iso(now),),
        ).fetchall()
    for row in rows:
        await send(row["id"])
    return len(rows)


async def run_forever() -> None:
    log.info("Scheduler started (checks every %s seconds).", CHECK_INTERVAL_SECONDS)
    while True:
        try:
            await run_due()
        except Exception:
            log.exception("Scheduler check failed; will try again.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
