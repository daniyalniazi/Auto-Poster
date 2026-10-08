"""Desktop integration: the system tray icon, desktop notifications and "start when I log in".

Everything here is optional. If the desktop has no tray (e.g. GNOME without the AppIndicator
extension), Auto Poster keeps running without it; opening it again just opens the browser tab.
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

APP_NAME = "Auto Poster"
AUTOSTART_ID = "AutoPoster"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

_icon = None  # the running pystray icon, if any


# ---- tray icon ------------------------------------------------------------------------------


def icon_image(size: int = 64):
    """A simple app icon drawn at runtime, so no image files need bundling."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=size // 5, fill=(47, 91, 211, 255))
    # A small megaphone: body + bell
    s = size / 64
    draw.polygon([(18 * s, 26 * s), (34 * s, 26 * s), (48 * s, 16 * s), (48 * s, 48 * s), (34 * s, 38 * s),
                  (18 * s, 38 * s)], fill="white")
    draw.rectangle((22 * s, 38 * s, 28 * s, 48 * s), fill="white")
    return image


def run_tray(url: str, on_quit: Callable[[], None]) -> bool:
    """Show the tray icon and block until Quit. Returns False if no tray is available."""
    global _icon
    try:
        import pystray
    except Exception as exc:  # missing system libraries on some Linux setups
        log.info("System tray not available (%s).", type(exc).__name__)
        return False

    def open_app(icon=None, item=None):
        webbrowser.open(url)

    def quit_app(icon, item):
        icon.visible = False
        on_quit()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Auto Poster", open_app, default=True),
        pystray.MenuItem("Quit", quit_app),
    )
    try:
        _icon = pystray.Icon(AUTOSTART_ID, icon_image(), f"{APP_NAME} is running", menu)
        _icon.run()
    except Exception as exc:
        log.warning("Could not show the tray icon (%s); running without it.", type(exc).__name__)
        _icon = None
        return False
    finally:
        _icon = None
    return True


def tray_active() -> bool:
    return _icon is not None


def notify(title: str, message: str) -> bool:
    """Show a desktop notification through the tray icon. Returns False if it couldn't."""
    icon = _icon
    if icon is None or not getattr(icon, "HAS_NOTIFICATION", False):
        return False
    try:
        icon.notify(message, title)
        return True
    except Exception as exc:
        log.info("Desktop notification failed (%s).", type(exc).__name__)
        return False


# ---- start when I log in -------------------------------------------------------------------


def launch_command(port: int | None = None) -> list[str]:
    """How to start Auto Poster quietly in the background."""
    if getattr(sys, "frozen", False):  # packaged app
        cmd = [sys.executable]
    else:
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        if sys.platform == "win32" and pythonw.exists():
            python = pythonw  # no console window at login
        cmd = [str(python), "-m", "auto_poster"]
    cmd.append("--background")
    if port:
        cmd += ["--port", str(port)]
    return cmd


def _linux_autostart_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "auto-poster.desktop"


def autostart_supported() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("linux")


def autostart_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                winreg.QueryValueEx(key, AUTOSTART_ID)
            return True
        except OSError:
            return False
    if sys.platform.startswith("linux"):
        return _linux_autostart_file().exists()
    return False


def set_autostart(enabled: bool, port: int | None = None) -> None:
    command = launch_command(port)
    if sys.platform == "win32":
        import subprocess
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_ID, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_ID)
                except FileNotFoundError:
                    pass
    elif sys.platform.startswith("linux"):
        path = _linux_autostart_file()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={APP_NAME}\n"
                "Comment=Write once, publish to multiple social platforms\n"
                f"Exec={shlex.join(command)}\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        else:
            path.unlink(missing_ok=True)
    else:
        raise OSError("Starting with the computer isn't supported on this system.")


def open_browser_later(url: str, delay: float = 1.0) -> None:
    threading.Timer(delay, lambda: webbrowser.open(url)).start()
