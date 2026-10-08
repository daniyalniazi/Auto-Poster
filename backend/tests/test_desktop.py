"""Desktop integration: start with the computer, tray icon helpers. No real registry or tray is touched."""

from __future__ import annotations

import sys
import types


from auto_poster import desktop


def test_icon_image_is_drawn():
    image = desktop.icon_image(32)
    assert image.size == (32, 32) and image.getpixel((16, 2))[3] > 0


def test_notify_without_tray_is_harmless():
    assert desktop.notify("Title", "Message") is False


def test_launch_command_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = desktop.launch_command(port=9000)
    assert cmd[-5:] == ["-m", "auto_poster", "--background", "--port", "9000"]


def test_launch_command_packaged(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/AutoPoster")
    assert desktop.launch_command() == ["/opt/AutoPoster", "--background"]


def test_linux_autostart_file(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/home/me/Auto Poster/AutoPoster")
    assert not desktop.autostart_enabled()
    desktop.set_autostart(True)
    entry = (tmp_path / "autostart" / "auto-poster.desktop").read_text()
    assert "Exec='/home/me/Auto Poster/AutoPoster' --background" in entry
    assert desktop.autostart_enabled()
    desktop.set_autostart(False)
    assert not desktop.autostart_enabled()


def test_windows_autostart_registry(monkeypatch):
    values: dict[str, str] = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def query(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    def delete(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=1,
        OpenKey=lambda *a: Key(),
        QueryValueEx=query,
        SetValueEx=lambda key, name, reserved, kind, value: values.__setitem__(name, value),
        DeleteValue=delete,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\AutoPoster\AutoPoster.exe")

    desktop.set_autostart(True)
    assert values["AutoPoster"] == r'"C:\Program Files\AutoPoster\AutoPoster.exe" --background'
    assert desktop.autostart_enabled()
    desktop.set_autostart(False)
    desktop.set_autostart(False)  # turning it off twice is fine
    assert not desktop.autostart_enabled()


def test_system_endpoint(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from auto_poster.main import create_app

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = create_app(8765, run_scheduler=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.headers["X-Auto-Poster-Token"] = c.get("/api/session").json()["token"]
        assert c.get("/api/system").json()["autostart_enabled"] is False
        assert c.put("/api/system/autostart", json={"enabled": True}).json()["autostart_enabled"] is True
        assert (tmp_path / "autostart" / "auto-poster.desktop").exists()
