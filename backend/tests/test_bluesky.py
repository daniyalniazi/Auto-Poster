"""Bluesky connector tests. All HTTP calls are mocked with respx; nothing is posted."""

from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

from auto_poster.connectors.base import safe_post, safe_test_connection
from auto_poster.connectors.bluesky import BlueskyConnector, find_links, find_mentions, find_tags
from auto_poster.models import Post

PDS = "https://bsky.social"
APP_PASSWORD = "abcd-efgh-ijkl-mnop"
DID = "did:plc:testuser123"


def session_body(access="access-1", refresh="refresh-1"):
    return {"accessJwt": access, "refreshJwt": refresh, "handle": "tester.bsky.social", "did": DID}


def xrpc_error(status, error, message=""):
    return httpx.Response(status, json={"error": error, "message": message})


@pytest.fixture
def bluesky():
    return BlueskyConnector()  # fresh instance: no cached sessions between tests


@pytest.fixture
def config():
    return {"handle": "tester.bsky.social", "app_password": APP_PASSWORD}


CREATED = {"uri": f"at://{DID}/app.bsky.feed.post/3kabc123", "cid": "bafy"}


# ---- connection -----------------------------------------------------------------------------


@respx.mock
async def test_connection_ok(bluesky, config, store):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=session_body())
    )
    status = await safe_test_connection(bluesky, config)
    assert status.ok and "@tester.bsky.social" in status.message
    saved = json.loads(store.data["bluesky:session"])
    assert saved["refresh"] == "refresh-1"  # reused later instead of logging in again


@respx.mock
async def test_connection_invalid_password(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=xrpc_error(401, "AuthenticationRequired", "Invalid identifier or password")
    )
    status = await safe_test_connection(bluesky, config)
    assert not status.ok and status.error_code == "invalid_credentials"
    assert APP_PASSWORD not in (status.technical_details or "")


@respx.mock
async def test_connection_main_password_with_2fa(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=xrpc_error(401, "AuthFactorTokenRequired")
    )
    status = await safe_test_connection(bluesky, config)
    assert "app password" in status.message


@respx.mock
async def test_connection_warns_if_not_app_password(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=session_body())
    )
    status = await safe_test_connection(bluesky, {**config, "app_password": "my-main-password"})
    assert status.ok and "doesn't look like an app password" in status.message


async def test_connection_missing_configuration(bluesky):
    status = await safe_test_connection(bluesky, {"handle": "tester.bsky.social"})
    assert status.error_code == "not_configured"


@respx.mock
async def test_stored_session_is_refreshed_not_recreated(bluesky, config):
    config["session"] = json.dumps({"handle": "tester.bsky.social", "refresh": "old-refresh", "pds": PDS})
    refresh = respx.post(f"{PDS}/xrpc/com.atproto.server.refreshSession").mock(
        return_value=httpx.Response(200, json=session_body("access-2", "refresh-2"))
    )
    create = respx.post(f"{PDS}/xrpc/com.atproto.server.createSession")
    respx.post(f"{PDS}/xrpc/com.atproto.repo.createRecord").mock(return_value=httpx.Response(200, json=CREATED))
    result = await safe_post(bluesky, Post(text="hi", images=[]), {}, config)
    assert result.success
    assert refresh.called and not create.called
    assert refresh.calls[0].request.headers["Authorization"] == "Bearer old-refresh"


# ---- validation -------------------------------------------------------------------------------


def test_valid_post(bluesky, config):
    assert bluesky.validate(Post(text="Hello Bluesky", images=[]), {}, config) == []


def test_too_long_counts_graphemes(bluesky, config):
    problems = bluesky.validate(Post(text="a" * 301, images=[]), {}, config)
    assert any("300" in p.message for p in problems)


def test_family_emoji_count_as_one(bluesky, config):
    problems = bluesky.validate(Post(text="👨‍👩‍👧" * 100, images=[]), {}, config)
    # 100 graphemes but 1800 bytes: within both limits
    assert not [p for p in problems if p.level == "error"]


def test_byte_limit(bluesky, config):
    problems = bluesky.validate(Post(text="👨‍👩‍👧" * 200, images=[]), {}, config)  # 200 graphemes, 3600 bytes
    assert any("bytes" in p.message for p in problems)


def test_too_many_images(bluesky, config, make_image):
    problems = bluesky.validate(Post(text="hi", images=[make_image() for _ in range(5)]), {}, config)
    assert any("at most 4" in p.message for p in problems)


def test_large_image_is_a_warning_not_error(bluesky, config, make_image):
    image = make_image()
    image.size_bytes = 3_000_000
    problems = bluesky.validate(Post(text="hi", images=[image]), {}, config)
    assert problems and all(p.level == "warning" for p in problems)


def test_unsupported_image(bluesky, config, make_image):
    image = make_image()
    image.format = "TIFF"
    problems = bluesky.validate(Post(text="hi", images=[image]), {}, config)
    assert any("TIFF" in p.message for p in problems)


# ---- rich text ----------------------------------------------------------------------------------


def test_facets_use_utf8_byte_offsets():
    text = "✨ go to https://example.com/x. #News @bob.bsky.social"
    raw = text.encode("utf-8")
    (start, end, uri), = find_links(text)
    assert raw[start:end].decode() == "https://example.com/x" == uri
    (start, end, tag), = find_tags(text)
    assert raw[start:end].decode() == "#News" and tag == "News"
    (start, end, handle), = find_mentions(text)
    assert raw[start:end].decode() == "@bob.bsky.social" and handle == "bob.bsky.social"


