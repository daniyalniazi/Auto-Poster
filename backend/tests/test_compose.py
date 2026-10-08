"""Write once, adapt per platform: structured posts, previews and per-platform edits."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from auto_poster.connectors.registry import get_connector
from auto_poster.connectors.telegram import API_BASE
from auto_poster.models import PostRequest
from auto_poster.services import compose, history, publisher, scheduler
from auto_poster.services import settings as settings_service

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"
SEND = f"{API_BASE}/bot{TOKEN}/sendMessage"
OK = httpx.Response(200, json={"ok": True, "result": {"message_id": 1, "chat": {"id": -1001, "type": "channel"}}})


@pytest.fixture
def telegram_configured():
    settings_service.save_settings(get_connector("telegram"), {"bot_token": TOKEN, "chat_id": "@chan"})


def structured(**kwargs) -> PostRequest:
    values = {"mode": "structured", "title": "Big launch", "text": "We shipped it.",
              "hashtags": "#launch opensource, news #Launch", "link": "https://example.com"}
    values.update(kwargs)
    return PostRequest(**values)


def test_parse_hashtags():
    assert compose.parse_hashtags("#launch opensource, news #Launch ＃café") == ["launch", "opensource", "news", "café"]


def test_default_layout():
    text = compose.text_for(get_connector("mastodon"), structured())
    assert text == "Big launch\n\nWe shipped it.\n\nhttps://example.com\n\n#launch #opensource #news"


def test_link_not_repeated_if_in_body():
    request = structured(text="Read https://example.com now")
    assert compose.text_for(get_connector("bluesky"), request).count("https://example.com") == 1


def test_hashtag_caps_per_platform():
    request = structured(hashtags="a b c d e f g")
    assert compose.text_for(get_connector("linkedin"), request).endswith("#a #b #c #d #e")
    assert compose.text_for(get_connector("facebook"), request).endswith("#a #b #c")
    assert compose.text_for(get_connector("mastodon"), request).endswith("#a #b #c #d #e #f #g")
    notes = compose.style_notes(get_connector("facebook"), request)
    assert notes and notes[0].level == "warning" and "3 of your 7" in notes[0].message


def test_quick_mode_posts_text_as_typed():
    request = PostRequest(mode="quick", text="Just this", title="ignored", hashtags="ignored")
    assert compose.text_for(get_connector("linkedin"), request) == "Just this"


def test_override_wins_for_that_platform_only():
    request = structured(overrides={"bluesky": "Short version"})
    assert compose.text_for(get_connector("bluesky"), request) == "Short version"
    assert compose.text_for(get_connector("mastodon"), request).startswith("Big launch")


def test_title_kept_for_bold_only_if_text_starts_with_it():
    connector = get_connector("telegram")
    assert compose.post_for(connector, structured(), []).title == "Big launch"
    edited = structured(overrides={"telegram": "Different start"})
    assert compose.post_for(connector, edited, []).title == ""


def test_prepare_shows_each_platforms_version_and_error(telegram_configured):
    settings_service.save_settings(get_connector("bluesky"), {"handle": "me.bsky.social", "app_password": "a-b-c-d"})
    request = structured(text="x" * 400, platforms=["telegram", "bluesky"])
    result = publisher.prepare(request)
    previews = result["previews"]
    assert previews["telegram"].problems == []
    assert any("300" in p.message for p in previews["bluesky"].problems)
    assert not previews["bluesky"].customized

    # Fix it for Bluesky only: Telegram keeps the full text.
    request.overrides = {"bluesky": "Big launch — we shipped it! https://example.com"}
    previews = publisher.prepare(request)["previews"]
    assert previews["bluesky"].problems == [] and previews["bluesky"].customized
    assert "x" * 400 in previews["telegram"].text


@respx.mock
async def test_publish_sends_bold_title_and_records_sent_text(telegram_configured):
    route = respx.post(SEND).mock(return_value=OK)
    request = structured(platforms=["telegram"])
    _, results = await publisher.publish_now(request, "compose-1")
    body = json.loads(route.calls[0].request.content)
    assert body["text"].startswith("Big launch\n\nWe shipped it.")
    assert body["entities"] == [{"type": "bold", "offset": 0, "length": len("Big launch")}]
    assert results[0].sent_text == body["text"]
    entry = history.find_by_request_id("compose-1")
    assert entry.text == "Big launch\n\nWe shipped it."
    assert entry.results[0].sent_text == body["text"]


def test_scheduled_post_keeps_structure_and_edits(telegram_configured):
    request = structured(platforms=["telegram"], overrides={"telegram": "Custom Telegram text"})
    post = scheduler.create(request, datetime.now(timezone.utc) + timedelta(hours=1))
    assert post.mode == "structured" and post.title == "Big launch" and post.link == "https://example.com"
    assert post.overrides == {"telegram": "Custom Telegram text"}
