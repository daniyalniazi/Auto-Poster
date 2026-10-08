"""Validates a post for each selected platform and publishes it."""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from auto_poster.connectors.base import safe_post
from auto_poster.connectors.registry import get_connector
from auto_poster.models import ImageFile, PostRequest, PostResult, Problem
from auto_poster.services import compose, history, media
from auto_poster.services.settings import load_config

log = logging.getLogger(__name__)

# Request IDs currently being published, to stop double-clicks from posting twice.
_in_flight: set[str] = set()


def load_images(request: PostRequest) -> tuple[list[ImageFile], list[Problem]]:
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
    return images, problems


def _check_platform(platform_id: str, request: PostRequest, images: list[ImageFile]) -> list[Problem]:
    connector = get_connector(platform_id)
    if connector is None:
        return [Problem(platform=platform_id, message="Unknown platform.")]
    config = load_config(connector)
    if not connector.is_configured(config):
        return [connector.problem(
            f"{connector.display_name} is not connected yet. Open Settings → {connector.display_name} to set it up."
        )]
    try:
        post = compose.post_for(connector, request, images)
        return compose.style_notes(connector, request) + connector.validate(
            post, request.options.get(platform_id, {}), config
        )
    except Exception:
        log.exception("validate() crashed for %s", platform_id)
        return [connector.problem("Could not check this post. See the app log for details.")]


def validate(request: PostRequest) -> dict[str, list[Problem]]:
    """Problems per selected platform. An empty list means that platform is ready."""
    images, shared_problems = load_images(request)
    result: dict[str, list[Problem]] = {}
    if not request.platforms:
        result["all"] = [Problem(platform="all", message="Choose at least one platform to post to.")]
    if shared_problems:
        result.setdefault("all", []).extend(shared_problems)
    for platform_id in request.platforms:
        result[platform_id] = _check_platform(platform_id, request, images)
    return result


class Preview(BaseModel):
    text: str
    customized: bool  # the user edited this platform's version by hand
    problems: list[Problem]


def prepare(request: PostRequest) -> dict:
    """Each selected platform's final text and problems, for the post form's previews."""
    images, shared_problems = load_images(request)
    previews: dict[str, Preview] = {}
    for platform_id in request.platforms:
        connector = get_connector(platform_id)
        if connector is None:
            continue
        previews[platform_id] = Preview(
            text=compose.text_for(connector, request),
            customized=compose.is_customized(platform_id, request),
            problems=_check_platform(platform_id, request, images),
        )
    general = list(shared_problems)
    if not request.platforms:
        general.insert(0, Problem(platform="all", message="Choose at least one platform to post to."))
    return {"previews": previews, "problems": general}


def has_errors(problems: dict[str, list[Problem]]) -> bool:
    return any(p.level == "error" for items in problems.values() for p in items)


async def publish_to_platforms(request: PostRequest, images: list[ImageFile]) -> list[PostResult]:
    async def one(platform_id: str) -> PostResult:
        connector = get_connector(platform_id)
        if connector is None:
            return PostResult(platform=platform_id, success=False, error_code="not_configured",
                              message="Unknown platform.")
        config = load_config(connector)
        platform_options = request.options.get(platform_id, {})
        if not connector.is_configured(config):
            return PostResult(
                platform=platform_id,
                success=False,
                error_code="not_configured",
                message=f"{connector.display_name} is not connected. Open Settings → "
                f"{connector.display_name} to set it up.",
            )
        post = compose.post_for(connector, request, images)
        # Re-check locally right before sending: never send something we already know is invalid.
        errors = [p.message for p in connector.validate(post, platform_options, config) if p.level == "error"]
        if errors:
            result = PostResult(
                platform=platform_id, success=False, error_code="validation_failed", message=" ".join(errors)
            )
        else:
            result = await safe_post(connector, post, platform_options, config)
        result.sent_text = post.text
        return result

    # Platforms are independent: run them together and never let one failure stop the others.
    return list(await asyncio.gather(*(one(p) for p in request.platforms)))


async def publish_now(request: PostRequest, request_id: str, source: str = "manual") -> tuple[int, list[PostResult]]:
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
        images, _ = load_images(request)
        post_id = history.create_post(
            request_id, compose.summary_text(request), len(images), request.platforms, source,
            compose=PostRequest.model_validate(request.model_dump(include=set(PostRequest.model_fields))),
        )
        results = await publish_to_platforms(request, images)
        history.save_results(post_id, results)
        # Images are kept with the history entry so the post can be posted again.
        return post_id, results
    finally:
        _in_flight.discard(request_id)


class DuplicateRequest(Exception):
    pass
