"""Images the user attaches. Stored only in the local media folder until posted."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from auto_poster.config import media_dir
from auto_poster.models import ImageFile, ImageInfo

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


def infos(image_ids: list[str]) -> list[ImageInfo]:
    """Details of the images that still exist, in order."""
    result = []
    for image_id in image_ids:
        image = load(image_id)
        if image:
            result.append(ImageInfo(id=image.id, filename=image.filename, format=image.format,
                                    size_bytes=image.size_bytes, width=image.width, height=image.height))
    return result


def total_size() -> int:
    return sum(p.stat().st_size for p in media_dir().iterdir() if p.is_file())


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


def fit_to_size(image: ImageFile, max_bytes: int, max_dimension: int | None = None) -> tuple[bytes, str]:
    """Return the image bytes, re-compressed and/or scaled down only if needed to fit a platform.

    Used by connectors whose platform has a strict file-size limit (the platforms' own
    apps do the same). Returns (bytes, mime type).
    """
    import io

    from PIL import ImageOps

    original = image.read_bytes()
    too_big_dims = max_dimension and max(image.width, image.height) > max_dimension
    if len(original) <= max_bytes and not too_big_dims:
        return original, image.mime_type

    with Image.open(image.path) as img:
        img = ImageOps.exif_transpose(img)  # keep the orientation the user sees
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if max_dimension:
            img.thumbnail((max_dimension, max_dimension))
        scale = 1.0
        for _ in range(12):
            candidate = img if scale == 1.0 else img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS
            )
            buffer = io.BytesIO()
            if has_alpha:
                candidate.save(buffer, format="PNG", optimize=True)
                mime = "image/png"
            else:
                candidate.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
                mime = "image/jpeg"
            if buffer.tell() <= max_bytes:
                return buffer.getvalue(), mime
            scale *= 0.8
    raise MediaError(f'"{image.filename}" could not be made small enough for this platform.')
