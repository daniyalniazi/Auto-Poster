"""Saved drafts: unfinished posts the user can come back to."""

from __future__ import annotations

import json

from pydantic import BaseModel

from auto_poster import database
from auto_poster.models import ImageInfo, PostRequest
from auto_poster.services import media, media_usage
from auto_poster.services.history import now_iso


class Draft(BaseModel):
    id: int
    created_at: str
    updated_at: str
    request: PostRequest
    images: list[ImageInfo]  # the draft's images that still exist


def _from_row(row) -> Draft:
    request = PostRequest.model_validate(json.loads(row["data"]))
    return Draft(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"],
                 request=request, images=media.infos(request.image_ids))


def list_all() -> list[Draft]:
    with database.session() as conn:
        rows = conn.execute("SELECT * FROM drafts ORDER BY updated_at DESC").fetchall()
    return [_from_row(r) for r in rows]


def get(draft_id: int) -> Draft | None:
    with database.session() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return _from_row(row) if row else None


def create(request: PostRequest) -> Draft:
    now = now_iso()
    with database.session() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (created_at, updated_at, data) VALUES (?, ?, ?)",
            (now, now, request.model_dump_json()),
        ).lastrowid
    return get(draft_id)


def update(draft_id: int, request: PostRequest) -> Draft | None:
    existing = get(draft_id)
    if existing is None:
        return None
    with database.session() as conn:
        conn.execute("UPDATE drafts SET updated_at = ?, data = ? WHERE id = ?",
                     (now_iso(), request.model_dump_json(), draft_id))
    media_usage.delete_unused(set(existing.request.image_ids) - set(request.image_ids))
    return get(draft_id)


def delete(draft_id: int) -> None:
    existing = get(draft_id)
    if existing is None:
        return
    with database.session() as conn:
        conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
    media_usage.delete_unused(existing.request.image_ids)
