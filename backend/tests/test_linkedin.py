"""LinkedIn connector tests. All HTTP calls are mocked with respx; nothing is posted."""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from auto_poster.connectors.base import safe_oauth_callback, safe_post, safe_run_action, safe_test_connection
from auto_poster.connectors.linkedin import API_BASE, AUTH_BASE, LINKEDIN_VERSION, LinkedInConnector, to_little_text
from auto_poster.models import Post
from auto_poster.services.settings import load_config, save_settings

TOKEN = "AQV-linkedin-test-token-0123456789abcdef"
SECRET = "linkedin-client-secret-xyz"
APP_URL = "http://127.0.0.1:8765"
UPLOAD_URL = "https://www.linkedin.com/dms-uploads/abc/uploaded-image/0"


@pytest.fixture
def linkedin():
    return LinkedInConnector()


@pytest.fixture
def config():
    return {
        "client_id": "cid123", "client_secret": SECRET, "access_token": TOKEN,
        "person_id": "abc123", "token_expires_at": str(int(time.time()) + 50 * 86400),
    }


def created(urn="urn:li:share:7000000000000000000"):
    return httpx.Response(201, headers={"x-restli-id": urn})


# ---- text format -------------------------------------------------------------------------------


def test_little_text_escapes_reserved_characters():
    assert to_little_text("Hi (all) @team *now*") == "Hi \\(all\\) \\@team \\*now\\*"


def test_little_text_keeps_hashtags():
    assert to_little_text("#launch day #2026 a#b") == "#launch day #2026 a\\#b"


# ---- connection --------------------------------------------------------------------------------


def test_not_connected_until_signed_in(linkedin):
    assert not linkedin.is_configured({"client_id": "x", "client_secret": "y"})


async def test_connect_requires_app_details(linkedin):
    result = await safe_run_action(linkedin, "connect", {}, APP_URL)
    assert not result.ok and "Client ID" in result.message


@respx.mock
async def test_connect_flow(linkedin, store):
    save_settings(linkedin, {"client_id": "cid123", "client_secret": SECRET})
    started = await safe_run_action(linkedin, "connect", load_config(linkedin), APP_URL)
    assert started.ok and started.open_url.startswith(f"{AUTH_BASE}/authorization?")
    query = parse_qs(urlparse(started.open_url).query)
    assert query["redirect_uri"] == ["http://localhost:8765/oauth/linkedin/callback"]
    assert query["scope"] == ["openid profile w_member_social"]
    assert SECRET not in started.open_url  # the secret never goes into a URL

    token_route = respx.post(f"{AUTH_BASE}/accessToken").mock(
        return_value=httpx.Response(200, json={"access_token": TOKEN, "expires_in": 5184000}))
    respx.get(f"{API_BASE}/v2/userinfo").mock(
        return_value=httpx.Response(200, json={"sub": "abc123", "name": "Test Person"}))
    done = await safe_oauth_callback(linkedin, {"code": "authcode", "state": query["state"][0]},
                                     load_config(linkedin))
    assert done.ok and "Test Person" in done.message
    assert store.data["linkedin:access_token"] == TOKEN
    config = load_config(linkedin)
    assert config["person_id"] == "abc123"
    assert float(config["token_expires_at"]) > time.time() + 59 * 86400
    sent = parse_qs(token_route.calls[0].request.content.decode())
    assert sent["client_secret"] == [SECRET] and sent["redirect_uri"] == [query["redirect_uri"][0]]
    assert linkedin.is_configured(config)


async def test_callback_user_cancelled(linkedin, config):
    started = await safe_run_action(linkedin, "connect", config, APP_URL)
    state = parse_qs(urlparse(started.open_url).query)["state"][0]
    result = await safe_oauth_callback(linkedin, {"error": "user_cancelled_authorize", "state": state}, config)
    assert not result.ok and "not approved" in result.message


async def test_callback_redirect_mismatch_explained(linkedin, config):
    started = await safe_run_action(linkedin, "connect", config, APP_URL)
    state = parse_qs(urlparse(started.open_url).query)["state"][0]
    result = await safe_oauth_callback(
        linkedin, {"error": "invalid_request", "error_description": "redirect_uri does not match", "state": state},
        config)
    assert "http://localhost:8765/oauth/linkedin/callback" in result.message


