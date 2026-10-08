"""'Delete everywhere'. All HTTP calls are mocked; nothing real is deleted."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from auto_poster.connectors.base import safe_delete
from auto_poster.connectors.bluesky import BlueskyConnector
from auto_poster.connectors.facebook import GRAPH, FacebookConnector
from auto_poster.connectors.linkedin import API_BASE as LINKEDIN_API, LinkedInConnector
from auto_poster.connectors.mastodon import MastodonConnector
from auto_poster.connectors.registry import get_connector
from auto_poster.connectors.telegram import API_BASE as TELEGRAM_API, TelegramConnector
from auto_poster.models import PostResult
from auto_poster.services import deleter, history
from auto_poster.services import settings as settings_service

TG_TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"
TG = f"{TELEGRAM_API}/bot{TG_TOKEN}"


def tg_error(description):
    return httpx.Response(400, json={"ok": False, "error_code": 400, "description": description})


# ---- connectors ----------------------------------------------------------------------------------


@respx.mock
async def test_telegram_deletes_whole_album():
    route = respx.post(f"{TG}/deleteMessages").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    error = await safe_delete(TelegramConnector(), "-1001:42,43", {"bot_token": TG_TOKEN, "chat_id": "@other"})
    assert error is None
    assert json.loads(route.calls[0].request.content) == {"chat_id": "-1001", "message_ids": [42, 43]}


@respx.mock
async def test_telegram_old_message_explains_48_hours():
    respx.post(f"{TG}/deleteMessages").mock(return_value=tg_error("Bad Request: message can't be deleted"))
    error = await safe_delete(TelegramConnector(), "-1001:42", {"bot_token": TG_TOKEN, "chat_id": "@c"})
    assert error.error_code == "missing_permission" and "48 hours" in error.message


@respx.mock
async def test_telegram_already_deleted():
    respx.post(f"{TG}/deleteMessages").mock(return_value=tg_error("Bad Request: message to delete not found"))
    assert await safe_delete(TelegramConnector(), "-1001:42", {"bot_token": TG_TOKEN, "chat_id": "@c"}) is None


@respx.mock
async def test_telegram_old_post_id_format_uses_settings_chat():
    route = respx.post(f"{TG}/deleteMessages").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    await safe_delete(TelegramConnector(), "42", {"bot_token": TG_TOKEN, "chat_id": "@chan"})
    assert json.loads(route.calls[0].request.content)["chat_id"] == "@chan"


@respx.mock
async def test_bluesky_delete_record():
    respx.post("https://bsky.social/xrpc/com.atproto.server.createSession").mock(return_value=httpx.Response(
        200, json={"accessJwt": "a", "refreshJwt": "r", "handle": "me.bsky.social", "did": "did:plc:me"}))
    route = respx.post("https://bsky.social/xrpc/com.atproto.repo.deleteRecord").mock(
        return_value=httpx.Response(200, json={}))
    error = await safe_delete(BlueskyConnector(), "at://did:plc:me/app.bsky.feed.post/3kabc",
                              {"handle": "me.bsky.social", "app_password": "a-b-c-d"})
    assert error is None
    assert json.loads(route.calls[0].request.content) == {
        "repo": "did:plc:me", "collection": "app.bsky.feed.post", "rkey": "3kabc"}


@respx.mock
async def test_mastodon_delete_and_already_gone():
    config = {"server": "social.example", "access_token": "tok"}
    respx.delete("https://social.example/api/v1/statuses/1").mock(return_value=httpx.Response(200, json={}))
    respx.delete("https://social.example/api/v1/statuses/2").mock(return_value=httpx.Response(404, json={}))
    respx.delete("https://social.example/api/v1/statuses/3").mock(return_value=httpx.Response(401, json={}))
    assert await safe_delete(MastodonConnector(), "1", config) is None
    assert await safe_delete(MastodonConnector(), "2", config) is None
    assert (await safe_delete(MastodonConnector(), "3", config)).error_code == "invalid_credentials"


@respx.mock
async def test_linkedin_delete():
    route = respx.delete(f"{LINKEDIN_API}/rest/posts/urn%3Ali%3Ashare%3A7").mock(return_value=httpx.Response(204))
    error = await safe_delete(LinkedInConnector(), "urn:li:share:7", {"access_token": "tok"})
    assert error is None and route.calls[0].request.headers["X-RestLi-Method"] == "DELETE"


@respx.mock
async def test_facebook_delete_uses_page_token():
    config = {"app_id": "a", "app_secret": "s", "page_tokens": json.dumps({"111": "PAGE_TOKEN"})}
    route = respx.delete(f"{GRAPH}/111_222").mock(return_value=httpx.Response(200, json={"success": True}))
    assert await safe_delete(FacebookConnector(), "111_222", config) is None
    assert route.calls[0].request.headers["Authorization"] == "Bearer PAGE_TOKEN"
    assert "PAGE_TOKEN" not in str(route.calls[0].request.url)


async def test_facebook_page_no_longer_connected():
    error = await safe_delete(FacebookConnector(), "999_1", {"app_id": "a", "app_secret": "s", "page_tokens": "{}"})
    assert error.error_code == "not_configured"


# ---- delete everywhere --------------------------------------------------------------------------


@pytest.fixture
def published():
    settings_service.save_settings(get_connector("telegram"), {"bot_token": TG_TOKEN, "chat_id": "@chan"})
    settings_service.save_settings(get_connector("mastodon"), {"server": "social.example", "access_token": "tok"})
    post_id = history.create_post("del-1", "Hello", 0, ["telegram", "mastodon", "bluesky"])
    history.save_results(post_id, [
        PostResult(platform="telegram", success=True, post_id="-1001:5"),
        PostResult(platform="mastodon", success=True, post_id="77"),
        PostResult(platform="bluesky", success=False, message="failed earlier"),
    ])
    return post_id


@respx.mock
async def test_delete_everywhere_partial_failure(published):
    respx.post(f"{TG}/deleteMessages").mock(return_value=tg_error("Bad Request: message can't be deleted"))
    respx.delete("https://social.example/api/v1/statuses/77").mock(return_value=httpx.Response(200, json={}))
    outcomes = {o.platform: o for o in await deleter.delete_everywhere(published)}
    assert set(outcomes) == {"telegram", "mastodon"}  # the failed Bluesky post is skipped
    assert outcomes["mastodon"].success and not outcomes["telegram"].success
    results = {r.platform: r for r in history.get_entry(published).results}
    assert results["mastodon"].deleted_at and not results["telegram"].deleted_at

    # Trying again only retries what is left.
    respx.post(f"{TG}/deleteMessages").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    outcomes = await deleter.delete_everywhere(published)
    assert [o.platform for o in outcomes] == ["telegram"] and outcomes[0].success


async def test_delete_everywhere_disconnected_platform(published):
    settings_service.delete_settings(get_connector("mastodon"))
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{TG}/deleteMessages").mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
        outcomes = {o.platform: o for o in await deleter.delete_everywhere(published)}
    assert not outcomes["mastodon"].success and "not connected" in outcomes["mastodon"].message


async def test_delete_everywhere_unknown_entry():
    assert await deleter.delete_everywhere(12345) is None
