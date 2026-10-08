"""Data models shared by the API, services and connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

FieldKind = Literal["text", "secret", "textarea", "select", "checkbox"]


class FieldOption(BaseModel):
    value: str
    label: str


class FieldSpec(BaseModel):
    """Describes one input field. The UI renders settings and post options from these."""

    key: str
    label: str
    kind: FieldKind = "text"
    help: str = ""
    placeholder: str = ""
    required: bool = False
    options: list[FieldOption] = []
    default: str = ""


class PlatformLimits(BaseModel):
    """Platform rules, kept in one place per connector. Values come from official docs."""

    max_chars: int
    max_chars_with_images: int | None = None  # e.g. Telegram caption limit
    # "chars" = Unicode code points, "utf16" = UTF-16 code units, "graphemes" = user-perceived characters,
    # "mastodon" = graphemes, but every link counts as 23 and @user@server mentions count as @user
    count_method: Literal["chars", "utf16", "graphemes", "mastodon"] = "chars"
    max_images: int = 0
    max_image_bytes: int = 0
    image_formats: list[str] = []  # Pillow format names: "JPEG", "PNG", "WEBP", "GIF"
    max_image_dimension_sum: int | None = None  # width + height
    max_aspect_ratio: float | None = None
    requires_text: bool = True


class ActionSpec(BaseModel):
    """A button on the Settings page, e.g. "Connect LinkedIn" (sign-in in the browser)."""

    id: str
    label: str
    help: str = ""


class ActionResult(BaseModel):
    ok: bool
    message: str
    open_url: str | None = None  # the UI opens this in a new tab (e.g. a sign-in page)


class SetupGuide(BaseModel):
    steps: list[str]
    docs_url: str = ""
    notes: list[str] = []


@dataclass
class ImageFile:
    """An image the user attached. Kept on the local disk until it is sent."""

    id: str
    filename: str
    path: str
    format: str  # Pillow format name
    mime_type: str
    size_bytes: int
    width: int
    height: int
    alt_text: str = ""

    def read_bytes(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()


@dataclass
class Post:
    """What one platform receives: its final text plus the shared images."""

    text: str
    images: list[ImageFile]
    title: str = ""  # the structured title, if the text starts with it (e.g. Telegram shows it in bold)


@dataclass
class PostContent:
    """The post as the user wrote it once, in structured mode. Connectors turn it into text."""

    title: str = ""
    body: str = ""
    hashtags: list[str] = field(default_factory=list)  # without "#"
    link: str = ""


ProblemLevel = Literal["error", "warning"]


class Problem(BaseModel):
    platform: str
    message: str
    level: ProblemLevel = "error"
    field: str | None = None


ErrorCode = Literal[
    "not_configured",
    "invalid_credentials",
    "expired_credentials",
    "missing_permission",
    "not_found",
    "rate_limited",
    "network_error",
    "timeout",
    "invalid_media",
    "too_long",
    "validation_failed",
    "platform_unavailable",
    "unknown_error",
]


class PostResult(BaseModel):
    platform: str
    success: bool
    post_id: str | None = None
    post_url: str | None = None
    message: str = ""  # plain-English explanation for the user
    error_code: ErrorCode | None = None
    technical_details: str | None = None  # redacted; shown under "Details"
    retry_after: int | None = None  # seconds, when the platform says so
    sent_text: str | None = None  # the exact text this platform was given (filled in by the publisher)
    deleted_at: str | None = None  # when the user deleted it from the platform via Auto Poster


class Comment(BaseModel):
    author: str
    text: str
    created_at: str | None = None


class PostStats(BaseModel):
    """Current totals for one post on one platform. None = the platform doesn't report it."""

    likes: int | None = None
    shares: int | None = None  # reposts, boosts, shares
    replies: int | None = None  # comments / replies
    views: int | None = None


class ConnectionStatus(BaseModel):
    ok: bool
    message: str
    account_name: str | None = None
    error_code: ErrorCode | None = None
    technical_details: str | None = None


class PlatformError(Exception):
    """Raised inside connectors; converted to a PostResult/ConnectionStatus by the base class."""

    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        technical_details: str | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.error_code: ErrorCode = error_code
        self.message = message
        self.technical_details = technical_details
        self.retry_after = retry_after


class ImageInfo(BaseModel):
    """What the UI learns about an uploaded image."""

    id: str
    filename: str
    format: str
    size_bytes: int
    width: int
    height: int


class PostRequest(BaseModel):
    # "quick": `text` is posted as typed. "structured": title/text/hashtags/link are assembled
    # per platform by each connector's format_post().
    mode: Literal["quick", "structured"] = "quick"
    title: str = ""
    text: str = ""  # quick-mode text, or the body in structured mode
    hashtags: str = ""  # as typed, e.g. "#launch opensource, news"
    link: str = ""
    # Platform versions the user edited by hand: platform id -> exact text to post.
    overrides: dict[str, str] = Field(default_factory=dict)
    platforms: list[str] = []
    image_ids: list[str] = []
    alt_texts: dict[str, str] = {}
    options: dict[str, dict[str, str]] = Field(default_factory=dict)
