"""The interface every platform connector implements, plus shared helpers.

A connector:
  * declares its settings fields, post-option fields, limits and setup guide,
  * implements test_connection(), validate() and post(),
  * raises PlatformError with a plain-English message when something goes wrong.

The app calls connectors only through safe_test_connection() and safe_post(), which
turn every failure (including unexpected crashes) into a standard result, so one
broken connector can never take the whole app down.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from auto_poster import __version__
from auto_poster.models import (
    ActionResult,
    ActionSpec,
    ConnectionStatus,
    FieldSpec,
    PlatformError,
    PlatformLimits,
    Post,
    PostContent,
    PostResult,
    Problem,
    SetupGuide,
)
from auto_poster.security.credentials import get_store
from auto_poster.security.redact import redact

log = logging.getLogger(__name__)

Config = dict[str, str]  # merged settings + secrets for one platform
Options = dict[str, str]  # per-post options from the post form

HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = f"AutoPoster/{__version__} (+https://github.com/daniyalniazi/Auto-Poster)"


class Connector(ABC):
    id: str
    display_name: str
    description: str = ""
    limits: PlatformLimits
    settings_fields: list[FieldSpec] = []
    post_fields: list[FieldSpec] = []
    actions: list[ActionSpec] = []  # extra Settings buttons, e.g. "Connect LinkedIn"
    # Most hashtags this platform's audience expects; extras are left out (with a warning).
    max_hashtags: int | None = None
    # Secrets the connector stores itself (e.g. session or OAuth tokens), never shown in the UI.
    internal_secret_keys: set[str] = set()
    setup_guide: SetupGuide

    # ---- what each connector implements -------------------------------------------------

    @abstractmethod
    async def test_connection(self, config: Config) -> ConnectionStatus:
        """Check the saved settings work. Must not publish anything."""

    @abstractmethod
    async def post(self, post: Post, options: Options, config: Config) -> PostResult:
        """Publish the post. Return a successful PostResult or raise PlatformError."""

    def format_post(self, content: PostContent) -> str:
        """Turn the structured post into this platform's text. Override for platform style.

        Default: title, body, link and hashtags as separate paragraphs.
        """
        tags = content.hashtags[: self.max_hashtags] if self.max_hashtags is not None else content.hashtags
        parts = [
            content.title.strip(),
            content.body.strip(),
            content.link.strip() if content.link.strip() not in content.body else "",
            " ".join(f"#{tag}" for tag in tags),
        ]
        return "\n\n".join(part for part in parts if part)

    def validate(self, post: Post, options: Options, config: Config) -> list[Problem]:
        """Local checks only (no network). Override and call super() to add platform rules."""
        limits = self.effective_limits(config)
        return check_text(self, post, limits) + check_images(self, post, limits)

    def settings_fields_for(self, config: Config) -> list[FieldSpec]:
        """Settings fields for the current state. Override when choices depend on the account
        (e.g. a list of Facebook Pages to pick from)."""
        return self.settings_fields

    def effective_limits(self, config: Config) -> PlatformLimits:
        """Limits for this user's account/server. Override when they vary (e.g. Mastodon servers)."""
        return self.limits

    def count_text(self, text: str, limits: PlatformLimits) -> int:
        """How the platform counts characters. Override for special rules (e.g. links count as 23)."""
        return text_length(text, limits.count_method)

    async def handle_oauth_callback(self, params: dict[str, str], config: Config) -> ActionResult:
        """Finish a browser sign-in started by run_action(). Only for OAuth platforms."""
        raise PlatformError("unknown_error", "This platform does not use browser sign-in.")

    async def run_action(self, action_id: str, config: Config, base_url: str) -> ActionResult:
        """Handle one of `actions`. base_url is where the local app is running."""
        raise PlatformError("unknown_error", "This action is not available.")

    # ---- shared helpers ------------------------------------------------------------------

    @property
    def secret_keys(self) -> set[str]:
        return {f.key for f in self.settings_fields if f.kind == "secret"} | self.internal_secret_keys

    def save_secret(self, key: str, value: str | None) -> None:
        """Store (or with None, remove) one of internal_secret_keys in the credential store."""
        store = get_store()
        if value:
            store.set(self.id, key, value)
        else:
            store.delete(self.id, key)

    def save_value(self, key: str, value: str) -> None:
        """Store a non-secret value the connector learned (e.g. an account ID) in the database."""
        from auto_poster.services.settings import save_internal

        save_internal(self, {key: value}, secret=False)

    def is_configured(self, config: Config) -> bool:
        return all(config.get(f.key) for f in self.settings_fields if f.required)

    def problem(self, message: str, field: str | None = None, level="error") -> Problem:
        return Problem(platform=self.id, message=message, field=field, level=level)

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})


def text_length(text: str, method: str) -> int:
    if method == "utf16":
        return len(text.encode("utf-16-le")) // 2
    if method == "graphemes":
        import regex  # only needed by platforms that count graphemes

        return len(regex.findall(r"\X", text))
    return len(text)


