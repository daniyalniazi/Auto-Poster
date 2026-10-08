"""Telegram connector tests. All HTTP calls are mocked with respx; nothing is posted."""

from __future__ import annotations

import json

import httpx
import respx

from auto_poster.connectors.base import safe_post, safe_test_connection
from auto_poster.connectors.telegram import API_BASE, TelegramConnector
from auto_poster.models import Post

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"
CONFIG = {"bot_token": TOKEN, "chat_id": "@testchannel"}
API = f"{API_BASE}/bot{TOKEN}"

telegram = TelegramConnector()


def ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def fail(status, description, **parameters):
    body = {"ok": False, "error_code": status, "description": description}
    if parameters:
        body["parameters"] = parameters
    return httpx.Response(status, json=body)


MESSAGE = {"message_id": 42, "chat": {"id": -1001234567890, "type": "channel", "username": "testchannel"}}


# ---- configuration / connection test ---------------------------------------------------


@respx.mock
async def test_connection_ok_for_channel_admin():
    respx.post(f"{API}/getMe").mock(return_value=ok({"id": 99, "username": "my_bot"}))
    respx.post(f"{API}/getChat").mock(return_value=ok({"id": -100, "type": "channel", "title": "News"}))
    respx.post(f"{API}/getChatMember").mock(
        return_value=ok({"status": "administrator", "can_post_messages": True})
    )
    status = await safe_test_connection(telegram, CONFIG)
    assert status.ok
    assert "@my_bot" in status.message and "News" in status.message


@respx.mock
async def test_connection_channel_admin_without_post_right():
    respx.post(f"{API}/getMe").mock(return_value=ok({"id": 99, "username": "my_bot"}))
    respx.post(f"{API}/getChat").mock(return_value=ok({"id": -100, "type": "channel", "title": "News"}))
    respx.post(f"{API}/getChatMember").mock(
        return_value=ok({"status": "administrator", "can_post_messages": False})
    )
    status = await safe_test_connection(telegram, CONFIG)
    assert not status.ok
    assert status.error_code == "missing_permission"


@respx.mock
async def test_connection_invalid_token():
    respx.post(f"{API}/getMe").mock(return_value=fail(401, "Unauthorized"))
    status = await safe_test_connection(telegram, CONFIG)
    assert not status.ok
    assert status.error_code == "invalid_credentials"
    assert "bot token" in status.message
    assert TOKEN not in (status.technical_details or "")


@respx.mock
async def test_connection_chat_not_found():
    respx.post(f"{API}/getMe").mock(return_value=ok({"id": 99, "username": "my_bot"}))
    respx.post(f"{API}/getChat").mock(return_value=fail(400, "Bad Request: chat not found"))
    status = await safe_test_connection(telegram, CONFIG)
    assert status.error_code == "not_found"


async def test_connection_missing_configuration():
    status = await safe_test_connection(telegram, {"bot_token": TOKEN})
    assert not status.ok
    assert status.error_code == "not_configured"


# ---- local validation -------------------------------------------------------------------


def test_valid_text_post_has_no_problems():
    assert telegram.validate(Post(text="Hello", images=[]), {}, CONFIG) == []


def test_too_long_text():
    problems = telegram.validate(Post(text="x" * 4097, images=[]), {}, CONFIG)
    assert len(problems) == 1 and "4096" in problems[0].message


def test_caption_limit_applies_with_images(make_image):
    problems = telegram.validate(Post(text="x" * 1025, images=[make_image()]), {}, CONFIG)
    assert any("1024" in p.message for p in problems)


def test_emoji_counted_as_utf16():
    # Each of these emoji is 2 UTF-16 units, so 2049 of them exceed 4096.
    problems = telegram.validate(Post(text="😀" * 2049, images=[]), {}, CONFIG)
    assert problems


def test_unsupported_image_format(make_image):
    problems = telegram.validate(Post(text="hi", images=[make_image("GIF")]), {}, CONFIG)
    assert any("GIF" in p.message for p in problems)


def test_image_dimensions_too_large(make_image):
    image = make_image()
    image.width, image.height = 9000, 2000
    problems = telegram.validate(Post(text="hi", images=[image]), {}, CONFIG)
    assert any("10000" in p.message for p in problems)


def test_too_many_images(make_image):
    images = [make_image() for _ in range(11)]
    problems = telegram.validate(Post(text="hi", images=images), {}, CONFIG)
    assert any("at most 10" in p.message for p in problems)


def test_image_only_post_is_allowed(make_image):
    assert telegram.validate(Post(text="", images=[make_image()]), {}, CONFIG) == []


# ---- posting ------------------------------------------------------------------------------


@respx.mock
async def test_post_text():
    route = respx.post(f"{API}/sendMessage").mock(return_value=ok(MESSAGE))
    result = await safe_post(telegram, Post(text="Hello *world*", images=[]), {}, CONFIG)
    assert result.success
    assert result.post_id == "-1001234567890:42"
    assert result.post_url == "https://t.me/testchannel/42"
    sent = json.loads(route.calls[0].request.content)
    assert sent["text"] == "Hello *world*"
    assert "parse_mode" not in sent  # text must appear exactly as typed


