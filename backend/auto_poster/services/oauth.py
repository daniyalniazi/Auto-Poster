"""Shared helpers for "Connect <platform>" browser sign-in (OAuth 2.0 authorization code flow).

The platform redirects the browser back to http://127.0.0.1:<port>/oauth/<platform>/callback.
A random, single-use `state` value ties the callback to the sign-in the user started in the
app, so another website can't complete a sign-in on the user's behalf.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import time
from dataclasses import dataclass, field

STATE_LIFETIME_SECONDS = 15 * 60


@dataclass
class PendingSignIn:
    platform: str
    redirect_uri: str
    created: float = field(default_factory=time.time)
    code_verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    extra: dict = field(default_factory=dict)

    @property
    def code_challenge(self) -> str:
        digest = hashlib.sha256(self.code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


_pending: dict[str, PendingSignIn] = {}


def redirect_uri(base_url: str, platform: str, host: str | None = None) -> str:
    """The local callback address. Some platforms (LinkedIn, Facebook) only accept "localhost"
    for http:// redirects, so the host can be swapped; the server accepts both."""
    if host:
        base_url = base_url.replace("127.0.0.1", host)
    return f"{base_url}/oauth/{platform}/callback"


def start(platform: str, base_url: str, host: str | None = None, **extra) -> tuple[str, PendingSignIn]:
    """Create a single-use state value. Returns (state, pending sign-in)."""
    _expire()
    state = secrets.token_urlsafe(24)
    pending = PendingSignIn(platform=platform, redirect_uri=redirect_uri(base_url, platform, host), extra=extra)
    _pending[state] = pending
    return state, pending


def finish(platform: str, state: str | None) -> PendingSignIn | None:
    """Look up and consume the state. None if unknown, expired or for another platform."""
    _expire()
    pending = _pending.pop(state or "", None)
    if pending is None or pending.platform != platform:
        return None
    return pending


def _expire() -> None:
    cutoff = time.time() - STATE_LIFETIME_SECONDS
    for state in [s for s, p in _pending.items() if p.created < cutoff]:
        _pending.pop(state, None)


def result_page(ok: bool, title: str, message: str) -> str:
    color = "#1d7a46" if ok else "#b3261e"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auto Poster</title>
<style>
  body {{ font: 16px/1.5 system-ui, sans-serif; background: #f6f7f9; color: #1c2330; margin: 0; }}
  main {{ max-width: 520px; margin: 12vh auto; padding: 24px; background: #fff; border-radius: 12px;
          border: 1px solid #dde1e7; }}
  h1 {{ font-size: 20px; color: {color}; margin-top: 0; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #14171c; color: #e7eaf0; }} main {{ background: #1c2027; border-color: #333a45; }}
  }}
</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<p>You can close this tab and go back to Auto Poster.</p></main></body></html>"""