def check_text(connector: Connector, post: Post, limits: PlatformLimits | None = None) -> list[Problem]:
    limits = limits or connector.limits
    text = post.text
    if not text.strip():
        if limits.requires_text or not post.images:
            return [connector.problem(f"{connector.display_name} needs some text to post.")]
        return []
    limit = limits.max_chars
    if post.images and limits.max_chars_with_images is not None:
        limit = limits.max_chars_with_images
    length = connector.count_text(text, limits)
    if length > limit:
        context = " when images are attached" if post.images and limit != limits.max_chars else ""
        return [
            connector.problem(
                f"The text is {length} characters, but {connector.display_name} allows at most "
                f"{limit}{context}. Shorten it by {length - limit} characters"
                + (" or remove the images." if context else ".")
            )
        ]
    return []


def check_images(connector: Connector, post: Post, limits: PlatformLimits | None = None) -> list[Problem]:
    limits = limits or connector.limits
    name = connector.display_name
    if not post.images:
        return []
    if limits.max_images == 0:
        return [connector.problem(f"{name} posts from Auto Poster can't include images.")]
    problems = []
    if len(post.images) > limits.max_images:
        problems.append(
            connector.problem(
                f"{name} allows at most {limits.max_images} images per post; "
                f"you attached {len(post.images)}."
            )
        )
    for image in post.images:
        if limits.image_formats and image.format not in limits.image_formats:
            allowed = ", ".join(limits.image_formats)
            problems.append(
                connector.problem(
                    f'"{image.filename}" is a {image.format} image. {name} accepts: {allowed}.'
                )
            )
        if limits.max_image_bytes and image.size_bytes > limits.max_image_bytes:
            problems.append(
                connector.problem(
                    f'"{image.filename}" is {_mb(image.size_bytes)}. '
                    f"{name} allows images up to {_mb(limits.max_image_bytes)}."
                )
            )
        if limits.max_image_dimension_sum and image.width + image.height > limits.max_image_dimension_sum:
            problems.append(
                connector.problem(
                    f'"{image.filename}" is {image.width}×{image.height} pixels. {name} requires '
                    f"width + height to be at most {limits.max_image_dimension_sum}."
                )
            )
        if limits.max_aspect_ratio and image.width and image.height:
            ratio = max(image.width / image.height, image.height / image.width)
            if ratio > limits.max_aspect_ratio:
                problems.append(
                    connector.problem(
                        f'"{image.filename}" is too long and thin for {name} '
                        f"(maximum ratio {limits.max_aspect_ratio:g}:1)."
                    )
                )
    return problems


def _mb(size: int) -> str:
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


# ---- wrappers the app uses --------------------------------------------------------------


def _exception_to_error(connector: Connector, exc: Exception) -> PlatformError:
    name = connector.display_name
    if isinstance(exc, PlatformError):
        return exc
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return PlatformError(
            "network_error",
            f"Could not reach {name}. Check your internet connection. If {name} is blocked on your "
            "network, it will not work until you can reach it.",
            technical_details=f"{type(exc).__name__}: {exc}",
        )
    if isinstance(exc, httpx.TimeoutException):
        return PlatformError(
            "timeout",
            f"{name} took too long to respond. Check your internet connection and try again.",
            technical_details=type(exc).__name__,
        )
    if isinstance(exc, httpx.TransportError):
        return PlatformError(
            "network_error",
            f"Could not reach {name}. Check your internet connection. If {name} is blocked on your "
            "network, it will not work until you can reach it.",
            technical_details=f"{type(exc).__name__}: {exc}",
        )
    log.exception("Unexpected error in %s connector", connector.id)
    return PlatformError(
        "unknown_error",
        f"Something unexpected went wrong while talking to {name}.",
        technical_details=f"{type(exc).__name__}: {exc}",
    )


async def safe_test_connection(connector: Connector, config: Config) -> ConnectionStatus:
    if not connector.is_configured(config):
        return ConnectionStatus(
            ok=False,
            error_code="not_configured",
            message=f"{connector.display_name} is not set up yet. Fill in the required fields first.",
        )
    try:
        status = await connector.test_connection(config)
    except Exception as exc:
        err = _exception_to_error(connector, exc)
        return ConnectionStatus(
            ok=False,
            message=err.message,
            error_code=err.error_code,
            technical_details=redact(err.technical_details),
        )
    status.technical_details = redact(status.technical_details)
    return status


async def safe_run_action(connector: Connector, action_id: str, config: Config, base_url: str) -> ActionResult:
    try:
        return await connector.run_action(action_id, config, base_url)
    except Exception as exc:
        err = _exception_to_error(connector, exc)
        return ActionResult(ok=False, message=err.message)


async def safe_oauth_callback(connector: Connector, params: dict[str, str], config: Config) -> ActionResult:
    try:
        return await connector.handle_oauth_callback(params, config)
    except Exception as exc:
        err = _exception_to_error(connector, exc)
        return ActionResult(ok=False, message=err.message)


async def safe_post(connector: Connector, post: Post, options: Options, config: Config) -> PostResult:
    try:
        result = await connector.post(post, options, config)
    except Exception as exc:
        err = _exception_to_error(connector, exc)
        return PostResult(
            platform=connector.id,
            success=False,
            message=err.message,
            error_code=err.error_code,
            technical_details=redact(err.technical_details),
            retry_after=err.retry_after,
        )
    result.technical_details = redact(result.technical_details)
    return result
