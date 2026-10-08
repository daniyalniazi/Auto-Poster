"""Turns what the user wrote once into each platform's own text.

Order of precedence for a platform's text:
  1. The user's hand-edited version for that platform (`overrides`), exactly as typed.
  2. Quick mode: the single text box, exactly as typed.
  3. Structured mode: the connector's format_post() of title, body, hashtags and link.
"""

from __future__ import annotations

import re

from auto_poster.connectors.base import Connector
from auto_poster.models import ImageFile, Post, PostContent, PostRequest, Problem

HASHTAG_SPLIT = re.compile(r"[\s,]+")


def parse_hashtags(raw: str) -> list[str]:
    """'#Launch, opensource #launch' -> ['Launch', 'opensource'] (deduplicated, case-insensitive)."""
    tags, seen = [], set()
    for word in HASHTAG_SPLIT.split(raw or ""):
        tag = word.lstrip("#＃").strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def content_of(request: PostRequest) -> PostContent:
    return PostContent(
        title=request.title.strip(),
        body=request.text.strip(),
        hashtags=parse_hashtags(request.hashtags),
        link=request.link.strip(),
    )


def is_customized(platform_id: str, request: PostRequest) -> bool:
    return platform_id in request.overrides


def text_for(connector: Connector, request: PostRequest) -> str:
    if connector.id in request.overrides:
        return request.overrides[connector.id]
    if request.mode == "quick":
        return request.text
    return connector.format_post(content_of(request))


def post_for(connector: Connector, request: PostRequest, images: list[ImageFile]) -> Post:
    text = text_for(connector, request)
    title = request.title.strip() if request.mode == "structured" else ""
    return Post(text=text, images=images, title=title if title and text.startswith(title) else "")


def style_notes(connector: Connector, request: PostRequest) -> list[Problem]:
    """Warnings about how the shared post was adapted for this platform."""
    if request.mode != "structured" or connector.id in request.overrides:
        return []
    tags = parse_hashtags(request.hashtags)
    if connector.max_hashtags is not None and len(tags) > connector.max_hashtags:
        return [connector.problem(
            f"{connector.display_name} works best with at most {connector.max_hashtags} hashtags, so only the "
            f"first {connector.max_hashtags} of your {len(tags)} are used here.",
            level="warning",
        )]
    return []


def summary_text(request: PostRequest) -> str:
    """One text to show in History for the whole post."""
    if request.mode == "structured" and request.title.strip():
        return f"{request.title.strip()}\n\n{request.text.strip()}".strip()
    return request.text
