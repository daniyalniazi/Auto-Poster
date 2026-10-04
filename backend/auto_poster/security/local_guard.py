"""Stops other websites from talking to the local server.

A page on any website could try to send requests to http://127.0.0.1:<port>.
We block that with three checks on every /api request:

1. Host header must be our own loopback address (blocks DNS rebinding).
2. Origin header, if the browser sends one, must be our own origin.
3. A per-launch session token must be sent in the X-Auto-Poster-Token header.
   Custom headers also force a CORS preflight, which we never approve.
"""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

TOKEN_HEADER = "x-auto-poster-token"
SESSION_PATH = "/api/session"


class LocalGuard:
    def __init__(self, port: int, extra_origins: list[str] | None = None):
        self.token = secrets.token_urlsafe(32)
        hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
        self.allowed_hosts = set(hosts)
        self.allowed_origins = {f"http://{h}" for h in hosts}
        for origin in extra_origins or []:
            self.allowed_origins.add(origin)
            self.allowed_hosts.add(origin.split("://", 1)[1])

    def check(self, request: Request) -> str | None:
        """Returns a reason string if the request must be rejected."""
        host = request.headers.get("host", "")
        if host not in self.allowed_hosts:
            return "unexpected host"
        origin = request.headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            return "unexpected origin"
        if request.url.path == SESSION_PATH:
            # Any web page can *send* this request, but only same-origin pages can read
            # the response, and the Host/Origin checks above already apply.
            return None
        supplied = request.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(supplied, self.token):
            return "missing or wrong session token"
        return None


class LocalGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, guard: LocalGuard):
        super().__init__(app)
        self.guard = guard

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            reason = self.guard.check(request)
            if reason:
                return JSONResponse({"detail": f"Request blocked: {reason}."}, status_code=403)
        elif request.headers.get("host", "") not in self.guard.allowed_hosts:
            return JSONResponse({"detail": "Request blocked: unexpected host."}, status_code=403)
        return await call_next(request)
