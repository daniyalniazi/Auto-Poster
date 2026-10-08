"""Repost reminders: they remind, never post."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from auto_poster import desktop
from auto_poster.main import create_app
from auto_poster.models import PostRequest
from auto_poster.services import history, media, reminders
from tests.conftest import image_bytes


@pytest.fixture
def post_id():
    image = media.save_upload("a.png", image_bytes())
    return history.create_post("r-1", "Launch", 1, ["telegram"], compose=PostRequest(
        mode="structured", title="Launch", text="We shipped it", platforms=["telegram"], image_ids=[image.id]))


def future(**delta) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**delta)


def test_next_occurrence_rules():
    start = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
    assert reminders.next_occurrence(start, "once") is None
    assert reminders.next_occurrence(start, "weekly") - start == timedelta(days=7)
    assert reminders.next_occurrence(start, "every_2_weeks") - start == timedelta(days=14)
    monthly = reminders.next_occurrence(start, "monthly").astimezone()
    assert (monthly.month, monthly.day) in {(2, 28), (2, 27), (3, 1)}  # clamped to the end of February (local time)


def test_weekly_keeps_local_time_across_dst(monkeypatch):
    import os
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("Changing the time zone in-process needs tzset (not on Windows)")
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    try:
        before_dst = datetime(2026, 3, 25, 9, 0).astimezone()  # 09:00 GMT
        after = reminders.next_occurrence(before_dst, "weekly").astimezone()
        assert (after.hour, after.minute) == (9, 0)  # still 09:00 local, now BST
    finally:
        os.environ.pop("TZ", None)
        time.tzset()


def test_create_and_due(post_id):
    reminder = reminders.create(post_id, future(hours=1), "weekly")
    assert reminder.label == "Launch — We shipped it" and not reminder.due
    assert reminders.due() == []
    assert [r.id for r in reminders.due(future(hours=2))] == [reminder.id]


def test_create_validation(post_id):
    with pytest.raises(reminders.ReminderError, match="future"):
        reminders.create(post_id, future(days=-1), "once")
    with pytest.raises(reminders.ReminderError, match="no longer"):
        reminders.create(99999, future(days=1), "once")


def test_advance_once_stops_and_repeating_moves_to_future(post_id):
    once = reminders.create(post_id, future(minutes=5), "once")
    assert reminders.advance(once.id).status == "stopped"

    weekly = reminders.create(post_id, future(minutes=5), "weekly")
    # Pretend the app was closed for three weeks: the next date must still be in the future.
    advanced = reminders.advance(weekly.id, now=future(days=21))
    assert advanced.status == "active"
    assert datetime.fromisoformat(advanced.next_at) > future(days=21)


def test_reminder_keeps_images_for_repost(post_id):
    reminder = reminders.create(post_id, future(days=1), "monthly")
    image_id = history.get_compose(post_id).image_ids[0]
    history.delete_entry(post_id)  # the history entry goes away...
    request, images, missing = reminders.compose(reminder.id)
    assert request.title == "Launch" and [i.id for i in images] == [image_id] and missing == 0  # ...the image stays
    reminders.delete(reminder.id)
    assert media.load(image_id) is None


def test_announce_due_notifies_once(post_id, monkeypatch):
    sent = []
    monkeypatch.setattr(desktop, "notify", lambda title, message: sent.append(message) or True)
    reminders.create(post_id, future(minutes=1), "weekly")
    assert reminders.announce_due(now=future(minutes=2)) == 1
    assert reminders.announce_due(now=future(minutes=3)) == 0  # not repeated every check
    assert "Launch" in sent[0]


def test_api_flow(post_id):
    app = create_app(8765, run_scheduler=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.headers["X-Auto-Poster-Token"] = c.get("/api/session").json()["token"]
        created = c.post("/api/reminders", json={
            "history_post_id": post_id, "next_at": future(seconds=1).isoformat(), "repeat": "weekly"}).json()
        assert c.get("/api/reminders").json()[0]["id"] == created["id"]
        compose = c.get(f"/api/reminders/{created['id']}/compose").json()
        assert compose["request"]["title"] == "Launch"
        skipped = c.post(f"/api/reminders/{created['id']}/advance").json()
        assert skipped["next_at"] > created["next_at"]
        assert c.post(f"/api/reminders/{created['id']}/stop").json()["status"] == "stopped"
        bad = c.post("/api/reminders", json={"history_post_id": post_id, "next_at": future(days=-2).isoformat(),
                                             "repeat": "once"})
        assert bad.status_code == 400
