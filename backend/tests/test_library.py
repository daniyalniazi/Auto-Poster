"""Drafts, "Post again", saved hashtag sets, and keeping images that are still needed."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from auto_poster.connectors.registry import get_connector
from auto_poster.connectors.telegram import API_BASE
from auto_poster.main import create_app
from auto_poster.services import media
from auto_poster.services import settings as settings_service
from tests.conftest import image_bytes

PORT = 8765
TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"


@pytest.fixture
def client():
    app = create_app(PORT, run_scheduler=False)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        c.headers["X-Auto-Poster-Token"] = c.get("/api/session").json()["token"]
        yield c


def upload(client) -> str:
    return client.post("/api/media", files={"file": ("a.png", image_bytes(), "image/png")}).json()["id"]


# ---- drafts -------------------------------------------------------------------------------------


def test_draft_lifecycle(client):
    image_id = upload(client)
    body = {"mode": "structured", "title": "Hi", "text": "Draft body", "hashtags": "#a", "platforms": ["bluesky"],
            "image_ids": [image_id], "overrides": {"bluesky": "Custom"}}
    draft = client.post("/api/drafts", json=body).json()
    assert draft["request"]["title"] == "Hi" and draft["images"][0]["id"] == image_id
    assert draft["request"]["overrides"] == {"bluesky": "Custom"}

    updated = client.put(f"/api/drafts/{draft['id']}", json={**body, "text": "Changed"}).json()
    assert updated["request"]["text"] == "Changed"
    assert [d["id"] for d in client.get("/api/drafts").json()] == [draft["id"]]

    client.delete(f"/api/drafts/{draft['id']}")
    assert client.get("/api/drafts").json() == []
    assert media.load(image_id) is None  # no longer needed by anything


def test_removing_image_from_draft_deletes_it(client):
    keep, drop = upload(client), upload(client)
    draft = client.post("/api/drafts", json={"text": "x", "image_ids": [keep, drop]}).json()
    client.put(f"/api/drafts/{draft['id']}", json={"text": "x", "image_ids": [keep]})
    assert media.load(keep) is not None and media.load(drop) is None


def test_missing_draft(client):
    assert client.get("/api/drafts/999").status_code == 404


# ---- post again -----------------------------------------------------------------------------------


@respx.mock
def test_post_again_restores_post_and_images(client):
    settings_service.save_settings(get_connector("telegram"), {"bot_token": TOKEN, "chat_id": "@chan"})
    respx.post(f"{API_BASE}/bot{TOKEN}/sendPhoto").mock(return_value=httpx.Response(200, json={
        "ok": True, "result": {"message_id": 5, "chat": {"id": -1001, "type": "channel"}}}))
    image_id = upload(client)
    published = client.post("/api/publish", json={
        "mode": "structured", "title": "Launch", "text": "Body", "hashtags": "#x", "platforms": ["telegram"],
        "image_ids": [image_id], "alt_texts": {image_id: "logo"}, "options": {"telegram": {"silent": "true"}},
        "request_id": "again-1"}).json()
    assert published["status"] == "success"
    assert media.load(image_id) is not None  # kept so it can be posted again

    data = client.get(f"/api/history/{published['history_id']}/compose").json()
    assert data["request"]["title"] == "Launch" and data["request"]["options"] == {"telegram": {"silent": "true"}}
    assert data["images"][0]["id"] == image_id and data["missing_images"] == 0

    client.delete(f"/api/history/{published['history_id']}")
    assert media.load(image_id) is None  # removing the history entry frees the image


def test_post_again_reports_missing_images(client):
    from auto_poster.models import PostRequest
    from auto_poster.services import history

    post_id = history.create_post("old-1", "Old", 1, ["telegram"],
                                  compose=PostRequest(text="Old", image_ids=["deadbeef"]))
    data = client.get(f"/api/history/{post_id}/compose").json()
    assert data["missing_images"] == 1 and data["request"]["image_ids"] == []


# ---- hashtag sets -----------------------------------------------------------------------------------


def test_hashtag_sets(client):
    created = client.post("/api/hashtag-sets", json={"name": "Launch", "tags": "#launch, OpenSource #launch"}).json()
    assert created["tags"] == ["launch", "OpenSource"]
    assert [s["name"] for s in client.get("/api/hashtag-sets").json()] == ["Launch"]
    client.delete(f"/api/hashtag-sets/{created['id']}")
    assert client.get("/api/hashtag-sets").json() == []


def test_hashtag_set_needs_name_and_tags(client):
    assert client.post("/api/hashtag-sets", json={"name": "", "tags": "#a"}).status_code == 400
    assert client.post("/api/hashtag-sets", json={"name": "Empty", "tags": "  "}).status_code == 400
