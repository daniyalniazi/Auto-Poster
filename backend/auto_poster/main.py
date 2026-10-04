"""Starts the local server and opens the app in the browser."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from auto_poster import database
from auto_poster.api.routes import router
from auto_poster.config import DEFAULT_PORT, data_dir
from auto_poster.security.local_guard import LocalGuard, LocalGuardMiddleware
from auto_poster.security.redact import setup_logging
from auto_poster.services import history, media, scheduler

log = logging.getLogger("auto_poster")

DEV_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def static_dir() -> Path | None:
    """Built frontend: bundled inside the package, or frontend/dist in a source checkout."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    for candidate in (base / "auto_poster" / "static", Path(__file__).parent / "static",
                      Path(__file__).resolve().parents[2] / "frontend" / "dist"):
        if (candidate / "index.html").exists():
            return candidate
    return None


def create_app(port: int = DEFAULT_PORT, dev: bool = False, run_scheduler: bool = True) -> FastAPI:
    database.migrate()
    history.mark_interrupted()
    scheduler.recover_after_restart()
    media.cleanup_orphans(keep=scheduler.referenced_image_ids())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(scheduler.run_forever()) if run_scheduler else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Auto Poster", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    guard = LocalGuard(port, extra_origins=DEV_FRONTEND_ORIGINS if dev else None)
    app.state.guard = guard
    app.state.port = port
    app.add_middleware(LocalGuardMiddleware, guard=guard)
    app.include_router(router)

    static = static_dir()
    if static:
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def index(path: str):
            file = (static / path).resolve()
            if path and file.is_file() and static.resolve() in file.parents:
                return FileResponse(file)
            return FileResponse(static / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return {"detail": "Frontend not built. Run: cd frontend && npm install && npm run build"}

    return app


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def run() -> None:
    parser = argparse.ArgumentParser(description="Auto Poster: write once, post everywhere.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AUTO_POSTER_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically.")
    parser.add_argument("--dev", action="store_true", help="Allow the Vite dev server (port 5173).")
    parser.add_argument("--debug", action="store_true", help="More detailed logs (secrets are still hidden).")
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    if not _port_free(args.port):
        log.error("Port %s is already in use. Is Auto Poster already running? "
                  "Close it, or start with --port <another number>.", args.port)
        sys.exit(1)

    import uvicorn

    app = create_app(args.port, dev=args.dev)
    url = f"http://127.0.0.1:{args.port}/"
    log.info("Auto Poster is running at %s (data folder: %s)", url, data_dir())
    log.info("Keep this window open while using the app, and for scheduled posts to be sent. "
             "Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", log_config=None)


if __name__ == "__main__":
    run()
