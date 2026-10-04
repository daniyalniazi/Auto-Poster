"""App-level tests: local request guard, settings, validation, publishing and history."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from auto_poster.connectors.telegram import API_BASE
from auto_poster.main import create_app
from auto_poster.security.redact import RedactingFilter, mask_for_display, redact
from tests.conftest import image_bytes

PORT = 8765
TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"


@pytest.fixture
def client():
    app = create_app(PORT, run_scheduler=False)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        token = c.get("/api/session").json()["token"]
        c.headers["X-Auto-Poster-Token"] = token
        yield c


def configure_telegram(client):
    r = client.put("/api/platforms/telegram/settings", json={"bot_token": TOKEN, "chat_id": "@chan"})
    assert r.status_code == 200


# ---- local guard -----------------------------------------------------------------------


def test_api_requires_session_token():
    app = create_app(PORT, run_scheduler=False)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        assert c.get("/api/platforms").status_code == 403
        assert c.get("/api/platforms", headers={"X-Auto-Poster-Token": "wrong"}).status_code == 403


def test_other_website_origin_blocked(client):
    r = client.get("/api/platforms", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_dns_rebinding_host_blocked(client):
    r = client.get("/api/session", headers={"Host": "evil.example:8765"})
    assert r.status_code == 403


# ---- settings -----------------------------------------------------------------------------


def test_secret_is_masked_and_not_in_database(client, store, tmp_path):
    configure_telegram(client)
    settings = client.get("/api/platforms/telegram/settings").json()
    assert settings["chat_id"] == "@chan"
    assert settings["bot_token"].startswith("••••") and TOKEN not in settings["bot_token"]
    assert store.data["telegram:bot_token"] == TOKEN
    assert TOKEN.encode() not in (tmp_path / "auto-poster.db").read_bytes()


def test_empty_secret_keeps_existing_value(client, store):
    configure_telegram(client)
    client.put("/api/platforms/telegram/settings", json={"bot_token": "", "chat_id": "@other"})
    assert store.data["telegram:bot_token"] == TOKEN


def test_platform_list_shows_connection_state(client):
    platforms = {p["id"]: p for p in client.get("/api/platforms").json()}
    assert platforms["telegram"]["configured"] is False
    configure_telegram(client)
    platforms = {p["id"]: p for p in client.get("/api/platforms").json()}
    assert platforms["telegram"]["configured"] is True


def test_disconnect_removes_secret(client, store):
    configure_telegram(client)
    client.delete("/api/platforms/telegram/settings")
    assert "telegram:bot_token" not in store.data


# ---- media, validation, publishing -----------------------------------------------------


def test_upload_rejects_non_image(client):
    r = client.post("/api/media", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    assert "not an image" in r.json()["detail"]


def test_validate_reports_unconfigured_platform(client):
    problems = client.post("/api/validate", json={"text": "Hi", "platforms": ["telegram"]}).json()
    assert "not connected" in problems["telegram"][0]["message"]


def test_validate_requires_a_platform(client):
    problems = client.post("/api/validate", json={"text": "Hi", "platforms": []}).json()
    assert problems["all"]


@respx.mock
def test_publish_records_history_and_is_idempotent(client):
    configure_telegram(client)
    route = respx.post(f"{API_BASE}/bot{TOKEN}/sendPhoto").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {
            "message_id": 5, "chat": {"id": -1001, "type": "channel", "username": "chan"}}})
    )
    image = client.post("/api/media", files={"file": ("a.png", image_bytes(), "image/png")}).json()
    body = {"text": "Hello", "platforms": ["telegram"], "image_ids": [image["id"]], "request_id": "abc-1"}

    first = client.post("/api/publish", json=body).json()
    assert first["status"] == "success"
    assert first["results"][0]["post_url"] == "https://t.me/chan/5"

    second = client.post("/api/publish", json=body).json()  # e.g. a double-click
    assert second["history_id"] == first["history_id"]
    assert route.call_count == 1

    entries = client.get("/api/history").json()
    assert len(entries) == 1 and entries[0]["results"][0]["success"] is True


@respx.mock
def test_publish_failure_is_recorded(client):
    configure_telegram(client)
    respx.post(f"{API_BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
    )
    result = client.post("/api/publish", json={"text": "Hi", "platforms": ["telegram"], "request_id": "r2"}).json()
    assert result["status"] == "failed"
    assert result["results"][0]["error_code"] == "invalid_credentials"


def test_publish_too_long_is_not_sent(client):
    configure_telegram(client)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(url__startswith=API_BASE)
        result = client.post(
            "/api/publish", json={"text": "x" * 5000, "platforms": ["telegram"], "request_id": "r3"}
        ).json()
        assert route.call_count == 0
    assert result["results"][0]["error_code"] == "validation_failed"


# ---- secrets never reach logs ------------------------------------------------------------


def test_log_filter_masks_tokens(caplog):
    logger = logging.getLogger("test.redact")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO):
        logger.info("POST https://api.telegram.org/bot%s/sendMessage", TOKEN)
        logger.info("Authorization: Bearer abc.def.ghi")
    assert TOKEN not in caplog.text
    assert "abc.def.ghi" not in caplog.text


def test_redact_and_mask():
    assert TOKEN not in redact(f"url /bot{TOKEN}/getMe")
    assert mask_for_display(TOKEN).endswith(TOKEN[-4:])
    assert TOKEN[:10] not in mask_for_display(TOKEN)
