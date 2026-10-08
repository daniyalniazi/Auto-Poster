"""'Delete everywhere': removes a published post from every platform it went to."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from auto_poster.connectors.base import safe_delete
from auto_poster.connectors.registry import get_connector
from auto_poster.services import history
from auto_poster.services.settings import load_config


class DeleteOutcome(BaseModel):
    platform: str
    success: bool
    message: str
    technical_details: str | None = None


async def delete_everywhere(history_post_id: int) -> list[DeleteOutcome] | None:
    """None if the history entry doesn't exist. Platforms are handled independently."""
    entry = history.get_entry(history_post_id)
    if entry is None:
        return None
    targets = [r for r in entry.results if r.success and r.post_id and not r.deleted_at]

    async def one(result) -> DeleteOutcome:
        connector = get_connector(result.platform)
        name = connector.display_name if connector else result.platform
        if connector is None or not connector.can_delete:
            return DeleteOutcome(platform=result.platform, success=False,
                                 message=f"{name} posts can't be deleted from Auto Poster.")
        config = load_config(connector)
        if not connector.is_configured(config):
            return DeleteOutcome(platform=result.platform, success=False,
                                 message=f"{name} is not connected any more. Reconnect it in Settings, or delete "
                                 f"the post on {name} yourself.")
        error = await safe_delete(connector, result.post_id, config)
        if error is None:
            history.mark_deleted(history_post_id, result.platform)
            return DeleteOutcome(platform=result.platform, success=True, message=f"Deleted from {name}.")
        return DeleteOutcome(platform=result.platform, success=False, message=error.message,
                             technical_details=error.technical_details)

    return list(await asyncio.gather(*(one(r) for r in targets)))