@respx.mock
async def test_post_single_image(make_image):
    route = respx.post(f"{API}/sendPhoto").mock(return_value=ok(MESSAGE))
    result = await safe_post(telegram, Post(text="Look", images=[make_image()]), {"silent": "true"}, CONFIG)
    assert result.success
    body = route.calls[0].request.content
    assert b'name="caption"' in body and b"Look" in body
    assert b'name="photo"' in body
    assert b'name="disable_notification"' in body


@respx.mock
async def test_post_album(make_image):
    route = respx.post(f"{API}/sendMediaGroup").mock(return_value=ok([MESSAGE, {**MESSAGE, "message_id": 43}]))
    result = await safe_post(telegram, Post(text="Album", images=[make_image(), make_image("JPEG")]), {}, CONFIG)
    assert result.success and result.post_id == "-1001234567890:42,43"
    body = route.calls[0].request.content
    assert b"attach://photo0" in body and b"attach://photo1" in body


@respx.mock
async def test_private_channel_url():
    message = {"message_id": 7, "chat": {"id": -1009876543210, "type": "channel"}}
    respx.post(f"{API}/sendMessage").mock(return_value=ok(message))
    result = await safe_post(telegram, Post(text="Hi", images=[]), {}, CONFIG)
    assert result.post_url == "https://t.me/c/9876543210/7"


@respx.mock
async def test_post_rate_limited():
    respx.post(f"{API}/sendMessage").mock(
        return_value=fail(429, "Too Many Requests: retry after 35", retry_after=35)
    )
    result = await safe_post(telegram, Post(text="Hi", images=[]), {}, CONFIG)
    assert not result.success
    assert result.error_code == "rate_limited"
    assert result.retry_after == 35
    assert "35 seconds" in result.message


@respx.mock
async def test_post_bot_not_admin():
    respx.post(f"{API}/sendMessage").mock(
        return_value=fail(403, "Forbidden: bot is not a member of the channel chat")
    )
    result = await safe_post(telegram, Post(text="Hi", images=[]), {}, CONFIG)
    assert result.error_code == "missing_permission"


@respx.mock
async def test_post_group_migrated():
    respx.post(f"{API}/sendMessage").mock(
        return_value=fail(400, "Bad Request: group chat was upgraded to a supergroup chat",
                          migrate_to_chat_id=-1001111)
    )
    result = await safe_post(telegram, Post(text="Hi", images=[]), {}, CONFIG)
    assert "-1001111" in result.message


@respx.mock
async def test_post_invalid_image_rejected_by_api(make_image):
    respx.post(f"{API}/sendPhoto").mock(return_value=fail(400, "Bad Request: PHOTO_INVALID_DIMENSIONS"))
    result = await safe_post(telegram, Post(text="Hi", images=[make_image()]), {}, CONFIG)
    assert result.error_code == "invalid_media"


@respx.mock
async def test_post_server_error():
    respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(502, text="Bad Gateway"))
    result = await safe_post(telegram, Post(text="Hi", images=[]), {}, CONFIG)
    assert result.error_code == "platform_unavailable"


@respx.mock
async def test_post_network_failure_hides_token():
    respx.post(f"{API}/sendMessage").mock(side_effect=httpx.ConnectError(f"failed for {API}/sendMessage"))
    result = await safe_post(telegram, Post(text="Hi", images=[]), {}, CONFIG)
    assert result.error_code == "network_error"
    assert TOKEN not in (result.technical_details or "")


@respx.mock
async def test_post_timeout():
    respx.post(f"{API}/sendMessage").mock(side_effect=httpx.ReadTimeout("timed out"))
    result = await safe_post(telegram, Post(text="Hi", images=[]), {}, CONFIG)
    assert result.error_code == "timeout"


# ---- "Find my channels and groups" ---------------------------------------------------------


@respx.mock
async def test_find_chats_lists_groups_and_channels():
    respx.post(f"{API}/getUpdates").mock(return_value=ok([
        {"update_id": 1, "my_chat_member": {"chat": {"id": -1001, "type": "channel", "title": "Private News"}}},
        {"update_id": 2, "message": {"chat": {"id": 555, "type": "private", "first_name": "Me"}}},
        {"update_id": 3, "channel_post": {"chat": {"id": -1002, "type": "channel", "title": "Pub",
                                                     "username": "pubchan"}}},
    ]))
    result = await telegram.run_action("find_chats", CONFIG, "http://127.0.0.1:8765")
    assert result.ok
    assert "-1001" in result.message and "@pubchan" in result.message
    assert "555" not in result.message  # private chats with users are not destinations


@respx.mock
async def test_find_chats_none_found():
    respx.post(f"{API}/getUpdates").mock(return_value=ok([]))
    result = await telegram.run_action("find_chats", CONFIG, "http://127.0.0.1:8765")
    assert not result.ok
