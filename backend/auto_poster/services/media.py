"""Images the user attaches. Stored only in the local media folder until posted."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from auto_poster.config import media_dir
from auto_poster.models import ImageFile

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # sanity cap; platforms enforce their own smaller limits
ORPHAN_MAX_AGE_SECONDS = 24 * 3600

MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}


class MediaError(ValueError):
    pass


def save_upload(filename: str, data: bytes) -> ImageFile:
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError(f'"{filename}" is larger than 50 MB, which no supported platform accepts.')
    image_id = uuid.uuid4().hex
    path = media_dir() / image_id
    path.write_bytes(data)
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            fmt, (width, height) = img.format or "", img.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        path.unlink(missing_ok=True)
        raise MediaError(f'"{filename}" is not an image file Auto Poster can read.')
    if fmt not in MIME_TYPES:
        path.unlink(missing_ok=True)
        raise MediaError(f'"{filename}" is a {fmt} image. Use JPEG, PNG, WEBP or GIF.')
    image = ImageFile(
        id=image_id,
        filename=Path(filename).name or "image",
        path=str(path),
        format=fmt,
        mime_type=MIME_TYPES[fmt],
        size_bytes=len(data),
        width=width,
        height=height,
    )
    _meta_path(image_id).write_text(json.dumps({"filename": image.filename}), encoding="utf-8")
    return image


def load(image_id: str) -> ImageFile | None:
    if not image_id.isalnum():
        return None
    path = media_dir() / image_id
    if not path.exists():
        return None
    meta = json.loads(_meta_path(image_id).read_text(encoding="utf-8")) if _meta_path(image_id).exists() else {}
    with Image.open(path) as img:
        fmt, (width, height) = img.format or "", img.size
    return ImageFile(
        id=image_id,
        filename=meta.get("filename", "image"),
        path=str(path),
        format=fmt,
        mime_type=MIME_TYPES.get(fmt, "application/octet-stream"),
        size_bytes=path.stat().st_size,
        width=width,
        height=height,
    )


def delete(image_id: str) -> None:
    if image_id.isalnum():
        (media_dir() / image_id).unlink(missing_ok=True)
        _meta_path(image_id).unlink(missing_ok=True)


def cleanup_orphans(keep: set[str] | None = None) -> None:
    """Remove old uploads that were never posted (e.g. the user closed the page)."""
    cutoff = time.time() - ORPHAN_MAX_AGE_SECONDS
    keep = keep or set()
    for path in media_dir().iterdir():
        image_id = path.name.split(".")[0]
        if image_id not in keep and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


def _meta_path(image_id: str) -> Path:
    return media_dir() / f"{image_id}.json"
