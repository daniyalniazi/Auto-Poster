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
from auto_poster.services import history, media, publisher, scheduler
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
                "settings_fields": [f.model_dump() for f in connector.settings_fields],
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
    media.delete(image_id)
    return {"ok": True}


@router.post("/validate")
def validate(request: PostRequest) -> dict[str, list[Problem]]:
    return publisher.validate(request)


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
