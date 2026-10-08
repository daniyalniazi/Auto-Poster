"""Build a ready-to-run Auto Poster for the current OS (Windows .exe or Linux binary).

Usage (from the repository root, with the virtual environment active):

    pip install -e ".[build]"
    python scripts/build.py

Output: dist/AutoPoster.exe (Windows) or dist/AutoPoster (Linux).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
STATIC = ROOT / "backend" / "auto_poster" / "static"


def run(*cmd: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True, shell=sys.platform == "win32" and cmd[0] == "npm")


def build_frontend() -> None:
    run("npm", "ci", cwd=FRONTEND)
    run("npm", "run", "build", cwd=FRONTEND)
    if STATIC.exists():
        shutil.rmtree(STATIC)
    shutil.copytree(FRONTEND / "dist", STATIC)


def build_binary() -> None:
    separator = ";" if sys.platform == "win32" else ":"
    run(
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", "AutoPoster",
        "--paths", str(ROOT / "backend"),
        "--add-data", f"{STATIC}{separator}auto_poster/static",
        # Loaded dynamically, so PyInstaller can't see them on its own:
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "keyring.backends",
        "--collect-submodules", "pystray",
        # Windows: no console window; logs go to the data folder and the app lives in the tray.
        *(["--windowed"] if sys.platform == "win32" else []),
        "--hidden-import", "auto_poster.main",
        str(ROOT / "scripts" / "launcher.py"),
    )


if __name__ == "__main__":
    build_frontend()
    build_binary()
    name = "AutoPoster.exe" if sys.platform == "win32" else "AutoPoster"
    print(f"\nDone: {ROOT / 'dist' / name}")
