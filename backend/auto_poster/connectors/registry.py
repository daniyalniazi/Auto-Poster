"""The list of available platforms. To add a platform, import its connector and add it here."""

from __future__ import annotations

from auto_poster.connectors.base import Connector
from auto_poster.connectors.telegram import TelegramConnector

CONNECTORS: dict[str, Connector] = {
    connector.id: connector
    for connector in [
        TelegramConnector(),
    ]
}


def get_connector(platform_id: str) -> Connector | None:
    return CONNECTORS.get(platform_id)
