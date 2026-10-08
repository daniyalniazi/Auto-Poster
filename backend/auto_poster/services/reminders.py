"""Repost reminders: "remind me to post this again" on a date the user picks.

Reminders never post anything. When one is due, the app shows a banner (and a desktop
notification if the tray icon is running); the user chooses Repost, Skip or Stop.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel

from auto_poster import database
from auto_poster.models import ImageInfo, PostRequest
from auto_poster.services import history, media, media_usage

log = logging.getLogger(__name__)

Repeat = Literal["once", "weekly", "every_2_weeks", "monthly"]
REPEAT_LABELS = {"once": "once", "weekly": "every week", "every_2_weeks": "every 2 weeks", "monthly": "every month"}


class Reminder(BaseModel):
    id: int
    created_at: str
    history_post_id: int | None
    label: str
    next_at: str  # UTC ISO 8601
    repeat: Repeat
    status: Literal["active", "stopped"]
    due: bool


class ReminderError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _label(request: PostRequest) -> str:
    text = " — ".join(t.strip() for t in (request.title, request.text) if t and t.strip()) or "(post without text)"
    return text if len(text) <= 80 else text[:79] + "…"


def _from_row(row, now: datetime | None = None) -> Reminder:
    now = now or _now()
    return Reminder(
        id=row["id"], created_at=row["created_at"], history_post_id=row["history_post_id"], label=row["label"],
        next_at=row["next_at"], repeat=row["repeat"], status=row["status"],
        due=row["status"] == "active" and datetime.fromisoformat(row["next_at"]) <= now,
    )


def next_occurrence(current: datetime, repeat: Repeat) -> datetime | None:
    """The following reminder time, keeping the same local wall-clock time (and day, for monthly)."""
    if repeat == "once":
        return None
    local = current.astimezone().replace(tzinfo=None)  # this computer's local time
    if repeat == "monthly":
        year, month = (local.year + local.month // 12, local.month % 12 + 1)
        day = min(local.day, calendar.monthrange(year, month)[1])
        local = local.replace(year=year, month=month, day=day)
    else:
        local += timedelta(days=7 if repeat == "weekly" else 14)
    return local.astimezone(timezone.utc)  # a naive datetime is read as local time


def create(history_post_id: int, first_at: datetime, repeat: Repeat) -> Reminder:
    if first_at.tzinfo is None:
        raise ReminderError("The reminder time must include a time zone.")
    if first_at < _now() - timedelta(minutes=1):
        raise ReminderError("Choose a date and time in the future.")
    request = history.get_compose(history_post_id)
    if request is None:
        raise ReminderError("That post is no longer in your history.")
    now = history.now_iso()
    with database.session() as conn:
        reminder_id = conn.execute(
            "INSERT INTO reminders (created_at, updated_at, history_post_id, label, request, next_at, repeat, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
            (now, now, history_post_id, _label(request), request.model_dump_json(), _iso(first_at), repeat),
        ).lastrowid
    return get(reminder_id)


def get(reminder_id: int) -> Reminder | None:
    with database.session() as conn:
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    return _from_row(row) if row else None


def list_all() -> list[Reminder]:
    with database.session() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, next_at"
        ).fetchall()
    return [_from_row(r) for r in rows]


def due(now: datetime | None = None) -> list[Reminder]:
    now = now or _now()
    with database.session() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'active' AND next_at <= ? ORDER BY next_at", (_iso(now),)
        ).fetchall()
    return [_from_row(r, now) for r in rows]


def compose(reminder_id: int) -> tuple[PostRequest, list[ImageInfo], int] | None:
    """The post to prefill when the user chooses Repost: (request, images that still exist, missing count)."""
    with database.session() as conn:
        row = conn.execute("SELECT request FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    if row is None:
        return None
    request = PostRequest.model_validate_json(row["request"])
    images = media.infos(request.image_ids)
    missing = len(request.image_ids) - len(images)
    request.image_ids = [i.id for i in images]
    return request, images, missing


def advance(reminder_id: int, now: datetime | None = None) -> Reminder | None:
    """After Repost or Skip: move to the next date (or stop, for a one-time reminder)."""
    now = now or _now()
    reminder = get(reminder_id)
    if reminder is None:
        return None
    following = next_occurrence(datetime.fromisoformat(reminder.next_at), reminder.repeat)
    while following is not None and following <= now:  # the app may have been closed for a while
        following = next_occurrence(following, reminder.repeat)
    with database.session() as conn:
        if following is None:
            conn.execute("UPDATE reminders SET status = 'stopped', updated_at = ? WHERE id = ?",
                         (history.now_iso(), reminder_id))
        else:
            conn.execute("UPDATE reminders SET next_at = ?, notified_at = NULL, updated_at = ? WHERE id = ?",
                         (_iso(following), history.now_iso(), reminder_id))
    return get(reminder_id)


def update(reminder_id: int, next_at: datetime, repeat: Repeat) -> Reminder | None:
    if next_at.tzinfo is None:
        raise ReminderError("The reminder time must include a time zone.")
    with database.session() as conn:
        conn.execute(
            "UPDATE reminders SET next_at = ?, repeat = ?, status = 'active', notified_at = NULL, updated_at = ? "
            "WHERE id = ?",
            (_iso(next_at), repeat, history.now_iso(), reminder_id),
        )
    return get(reminder_id)


def stop(reminder_id: int) -> Reminder | None:
    with database.session() as conn:
        conn.execute("UPDATE reminders SET status = 'stopped', updated_at = ? WHERE id = ?",
                     (history.now_iso(), reminder_id))
    return get(reminder_id)


def delete(reminder_id: int) -> None:
    found = compose(reminder_id)
    with database.session() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    if found:
        media_usage.delete_unused(found[0].image_ids)


def announce_due(now: datetime | None = None) -> int:
    """Show one desktop notification per newly due reminder. Called by the scheduler loop."""
    from auto_poster import desktop

    now = now or _now()
    announced = 0
    with database.session() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'active' AND next_at <= ? AND notified_at IS NULL",
            (_iso(now),),
        ).fetchall()
        for row in rows:
            desktop.notify("Time to repost?", f"“{row['label']}” — open Auto Poster to repost it.")
            conn.execute("UPDATE reminders SET notified_at = ? WHERE id = ?", (_iso(now), row["id"]))
            announced += 1
    return announced