async def test_callback_forged_state(linkedin, config):
    result = await safe_oauth_callback(linkedin, {"code": "x", "state": "forged"}, config)
    assert not result.ok


@respx.mock
async def test_wrong_client_secret(linkedin, config):
    started = await safe_run_action(linkedin, "connect", config, APP_URL)
    state = parse_qs(urlparse(started.open_url).query)["state"][0]
    respx.post(f"{AUTH_BASE}/accessToken").mock(return_value=httpx.Response(
        401, json={"error": "invalid_client", "error_description": "Client authentication failed"}))
    result = await safe_oauth_callback(linkedin, {"code": "x", "state": state}, config)
    assert not result.ok and "Client Secret" in result.message


@respx.mock
async def test_test_connection(linkedin, config):
    respx.get(f"{API_BASE}/v2/userinfo").mock(
        return_value=httpx.Response(200, json={"sub": "abc123", "name": "Test Person"}))
    status = await safe_test_connection(linkedin, config)
    assert status.ok and "Test Person" in status.message and "lasts until" in status.message


@respx.mock
async def test_expired_token(linkedin, config):
    respx.get(f"{API_BASE}/v2/userinfo").mock(
        return_value=httpx.Response(401, json={"message": "Invalid access token", "status": 401}))
    status = await safe_test_connection(linkedin, config)
    assert status.error_code == "expired_credentials" and "Connect LinkedIn" in status.message


async def test_missing_configuration(linkedin):
    status = await safe_test_connection(linkedin, {"client_id": "x"})
    assert status.error_code == "not_configured"


# ---- validation -----------------------------------------------------------------------------------


def test_valid_post(linkedin, config):
    assert linkedin.validate(Post(text="Hello LinkedIn", images=[]), {"visibility": "PUBLIC"}, config) == []


def test_too_long(linkedin, config):
    problems = linkedin.validate(Post(text="x" * 3001, images=[]), {}, config)
    assert any("3000" in p.message for p in problems)


def test_expiring_soon_is_warning(linkedin, config):
    config["token_expires_at"] = str(int(time.time()) + 3 * 86400)
    problems = linkedin.validate(Post(text="hi", images=[]), {}, config)
    assert problems and problems[0].level == "warning" and "expires" in problems[0].message


def test_expired_blocks_posting(linkedin, config):
    config["token_expires_at"] = str(int(time.time()) - 10)
    problems = linkedin.validate(Post(text="hi", images=[]), {}, config)
    assert any(p.level == "error" and "expired" in p.message for p in problems)


def test_unsupported_image(linkedin, config, make_image):
    problems = linkedin.validate(Post(text="hi", images=[make_image("WEBP")]), {}, config)
    assert any("WEBP" in p.message for p in problems)


def test_too_many_pixels(linkedin, config, make_image):
    image = make_image()
    image.width, image.height = 8000, 5000
    problems = linkedin.validate(Post(text="hi", images=[image]), {}, config)
    assert any("pixels" in p.message for p in problems)


def test_too_many_images(linkedin, config, make_image):
    problems = linkedin.validate(Post(text="hi", images=[make_image() for _ in range(21)]), {}, config)
    assert any("at most 20" in p.message for p in problems)


# ---- posting ----------------------------------------------------------------------------------------


@respx.mock
async def test_post_text(linkedin, config):
    route = respx.post(f"{API_BASE}/rest/posts").mock(return_value=created())
    result = await safe_post(linkedin, Post(text="Big news (really)", images=[]), {"visibility": "CONNECTIONS"},
                             config)
    assert result.success
    assert result.post_url == "https://www.linkedin.com/feed/update/urn%3Ali%3Ashare%3A7000000000000000000/"
    request = route.calls[0].request
    assert request.headers["LinkedIn-Version"] == LINKEDIN_VERSION
    assert request.headers["X-Restli-Protocol-Version"] == "2.0.0"
    body = json.loads(request.content)
    assert body["author"] == "urn:li:person:abc123"
    assert body["commentary"] == "Big news \\(really\\)"
    assert body["visibility"] == "CONNECTIONS" and body["lifecycleState"] == "PUBLISHED"
    assert "content" not in body


