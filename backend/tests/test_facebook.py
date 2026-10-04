"""Facebook Page connector tests. All HTTP calls are mocked with respx; nothing is posted."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from auto_poster.connectors.base import safe_oauth_callback, safe_post, safe_run_action, safe_test_connection
from auto_poster.connectors.facebook import DIALOG, GRAPH, FacebookConnector
from auto_poster.models import Post
from auto_poster.services.settings import load_config, public_settings, save_settings

APP_SECRET = "fb-app-secret-0123456789"
PAGE_TOKEN = "EAAPageToken0123456789abcdef"
APP_URL = "http://127.0.0.1:8765"
PAGE_ID = "1111"


@pytest.fixture
def facebook():
    return FacebookConnector()


@pytest.fixture
def config():
    return {
        "app_id": "appid", "app_secret": APP_SECRET, "page_id": PAGE_ID,
        "page_tokens": json.dumps({PAGE_ID: PAGE_TOKEN}),
        "pages": json.dumps([{"id": PAGE_ID, "name": "My Shop", "tasks": ["CREATE_CONTENT", "MANAGE"]}]),
    }


def graph_error(code, message, status=400, subcode=None):
    error = {"message": message, "type": "OAuthException", "code": code}
    if subcode:
        error["error_subcode"] = subcode
    return httpx.Response(status, json={"error": error})


def mock_token_exchange_and_pages(pages):
    respx.post(f"{GRAPH}/oauth/access_token").mock(side_effect=[
        httpx.Response(200, json={"access_token": "short-user-token", "token_type": "bearer"}),
        httpx.Response(200, json={"access_token": "long-user-token", "token_type": "bearer", "expires_in": 5183944}),
    ])
    return respx.get(f"{GRAPH}/me/accounts").mock(return_value=httpx.Response(200, json={"data": pages}))


# ---- connecting ---------------------------------------------------------------------------------


async def test_connect_requires_app_details(facebook):
    result = await safe_run_action(facebook, "connect", {}, APP_URL)
    assert not result.ok and "App ID" in result.message


async def test_connect_url(facebook):
    result = await safe_run_action(facebook, "connect", {"app_id": "appid", "app_secret": APP_SECRET}, APP_URL)
    assert result.open_url.startswith(DIALOG)
    query = parse_qs(urlparse(result.open_url).query)
    assert query["redirect_uri"] == ["http://localhost:8765/oauth/facebook/callback"]
    assert "pages_manage_posts" in query["scope"][0]
    assert APP_SECRET not in result.open_url


async def test_connect_with_login_configuration(facebook):
    result = await safe_run_action(
        facebook, "connect", {"app_id": "appid", "app_secret": APP_SECRET, "config_id": "cfg42"}, APP_URL)
    query = parse_qs(urlparse(result.open_url).query)
    assert query["config_id"] == ["cfg42"] and "scope" not in query


@respx.mock
async def test_callback_single_page_is_selected_automatically(facebook, store):
    save_settings(facebook, {"app_id": "appid", "app_secret": APP_SECRET})
    started = await safe_run_action(facebook, "connect", load_config(facebook), APP_URL)
    state = parse_qs(urlparse(started.open_url).query)["state"][0]
    accounts = mock_token_exchange_and_pages([
        {"id": PAGE_ID, "name": "My Shop", "access_token": PAGE_TOKEN, "tasks": ["CREATE_CONTENT"]},
    ])
    done = await safe_oauth_callback(facebook, {"code": "c", "state": state}, load_config(facebook))
    assert done.ok and "My Shop" in done.message
    config = load_config(facebook)
    assert config["page_id"] == PAGE_ID
    assert facebook.is_configured(config)
    assert json.loads(store.data["facebook:page_tokens"]) == {PAGE_ID: PAGE_TOKEN}
    # The token is sent in a header, not the URL.
    request = accounts.calls[0].request
    assert "long-user-token" not in str(request.url)
    assert request.headers["Authorization"] == "Bearer long-user-token"
    assert "appsecret_proof" in str(request.url)


@respx.mock
async def test_several_pages_need_a_choice(facebook):
    save_settings(facebook, {"app_id": "appid", "app_secret": APP_SECRET})
    started = await safe_run_action(facebook, "connect", load_config(facebook), APP_URL)
    state = parse_qs(urlparse(started.open_url).query)["state"][0]
    mock_token_exchange_and_pages([
        {"id": "1", "name": "Shop A", "access_token": "tA", "tasks": ["CREATE_CONTENT"]},
        {"id": "2", "name": "Shop B", "access_token": "tB", "tasks": ["CREATE_CONTENT"]},
    ])
    done = await safe_oauth_callback(facebook, {"code": "c", "state": state}, load_config(facebook))
    assert done.ok and "Choose one" in done.message
    config = load_config(facebook)
    assert not facebook.is_configured(config)
    page_field = facebook.settings_fields_for(config)[-1]
    assert page_field.key == "page_id" and [o.label for o in page_field.options][1:] == ["Shop A", "Shop B"]
    save_settings(facebook, {"page_id": "2"})
    config = load_config(facebook)
    assert facebook.is_configured(config) and public_settings(facebook)["page_id"] == "2"


@respx.mock
async def test_graph_explorer_token_alternative(facebook, store):
    save_settings(facebook, {"app_id": "appid", "app_secret": APP_SECRET, "user_token": "explorer-token"})
    respx.post(f"{GRAPH}/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "long-user-token"}))
    respx.get(f"{GRAPH}/me/accounts").mock(return_value=httpx.Response(200, json={"data": [
        {"id": PAGE_ID, "name": "My Shop", "access_token": PAGE_TOKEN, "tasks": ["CREATE_CONTENT"]}]}))
    result = await safe_run_action(facebook, "load_pages", load_config(facebook), APP_URL)
    assert result.ok
    assert "facebook:user_token" not in store.data  # pasted token is discarded after use


@respx.mock
async def test_no_pages_found(facebook):
    save_settings(facebook, {"app_id": "appid", "app_secret": APP_SECRET, "user_token": "explorer-token"})
    respx.post(f"{GRAPH}/oauth/access_token").mock(return_value=httpx.Response(200, json={"access_token": "t"}))
    respx.get(f"{GRAPH}/me/accounts").mock(return_value=httpx.Response(200, json={"data": []}))
    result = await safe_run_action(facebook, "load_pages", load_config(facebook), APP_URL)
    assert not result.ok and "No Facebook Pages" in result.message


@respx.mock
async def test_wrong_app_secret(facebook):
    save_settings(facebook, {"app_id": "appid", "app_secret": "wrong", "user_token": "explorer-token"})
    respx.post(f"{GRAPH}/oauth/access_token").mock(
        return_value=graph_error(1, "Error validating client secret."))
    result = await safe_run_action(facebook, "load_pages", load_config(facebook), APP_URL)
    assert not result.ok and "App secret" in result.message


async def test_callback_denied(facebook, config):
    started = await safe_run_action(facebook, "connect", config, APP_URL)
    state = parse_qs(urlparse(started.open_url).query)["state"][0]
    result = await safe_oauth_callback(facebook, {"error": "access_denied", "state": state}, config)
    assert not result.ok and "not approved" in result.message


# ---- connection test ------------------------------------------------------------------------------


@respx.mock
async def test_connection_ok(facebook, config):
    respx.get(f"{GRAPH}/{PAGE_ID}").mock(return_value=httpx.Response(200, json={"id": PAGE_ID, "name": "My Shop"}))
    status = await safe_test_connection(facebook, config)
    assert status.ok and "My Shop" in status.message


@respx.mock
async def test_connection_token_invalidated(facebook, config):
    respx.get(f"{GRAPH}/{PAGE_ID}").mock(return_value=graph_error(
        190, "Error validating access token: The session has been invalidated because the user changed their password",
        subcode=460))
    status = await safe_test_connection(facebook, config)
    assert status.error_code == "expired_credentials" and "Connect Facebook" in status.message
    assert PAGE_TOKEN not in (status.technical_details or "")


async def test_not_configured_without_page(facebook):
    status = await safe_test_connection(facebook, {"app_id": "a", "app_secret": "b"})
    assert status.error_code == "not_configured"


# ---- validation -------------------------------------------------------------------------------------


def test_valid_post(facebook, config):
    assert facebook.validate(Post(text="Hello Page", images=[]), {}, config) == []


def test_role_without_create_content(facebook, config):
    config["pages"] = json.dumps([{"id": PAGE_ID, "name": "My Shop", "tasks": ["ANALYZE"]}])
    problems = facebook.validate(Post(text="hi", images=[]), {}, config)
    assert any("doesn't allow creating posts" in p.message for p in problems)


def test_unsupported_image(facebook, config, make_image):
    problems = facebook.validate(Post(text="hi", images=[make_image("WEBP")]), {}, config)
    assert any("WEBP" in p.message for p in problems)


def test_image_too_large(facebook, config, make_image):
    image = make_image()
    image.size_bytes = 11 * 1024 * 1024
    problems = facebook.validate(Post(text="hi", images=[image]), {}, config)
    assert any("10.0 MB" in p.message for p in problems)


def test_too_long(facebook, config):
    problems = facebook.validate(Post(text="x" * 63207, images=[]), {}, config)
    assert any("63206" in p.message for p in problems)


# ---- posting ------------------------------------------------------------------------------------------


@respx.mock
async def test_post_text(facebook, config):
    route = respx.post(f"{GRAPH}/{PAGE_ID}/feed").mock(
        return_value=httpx.Response(200, json={"id": f"{PAGE_ID}_222"}))
    result = await safe_post(facebook, Post(text="Hello Page", images=[]), {}, config)
    assert result.success and result.post_url == f"https://www.facebook.com/{PAGE_ID}_222"
    request = route.calls[0].request
    assert parse_qs(request.content.decode())["message"] == ["Hello Page"]
    assert request.headers["Authorization"] == f"Bearer {PAGE_TOKEN}"
    assert PAGE_TOKEN not in str(request.url)


@respx.mock
async def test_post_single_photo(facebook, config, make_image):
    route = respx.post(f"{GRAPH}/{PAGE_ID}/photos").mock(
        return_value=httpx.Response(200, json={"id": "333", "post_id": f"{PAGE_ID}_444"}))
    result = await safe_post(facebook, Post(text="Look", images=[make_image()]), {}, config)
    assert result.success and result.post_id == f"{PAGE_ID}_444"
    body = route.calls[0].request.content
    assert b'name="caption"' in body and b'name="source"' in body


@respx.mock
async def test_post_multi_photo(facebook, config, make_image):
    photos = respx.post(f"{GRAPH}/{PAGE_ID}/photos").mock(side_effect=[
        httpx.Response(200, json={"id": "p1"}), httpx.Response(200, json={"id": "p2"})])
    feed = respx.post(f"{GRAPH}/{PAGE_ID}/feed").mock(return_value=httpx.Response(200, json={"id": f"{PAGE_ID}_9"}))
    result = await safe_post(facebook, Post(text="Album", images=[make_image(), make_image()]), {}, config)
    assert result.success
    assert b'name="published"' in photos.calls[0].request.content
    sent = parse_qs(feed.calls[0].request.content.decode())
    assert json.loads(sent["attached_media[0]"][0]) == {"media_fbid": "p1"}
    assert json.loads(sent["attached_media[1]"][0]) == {"media_fbid": "p2"}
    assert sent["message"] == ["Album"]


@respx.mock
async def test_missing_permission(facebook, config):
    respx.post(f"{GRAPH}/{PAGE_ID}/feed").mock(return_value=graph_error(
        200, "(#200) The user hasn't authorized the application to perform this action", status=403))
    result = await safe_post(facebook, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "missing_permission"


@respx.mock
async def test_duplicate(facebook, config):
    respx.post(f"{GRAPH}/{PAGE_ID}/feed").mock(return_value=graph_error(506, "Duplicate status message"))
    result = await safe_post(facebook, Post(text="hi", images=[]), {}, config)
    assert "same as a recent one" in result.message


@respx.mock
async def test_invalid_image(facebook, config, make_image):
    respx.post(f"{GRAPH}/{PAGE_ID}/photos").mock(return_value=graph_error(324, "Missing or invalid image file"))
    result = await safe_post(facebook, Post(text="hi", images=[make_image()]), {}, config)
    assert result.error_code == "invalid_media"


@respx.mock
async def test_rate_limited(facebook, config):
    respx.post(f"{GRAPH}/{PAGE_ID}/feed").mock(return_value=graph_error(32, "Page request limit reached"))
    result = await safe_post(facebook, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "rate_limited"


@respx.mock
async def test_temporary_error(facebook, config):
    respx.post(f"{GRAPH}/{PAGE_ID}/feed").mock(return_value=graph_error(2, "Service temporarily unavailable",
                                                                        status=500))
    result = await safe_post(facebook, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "platform_unavailable"


@respx.mock
async def test_network_failure(facebook, config):
    respx.post(f"{GRAPH}/{PAGE_ID}/feed").mock(side_effect=httpx.ConnectError("down"))
    result = await safe_post(facebook, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "network_error"
