"""Secret storage: the OS keyring, or a user-only file when no keyring exists.

Secrets never go into SQLite. Every secret read or written here is registered
with the log redactor so it cannot leak into logs or error details.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Protocol

from auto_poster.config import data_dir
from auto_poster.security.redact import register_secret

log = logging.getLogger(__name__)

SERVICE_NAME = "auto-poster"


class CredentialStore(Protocol):
    kind: str  # "keyring" or "file"

    def get(self, platform: str, key: str) -> str | None: ...
    def set(self, platform: str, key: str, value: str) -> None: ...
    def delete(self, platform: str, key: str) -> None: ...


class KeyringStore:
    kind = "keyring"

    def __init__(self, backend):
        self._backend = backend

    @staticmethod
    def _name(platform: str, key: str) -> str:
        return f"{platform}:{key}"

    def get(self, platform: str, key: str) -> str | None:
        value = self._backend.get_password(SERVICE_NAME, self._name(platform, key))
        register_secret(value)
        return value

    def set(self, platform: str, key: str, value: str) -> None:
        register_secret(value)
        self._backend.set_password(SERVICE_NAME, self._name(platform, key), value)

    def delete(self, platform: str, key: str) -> None:
        try:
            self._backend.delete_password(SERVICE_NAME, self._name(platform, key))
        except Exception:  # keyring raises PasswordDeleteError when missing
            pass


class FileStore:
    """Fallback for systems without a keyring. File is readable only by the current user."""

    kind = "file"

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        with open(self._path, encoding="utf-8") as f:
            data = json.load(f)
        for value in data.values():
            register_secret(value)
        return data

    def _save(self, data: dict[str, str]) -> None:
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def get(self, platform: str, key: str) -> str | None:
        with self._lock:
            return self._load().get(f"{platform}:{key}")

    def set(self, platform: str, key: str, value: str) -> None:
        register_secret(value)
        with self._lock:
            data = self._load()
            data[f"{platform}:{key}"] = value
            self._save(data)

    def delete(self, platform: str, key: str) -> None:
        with self._lock:
            data = self._load()
            if data.pop(f"{platform}:{key}", None) is not None:
                self._save(data)


def _usable_keyring():
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        # A chainer with no real backends behaves like the fail backend.
        if getattr(backend, "backends", None) == []:
            return None
        if getattr(backend, "priority", 1) <= 0:
            return None
        return backend
    except Exception as exc:  # broken D-Bus, missing libraries, etc.
        log.warning("OS keyring unavailable: %s", type(exc).__name__)
        return None


_store: CredentialStore | None = None


def get_store() -> CredentialStore:
    global _store
    if _store is None:
        if os.environ.get("AUTO_POSTER_SECRET_STORE") == "file":
            backend = None
        else:
            backend = _usable_keyring()
        if backend is not None:
            _store = KeyringStore(backend)
        else:
            log.warning("No OS keyring found; storing secrets in a user-only file instead.")
            _store = FileStore(data_dir() / "secrets.json")
    return _store


def set_store(store: CredentialStore | None) -> None:
    """Used by tests to swap in an in-memory store."""
    global _store
    _store = store
