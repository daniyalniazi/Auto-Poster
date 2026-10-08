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

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from auto_poster import database, desktop
from auto_poster.api.routes import router
from auto_poster.config import DEFAULT_PORT, data_dir
from auto_poster.connectors.base import safe_oauth_callback
from auto_poster.connectors.registry import get_connector
from auto_poster.security.local_guard import LocalGuard, LocalGuardMiddleware
from auto_poster.security.redact import setup_logging
from auto_poster.services import history, media, media_usage, oauth, scheduler
from auto_poster.services.settings import load_config

log = logging.getLogger("auto_poster")

DEV_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def static_dir() -> Path | None:
    """Built frontend. In the packaged app it's bundled inside; in a source checkout the fresh
    frontend/dist wins over any older copy that scripts/build.py left in the package."""
    if hasattr(sys, "_MEIPASS"):
        candidates = [Path(sys._MEIPASS) / "auto_poster" / "static"]
    else:
        candidates = [Path(__file__).resolve().parents[2] / "frontend" / "dist", Path(__file__).parent / "static"]
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None


def create_app(port: int = DEFAULT_PORT, dev: bool = False, run_scheduler: bool = True) -> FastAPI:
    database.migrate()
    history.mark_interrupted()
    scheduler.recover_after_restart()
    media.cleanup_orphans(keep=media_usage.in_use_image_ids())

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

    @app.get("/oauth/{platform_id}/callback", include_in_schema=False)
    async def oauth_callback(platform_id: str, request: Request):
        connector = get_connector(platform_id)
        if connector is None:
            return HTMLResponse(oauth.result_page(False, "Unknown platform", "This sign-in link is not valid."), 404)
        result = await safe_oauth_callback(connector, dict(request.query_params), load_config(connector))
        title = f"{connector.display_name} connected" if result.ok else f"Could not connect {connector.display_name}"
        return HTMLResponse(oauth.result_page(result.ok, title, result.message))

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


def _is_auto_poster(url: str) -> bool:
    import httpx

    try:
        return "token" in httpx.get(url + "api/session", timeout=2).json()
    except Exception:
        return False


def _pause_if_double_clicked() -> None:
    """Keep the window open long enough to read the error when started by double-click."""
    if getattr(sys, "frozen", False) and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("Press Enter to close this window.")
        except EOFError:
            pass


def run() -> None:
    parser = argparse.ArgumentParser(description="Auto Poster: write once, post everywhere.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AUTO_POSTER_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically.")
    parser.add_argument("--dev", action="store_true", help="Allow the Vite dev server (port 5173).")
    parser.add_argument("--debug", action="store_true", help="More detailed logs (secrets are still hidden).")
    parser.add_argument("--background", action="store_true",
                        help="Start quietly (used when starting with the computer): no browser, tray icon only.")
    parser.add_argument("--tray", action="store_true", help="Show a system tray icon (default for the packaged app).")
    parser.add_argument("--no-tray", action="store_true", help="Don't show a system tray icon.")
    args = parser.parse_args()

    setup_logging(debug=args.debug, log_file=data_dir() / "logs" / "auto-poster.log")
    url = f"http://127.0.0.1:{args.port}/"
    if not _port_free(args.port):
        if _is_auto_poster(url):
            log.info("Auto Poster is already running. Opening it in your browser.")
            if not (args.no_browser or args.background):
                webbrowser.open(url)
            return
        log.error("Port %s is used by another program. Start Auto Poster with --port <another number>.", args.port)
        _pause_if_double_clicked()
        sys.exit(1)

    import uvicorn

    app = create_app(args.port, dev=args.dev)
    app.state.port_arg = args.port if args.port != DEFAULT_PORT else None
    log.info("Auto Poster is running at %s (data folder: %s)", url, data_dir())
    if not (args.no_browser or args.background):
        desktop.open_browser_later(url)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning",
                                           log_config=None))
    # Tray by default for the packaged app and background starts; a terminal run keeps Ctrl+C working.
    use_tray = not args.no_tray and (getattr(sys, "frozen", False) or args.background or args.tray)
    if not use_tray:
        log.info("Keep this window open while using the app. Press Ctrl+C to stop.")
        server.run()
        return

    # The tray icon needs the main thread, so the web server runs alongside it.
    server_thread = threading.Thread(target=server.run, name="web-server", daemon=True)
    server_thread.start()
    if desktop.run_tray(url, on_quit=lambda: setattr(server, "should_exit", True)):
        server_thread.join(timeout=10)
        return
    log.info("Running without a tray icon. Keep this window open while using the app. Press Ctrl+C to stop.")
    try:
        server_thread.join()
    except KeyboardInterrupt:
        server.should_exit = True
        server_thread.join(timeout=10)


if __name__ == "__main__":
    run()
