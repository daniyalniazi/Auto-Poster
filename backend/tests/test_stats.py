"""Post stats (totals + comments). All HTTP is mocked."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from auto_poster.connectors.base import safe_comments, safe_stats
from auto_poster.connectors.bluesky import PUBLIC_APPVIEW, BlueskyConnector
from auto_poster.connectors.facebook import GRAPH, FacebookConnector
from auto_poster.connectors.mastodon import MastodonConnector
from auto_poster.connectors.registry import get_connector
from auto_poster.models import PostResult
from auto_poster.services import history, stats
from auto_poster.services import settings as settings_service

URI = "at://did:plc:me/app.bsky.feed.post/3k1"
MASTO = {"server": "social.example", "access_token": "tok"}
FB = {"app_id": "a", "app_secret": "s", "page_tokens": json.dumps({"111": "PAGE"})}


# ---- connectors ------------------------------------------------------------------------------


@respx.mock
async def test_bluesky_stats_and_comments():
    respx.get(f"{PUBLIC_APPVIEW}/xrpc/app.bsky.feed.getPosts").mock(return_value=httpx.Response(200, json={
        "posts": [{"likeCount": 5, "repostCount": 2, "quoteCount": 1, "replyCount": 3}]}))
    respx.get(f"{PUBLIC_APPVIEW}/xrpc/app.bsky.feed.getPostThread").mock(return_value=httpx.Response(200, json={
        "thread": {"replies": [
            {"post": {"author": {"handle": "bob.bsky.social"}, "record": {"text": "Nice!"},
                      "indexedAt": "2026-10-01T10:00:00Z"}},
            {"$type": "app.bsky.feed.defs#blockedPost"},
            {"post": {"author": {"displayName": "Alice", "handle": "alice"}, "record": {"text": "Later"},
                      "indexedAt": "2026-10-02T10:00:00Z"}},
        ]}}))
    result, error = await safe_stats(BlueskyConnector(), URI, {})
    assert error is None and (result.likes, result.shares, result.replies, result.views) == (5, 3, 3, None)
    comments, error = await safe_comments(BlueskyConnector(), URI, {})
    assert [c.author for c in comments] == ["Alice", "@bob.bsky.social"]  # newest first, blocked skipped


@respx.mock
async def test_bluesky_deleted_post():
    respx.get(f"{PUBLIC_APPVIEW}/xrpc/app.bsky.feed.getPosts").mock(return_value=httpx.Response(200, json={"posts": []}))
    _, error = await safe_stats(BlueskyConnector(), URI, {})
    assert error.error_code == "not_found"


@respx.mock
async def test_mastodon_stats_and_comments():
    respx.get("https://social.example/api/v1/statuses/9").mock(return_value=httpx.Response(200, json={
        "favourites_count": 7, "reblogs_count": 2, "replies_count": 1}))
    respx.get("https://social.example/api/v1/statuses/9/context").mock(return_value=httpx.Response(200, json={
        "descendants": [
            {"in_reply_to_id": "9", "account": {"display_name": "Bob", "acct": "bob"},
             "content": "<p>Great &amp; useful</p>", "created_at": "2026-10-01T00:00:00Z"},
            {"in_reply_to_id": "10", "account": {"acct": "deep"}, "content": "<p>reply to a reply</p>"},
        ]}))
    result, _ = await safe_stats(MastodonConnector(), "9", MASTO)
    assert (result.likes, result.shares, result.replies) == (7, 2, 1)
    comments, _ = await safe_comments(MastodonConnector(), "9", MASTO)
    assert [(c.author, c.text) for c in comments] == [("Bob", "Great & useful")]


@respx.mock
async def test_mastodon_old_token_falls_back_to_public_read():
    route = respx.get("https://social.example/api/v1/statuses/9").mock(side_effect=[
        httpx.Response(403, json={"error": "This action is outside the authorized scopes"}),
        httpx.Response(200, json={"favourites_count": 1, "reblogs_count": 0, "replies_count": 0}),
    ])
    result, error = await safe_stats(MastodonConnector(), "9", MASTO)
    assert error is None and result.likes == 1
    assert "Authorization" not in route.calls[1].request.headers


@respx.mock
async def test_mastodon_private_post_needs_reconnect():
    respx.get("https://social.example/api/v1/statuses/9").mock(side_effect=[
        httpx.Response(403, json={"error": "outside the authorized scopes"}), httpx.Response(404, json={})])
    _, error = await safe_stats(MastodonConnector(), "9", MASTO)
    assert error.error_code == "missing_permission" and "reconnect Mastodon" in error.message


@respx.mock
async def test_facebook_stats_with_views():
    respx.get(f"{GRAPH}/111_2").mock(return_value=httpx.Response(200, json={
        "reactions": {"summary": {"total_count": 12}}, "comments": {"summary": {"total_count": 4}},
        "shares": {"count": 3}}))
    respx.get(f"{GRAPH}/111_2/insights").mock(return_value=httpx.Response(200, json={
        "data": [{"name": "post_media_view", "values": [{"value": 250}]}]}))
    result, _ = await safe_stats(FacebookConnector(), "111_2", FB)
    assert (result.likes, result.replies, result.shares, result.views) == (12, 4, 3, 250)


@respx.mock
async def test_facebook_views_missing_without_permission():
    respx.get(f"{GRAPH}/111_2").mock(return_value=httpx.Response(200, json={"reactions": {"summary": {"total_count": 1}}}))
    respx.get(f"{GRAPH}/111_2/insights").mock(return_value=httpx.Response(
        403, json={"error": {"code": 10, "message": "(#10) Requires read_insights permission"}}))
    result, error = await safe_stats(FacebookConnector(), "111_2", FB)
    assert error is None and result.likes == 1 and result.views is None and result.shares == 0


@respx.mock
async def test_facebook_comments_need_reconnect():
    respx.get(f"{GRAPH}/111_2/comments").mock(return_value=httpx.Response(
        403, json={"error": {"code": 10, "message": "(#10) pages_read_user_content required"}}))
    comments, error = await safe_comments(FacebookConnector(), "111_2", FB)
    assert comments == [] and "reconnect Facebook" in error.message


def test_telegram_and_linkedin_explain_why_not():
    for platform in ("telegram", "linkedin"):
        connector = get_connector(platform)
        assert not connector.supports_stats and connector.stats_unavailable_reason


# ---- service -----------------------------------------------------------------------------------


@pytest.fixture
def published():
    settings_service.save_settings(get_connector("bluesky"), {"handle": "me.bsky.social", "app_password": "a-b-c-d"})
    post_id = history.create_post("s-1", "Launch", 0, ["bluesky", "telegram"])
    history.save_results(post_id, [
        PostResult(platform="bluesky", success=True, post_id=URI, post_url="https://bsky.app/x"),
        PostResult(platform="telegram", success=True, post_id="-100:5"),
    ])
    return post_id


@respx.mock
async def test_refresh_saves_totals_and_throttles(published):
    route = respx.get(f"{PUBLIC_APPVIEW}/xrpc/app.bsky.feed.getPosts").mock(return_value=httpx.Response(200, json={
        "posts": [{"likeCount": 5, "repostCount": 1, "quoteCount": 0, "replyCount": 2}]}))
    await stats.refresh()
    await stats.refresh()  # within 15 minutes: not fetched again
    assert route.call_count == 1
    overview = stats.overview()
    assert overview.totals.likes == 5 and overview.totals.posts == 1 and overview.totals.views is None
    by_platform = {r.platform: r for r in overview.posts[0].results}
    assert by_platform["bluesky"].stats.likes == 5
    assert by_platform["telegram"].stats is None  # not supported
    assert overview.platforms["telegram"]["supported"] is False
    await stats.refresh(force=True)
    assert route.call_count == 2


@respx.mock
async def test_refresh_error_keeps_last_numbers(published):
    respx.get(f"{PUBLIC_APPVIEW}/xrpc/app.bsky.feed.getPosts").mock(side_effect=[
        httpx.Response(200, json={"posts": [{"likeCount": 5, "repostCount": 0, "quoteCount": 0, "replyCount": 0}]}),
        httpx.ConnectError("offline"),
    ])
    await stats.refresh()
    await stats.refresh(force=True)
    result = next(r for r in stats.overview().posts[0].results if r.platform == "bluesky")
    assert result.stats.likes == 5 and "Could not reach Bluesky" in result.error


async def test_comments_for_unsupported_platform(published):
    comments, error = await stats.comments(published, "telegram")
    assert comments == [] and "Telegram" in error
