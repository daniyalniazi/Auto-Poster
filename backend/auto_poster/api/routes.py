"""HTTP routes used by the local web UI."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from auto_poster.connectors.base import safe_run_action, safe_test_connection
from auto_poster.connectors.registry import CONNECTORS, get_connector
from auto_poster.models import ActionResult, ConnectionStatus, ImageInfo, PostRequest, PostResult, Problem
from auto_poster.security.credentials import get_store
from auto_poster.services import deleter, drafts, hashtag_sets, history, media, media_usage, publisher, scheduler
from auto_poster.services import settings as settings_service

router = APIRouter(prefix="/api")


def _connector_or_404(platform_id: str):
    connector = get_connector(platform_id)
    if connector is None:
        raise HTTPException(404, "Unknown platform.")
    return connector


@router.get("/session")
def session(request: Request) -> dict:
    return {"token": request.app.state.guard.token}


@router.get("/info")
def info() -> dict:
    return {"secret_storage": get_store().kind}


@router.get("/platforms")
def platforms(request: Request) -> list[dict]:
    port = str(request.app.state.port)
    result = []
    for connector in CONNECTORS.values():
        config = settings_service.load_config(connector)
        guide = connector.setup_guide.model_dump()
        # Guides can mention the local address (e.g. OAuth redirect URLs) as {port}.
        guide["steps"] = [step.replace("{port}", port) for step in guide["steps"]]
        guide["notes"] = [note.replace("{port}", port) for note in guide["notes"]]
        result.append(
            {
                "id": connector.id,
                "name": connector.display_name,
                "description": connector.description,
                "configured": connector.is_configured(config),
                "limits": connector.effective_limits(config).model_dump(),
                "settings_fields": [f.model_dump() for f in connector.settings_fields_for(config)],
                "post_fields": [f.model_dump() for f in connector.post_fields],
                "actions": [a.model_dump() for a in connector.actions],
                "setup_guide": guide,
            }
        )
    return result


@router.get("/platforms/{platform_id}/settings")
def get_settings(platform_id: str) -> dict[str, str]:
    return settings_service.public_settings(_connector_or_404(platform_id))


@router.put("/platforms/{platform_id}/settings")
def put_settings(platform_id: str, values: dict[str, str]) -> dict[str, str]:
    connector = _connector_or_404(platform_id)
    settings_service.save_settings(connector, values)
    return settings_service.public_settings(connector)


@router.delete("/platforms/{platform_id}/settings")
def delete_settings(platform_id: str) -> dict:
    settings_service.delete_settings(_connector_or_404(platform_id))
    return {"ok": True}


@router.post("/platforms/{platform_id}/test")
async def test_connection(platform_id: str) -> ConnectionStatus:
    connector = _connector_or_404(platform_id)
    return await safe_test_connection(connector, settings_service.load_config(connector))


@router.post("/platforms/{platform_id}/actions/{action_id}")
async def run_action(platform_id: str, action_id: str, request: Request) -> ActionResult:
    connector = _connector_or_404(platform_id)
    if action_id not in {a.id for a in connector.actions}:
        raise HTTPException(404, "Unknown action.")
    base_url = f"http://127.0.0.1:{request.app.state.port}"
    return await safe_run_action(connector, action_id, settings_service.load_config(connector), base_url)


@router.post("/media")
async def upload_media(file: UploadFile = File(...)) -> ImageInfo:
    data = await file.read()
    try:
        image = media.save_upload(file.filename or "image", data)
    except media.MediaError as exc:
        raise HTTPException(400, str(exc))
    return ImageInfo(
        id=image.id,
        filename=image.filename,
        format=image.format,
        size_bytes=image.size_bytes,
        width=image.width,
        height=image.height,
    )


@router.get("/media/{image_id}")
def get_media(image_id: str) -> FileResponse:
    image = media.load(image_id)
    if image is None:
        raise HTTPException(404, "Image not found.")
    return FileResponse(image.path, media_type=image.mime_type)


@router.delete("/media/{image_id}")
def delete_media(image_id: str) -> dict:
    """Called when the user removes an image from the form. Kept if a draft, scheduled post or
    history entry still uses it."""
    media_usage.delete_unused([image_id])
    return {"ok": True}


@router.post("/validate")
def validate(request: PostRequest) -> dict[str, list[Problem]]:
    return publisher.validate(request)


@router.post("/prepare")
def prepare(request: PostRequest) -> dict:
    """Each selected platform's final text (for the editable previews) and its problems."""
    return publisher.prepare(request)


class PublishRequest(PostRequest):
    request_id: str


class PublishResponse(BaseModel):
    history_id: int
    status: str
    results: list[PostResult]


@router.post("/publish")
async def publish(request: PublishRequest) -> PublishResponse:
    if not request.request_id or len(request.request_id) > 100:
        raise HTTPException(400, "Missing request ID.")
    try:
        history_id, results = await publisher.publish_now(request, request.request_id)
    except publisher.DuplicateRequest:
        raise HTTPException(409, "This post is already being published.")
    return PublishResponse(history_id=history_id, status=history.overall_status(results), results=results)


