"""Backup, restore and freeing space. Secrets must never end up in a backup."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from auto_poster import database
from auto_poster.connectors.registry import get_connector
from auto_poster.main import create_app
from auto_poster.models import PostRequest
from auto_poster.services import drafts, history, media
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


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_backup_round_trip_without_secrets(client, store, tmp_path, monkeypatch):
    settings_service.save_settings(get_connector("telegram"), {"bot_token": TOKEN, "chat_id": "@chan"})
    image = media.save_upload("pic.png", image_bytes())
    drafts.create(PostRequest(text="My draft", image_ids=[image.id]))
    history.create_post("b-1", "Published", 0, ["telegram"], compose=PostRequest(text="Published"))

    response = client.get("/api/backup")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = response.content
    assert TOKEN.encode() not in data  # no secrets, even though Telegram is connected
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert {"manifest.json", "auto-poster.db", f"media/{image.id}"} <= set(names)

    # Restore on a "new computer": empty data folder and empty keyring.
    monkeypatch.setenv("AUTO_POSTER_DATA_DIR", str(tmp_path / "new"))
    store.data.clear()
    database.migrate()
    result = client.post("/api/backup/restore", files={"file": ("b.zip", data, "application/zip")}).json()
    assert result == {"posts": 1, "images": 1}
    assert [d.request.text for d in drafts.list_all()] == ["My draft"]
    assert media.load(image.id) is not None
    config = settings_service.load_config(get_connector("telegram"))
    assert config["chat_id"] == "@chan" and "bot_token" not in config  # reconnect needed


def test_restore_rejects_other_files(client):
    for data in (b"not a zip", make_zip({"hello.txt": b"x"})):
        r = client.post("/api/backup/restore", files={"file": ("x.zip", data, "application/zip")})
        assert r.status_code == 400 and "isn't an Auto Poster backup" in r.json()["detail"]


def test_restore_rejects_unexpected_paths(client):
    data = make_zip({"manifest.json": json.dumps({"app": "auto-poster", "format": 1}).encode(),
                     "auto-poster.db": b"", "../../evil.txt": b"x"})
    r = client.post("/api/backup/restore", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 400 and "unexpected files" in r.json()["detail"]


def test_restore_rejects_newer_format(client):
    data = make_zip({"manifest.json": json.dumps({"app": "auto-poster", "format": 99}).encode(),
                     "auto-poster.db": b""})
    r = client.post("/api/backup/restore", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 400 and "newer version" in r.json()["detail"]


def test_restore_rejects_damaged_database(client):
    data = make_zip({"manifest.json": json.dumps({"app": "auto-poster", "format": 1}).encode(),
                     "auto-poster.db": b"garbage" * 100})
    r = client.post("/api/backup/restore", files={"file": ("x.zip", data, "application/zip")})
    assert r.status_code == 400 and "damaged" in r.json()["detail"]
    assert client.get("/api/history").status_code == 200  # current data untouched


def test_free_space_removes_only_old_post_images(client):
    old_image = media.save_upload("old.png", image_bytes())
    new_image = media.save_upload("new.png", image_bytes())
    draft_image = media.save_upload("draft.png", image_bytes())
    old_id = history.create_post("old", "Old", 1, [], compose=PostRequest(text="Old", image_ids=[old_image.id]))
    history.create_post("new", "New", 1, [], compose=PostRequest(text="New", image_ids=[new_image.id]))
    history.create_post("old2", "Old2", 1, [], compose=PostRequest(text="x", image_ids=[draft_image.id]))
    drafts.create(PostRequest(text="Draft", image_ids=[draft_image.id]))
    long_ago = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
    with database.session() as conn:
        conn.execute("UPDATE posts SET created_at = ? WHERE request_id IN ('old', 'old2')", (long_ago,))

    result = client.post("/api/storage/cleanup", json={"days": 30}).json()
    assert result["freed_bytes"] > 0
    assert media.load(old_image.id) is None
    assert media.load(new_image.id) is not None
    assert media.load(draft_image.id) is not None  # still used by a draft
    assert history.get_compose(old_id).image_ids == []
    assert client.get("/api/storage").json()["media_bytes"] > 0
