"""Data models shared by the API, services and connectors."""

from __future__ import annotations

from dataclasses import dataclass
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
    text: str
    images: list[ImageFile]


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
    text: str = ""
    platforms: list[str] = []
    image_ids: list[str] = []
    alt_texts: dict[str, str] = {}
    options: dict[str, dict[str, str]] = Field(default_factory=dict)