@router.get("/history")
def get_history(limit: int = 50, offset: int = 0, status: str | None = None) -> list[history.HistoryEntry]:
    return history.list_entries(limit=min(limit, 200), offset=max(offset, 0), status=status)


class ComposeData(BaseModel):
    request: PostRequest
    images: list[ImageInfo]  # the images that still exist
    missing_images: int


@router.get("/history/{post_id}/compose")
def history_compose(post_id: int) -> ComposeData:
    """Everything needed to "Post again"."""
    request = history.get_compose(post_id)
    if request is None:
        raise HTTPException(404, "That post is no longer in your history.")
    images = media.infos(request.image_ids)
    missing = len(request.image_ids) - len(images)
    request.image_ids = [i.id for i in images]
    return ComposeData(request=request, images=images, missing_images=missing)


@router.delete("/history/{post_id}")
def delete_history(post_id: int) -> dict:
    history.delete_entry(post_id)
    return {"ok": True}


# ---- scheduled posts -------------------------------------------------------------------


class ScheduleRequest(PostRequest):
    scheduled_at: datetime  # must include a time zone offset


@router.get("/scheduled")
def list_scheduled() -> list[scheduler.ScheduledPost]:
    return scheduler.list_all()


def _scheduled_or_404(post_id: int) -> scheduler.ScheduledPost:
    post = scheduler.get(post_id)
    if post is None:
        raise HTTPException(404, "That scheduled post no longer exists.")
    return post


@router.get("/scheduled/{post_id}")
def get_scheduled(post_id: int) -> scheduler.ScheduledPost:
    return _scheduled_or_404(post_id)


@router.post("/scheduled")
def create_scheduled(request: ScheduleRequest) -> scheduler.ScheduledPost:
    try:
        return scheduler.create(request, request.scheduled_at)
    except scheduler.ScheduleError as exc:
        raise HTTPException(400, str(exc))


@router.put("/scheduled/{post_id}")
def update_scheduled(post_id: int, request: ScheduleRequest) -> scheduler.ScheduledPost:
    _scheduled_or_404(post_id)
    try:
        return scheduler.update(post_id, request, request.scheduled_at)
    except scheduler.ScheduleError as exc:
        raise HTTPException(400, str(exc))


class EnabledRequest(BaseModel):
    enabled: bool


@router.put("/scheduled/{post_id}/enabled")
def set_scheduled_enabled(post_id: int, body: EnabledRequest) -> scheduler.ScheduledPost:
    _scheduled_or_404(post_id)
    return scheduler.set_enabled(post_id, body.enabled)


@router.post("/scheduled/{post_id}/send-now")
async def send_scheduled_now(post_id: int) -> scheduler.ScheduledPost:
    _scheduled_or_404(post_id)
    result = await scheduler.send(post_id, allowed_from=("scheduled", "missed"))
    if result is None:
        raise HTTPException(409, "This post is already being sent or was already sent.")
    return result


@router.delete("/scheduled/{post_id}")
def delete_scheduled(post_id: int) -> dict:
    try:
        scheduler.delete(post_id)
    except scheduler.ScheduleError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


# ---- drafts ------------------------------------------------------------------------------


@router.get("/drafts")
def list_drafts() -> list[drafts.Draft]:
    return drafts.list_all()


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int) -> drafts.Draft:
    draft = drafts.get(draft_id)
    if draft is None:
        raise HTTPException(404, "That draft no longer exists.")
    return draft


@router.post("/drafts")
def create_draft(request: PostRequest) -> drafts.Draft:
    return drafts.create(request)


@router.put("/drafts/{draft_id}")
def update_draft(draft_id: int, request: PostRequest) -> drafts.Draft:
    draft = drafts.update(draft_id, request)
    if draft is None:
        raise HTTPException(404, "That draft no longer exists.")
    return draft


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: int) -> dict:
    drafts.delete(draft_id)
    return {"ok": True}


# ---- hashtag sets ---------------------------------------------------------------------------


class HashtagSetRequest(BaseModel):
    name: str
    tags: str


@router.get("/hashtag-sets")
def list_hashtag_sets() -> list[hashtag_sets.HashtagSet]:
    return hashtag_sets.list_all()


@router.post("/hashtag-sets")
def create_hashtag_set(body: HashtagSetRequest) -> hashtag_sets.HashtagSet:
    try:
        return hashtag_sets.create(body.name, body.tags)
    except hashtag_sets.HashtagSetError as exc:
        raise HTTPException(400, str(exc)) from None


@router.delete("/hashtag-sets/{set_id}")
def delete_hashtag_set(set_id: int) -> dict:
    hashtag_sets.delete(set_id)
    return {"ok": True}


# ---- delete everywhere ------------------------------------------------------------------------


@router.post("/history/{post_id}/delete-everywhere")
async def delete_everywhere(post_id: int) -> list[deleter.DeleteOutcome]:
    outcomes = await deleter.delete_everywhere(post_id)
    if outcomes is None:
        raise HTTPException(404, "That post is no longer in your history.")
    return outcomes
