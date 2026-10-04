"""Mastodon connector tests. All HTTP calls are mocked with respx; nothing is posted."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from auto_poster.connectors.base import safe_oauth_callback, safe_post, safe_run_action, safe_test_connection
from auto_poster.connectors.mastodon import MastodonConnector, mastodon_length, normalize_server
from auto_poster.models import Post
from auto_poster.services.settings import load_config

SERVER = "social.example"
BASE = f"https://{SERVER}"
TOKEN = "mastodon-test-token-1234567890"
APP_URL = "http://127.0.0.1:8765"

INSTANCE = {
    "configuration": {
        "statuses": {"max_characters": 1000, "max_media_attachments": 4, "characters_reserved_per_url": 23},
        "media_attachments": {"image_size_limit": 8_000_000, "image_matrix_limit": 16_777_216,
                              "supported_mime_types": ["image/jpeg", "image/png", "video/mp4"]},
    }
}
STATUS = {"id": "1111", "url": f"{BASE}/@me/1111"}


@pytest.fixture
def mastodon():
    return MastodonConnector()


@pytest.fixture
def config():
    return {"server": SERVER, "access_token": TOKEN}


def mock_account_and_instance():
    respx.get(f"{BASE}/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(200, json={"acct": "me", "username": "me"}))
    respx.get(f"{BASE}/api/v2/instance").mock(return_value=httpx.Response(200, json=INSTANCE))


# ---- helpers ---------------------------------------------------------------------------------


def test_normalize_server():
    assert normalize_server("https://Social.Example/") == SERVER
    assert normalize_server("@me@social.example") == SERVER
    assert normalize_server("social.example") == SERVER


def test_mastodon_counting_rules():
    assert mastodon_length("https://example.com/" + "x" * 100) == 23
    assert mastodon_length("@alice@remote.example hi") == len("@alice hi")


# ---- connection ------------------------------------------------------------------------------


@respx.mock
async def test_connection_loads_server_limits(mastodon, config):
    mock_account_and_instance()
    status = await safe_test_connection(mastodon, config)
    assert status.ok and "@me@social.example" in status.message and "1000" in status.message
    limits = mastodon.effective_limits(load_config(mastodon))
    assert limits.max_chars == 1000
    assert limits.max_image_bytes == 8_000_000
    assert limits.image_formats == ["JPEG", "PNG"]


@respx.mock
async def test_connection_invalid_token(mastodon, config):
    respx.get(f"{BASE}/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(401, json={"error": "The access token is invalid"}))
    status = await safe_test_connection(mastodon, config)
    assert status.error_code == "invalid_credentials"
    assert "Connect Mastodon" in status.message


async def test_connection_missing_configuration(mastodon):
    status = await safe_test_connection(mastodon, {"server": SERVER})
    assert status.error_code == "not_configured"


@respx.mock
async def test_falls_back_to_v1_instance(mastodon, config):
    respx.get(f"{BASE}/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(200, json={"acct": "me"}))
    respx.get(f"{BASE}/api/v2/instance").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/api/v1/instance").mock(return_value=httpx.Response(200, json={"max_toot_chars": 5000}))
    status = await safe_test_connection(mastodon, config)
    assert status.ok and "5000" in status.message


# ---- browser sign-in -------------------------------------------------------------------------


@respx.mock
async def test_connect_flow(mastodon, store):
    register = respx.post(f"{BASE}/api/v1/apps").mock(
        return_value=httpx.Response(200, json={"client_id": "cid", "client_secret": "csecret"}))
    result = await safe_run_action(mastodon, "connect", {"server": SERVER}, APP_URL)
    assert result.ok and result.open_url.startswith(f"{BASE}/oauth/authorize?")
    query = parse_qs(urlparse(result.open_url).query)
    assert query["redirect_uri"] == [f"{APP_URL}/oauth/mastodon/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert "write:statuses" in query["scope"][0]
    assert register.call_count == 1

    token = respx.post(f"{BASE}/oauth/token").mock(return_value=httpx.Response(200, json={"access_token": TOKEN}))
    mock_account_and_instance()
    done = await safe_oauth_callback(mastodon, {"code": "abc", "state": query["state"][0]}, load_config(mastodon))
    assert done.ok and "@me@social.example" in done.message
    assert store.data["mastodon:access_token"] == TOKEN
    sent = parse_qs(token.calls[0].request.content.decode())
    assert sent["code_verifier"] and sent["client_secret"] == ["csecret"]

    # Connecting again reuses the registered app.
    await safe_run_action(mastodon, "connect", load_config(mastodon), APP_URL)
    assert register.call_count == 1


async def test_callback_with_unknown_state_is_rejected(mastodon):
    result = await safe_oauth_callback(mastodon, {"code": "abc", "state": "forged"}, {})
    assert not result.ok and "expired" in result.message


@respx.mock
async def test_callback_when_user_denies(mastodon):
    respx.post(f"{BASE}/api/v1/apps").mock(
        return_value=httpx.Response(200, json={"client_id": "cid", "client_secret": "csecret"}))
    started = await safe_run_action(mastodon, "connect", {"server": SERVER}, APP_URL)
    state = parse_qs(urlparse(started.open_url).query)["state"][0]
    result = await safe_oauth_callback(mastodon, {"error": "access_denied", "state": state}, {})
    assert not result.ok and "not approved" in result.message


async def test_connect_requires_server(mastodon):
    result = await safe_run_action(mastodon, "connect", {}, APP_URL)
    assert not result.ok


# ---- validation ------------------------------------------------------------------------------


def test_uses_server_character_limit(mastodon, config):
    config["instance_config"] = json.dumps({"max_characters": 1000})
    assert mastodon.validate(Post(text="x" * 900, images=[]), {}, config) == []
    problems = mastodon.validate(Post(text="x" * 1001, images=[]), {}, config)
    assert any("1000" in p.message for p in problems)


def test_default_limit_before_connecting(mastodon, config):
    problems = mastodon.validate(Post(text="x" * 501, images=[]), {}, config)
    assert any("500" in p.message for p in problems)


def test_long_links_count_as_23(mastodon, config):
    text = "x" * 470 + " https://example.com/" + "a" * 200
    assert mastodon.validate(Post(text=text, images=[]), {}, config) == []


def test_content_warning_counts_toward_limit(mastodon, config):
    problems = mastodon.validate(Post(text="x" * 490, images=[]), {"spoiler_text": "y" * 20}, config)
    assert any("content warning" in p.message for p in problems)


def test_server_image_formats(mastodon, config, make_image):
    config["instance_config"] = json.dumps({"mime_types": ["image/jpeg", "image/png"]})
    problems = mastodon.validate(Post(text="hi", images=[make_image("WEBP")]), {}, config)
    assert any("WEBP" in p.message for p in problems)


def test_too_many_pixels(mastodon, config, make_image):
    image = make_image()
    image.width, image.height = 8000, 6000
    problems = mastodon.validate(Post(text="hi", images=[image]), {}, config)
    assert any("pixels" in p.message for p in problems)


def test_too_many_images(mastodon, config, make_image):
    problems = mastodon.validate(Post(text="hi", images=[make_image() for _ in range(5)]), {}, config)
    assert any("at most 4" in p.message for p in problems)


# ---- posting ---------------------------------------------------------------------------------


@respx.mock
async def test_post_text_with_options(mastodon, config):
    route = respx.post(f"{BASE}/api/v1/statuses").mock(return_value=httpx.Response(200, json=STATUS))
    result = await safe_post(
        mastodon, Post(text="Hello fediverse", images=[]),
        {"visibility": "unlisted", "spoiler_text": "Spoilers", "sensitive": "false"}, config,
    )
    assert result.success and result.post_url == f"{BASE}/@me/1111"
    request = route.calls[0].request
    body = json.loads(request.content)
    assert body == {"status": "Hello fediverse", "visibility": "unlisted", "spoiler_text": "Spoilers"}
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Idempotency-Key"]


@respx.mock
async def test_post_with_images_waits_for_processing(mastodon, config, make_image, monkeypatch):
    monkeypatch.setattr("auto_poster.connectors.mastodon.MEDIA_POLL_SECONDS", 0)
    upload = respx.post(f"{BASE}/api/v2/media").mock(side_effect=[
        httpx.Response(200, json={"id": "m1", "url": f"{BASE}/m1.png"}),
        httpx.Response(202, json={"id": "m2", "url": None}),
    ])
    respx.get(f"{BASE}/api/v1/media/m2").mock(side_effect=[
        httpx.Response(206, json={"id": "m2", "url": None}),
        httpx.Response(200, json={"id": "m2", "url": f"{BASE}/m2.png"}),
    ])
    route = respx.post(f"{BASE}/api/v1/statuses").mock(return_value=httpx.Response(200, json=STATUS))
    image = make_image()
    image.alt_text = "A chart"
    result = await safe_post(mastodon, Post(text="", images=[image, make_image()]), {"sensitive": "true"}, config)
    assert result.success
    assert b"A chart" in upload.calls[0].request.content
    body = json.loads(route.calls[0].request.content)
    assert body["media_ids"] == ["m1", "m2"] and body["sensitive"] is True


@respx.mock
async def test_post_too_long_rejected_by_server(mastodon, config):
    respx.post(f"{BASE}/api/v1/statuses").mock(return_value=httpx.Response(
        422, json={"error": "Validation failed: Text character limit of 500 exceeded"}))
    result = await safe_post(mastodon, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "too_long"


@respx.mock
async def test_invalid_image_rejected_by_server(mastodon, config, make_image):
    respx.post(f"{BASE}/api/v2/media").mock(return_value=httpx.Response(
        422, json={"error": "Validation failed: File content type is invalid"}))
    result = await safe_post(mastodon, Post(text="hi", images=[make_image()]), {}, config)
    assert result.error_code == "invalid_media"


@respx.mock
async def test_missing_scope(mastodon, config):
    respx.post(f"{BASE}/api/v1/statuses").mock(return_value=httpx.Response(
        403, json={"error": "This action is outside the authorized scopes"}))
    result = await safe_post(mastodon, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "missing_permission"


@respx.mock
async def test_rate_limited(mastodon, config):
    respx.post(f"{BASE}/api/v1/statuses").mock(return_value=httpx.Response(
        429, json={"error": "Too many requests"}, headers={"X-RateLimit-Reset": "2999-01-01T00:00:00.000Z"}))
    result = await safe_post(mastodon, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "rate_limited" and result.retry_after > 0


@respx.mock
async def test_server_down(mastodon, config):
    respx.post(f"{BASE}/api/v1/statuses").mock(return_value=httpx.Response(502, text="Bad gateway"))
    result = await safe_post(mastodon, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "platform_unavailable"


@respx.mock
async def test_network_failure(mastodon, config):
    respx.post(f"{BASE}/api/v1/statuses").mock(side_effect=httpx.ConnectError("unreachable"))
    result = await safe_post(mastodon, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "network_error"
