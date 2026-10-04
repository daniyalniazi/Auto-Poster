"""Validates a post for each selected platform and publishes it."""

from __future__ import annotations

import asyncio
import logging

from auto_poster.connectors.base import safe_post
from auto_poster.connectors.registry import get_connector
from auto_poster.models import Post, PostRequest, PostResult, Problem
from auto_poster.services import history, media
from auto_poster.services.settings import load_config

log = logging.getLogger(__name__)

# Request IDs currently being published, to stop double-clicks from posting twice.
_in_flight: set[str] = set()


def build_post(request: PostRequest) -> tuple[Post, list[Problem]]:
    images, problems = [], []
    for image_id in request.image_ids:
        image = media.load(image_id)
        if image is None:
            problems.append(
                Problem(platform="all", message="One of the attached images is missing. Please attach it again.")
            )
            continue
        image.alt_text = request.alt_texts.get(image_id, "")
        images.append(image)
    return Post(text=request.text, images=images), problems


def validate(request: PostRequest) -> dict[str, list[Problem]]:
    """Problems per selected platform. An empty list means that platform is ready."""
    post, shared_problems = build_post(request)
    result: dict[str, list[Problem]] = {}
    if not request.platforms:
        result["all"] = [Problem(platform="all", message="Choose at least one platform to post to.")]
    if shared_problems:
        result.setdefault("all", []).extend(shared_problems)
    for platform_id in request.platforms:
        connector = get_connector(platform_id)
        if connector is None:
            result[platform_id] = [Problem(platform=platform_id, message="Unknown platform.")]
            continue
        config = load_config(connector)
        if not connector.is_configured(config):
            result[platform_id] = [
                connector.problem(
                    f"{connector.display_name} is not connected yet. Open Settings → "
                    f"{connector.display_name} to set it up."
                )
            ]
            continue
        try:
            result[platform_id] = connector.validate(post, request.options.get(platform_id, {}), config)
        except Exception:
            log.exception("validate() crashed for %s", platform_id)
            result[platform_id] = [connector.problem("Could not check this post. See the app log for details.")]
    return result


def has_errors(problems: dict[str, list[Problem]]) -> bool:
    return any(p.level == "error" for items in problems.values() for p in items)


async def publish_to_platforms(post: Post, platforms: list[str], options: dict[str, dict[str, str]]) -> list[PostResult]:
    async def one(platform_id: str) -> PostResult:
        connector = get_connector(platform_id)
        if connector is None:
            return PostResult(platform=platform_id, success=False, error_code="not_configured",
                              message="Unknown platform.")
        config = load_config(connector)
        platform_options = options.get(platform_id, {})
        if not connector.is_configured(config):
            return PostResult(
                platform=platform_id,
                success=False,
                error_code="not_configured",
                message=f"{connector.display_name} is not connected. Open Settings → "
                f"{connector.display_name} to set it up.",
            )
        # Re-check locally right before sending: never send something we already know is invalid.
        errors = [p.message for p in connector.validate(post, platform_options, config) if p.level == "error"]
        if errors:
            return PostResult(
                platform=platform_id, success=False, error_code="validation_failed", message=" ".join(errors)
            )
        return await safe_post(connector, post, platform_options, config)

    # Platforms are independent: run them together and never let one failure stop the others.
    return list(await asyncio.gather(*(one(p) for p in platforms)))


async def publish_now(
    request: PostRequest, request_id: str, source: str = "manual", delete_media: bool = True
) -> tuple[int, list[PostResult]]:
    """Publish immediately. Returns (history post id, results).

    The request ID is stored with the history entry, so the same request can never be
    published twice (double-clicks, retries, restarts).
    """
    existing = history.find_by_request_id(request_id)
    if existing is not None:
        return existing.id, existing.results
    if request_id in _in_flight:
        raise DuplicateRequest()
    _in_flight.add(request_id)
    try:
        post, _ = build_post(request)
        post_id = history.create_post(request_id, post, request.platforms, source)
        results = await publish_to_platforms(post, request.platforms, request.options)
        history.save_results(post_id, results)
        if delete_media and all(r.success for r in results):
            for image in post.images:
                media.delete(image.id)
        # Otherwise keep the images so the user can go back and retry; old uploads are cleaned at startup.
        return post_id, results
    finally:
        _in_flight.discard(request_id)


class DuplicateRequest(Exception):
    pass