def test_number_only_hashtag_ignored():
    assert find_tags("Issue #123") == []


# ---- posting --------------------------------------------------------------------------------------


@respx.mock
async def test_post_text_with_facets(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=session_body())
    )
    respx.get(f"{PDS}/xrpc/com.atproto.identity.resolveHandle").mock(
        return_value=httpx.Response(200, json={"did": "did:plc:bob"})
    )
    create = respx.post(f"{PDS}/xrpc/com.atproto.repo.createRecord").mock(
        return_value=httpx.Response(200, json=CREATED)
    )
    result = await safe_post(bluesky, Post(text="Hi @bob.bsky.social https://a.com #tag", images=[]), {}, config)
    assert result.success
    assert result.post_url == "https://bsky.app/profile/tester.bsky.social/post/3kabc123"
    body = json.loads(create.calls[0].request.content)
    assert body["repo"] == DID and body["collection"] == "app.bsky.feed.post"
    kinds = {f["features"][0]["$type"].split("#")[1] for f in body["record"]["facets"]}
    assert kinds == {"mention", "link", "tag"}
    assert create.calls[0].request.headers["Authorization"] == "Bearer access-1"


@respx.mock
async def test_post_with_images_and_alt_text(bluesky, config, make_image):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=session_body())
    )
    upload = respx.post(f"{PDS}/xrpc/com.atproto.repo.uploadBlob").mock(
        return_value=httpx.Response(200, json={"blob": {"$type": "blob", "ref": {"$link": "bafk"},
                                                       "mimeType": "image/png", "size": 100}})
    )
    create = respx.post(f"{PDS}/xrpc/com.atproto.repo.createRecord").mock(
        return_value=httpx.Response(200, json=CREATED)
    )
    image = make_image()
    image.alt_text = "A red square"
    result = await safe_post(bluesky, Post(text="Pics", images=[image, make_image("JPEG")]), {}, config)
    assert result.success and upload.call_count == 2
    embed = json.loads(create.calls[0].request.content)["record"]["embed"]
    assert embed["$type"] == "app.bsky.embed.images"
    assert embed["images"][0]["alt"] == "A red square"
    assert embed["images"][0]["aspectRatio"] == {"width": 20, "height": 10}


@respx.mock
async def test_large_image_is_compressed_below_limit(bluesky, config, make_image):
    import os
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=session_body())
    )
    upload = respx.post(f"{PDS}/xrpc/com.atproto.repo.uploadBlob").mock(
        return_value=httpx.Response(200, json={"blob": {"$type": "blob"}})
    )
    respx.post(f"{PDS}/xrpc/com.atproto.repo.createRecord").mock(return_value=httpx.Response(200, json=CREATED))
    from PIL import Image
    from auto_poster.services import media
    noisy = Image.frombytes("RGB", (1800, 1800), os.urandom(1800 * 1800 * 3))
    import io
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")
    assert buffer.tell() > 2_000_000
    image = media.save_upload("big.png", buffer.getvalue())
    result = await safe_post(bluesky, Post(text="big", images=[image]), {}, config)
    assert result.success
    assert len(upload.calls[0].request.content) <= 2_000_000


@respx.mock
async def test_expired_access_token_is_refreshed_once(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=session_body())
    )
    respx.post(f"{PDS}/xrpc/com.atproto.server.refreshSession").mock(
        return_value=httpx.Response(200, json=session_body("access-2", "refresh-2"))
    )
    create = respx.post(f"{PDS}/xrpc/com.atproto.repo.createRecord").mock(side_effect=[
        xrpc_error(400, "ExpiredToken", "Token has expired"),
        httpx.Response(200, json=CREATED),
    ])
    result = await safe_post(bluesky, Post(text="hi", images=[]), {}, config)
    assert result.success
    assert create.calls[1].request.headers["Authorization"] == "Bearer access-2"


@respx.mock
async def test_rate_limited(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=session_body())
    )
    reset = str(int(time.time()) + 120)
    respx.post(f"{PDS}/xrpc/com.atproto.repo.createRecord").mock(
        return_value=httpx.Response(429, json={"error": "RateLimitExceeded"}, headers={"ratelimit-reset": reset})
    )
    result = await safe_post(bluesky, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "rate_limited"
    assert 100 <= result.retry_after <= 120


@respx.mock
async def test_api_validation_error(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(
        return_value=httpx.Response(200, json=session_body())
    )
    respx.post(f"{PDS}/xrpc/com.atproto.repo.createRecord").mock(
        return_value=xrpc_error(400, "InvalidRequest", "Invalid app.bsky.feed.post record")
    )
    result = await safe_post(bluesky, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "validation_failed" and "Invalid app.bsky.feed.post" in result.message


@respx.mock
async def test_server_error(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(return_value=httpx.Response(503))
    result = await safe_post(bluesky, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "platform_unavailable"


@respx.mock
async def test_network_failure(bluesky, config):
    respx.post(f"{PDS}/xrpc/com.atproto.server.createSession").mock(side_effect=httpx.ConnectError("down"))
    result = await safe_post(bluesky, Post(text="hi", images=[]), {}, config)
    assert result.error_code == "network_error"
