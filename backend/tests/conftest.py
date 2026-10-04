"""Shared test setup. Tests never touch the real keyring, real data folder or real APIs."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from auto_poster import database
from auto_poster.security import credentials


class MemoryStore:
    kind = "memory"

    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, platform, key):
        value = self.data.get(f"{platform}:{key}")
        credentials.register_secret(value)
        return value

    def set(self, platform, key, value):
        credentials.register_secret(value)
        self.data[f"{platform}:{key}"] = value

    def delete(self, platform, key):
        self.data.pop(f"{platform}:{key}", None)


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_POSTER_DATA_DIR", str(tmp_path))
    store = MemoryStore()
    credentials.set_store(store)
    database.migrate()
    yield store
    credentials.set_store(None)


@pytest.fixture
def store(isolated_app_data) -> MemoryStore:
    return isolated_app_data


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (20, 10)) -> bytes:
    buffer = io.BytesIO()
    mode = "RGB" if fmt == "JPEG" else "RGBA"
    Image.new(mode, size, (200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def make_image():
    from auto_poster.services import media

    def _make(fmt: str = "PNG", size: tuple[int, int] = (20, 10), name: str | None = None):
        return media.save_upload(name or f"test.{fmt.lower()}", image_bytes(fmt, size))

    return _make
