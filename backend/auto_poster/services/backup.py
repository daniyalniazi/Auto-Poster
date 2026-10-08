"""Backup and restore: one .zip with the database and stored images.

Secrets (passwords, tokens) live in the OS keyring, never in the database, so a backup
contains none. After restoring on another computer, platforms are reconnected in Settings.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from auto_poster import __version__, database
from auto_poster.config import media_dir
from auto_poster.services import media

FORMAT = 1
MANIFEST = "manifest.json"
DB_NAME = "auto-poster.db"
MEDIA_NAME = re.compile(r"^media/[0-9a-f]{32}(\.json)?$")
MAX_UNCOMPRESSED = 4 * 1024**3  # refuse suspiciously large archives


class BackupError(ValueError):
    pass


def export_to(path: Path) -> None:
    """Write a backup zip to `path`."""
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / DB_NAME
        src, dst = database.connect(), sqlite3.connect(db_copy)
        try:
            src.backup(dst)  # consistent copy even while the app is running
        finally:
            src.close()
            dst.close()
        manifest = {"app": "auto-poster", "format": FORMAT, "app_version": __version__,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
            zf.write(db_copy, DB_NAME)
            for file in sorted(media_dir().iterdir()):
                if file.is_file() and MEDIA_NAME.match(f"media/{file.name}"):
                    zf.write(file, f"media/{file.name}")


def _check(zf: zipfile.ZipFile) -> None:
    names = zf.namelist()
    if MANIFEST not in names or DB_NAME not in names:
        raise BackupError("This file isn't an Auto Poster backup.")
    for name in names:
        if name not in (MANIFEST, DB_NAME) and not MEDIA_NAME.match(name):
            raise BackupError("This backup contains unexpected files, so it wasn't restored.")
    if sum(info.file_size for info in zf.infolist()) > MAX_UNCOMPRESSED:
        raise BackupError("This backup is too large to restore.")
    try:
        manifest = json.loads(zf.read(MANIFEST))
    except ValueError:
        raise BackupError("This backup is damaged (its description can't be read).") from None
    if manifest.get("app") != "auto-poster":
        raise BackupError("This file isn't an Auto Poster backup.")
    if int(manifest.get("format", 0)) > FORMAT:
        raise BackupError("This backup was made by a newer version of Auto Poster. Update Auto Poster first.")


def restore_from(data: bytes) -> dict:
    """Replace history, drafts, scheduled posts, reminders, settings and images with the backup's."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BackupError("This file isn't an Auto Poster backup.") from None
    with zf, tempfile.TemporaryDirectory() as tmp:
        _check(zf)
        db_path = Path(tmp) / DB_NAME
        db_path.write_bytes(zf.read(DB_NAME))
        src = sqlite3.connect(db_path)
        try:
            try:
                version = src.execute("PRAGMA user_version").fetchone()[0]
                src.execute("SELECT count(*) FROM posts").fetchone()
            except sqlite3.DatabaseError:
                raise BackupError("This backup is damaged (its database can't be read).") from None
            if version > len(database.MIGRATIONS):
                raise BackupError("This backup was made by a newer version of Auto Poster. Update Auto Poster first.")

            # Everything checked: now replace the current data.
            dst = database.connect()
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        database.migrate()  # bring an older backup up to date

        folder = media_dir()
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        images = 0
        for name in zf.namelist():
            if name.startswith("media/"):
                (folder / name.split("/", 1)[1]).write_bytes(zf.read(name))
                images += not name.endswith(".json")

    from auto_poster.services import scheduler

    scheduler.recover_after_restart()  # posts that were "sending" in the backup aren't resent
    with database.session() as conn:
        posts = conn.execute("SELECT count(*) FROM posts").fetchone()[0]
    return {"posts": posts, "images": images}


def remove_old_images(days: int) -> dict:
    """Free space: forget images of published posts older than `days` (drafts, scheduled posts
    and reminders keep theirs)."""
    from auto_poster.services import media_usage

    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    candidates: set[str] = set()
    with database.session() as conn:
        rows = conn.execute("SELECT id, created_at, compose FROM posts WHERE compose IS NOT NULL").fetchall()
        for row in rows:
            if datetime.fromisoformat(row["created_at"]).timestamp() >= cutoff:
                continue
            compose = json.loads(row["compose"])
            if compose.get("image_ids"):
                candidates.update(compose["image_ids"])
                compose["image_ids"], compose["alt_texts"] = [], {}
                conn.execute("UPDATE posts SET compose = ? WHERE id = ?", (json.dumps(compose), row["id"]))
    before = media.total_size()
    media_usage.delete_unused(candidates)
    return {"freed_bytes": before - media.total_size()}
