"""Which stored images are still needed. An image is kept while anything refers to it:
a draft, a scheduled post, a published post (for "Post again") or a repost reminder."""

from __future__ import annotations

import json
import logging

from auto_poster import database
from auto_poster.services import media

log = logging.getLogger(__name__)


def _ids_from_requests(rows, column: str) -> set[str]:
    ids: set[str] = set()
    for row in rows:
        try:
            ids.update(json.loads(row[column] or "{}").get("image_ids", []))
        except (ValueError, AttributeError):
            log.warning("Unreadable stored post data in %s", column)
    return ids


def in_use_image_ids() -> set[str]:
    with database.session() as conn:
        ids = {i for row in conn.execute("SELECT image_ids FROM scheduled_posts") for i in json.loads(row["image_ids"])}
        ids |= _ids_from_requests(conn.execute("SELECT data FROM drafts").fetchall(), "data")
        ids |= _ids_from_requests(conn.execute("SELECT compose FROM posts WHERE compose IS NOT NULL").fetchall(),
                                  "compose")
        ids |= _ids_from_requests(conn.execute("SELECT request FROM reminders").fetchall(), "request")
    return ids


def delete_unused(image_ids: set[str] | list[str]) -> None:
    """Delete these images unless something else still uses them."""
    still_used = in_use_image_ids()
    for image_id in set(image_ids) - still_used:
        media.delete(image_id)
