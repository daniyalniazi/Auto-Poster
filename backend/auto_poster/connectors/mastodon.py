"""Mastodon connector, using the official Mastodon REST API.

Docs: https://docs.joinmastodon.org/client/intro/
Endpoints: POST /api/v1/apps, GET /oauth/authorize, POST /oauth/token,
           GET /api/v1/accounts/verify_credentials, GET /api/v2/instance (fallback /api/v1/instance),
           POST /api/v2/media, GET /api/v1/media/:id, POST /api/v1/statuses

Every Mastodon server can have its own limits (characters, images, file size), so they are
read from the server's instance information when the user connects or tests the connection.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import httpx
import regex

from auto_poster.connectors.base import Config, Connector, Options
from auto_poster.models import (
    ActionResult,
    ActionSpec,
    ConnectionStatus,
    FieldOption,
    FieldSpec,
    PlatformError,
    PlatformLimits,
    Post,
    Comment,
    PostResult,
    PostStats,
    Problem,
    SetupGuide,
)
from auto_poster.services import oauth

SCOPES = "read:accounts read:statuses write:statuses write:media"  # read:statuses: post stats
APP_NAME = "Auto Poster"
APP_WEBSITE = "https://github.com/daniyalniazi/Auto-Poster"
MEDIA_POLL_SECONDS = 1.0
MEDIA_POLL_ATTEMPTS = 30

MIME_TO_FORMAT = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "image/gif": "GIF"}

# Mastodon's defaults (used until the server's own values are known).
DEFAULT_LIMITS = PlatformLimits(
    max_chars=500,
    count_method="mastodon",
    max_images=4,
    max_image_bytes=16 * 1024 * 1024,
    image_formats=["JPEG", "PNG", "WEBP", "GIF"],
    requires_text=False,
)
DEFAULT_URL_LENGTH = 23
DEFAULT_PIXEL_LIMIT = 33_177_600

URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
REMOTE_MENTION = re.compile(r"(@[a-zA-Z0-9_]+)@[a-zA-Z0-9.-]+[a-zA-Z0-9]")


def normalize_server(value: str) -> str:
    """'https://mastodon.social/', '@me@mastodon.social' or 'mastodon.social' -> 'mastodon.social'."""
    value = (value or "").strip()
    if value.startswith("@") and value.count("@") == 2:
        value = value.rsplit("@", 1)[1]
    if "://" not in value:
        value = "https://" + value
    return (urlparse(value).hostname or "").lower()


def html_to_text(content: str) -> str:
    """Mastodon sends post text as simple HTML; show it as plain text."""
    import html as html_lib

    text = re.sub(r"<br\s*/?>", "\n", content)
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def mastodon_length(text: str, url_length: int = DEFAULT_URL_LENGTH) -> int:
    """Count like Mastodon: links are always `url_length`, @user@server counts as @user."""
    countable = URL_PATTERN.sub("x" * url_length, text)
    countable = REMOTE_MENTION.sub(r"\1", countable)
    return len(regex.findall(r"\X", countable))


class MastodonConnector(Connector):
    id = "mastodon"
    display_name = "Mastodon"
    description = "Post to your account on any Mastodon server."
    limits = DEFAULT_LIMITS
    settings_fields = [
        FieldSpec(
            key="server",
            label="Server",
            required=True,
            placeholder="mastodon.social",
            help="The server your account is on — the part after the second @ in your username.",
        ),
        FieldSpec(
            key="access_token",
            label="Access token",
            kind="secret",
            required=True,
            help="Filled in for you when you click “Connect Mastodon”. Advanced: you can instead paste a token "
            "from Preferences → Development on your server (scopes: read:accounts write:statuses write:media).",
        ),
    ]
    post_fields = [
        FieldSpec(
            key="visibility",
            label="Who can see it",
            kind="select",
            default="public",
            options=[
                FieldOption(value="public", label="Public"),
                FieldOption(value="unlisted", label="Quiet public (not shown in public timelines)"),
                FieldOption(value="private", label="Followers only"),
                FieldOption(value="direct", label="Only people mentioned"),
            ],
        ),
        FieldSpec(
            key="spoiler_text",
            label="Content warning",
            placeholder="Leave empty for no warning",
            help="If filled in, people see this text first and must click to show the post.",
        ),
        FieldSpec(key="sensitive", label="Mark images as sensitive", kind="checkbox",
                  help="Images are blurred until someone clicks them."),
    ]
    actions = [
        ActionSpec(id="connect", label="Connect Mastodon",
                   help="Opens your Mastodon server so you can approve Auto Poster."),
    ]
    internal_secret_keys = {"oauth_app"}
    setup_guide = SetupGuide(
        steps=[
            "Type your server's address in Server (for example mastodon.social), then click “Connect Mastodon”.",
            "A new tab opens on your Mastodon server. Sign in if asked, then click “Authorize”.",
            "Come back to this tab. Mastodon now shows as connected.",
            "Click Test connection to check everything works.",
        ],
        docs_url="https://docs.joinmastodon.org/user/signup/",
        notes=[
            "Auto Poster only asks for permission to read your basic account details and to post. It can't "
            "read your messages or change your settings.",
            "You can remove Auto Poster's access any time in Mastodon: Preferences → Account → Authorized apps.",
        ],
    )

    # ---- per-server limits --------------------------------------------------------------

    def _instance(self, config: Config) -> dict:
        try:
            return json.loads(config.get("instance_config") or "{}")
        except ValueError:
            return {}

    def effective_limits(self, config: Config) -> PlatformLimits:
        info = self._instance(config)
        if not info:
            return self.limits
        formats = [MIME_TO_FORMAT[m] for m in info.get("mime_types", []) if m in MIME_TO_FORMAT]
        return self.limits.model_copy(update={
            "max_chars": info.get("max_characters") or self.limits.max_chars,
            "max_images": info.get("max_media_attachments") or self.limits.max_images,
            "max_image_bytes": info.get("image_size_limit") or self.limits.max_image_bytes,
            "image_formats": formats or self.limits.image_formats,
        })

    def count_text(self, text: str, limits: PlatformLimits) -> int:
        return mastodon_length(text, DEFAULT_URL_LENGTH)

    def validate(self, post: Post, options: Options, config: Config) -> list[Problem]:
        problems = super().validate(post, options, config)
        info = self._instance(config)
        limits = self.effective_limits(config)
        url_length = info.get("characters_reserved_per_url") or DEFAULT_URL_LENGTH
        spoiler = options.get("spoiler_text", "")
        # Mastodon counts the content warning together with the text.
        total = mastodon_length(post.text, url_length) + mastodon_length(spoiler, url_length)
        if spoiler and total > limits.max_chars:
            problems.append(self.problem(
                f"The text plus the content warning is {total} characters; this server allows {limits.max_chars}."))
        pixel_limit = info.get("image_matrix_limit") or DEFAULT_PIXEL_LIMIT
        for image in post.images:
            if image.width * image.height > pixel_limit:
                problems.append(self.problem(
                    f'"{image.filename}" has too many pixels for this server ({image.width}×{image.height}). '
                    "Use a smaller image."))
        if options.get("visibility") not in (None, "", "public", "unlisted", "private", "direct"):
            problems.append(self.problem("Choose who can see the post."))
        return problems

    async def _load_instance(self, client: httpx.AsyncClient, server: str) -> dict:
        response = await client.get(f"https://{server}/api/v2/instance")
        if response.status_code == 404:
            response = await client.get(f"https://{server}/api/v1/instance")
        if response.status_code != 200:
            raise self._error(response, "instance")
        data = response.json()
        conf = data.get("configuration", {})
        statuses, media = conf.get("statuses", {}), conf.get("media_attachments", {})
        info = {
            "max_characters": statuses.get("max_characters") or data.get("max_toot_chars"),
            "max_media_attachments": statuses.get("max_media_attachments"),
            "characters_reserved_per_url": statuses.get("characters_reserved_per_url"),
            "image_size_limit": media.get("image_size_limit"),
            "image_matrix_limit": media.get("image_matrix_limit"),
            "mime_types": [m for m in media.get("supported_mime_types", []) if m.startswith("image/")],
        }
        info = {k: v for k, v in info.items() if v}
        self.save_value("instance_config", json.dumps(info))
        return info

    # ---- browser sign-in ------------------------------------------------------------------

    async def run_action(self, action_id: str, config: Config, base_url: str) -> ActionResult:
        server = normalize_server(config.get("server", ""))
        if not server:
            return ActionResult(ok=False, message="Enter your Mastodon server first (for example mastodon.social).")
        state, pending = oauth.start(self.id, base_url, server=server)
        async with self.http() as client:
            app = await self._registered_app(client, config, server, pending.redirect_uri)
        query = urlencode({
            "response_type": "code",
            "client_id": app["client_id"],
            "redirect_uri": pending.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": pending.code_challenge,
            "code_challenge_method": "S256",
        })
        return ActionResult(
            ok=True,
            message=f"A new tab opened on {server}. Approve Auto Poster there, then come back here.",
            open_url=f"https://{server}/oauth/authorize?{query}",
        )

    async def _registered_app(self, client, config: Config, server: str, redirect: str) -> dict:
        """Register Auto Poster as an app on this server once, and reuse it."""
        try:
            app = json.loads(config.get("oauth_app") or "{}")
        except ValueError:
            app = {}
        if app.get("server") == server and app.get("redirect_uri") == redirect and app.get("client_id"):
            return app
        response = await client.post(f"https://{server}/api/v1/apps", data={
            "client_name": APP_NAME, "redirect_uris": redirect, "scopes": SCOPES, "website": APP_WEBSITE,
        })
        if response.status_code != 200:
            raise self._error(response, "apps")
        data = response.json()
        app = {"server": server, "redirect_uri": redirect,
               "client_id": data["client_id"], "client_secret": data["client_secret"]}
        self.save_secret("oauth_app", json.dumps(app))
        return app

    async def handle_oauth_callback(self, params: dict[str, str], config: Config) -> ActionResult:
        pending = oauth.finish(self.id, params.get("state"))
        if pending is None:
            return ActionResult(ok=False, message="This sign-in link has expired. Click “Connect Mastodon” again.")
        if params.get("error"):
            return ActionResult(ok=False, message="Access was not approved on Mastodon, so nothing was connected.")
        server = pending.extra["server"]
        async with self.http() as client:
            app = await self._registered_app(client, config, server, pending.redirect_uri)
            response = await client.post(f"https://{server}/oauth/token", data={
                "grant_type": "authorization_code",
                "code": params.get("code", ""),
                "client_id": app["client_id"],
                "client_secret": app["client_secret"],
                "redirect_uri": pending.redirect_uri,
                "code_verifier": pending.code_verifier,
                "scope": SCOPES,
            })
            if response.status_code != 200:
                raise self._error(response, "oauth/token")
            token = response.json()["access_token"]
            self.save_secret("access_token", token)
            self.save_value("server", server)
            account = await self._get(client, server, token, "/api/v1/accounts/verify_credentials")
            await self._load_instance(client, server)
        return ActionResult(ok=True, message=f"Connected as @{account.get('acct')}@{server}.")

    # ---- API plumbing --------------------------------------------------------------------

    async def _get(self, client, server: str, token: str, path: str) -> dict:
        response = await client.get(f"https://{server}{path}", headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            raise self._error(response, path)
        return response.json()

    def _error(self, response: httpx.Response, what: str) -> PlatformError:
        status = response.status_code
        try:
            body = response.json()
            message = body.get("error_description") or body.get("error") or ""
        except ValueError:
            message = response.text[:200]
        details = f"{what}: HTTP {status}, error={message!r}"
        lower = str(message).lower()

        if status == 401:
            return PlatformError(
                "invalid_credentials",
                "Mastodon did not accept Auto Poster's access. It may have been removed from your account. "
                "Open Settings → Mastodon and click “Connect Mastodon” again.",
                details,
            )
        if status == 403:
            if "scope" in lower:
                return PlatformError(
                    "missing_permission",
                    "The Mastodon connection doesn't have permission to post. Click “Connect Mastodon” again.",
                    details,
                )
            return PlatformError(
                "missing_permission",
                "Mastodon refused the request. Your account may be limited or suspended on this server.",
                details,
            )
        if status == 429:
            reset = response.headers.get("X-RateLimit-Reset")
            retry = None
            if reset:
                try:
                    reset_at = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                    retry = max(0, int((reset_at - datetime.now(timezone.utc)).total_seconds()))
                except ValueError:
                    pass
            return PlatformError("rate_limited",
                                 "Your Mastodon server is limiting requests right now. Try again later.",
                                 details, retry_after=retry)
        if status == 404:
            return PlatformError("not_found",
                                 "That doesn't look like a Mastodon server. Check the server address.", details)
        if status == 413 or (status == 422 and any(w in lower for w in ("file", "media", "image"))):
            return PlatformError("invalid_media", "Your Mastodon server could not accept one of the images.", details)
        if status == 422 and "character limit" in lower:
            return PlatformError("too_long", "The post is longer than your Mastodon server allows.", details)
        if status >= 500:
            return PlatformError("platform_unavailable",
                                 "Your Mastodon server is having problems right now. Try again later.", details)
        return PlatformError("validation_failed",
                             f"Mastodon rejected the post: {message or 'no reason given'}.", details)

    # ---- connector interface ---------------------------------------------------------------

    async def test_connection(self, config: Config) -> ConnectionStatus:
        server = normalize_server(config["server"])
        if not server:
            return ConnectionStatus(ok=False, error_code="not_configured", message="Enter your Mastodon server.")
        async with self.http() as client:
            account = await self._get(client, server, config["access_token"].strip(),
                                      "/api/v1/accounts/verify_credentials")
            info = await self._load_instance(client, server)
        name = f"@{account.get('acct')}@{server}"
        limit = info.get("max_characters", DEFAULT_LIMITS.max_chars)
        return ConnectionStatus(ok=True, account_name=name,
                                message=f"Connected as {name}. This server allows {limit} characters per post.")

    async def _upload(self, client, server: str, token: str, image) -> str:
        response = await client.post(
            f"https://{server}/api/v2/media",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (image.filename, image.read_bytes(), image.mime_type)},
            data={"description": image.alt_text} if image.alt_text else {},
        )
        if response.status_code not in (200, 202):
            raise self._error(response, "media")
        media = response.json()
        attempts = 0
        while media.get("url") is None and attempts < MEDIA_POLL_ATTEMPTS:  # still processing
            await asyncio.sleep(MEDIA_POLL_SECONDS)
            attempts += 1
            poll = await client.get(f"https://{server}/api/v1/media/{media['id']}",
                                    headers={"Authorization": f"Bearer {token}"})
            if poll.status_code == 200:
                media = poll.json()
            elif poll.status_code != 206:
                raise self._error(poll, "media")
        if media.get("url") is None:
            raise PlatformError("invalid_media", "Mastodon took too long to process an image. Try again.")
        return media["id"]

    can_delete = True
    supports_stats = True

    async def _read(self, client, server: str, token: str, path: str) -> dict | list:
        """GET with the token; if the token predates the stats permission, try without it
        (works for public and quiet-public posts)."""
        response = await client.get(f"https://{server}{path}", headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 403:
            response = await client.get(f"https://{server}{path}")
            if response.status_code in (401, 403, 404):
                raise PlatformError("missing_permission", "To see stats for this post, reconnect Mastodon in "
                                    "Settings (Auto Poster now asks for permission to read your posts).")
        if response.status_code == 404:
            raise PlatformError("not_found", "This post is no longer on Mastodon.")
        if response.status_code != 200:
            raise self._error(response, path)
        return response.json()

    async def fetch_stats(self, post_id: str, config: Config) -> PostStats:
        server = normalize_server(config["server"])
        async with self.http() as client:
            status = await self._read(client, server, config["access_token"].strip(), f"/api/v1/statuses/{post_id}")
        return PostStats(likes=status.get("favourites_count", 0), shares=status.get("reblogs_count", 0),
                         replies=status.get("replies_count", 0))

    async def fetch_comments(self, post_id: str, config: Config) -> list[Comment]:
        server = normalize_server(config["server"])
        async with self.http() as client:
            context = await self._read(client, server, config["access_token"].strip(),
                                       f"/api/v1/statuses/{post_id}/context")
        comments = [
            Comment(author=s.get("account", {}).get("display_name") or f"@{s.get('account', {}).get('acct', '')}",
                    text=html_to_text(s.get("content", "")), created_at=s.get("created_at"))
            for s in context.get("descendants", [])
            if s.get("in_reply_to_id") == post_id
        ]
        comments.sort(key=lambda c: c.created_at or "", reverse=True)
        return comments[:20]

    async def delete_post(self, post_id: str, config: Config) -> None:
        server = normalize_server(config["server"])
        async with self.http() as client:
            response = await client.delete(f"https://{server}/api/v1/statuses/{post_id}",
                                           headers={"Authorization": f"Bearer {config['access_token'].strip()}"})
        if response.status_code not in (200, 404):  # 404: already deleted
            raise self._error(response, "delete status")

    async def post(self, post: Post, options: Options, config: Config) -> PostResult:
        server = normalize_server(config["server"])
        token = config["access_token"].strip()
        async with self.http() as client:
            media_ids = [await self._upload(client, server, token, image) for image in post.images]
            data: dict = {"status": post.text, "visibility": options.get("visibility") or "public"}
            if media_ids:
                data["media_ids"] = media_ids
            if options.get("spoiler_text"):
                data["spoiler_text"] = options["spoiler_text"]
            if options.get("sensitive") == "true":
                data["sensitive"] = True
            response = await client.post(
                f"https://{server}/api/v1/statuses",
                json=data,
                # Mastodon ignores a repeated request with the same key for an hour: no double posts.
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
            )
        if response.status_code != 200:
            raise self._error(response, "statuses")
        status = response.json()
        return PostResult(platform=self.id, success=True, post_id=str(status["id"]),
                          post_url=status.get("url"), message="Posted to Mastodon.")