@respx.mock
async def test_post_single_image(linkedin, config, make_image):
    init = respx.post(f"{API_BASE}/rest/images", params={"action": "initializeUpload"}).mock(
        return_value=httpx.Response(200, json={"value": {"uploadUrl": UPLOAD_URL, "image": "urn:li:image:I1"}}))
    put = respx.put(UPLOAD_URL).mock(return_value=httpx.Response(201))
    route = respx.post(f"{API_BASE}/rest/posts").mock(return_value=created())
    image = make_image()
    image.alt_text = "Logo"
    result = await safe_post(linkedin, Post(text="With image", images=[image]), {}, config)
    assert result.success
    assert json.loads(init.calls[0].request.content) == {"initializeUploadRequest": {"owner": "urn:li:person:abc123"}}
    assert put.calls[0].request.content == image.read_bytes()
    assert json.loads(route.calls[0].request.content)["content"] == {"media": {"id": "urn:li:image:I1",
                                                                              "altText": "Logo"}}


@respx.mock
async def test_post_multi_image(linkedin, config, make_image):
    respx.post(f"{API_BASE}/rest/images", params={"action": "initializeUpload"}).mock(side_effect=[
        httpx.Response(200, json={"value": {"uploadUrl": UPLOAD_URL, "image": f"urn:li:image:I{i}"}})
        for i in range(3)
    ])
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(201))
    route = respx.post(f"{API_BASE}/rest/posts").mock(return_value=created())
    result = await safe_post(linkedin, Post(text="", images=[make_image() for _ in range(3)]), {}, config)
    assert result.success
    images = json.loads(route.calls[0].request.content)["content"]["multiImage"]["images"]
    assert [i["id"] for i in images] == ["urn:li:image:I0", "urn:li:image:I1", "urn:li:image:I2"]


@respx.mock
async def test_missing_permission(linkedin, config):
    respx.post(f"{API_BASE}/rest/posts").mock(return_value=httpx.Response(
        403, json={"message": "Not enough permissions to access: partnerApiPostsExternal.CREATE", "status": 403}))
    result = await safe_post(linkedin, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "missing_permission" and "Share on LinkedIn" in result.message
    assert TOKEN not in (result.technical_details or "")


@respx.mock
async def test_duplicate_post(linkedin, config):
    respx.post(f"{API_BASE}/rest/posts").mock(return_value=httpx.Response(
        422, json={"message": "Content is a duplicate of urn:li:share:123", "status": 422}))
    result = await safe_post(linkedin, Post(text="hi", images=[]), {}, config)
    assert "same as one you posted recently" in result.message


@respx.mock
async def test_image_upload_failure(linkedin, config, make_image):
    respx.post(f"{API_BASE}/rest/images", params={"action": "initializeUpload"}).mock(
        return_value=httpx.Response(200, json={"value": {"uploadUrl": UPLOAD_URL, "image": "urn:li:image:I1"}}))
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(400, text="bad image"))
    result = await safe_post(linkedin, Post(text="hi", images=[make_image()]), {}, config)
    assert result.error_code == "invalid_media"


@respx.mock
async def test_rate_limited(linkedin, config):
    respx.post(f"{API_BASE}/rest/posts").mock(return_value=httpx.Response(
        429, json={"message": "Resource level throttle limit reached", "status": 429}))
    result = await safe_post(linkedin, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "rate_limited"


@respx.mock
async def test_server_error(linkedin, config):
    respx.post(f"{API_BASE}/rest/posts").mock(return_value=httpx.Response(503, json={"message": "unavailable"}))
    result = await safe_post(linkedin, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "platform_unavailable"


@respx.mock
async def test_network_failure(linkedin, config):
    respx.post(f"{API_BASE}/rest/posts").mock(side_effect=httpx.ConnectError("down"))
    result = await safe_post(linkedin, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "network_error"
