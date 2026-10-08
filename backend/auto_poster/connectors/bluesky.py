"""Bluesky connector, using the AT Protocol XRPC API with an app password.

Docs:      https://docs.bsky.app/docs/get-started
Lexicons:  https://github.com/bluesky-social/atproto/tree/main/lexicons
Endpoints: com.atproto.server.createSession / refreshSession, com.atproto.repo.uploadBlob,
           com.atproto.repo.createRecord (app.bsky.feed.post), com.atproto.identity.resolveHandle

Authentication: app passwords. Bluesky still supports them but plans to move apps to OAuth.
Production OAuth requires a publicly hosted client-metadata file, which conflicts with
"no project server", so v1 uses app passwords. All auth code is in _session().

createSession is rate limited (30 per 5 minutes, 300 per day), so the refresh token is
stored in the credential store and reused instead of logging in for every post.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import regex

from auto_poster.connectors.base import Config, Connector, Options
from auto_poster.models import (
    ConnectionStatus,
    FieldSpec,
    PlatformError,
    PlatformLimits,
    Post,
    PostResult,
    Problem,
    SetupGuide,
)
from auto_poster.services.media import MediaError, fit_to_size

DEFAULT_SERVICE = "https://bsky.social"
MAX_TEXT_BYTES = 3000  # app.bsky.feed.post: text maxLength (UTF-8 bytes)
MAX_BLOB_BYTES = 2_000_000  # app.bsky.embed.images: image maxSize

LIMITS = PlatformLimits(
    max_chars=300,  # maxGraphemes in app.bsky.feed.post
    count_method="graphemes",
    max_images=4,
    # Larger images are compressed to fit automatically, so the upload cap is generous.
    max_image_bytes=50 * 1024 * 1024,
    image_formats=["JPEG", "PNG", "WEBP", "GIF"],
    requires_text=False,
)

APP_PASSWORD_PATTERN = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$")

# Rich text detection, based on the patterns in Bluesky's guide to links, mentions and rich text.
# Only full http(s) URLs become links; bare domains stay plain text to avoid false positives.
URL_PATTERN = regex.compile(r"(?:^|\s|\()(https?://\S+)", regex.IGNORECASE)
MENTION_PATTERN = regex.compile(
    r"(?:^|\s|\()(@(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
)
TAG_PATTERN = regex.compile(r"(?:^|\s)([#＃]((?!️)[^\s­⁠ ​‌‍⃢]*[^\d\s\p{P}­⁠ ​‌‍⃢]+[^\s­⁠ ​‌‍⃢]*)?)")
TRAILING_PUNCTUATION = ".,;:!?)\"'"


@dataclass
class Session:
    access_jwt: str
    did: str
    handle: str
    pds: str


def _byte_index(text: str, char_index: int) -> int:
    return len(text[:char_index].encode("utf-8"))


def find_links(text: str) -> list[tuple[int, int, str]]:
    """(byte_start, byte_end, uri) for http(s) URLs in the text."""
    links = []
    for match in URL_PATTERN.finditer(text):
        uri = match.group(1)
        start, end = match.start(1), match.end(1)
        while uri and uri[-1] in TRAILING_PUNCTUATION:
            if uri[-1] == ")" and uri.count("(") >= uri.count(")"):
                break
            uri, end = uri[:-1], end - 1
        links.append((_byte_index(text, start), _byte_index(text, end), uri))
    return links


def find_mentions(text: str) -> list[tuple[int, int, str]]:
    """(byte_start, byte_end, handle-without-@)."""
    return [
        (_byte_index(text, m.start(1)), _byte_index(text, m.end(1)), m.group(1)[1:])
        for m in MENTION_PATTERN.finditer(text)
    ]


def find_tags(text: str) -> list[tuple[int, int, str]]:
    """(byte_start, byte_end, tag-without-#)."""
    tags = []
    for match in TAG_PATTERN.finditer(text):
        tag = (match.group(2) or "").rstrip(TRAILING_PUNCTUATION)
        if not tag or len(regex.findall(r"\X", tag)) > 64:
            continue
        start = match.start(1)
        end = start + 1 + len(tag)
        tags.append((_byte_index(text, start), _byte_index(text, end), tag))
    return tags


class BlueskyConnector(Connector):
    id = "bluesky"
    display_name = "Bluesky"
    description = "Post to your Bluesky account using an app password."
    limits = LIMITS
    settings_fields = [
        FieldSpec(
            key="handle",
            label="Handle",
            required=True,
            placeholder="yourname.bsky.social",
            help="Your Bluesky username, without the @.",
        ),
        FieldSpec(
            key="app_password",
            label="App password",
            kind="secret",
            required=True,
            placeholder="xxxx-xxxx-xxxx-xxxx",
            help="Create one in Bluesky: Settings → Privacy and security → App passwords. "
            "Don't use your main password.",
        ),
        FieldSpec(
            key="service",
            label="Server",
            placeholder=DEFAULT_SERVICE,
            help="Leave empty unless your account is hosted on your own server (PDS).",
        ),
    ]
    post_fields = []
    internal_secret_keys = {"session"}
    setup_guide = SetupGuide(
        steps=[
            "Open Bluesky (the app or bsky.app) and sign in.",
            "Go to Settings → Privacy and security → App passwords.",
            "Click “Add App Password”, give it a name like “Auto Poster”, and create it.",
            "Copy the password it shows (it looks like xxxx-xxxx-xxxx-xxxx).",
            "Enter your handle (for example yourname.bsky.social) and paste the app password below.",
            "Click Save, then Test connection.",
        ],
        docs_url="https://bsky.app/settings/app-passwords",
        notes=[
            "An app password can only post and read; it can't change your password or delete your account. "
            "You can revoke it in Bluesky at any time.",
            "Links, @mentions and #hashtags in your text become clickable on Bluesky. Link preview cards "
            "are not created.",
        ],
    )

    def __init__(self):
        self._sessions: dict[str, Session] = {}  # in-memory access tokens, keyed by handle

    # ---- validation ----------------------------------------------------------------------

    def validate(self, post: Post, options: Options, config: Config) -> list[Problem]:
        problems = super().validate(post, options, config)
        size = len(post.text.encode("utf-8"))
        if size > MAX_TEXT_BYTES:
            problems.append(self.problem(f"The text is too large for Bluesky ({size} bytes; the limit is "
                                         f"{MAX_TEXT_BYTES}). Shorten it."))
        for image in post.images:
            if image.size_bytes > MAX_BLOB_BYTES:
                problems.append(self.problem(
                    f'"{image.filename}" is larger than Bluesky\'s 2 MB limit, so it will be compressed to fit.',
                    level="warning",
                ))
        return problems

    # ---- sessions -----------------------------------------------------------------------

    @staticmethod
    def _handle(config: Config) -> str:
        return config["handle"].strip().lstrip("@").lower()

    @staticmethod
    def _service(config: Config) -> str:
        return (config.get("service") or DEFAULT_SERVICE).strip().rstrip("/")

    def _remember(self, data: dict, fallback_pds: str, config: Config) -> Session:
        pds = fallback_pds
        for service in (data.get("didDoc") or {}).get("service", []):
            if service.get("id", "").endswith("#atproto_pds") and service.get("serviceEndpoint"):
                pds = service["serviceEndpoint"].rstrip("/")
        session = Session(access_jwt=data["accessJwt"], did=data["did"], handle=data["handle"], pds=pds)
        self._sessions[data["handle"].lower()] = session
        # Refresh tokens rotate: keep the newest one, both stored and in this request's config.
        config["session"] = json.dumps({"handle": data["handle"].lower(), "refresh": data["refreshJwt"], "pds": pds})
        self.save_secret("session", config["session"])
        return session

    async def _session(self, client: httpx.AsyncClient, config: Config, fresh: bool = False) -> Session:
        handle = self._handle(config)
        if not fresh and handle in self._sessions:
            return self._sessions[handle]
        self._sessions.pop(handle, None)

        stored = json.loads(config.get("session") or "{}")
        if stored.get("handle") == handle and stored.get("refresh"):
            response = await client.post(
                f"{stored['pds']}/xrpc/com.atproto.server.refreshSession",
                headers={"Authorization": f"Bearer {stored['refresh']}"},
            )
            if response.status_code == 200:
                return self._remember(response.json(), stored["pds"], config)
            # Refresh token expired or revoked: fall back to a normal login below.

        service = self._service(config)
        response = await client.post(
            f"{service}/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": config["app_password"].strip()},
        )
        if response.status_code != 200:
            raise self._error(response, "createSession")
        return self._remember(response.json(), service, config)

    async def _xrpc(self, client, config, method: str, path: str, headers: dict | None = None, **kwargs) -> dict:
        """Authenticated request; refreshes the session once if the access token expired."""
        for attempt in range(2):
            session = await self._session(client, config, fresh=attempt > 0)
            response = await client.request(
                method, f"{session.pds}/xrpc/{path}",
                headers={**(headers or {}), "Authorization": f"Bearer {session.access_jwt}"}, **kwargs,
            )
            if response.status_code in (400, 401) and _error_name(response) in ("ExpiredToken", "InvalidToken"):
                continue
            if response.status_code != 200:
                raise self._error(response, path)
            return response.json()
        raise PlatformError("expired_credentials", "Bluesky signed Auto Poster out. Test the connection again.")

    def _error(self, response: httpx.Response, method: str) -> PlatformError:
        name = _error_name(response)
        try:
            message = response.json().get("message", "")
        except ValueError:
            message = response.text[:200]
        details = f"{method}: HTTP {response.status_code}, error={name!r}, message={message!r}"
        status = response.status_code

        if name == "AuthFactorTokenRequired":
            return PlatformError(
                "invalid_credentials",
                "Bluesky asked for a sign-in code, which means a normal password was used. Create an app "
                "password in Bluesky (Settings → Privacy and security → App passwords) and use that instead.",
                details,
            )
        if name == "AccountTakedown":
            return PlatformError("missing_permission", "Bluesky says this account has been suspended.", details)
        if status == 429 or name == "RateLimitExceeded":
            reset = response.headers.get("ratelimit-reset")
            retry = max(0, int(reset) - int(time.time())) if reset and reset.isdigit() else None
            return PlatformError(
                "rate_limited",
                "Bluesky is limiting requests from this account right now. Try again later.",
                details,
                retry_after=retry,
            )
        if status == 401 or name in ("AuthenticationRequired", "InvalidToken", "ExpiredToken"):
            return PlatformError(
                "invalid_credentials",
                "Bluesky did not accept the handle or app password. Check both in Settings → Bluesky. "
                "If you deleted the app password in Bluesky, create a new one.",
                details,
            )
        if name == "BlobTooLarge" or "blob" in message.lower():
            return PlatformError("invalid_media", "Bluesky could not accept one of the images.", details)
        if status >= 500:
            return PlatformError(
                "platform_unavailable", "Bluesky is having problems right now. Try again in a few minutes.", details
            )
        return PlatformError(
            "validation_failed", f"Bluesky rejected the post: {message or name or 'no reason given'}.", details
        )

    # ---- connector interface -----------------------------------------------------------------

    async def test_connection(self, config: Config) -> ConnectionStatus:
        warning = ""
        if not APP_PASSWORD_PATTERN.match(config["app_password"].strip()):
            warning = (" Note: this doesn't look like an app password (xxxx-xxxx-xxxx-xxxx). Using an app password "
                       "is safer than your main password.")
        async with self.http() as client:
            session = await self._session(client, config, fresh=True)
        return ConnectionStatus(
            ok=True, account_name=f"@{session.handle}", message=f"Connected as @{session.handle}.{warning}"
        )

    async def _facets(self, client: httpx.AsyncClient, session: Session, text: str) -> list[dict]:
        facets = []
        for start, end, uri in find_links(text):
            facets.append({"index": {"byteStart": start, "byteEnd": end},
                           "features": [{"$type": "app.bsky.richtext.facet#link", "uri": uri}]})
        for start, end, handle in find_mentions(text):
            response = await client.get(f"{session.pds}/xrpc/com.atproto.identity.resolveHandle",
                                        params={"handle": handle})
            if response.status_code == 200 and response.json().get("did"):
                facets.append({"index": {"byteStart": start, "byteEnd": end},
                               "features": [{"$type": "app.bsky.richtext.facet#mention",
                                             "did": response.json()["did"]}]})
        for start, end, tag in find_tags(text):
            facets.append({"index": {"byteStart": start, "byteEnd": end},
                           "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}]})
        return facets

    can_delete = True

    async def delete_post(self, post_id: str, config: Config) -> None:
        # post_id is the at:// URI: at://<did>/app.bsky.feed.post/<rkey>
        repo, collection, rkey = post_id.removeprefix("at://").split("/", 2)
        async with self.http() as client:
            await self._xrpc(client, config, "POST", "com.atproto.repo.deleteRecord",
                             json={"repo": repo, "collection": collection, "rkey": rkey})

    async def post(self, post: Post, options: Options, config: Config) -> PostResult:
        async with self.http() as client:
            session = await self._session(client, config)
            record: dict = {
                "$type": "app.bsky.feed.post",
                "text": post.text,
                "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            facets = await self._facets(client, session, post.text)
            if facets:
                record["facets"] = facets

            if post.images:
                embedded = []
                for image in post.images:
                    try:
                        data, mime = fit_to_size(image, MAX_BLOB_BYTES)
                    except MediaError as exc:
                        raise PlatformError("invalid_media", str(exc))
                    uploaded = await self._xrpc(
                        client, config, "POST", "com.atproto.repo.uploadBlob",
                        content=data, headers={"Content-Type": mime},
                    )
                    embedded.append({
                        "alt": image.alt_text or "",
                        "image": uploaded["blob"],
                        "aspectRatio": {"width": image.width, "height": image.height},
                    })
                record["embed"] = {"$type": "app.bsky.embed.images", "images": embedded}

            session = await self._session(client, config)
            created = await self._xrpc(
                client, config, "POST", "com.atproto.repo.createRecord",
                json={"repo": session.did, "collection": "app.bsky.feed.post", "record": record},
            )

        rkey = created["uri"].rsplit("/", 1)[-1]
        return PostResult(
            platform=self.id,
            success=True,
            post_id=created["uri"],
            post_url=f"https://bsky.app/profile/{session.handle}/post/{rkey}",
            message="Posted to Bluesky.",
        )


def _error_name(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", ""))
    except ValueError:
        return ""

