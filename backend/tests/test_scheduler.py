"""Scheduling tests. Telegram HTTP calls are mocked; time is passed in explicitly."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from auto_poster import database
from auto_poster.connectors.telegram import API_BASE
from auto_poster.connectors.registry import get_connector
from auto_poster.models import PostRequest
from auto_poster.services import history, scheduler
from auto_poster.services import settings as settings_service

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"
SEND = f"{API_BASE}/bot{TOKEN}/sendMessage"
OK = httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "chat": {"id": -1001, "type": "channel"}}})


@pytest.fixture(autouse=True)
def telegram_configured():
    settings_service.save_settings(get_connector("telegram"), {"bot_token": TOKEN, "chat_id": "@chan"})


def now() -> datetime:
    return datetime.now(timezone.utc)


def request(text="Scheduled hello") -> PostRequest:
    return PostRequest(text=text, platforms=["telegram"])


def schedule_in(minutes: float, text="Scheduled hello") -> scheduler.ScheduledPost:
    return scheduler.create(request(text), now() + timedelta(minutes=minutes))


def force_time(post_id: int, when: datetime) -> None:
    with database.session() as conn:
        conn.execute("UPDATE scheduled_posts SET scheduled_at = ? WHERE id = ?", (scheduler.to_utc_iso(when), post_id))


def test_create_rejects_past_time():
    with pytest.raises(scheduler.ScheduleError, match="past"):
        scheduler.create(request(), now() - timedelta(hours=1))


def test_create_rejects_invalid_post():
    with pytest.raises(scheduler.ScheduleError, match="4096"):
        scheduler.create(request("x" * 5000), now() + timedelta(hours=1))


def test_create_rejects_naive_time():
    with pytest.raises(scheduler.ScheduleError, match="time zone"):
        scheduler.create(request(), datetime.now() + timedelta(hours=1))


@respx.mock
async def test_not_sent_before_time():
    route = respx.post(SEND).mock(return_value=OK)
    schedule_in(30)
    assert await scheduler.run_due() == 0
    assert route.call_count == 0


@respx.mock
async def test_due_post_is_sent_once():
    route = respx.post(SEND).mock(return_value=OK)
    post = schedule_in(30)
    later = now() + timedelta(minutes=31)
    assert await scheduler.run_due(later) == 1
    assert await scheduler.run_due(later) == 0  # second check must not resend
    assert route.call_count == 1
    sent = scheduler.get(post.id)
    assert sent.status == "sent"
    entry = history.get_entry(sent.history_post_id)
    assert entry.source == "scheduled" and entry.status == "success"


@respx.mock
async def test_disabled_post_is_not_sent():
    route = respx.post(SEND).mock(return_value=OK)
    post = schedule_in(30)
    scheduler.set_enabled(post.id, False)
    await scheduler.run_due(now() + timedelta(minutes=31))
    assert route.call_count == 0
    assert scheduler.get(post.id).status == "scheduled"


@respx.mock
async def test_failed_scheduled_post_is_recorded():
    respx.post(SEND).mock(return_value=httpx.Response(
        403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot is not a member"}))
    post = schedule_in(30)
    await scheduler.run_due(now() + timedelta(minutes=31))
    failed = scheduler.get(post.id)
    assert failed.status == "failed"
    assert "failed" in failed.note
    assert history.get_entry(failed.history_post_id).results[0].error_code == "missing_permission"


def test_sending_post_is_not_resent_after_restart():
    post = schedule_in(30)
    with database.session() as conn:
        conn.execute("UPDATE scheduled_posts SET status = 'sending' WHERE id = ?", (post.id,))
    scheduler.recover_after_restart()
    recovered = scheduler.get(post.id)
    assert recovered.status == "failed"
    assert "closed while" in recovered.note


@respx.mock
async def test_slightly_late_post_still_sent():
    route = respx.post(SEND).mock(return_value=OK)
    post = schedule_in(30)
    force_time(post.id, now() - timedelta(minutes=20))
    await scheduler.run_due()
    assert route.call_count == 1


@respx.mock
async def test_very_late_post_marked_missed_not_sent():
    route = respx.post(SEND).mock(return_value=OK)
    post = schedule_in(30)
    force_time(post.id, now() - timedelta(hours=5))
    await scheduler.run_due()
    assert route.call_count == 0
    assert scheduler.get(post.id).status == "missed"

    # The user can still choose to send it now.
    result = await scheduler.send(post.id, allowed_from=("scheduled", "missed"))
    assert result.status == "sent" and route.call_count == 1


@respx.mock
async def test_edit_and_reschedule():
    respx.post(SEND).mock(return_value=OK)
    post = schedule_in(30)
    updated = scheduler.update(post.id, request("Edited"), now() + timedelta(hours=2))
    assert updated.text == "Edited"
    assert updated.scheduled_at > post.scheduled_at


async def test_cannot_edit_sent_post():
    post = schedule_in(30)
    with database.session() as conn:
        conn.execute("UPDATE scheduled_posts SET status = 'sent' WHERE id = ?", (post.id,))
    with pytest.raises(scheduler.ScheduleError):
        scheduler.update(post.id, request("Edited"), now() + timedelta(hours=2))


def test_delete_removes_post_and_images(make_image):
    image = make_image()
    post = scheduler.create(PostRequest(text="With image", platforms=["telegram"], image_ids=[image.id]),
                            now() + timedelta(hours=1))
    scheduler.delete(post.id)
    assert scheduler.get(post.id) is None
    from auto_poster.services import media
    assert media.load(image.id) is None


@respx.mock
async def test_send_now_twice_posts_once():
    route = respx.post(SEND).mock(return_value=OK)
    post = schedule_in(30)
    first = await scheduler.send(post.id, allowed_from=("scheduled", "missed"))
    second = await scheduler.send(post.id, allowed_from=("scheduled", "missed"))
    assert first.status == "sent" and second is None
    assert route.call_count == 1
