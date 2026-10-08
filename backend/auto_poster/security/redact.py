"""Keeps secrets out of logs, error details and anything shown in the UI."""

from __future__ import annotations

import logging
import re
import sys
import threading

MASK = "***"

# Secret-looking patterns that should never be printed even if not registered.
_PATTERNS = [
    re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot token
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:access_token|refresh_token|client_secret|password|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+"),
]

_known_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str | None) -> None:
    """Remember a secret value so it is masked wherever it appears."""
    if value and len(value) >= 4:
        with _lock:
            _known_secrets.add(value)


def redact(text: str | None) -> str | None:
    if not text:
        return text
    with _lock:
        secrets = sorted(_known_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, MASK)
    for pattern in _PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + MASK, text)
        else:
            text = pattern.sub(MASK, text)
    return text


def mask_for_display(value: str | None) -> str:
    """'123456:ABCDEF...wxyz' -> '••••••••wxyz'. Never returns the full value."""
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 12 else ""
    return "••••••••" + tail


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = None
        return True


def setup_logging(debug: bool = False, log_file=None) -> None:
    """Log to the console (when there is one) and, if given, to a rotating file. Both redacted."""
    from logging.handlers import RotatingFileHandler

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # the packaged Windows app has no console
        handlers.append(logging.StreamHandler())
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    for handler in handlers:
        handler.addFilter(RedactingFilter())
        handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    # httpx logs full request URLs at INFO. Telegram puts the bot token in the URL path,
    # so keep httpx quiet unless debugging (and even then the filter masks tokens).
    logging.getLogger("httpx").setLevel(logging.DEBUG if debug else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
