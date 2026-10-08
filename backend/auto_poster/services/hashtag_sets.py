"""Saved hashtag sets, e.g. "Launch" = #launch #OpenSource, added to a post with one click."""

from __future__ import annotations

import json

from pydantic import BaseModel

from auto_poster import database
from auto_poster.services.compose import parse_hashtags
from auto_poster.services.history import now_iso


class HashtagSet(BaseModel):
    id: int
    name: str
    tags: list[str]  # without "#"


class HashtagSetError(ValueError):
    pass


def list_all() -> list[HashtagSet]:
    with database.session() as conn:
        rows = conn.execute("SELECT * FROM hashtag_sets ORDER BY name COLLATE NOCASE").fetchall()
    return [HashtagSet(id=r["id"], name=r["name"], tags=json.loads(r["tags"])) for r in rows]


def create(name: str, tags: str) -> HashtagSet:
    name, parsed = name.strip(), parse_hashtags(tags)
    if not name:
        raise HashtagSetError("Give the hashtag set a name.")
    if not parsed:
        raise HashtagSetError("Add at least one hashtag to save as a set.")
    with database.session() as conn:
        set_id = conn.execute("INSERT INTO hashtag_sets (name, tags, created_at) VALUES (?, ?, ?)",
                              (name[:60], json.dumps(parsed), now_iso())).lastrowid
    return HashtagSet(id=set_id, name=name[:60], tags=parsed)


def delete(set_id: int) -> None:
    with database.session() as conn:
        conn.execute("DELETE FROM hashtag_sets WHERE id = ?", (set_id,))
