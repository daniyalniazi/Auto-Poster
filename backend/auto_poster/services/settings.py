"""Per-platform settings. Secret fields go to the credential store, the rest to SQLite."""

from __future__ import annotations

from auto_poster import database
from auto_poster.connectors.base import Config, Connector
from auto_poster.security.credentials import get_store
from auto_poster.security.redact import mask_for_display


def load_config(connector: Connector) -> Config:
    """Everything a connector needs, including secrets. Never send this to the UI."""
    config: Config = {}
    with database.session() as conn:
        rows = conn.execute(
            "SELECT key, value FROM platform_settings WHERE platform = ?", (connector.id,)
        ).fetchall()
    config.update({row["key"]: row["value"] for row in rows})
    store = get_store()
    for key in connector.secret_keys:
        value = store.get(connector.id, key)
        if value:
            config[key] = value
    return config


def public_settings(connector: Connector) -> dict[str, str]:
    """Settings safe to show in the UI: secrets are masked."""
    config = load_config(connector)
    return {
        key: (mask_for_display(value) if key in connector.secret_keys else value)
        for key, value in config.items()
        if key in {f.key for f in connector.settings_fields}
    }


def save_settings(connector: Connector, values: dict[str, str]) -> None:
    """Save the submitted fields. An empty secret field means 'keep the current value'."""
    store = get_store()
    known = {f.key: f for f in connector.settings_fields}
    with database.session() as conn:
        for key, value in values.items():
            if key not in known:
                continue
            value = (value or "").strip()
            if key in connector.secret_keys:
                if value:
                    store.set(connector.id, key, value)
            else:
                conn.execute(
                    "INSERT INTO platform_settings (platform, key, value) VALUES (?, ?, ?) "
                    "ON CONFLICT(platform, key) DO UPDATE SET value = excluded.value",
                    (connector.id, key, value),
                )


def save_internal(connector: Connector, values: dict[str, str], secret: bool) -> None:
    """For values a connector stores itself (e.g. OAuth tokens or account IDs)."""
    store = get_store()
    with database.session() as conn:
        for key, value in values.items():
            if secret:
                store.set(connector.id, key, value)
            else:
                conn.execute(
                    "INSERT INTO platform_settings (platform, key, value) VALUES (?, ?, ?) "
                    "ON CONFLICT(platform, key) DO UPDATE SET value = excluded.value",
                    (connector.id, key, value),
                )


def delete_settings(connector: Connector, secret_keys: set[str] | None = None) -> None:
    store = get_store()
    for key in secret_keys or connector.secret_keys:
        store.delete(connector.id, key)
    with database.session() as conn:
        conn.execute("DELETE FROM platform_settings WHERE platform = ?", (connector.id,))
