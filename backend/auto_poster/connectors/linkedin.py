"""LinkedIn connector: posts to the user's personal profile (not company pages).

Docs:
  Sign-in:  https://learn.microsoft.com/linkedin/shared/authentication/authorization-code-flow
  Profile:  https://learn.microsoft.com/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2
  Posts:    https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api
  Images:   https://learn.microsoft.com/linkedin/marketing/community-management/shares/images-api
  Text:     https://learn.microsoft.com/linkedin/marketing/community-management/shares/little-text-format

There is no shared "Auto Poster" LinkedIn app: LinkedIn requires a client secret, which can't be
kept secret in open-source code without a project server. Each user creates their own free app
with the "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect" products.

Access tokens last 60 days. LinkedIn only gives refresh tokens to approved partners, so users
reconnect about every two months (one click if they're still signed in to LinkedIn).
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import quote, urlencode

import httpx

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
    PostResult,
    Problem,
    SetupGuide,
)
from auto_poster.services import oauth

# LinkedIn versions its API monthly (YYYYMM); each version is supported for about a year.
# Check https://learn.microsoft.com/linkedin/marketing/versioning when updating.
LINKEDIN_VERSION = "202609"
API_BASE = "https://api.linkedin.com"
AUTH_BASE = "https://www.linkedin.com/oauth/v2"
SCOPES = "openid profile w_member_social"
WARN_BEFORE_EXPIRY_SECONDS = 7 * 24 * 3600
MAX_IMAGE_PIXELS = 36_152_320  # "Images with less than 36,152,320 pixels"

LIMITS = PlatformLimits(
    max_chars=3000,
    max_images=20,  # multiImage posts: 2-20 images
    image_formats=["JPEG", "PNG", "GIF"],
    requires_text=False,
)

# Characters with special meaning in LinkedIn's "little" text format. They must be escaped
# with a backslash or LinkedIn may cut the post short or change it.
RESERVED = set("|{}@[]()<>#\\*_~")
HASHTAG = re.compile(r"(?:(?<=\s)|^)#(\w+)")


def to_little_text(text: str) -> str:
    """Escape text for the Posts API `commentary` field, keeping #hashtags as hashtags."""
    keep = {m.start() for m in HASHTAG.finditer(text)}  # '#' that start a real hashtag
    return "".join(
        "\\" + ch if ch in RESERVED and i not in keep else ch for i, ch in enumerate(text)
    )


class LinkedInConnector(Connector):
    id = "linkedin"
    display_name = "LinkedIn"
    description = "Post to your personal LinkedIn profile (company pages are not supported)."
    limits = LIMITS
    max_hashtags = 5  # LinkedIn recommends 3-5 hashtags
    settings_fields = [
        FieldSpec(key="client_id", label="Client ID", required=True,
                  help="From your LinkedIn app's Auth tab."),
        FieldSpec(key="client_secret", label="Primary Client Secret", kind="secret", required=True,
                  help="From your LinkedIn app's Auth tab."),
    ]
    post_fields = [
        FieldSpec(
            key="visibility",
            label="Who can see it",
            kind="select",
            default="PUBLIC",
            options=[
                FieldOption(value="PUBLIC", label="Anyone"),
                FieldOption(value="CONNECTIONS", label="Connections only"),
            ],
        ),
    ]
    actions = [
        ActionSpec(id="connect", label="Connect LinkedIn",
                   help="Opens LinkedIn so you can approve Auto Poster."),
    ]
    internal_secret_keys = {"access_token"}
    setup_guide = SetupGuide(
        steps=[
            "LinkedIn requires every app that posts to have its own developer app. It's free and takes about "
            "10 minutes, once.",
            "Go to linkedin.com/developers/apps and click “Create app”. Give it a name (e.g. “My Auto Poster”). "
            "LinkedIn asks for a LinkedIn Page to link the app to — choose one of your pages, or create a simple "
            "page first. Nothing is posted to that page.",
            "In the app's “Products” tab, request “Share on LinkedIn” and “Sign In with LinkedIn using OpenID "
            "Connect”. Both are approved instantly.",
            "In the “Auth” tab, under “Authorized redirect URLs for your app”, add: "
            "http://localhost:{port}/oauth/linkedin/callback",
            "Copy the Client ID and Primary Client Secret from the Auth tab into the fields below and click Save.",
            "Click “Connect LinkedIn” and approve the request in the tab that opens.",
        ],
        docs_url="https://www.linkedin.com/developers/apps",
        notes=[
            "LinkedIn connections last 60 days. Auto Poster warns you a week before; reconnecting takes one click.",
            "LinkedIn allows about 150 posting actions per member per day.",
        ],
    )

    # ---- connection state ----------------------------------------------------------------

    def is_configured(self, config: Config) -> bool:
        return bool(config.get("client_id") and config.get("client_secret")
                    and config.get("access_token") and config.get("person_id"))

    @staticmethod
    def _expires_at(config: Config) -> float | None:
        try:
            return float(config.get("token_expires_at", ""))
        except ValueError:
            return None

    def validate(self, post: Post, options: Options, config: Config) -> list[Problem]:
        problems = super().validate(post, options, config)
        expires = self._expires_at(config)
        if expires is not None:
            remaining = expires - time.time()
            day = datetime.fromtimestamp(expires).strftime("%d %B %Y")
            if remaining <= 0:
                problems.append(self.problem(
                    "Your LinkedIn connection has expired. Open Settings → LinkedIn and click “Connect LinkedIn”."))
            elif remaining < WARN_BEFORE_EXPIRY_SECONDS:
                problems.append(self.problem(
                    f"Your LinkedIn connection expires on {day}. Reconnect in Settings → LinkedIn to keep "
                    "posting.", level="warning"))
        for image in post.images:
            if image.width * image.height >= MAX_IMAGE_PIXELS:
                problems.append(self.problem(
                    f'"{image.filename}" has too many pixels for LinkedIn ({image.width}×{image.height}).'))
        if options.get("visibility") not in (None, "", "PUBLIC", "CONNECTIONS"):
            problems.append(self.problem("Choose who can see the post."))
        return problems

    # ---- browser sign-in -----------------------------------------------------------------

    async def run_action(self, action_id: str, config: Config, base_url: str) -> ActionResult:
        if not (config.get("client_id") and config.get("client_secret")):
            return ActionResult(ok=False, message="Enter your app's Client ID and Client Secret and click Save first.")
        state, pending = oauth.start(self.id, base_url, host="localhost")
        query = urlencode({
            "response_type": "code",
            "client_id": config["client_id"].strip(),
            "redirect_uri": pending.redirect_uri,
            "state": state,
            "scope": SCOPES,
        })
        return ActionResult(ok=True, open_url=f"{AUTH_BASE}/authorization?{query}",
                            message="A new tab opened on LinkedIn. Approve Auto Poster there, then come back here.")

    async def handle_oauth_callback(self, params: dict[str, str], config: Config) -> ActionResult:
        pending = oauth.finish(self.id, params.get("state"))
        if pending is None:
            return ActionResult(ok=False, message="This sign-in link has expired. Click “Connect LinkedIn” again.")
        if params.get("error"):
            if params["error"] in ("user_cancelled_authorize", "user_cancelled_login"):
                return ActionResult(ok=False, message="Access was not approved on LinkedIn, so nothing was connected.")
            return ActionResult(ok=False, message=_auth_error_message(params.get("error_description", ""),
                                                                      pending.redirect_uri))
        async with self.http() as client:
            response = await client.post(f"{AUTH_BASE}/accessToken", data={
                "grant_type": "authorization_code",
                "code": params.get("code", ""),
                "client_id": config["client_id"].strip(),
                "client_secret": config["client_secret"].strip(),
                "redirect_uri": pending.redirect_uri,
            })
            if response.status_code != 200:
                raise self._error(response, "accessToken")
            data = response.json()
            token = data["access_token"]
            profile = await self._userinfo(client, token)
        self.save_secret("access_token", token)
        self.save_value("person_id", profile["sub"])
        self.save_value("account_name", profile.get("name", ""))
        self.save_value("token_expires_at", str(int(time.time() + int(data.get("expires_in", 0)))))
        return ActionResult(ok=True, message=f"Connected as {profile.get('name', 'your LinkedIn profile')}.")

    # ---- API plumbing ----------------------------------------------------------------------

    @staticmethod
    def _headers(token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": LINKEDIN_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    async def _userinfo(self, client, token: str) -> dict:
        response = await client.get(f"{API_BASE}/v2/userinfo", headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            raise self._error(response, "userinfo")
        return response.json()

    def _error(self, response: httpx.Response, what: str) -> PlatformError:
        status = response.status_code
        try:
            body = response.json()
            message = str(body.get("message") or body.get("error_description") or body.get("error") or "")
            code = str(body.get("serviceErrorCode") or body.get("code") or "")
        except ValueError:
            message, code = response.text[:200], ""
        details = f"{what}: HTTP {status}, code={code!r}, message={message!r}"
        lower = message.lower()

        if status == 401 or "invalid_client" in lower:
            if "client" in lower:
                return PlatformError("invalid_credentials",
                                     "LinkedIn did not accept the Client ID or Client Secret. Copy them again from "
                                     "your app's Auth tab.", details)
            return PlatformError("expired_credentials",
                                 "Your LinkedIn connection has expired or was removed. Open Settings → LinkedIn "
                                 "and click “Connect LinkedIn”.", details)
        if status == 403:
            return PlatformError("missing_permission",
                                 "LinkedIn says Auto Poster isn't allowed to post for you. Check that your LinkedIn "
                                 "app has the “Share on LinkedIn” product, then click “Connect LinkedIn” again.",
                                 details)
        if status == 429:
            retry = response.headers.get("Retry-After")
            return PlatformError("rate_limited",
                                 "LinkedIn's daily posting limit was reached. Try again tomorrow.",
                                 details, retry_after=int(retry) if retry and retry.isdigit() else None)
        if "duplicate" in lower:
            return PlatformError("validation_failed",
                                 "LinkedIn rejected this post because it's the same as one you posted recently.",
                                 details)
        if "too_long" in lower or "length" in lower:
            return PlatformError("too_long", "LinkedIn says the text is too long.", details)
        if what in ("initializeUpload", "upload"):
            return PlatformError("invalid_media", "LinkedIn could not accept one of the images.", details)
        if "redirect" in lower:
            return PlatformError("invalid_credentials", _auth_error_message(message), details)
        if status >= 500:
            return PlatformError("platform_unavailable",
                                 "LinkedIn is having problems right now. Try again in a few minutes.", details)
        return PlatformError("validation_failed",
                             f"LinkedIn rejected the request: {message or 'no reason given'}.", details)

    # ---- connector interface ----------------------------------------------------------------

    async def test_connection(self, config: Config) -> ConnectionStatus:
        if not config.get("access_token"):
            return ConnectionStatus(ok=False, error_code="not_configured",
                                    message="Click “Connect LinkedIn” to finish connecting.")
        async with self.http() as client:
            profile = await self._userinfo(client, config["access_token"])
        name = profile.get("name", "your profile")
        expires = self._expires_at(config)
        until = f" The connection lasts until {datetime.fromtimestamp(expires).strftime('%d %B %Y')}." if expires else ""
        return ConnectionStatus(ok=True, account_name=name, message=f"Connected as {name}.{until}")

    async def _upload_image(self, client, token: str, owner: str, image) -> str:
        response = await client.post(
            f"{API_BASE}/rest/images?action=initializeUpload",
            headers=self._headers(token),
            json={"initializeUploadRequest": {"owner": owner}},
        )
        if response.status_code != 200:
            raise self._error(response, "initializeUpload")
        value = response.json()["value"]
        upload = await client.put(
            value["uploadUrl"],
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
            content=image.read_bytes(),
        )
        if upload.status_code not in (200, 201):
            raise self._error(upload, "upload")
        return value["image"]

    can_delete = True

    async def delete_post(self, post_id: str, config: Config) -> None:
        async with self.http() as client:
            response = await client.delete(
                f"{API_BASE}/rest/posts/{quote(post_id, safe='')}",
                headers={**self._headers(config["access_token"]), "X-RestLi-Method": "DELETE"},
            )
        if response.status_code not in (200, 204, 404):  # deleting twice is fine
            raise self._error(response, "delete post")

    async def post(self, post: Post, options: Options, config: Config) -> PostResult:
        token = config["access_token"]
        author = f"urn:li:person:{config['person_id']}"
        async with self.http() as client:
            image_urns = [await self._upload_image(client, token, author, image) for image in post.images]
            body: dict = {
                "author": author,
                "commentary": to_little_text(post.text),
                "visibility": options.get("visibility") or "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                 "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            }
            if len(image_urns) == 1:
                body["content"] = {"media": {"id": image_urns[0], "altText": post.images[0].alt_text or ""}}
            elif image_urns:
                body["content"] = {"multiImage": {"images": [
                    {"id": urn, "altText": image.alt_text or ""} for urn, image in zip(image_urns, post.images)
                ]}}
            response = await client.post(f"{API_BASE}/rest/posts", headers=self._headers(token), json=body)
        if response.status_code != 201:
            raise self._error(response, "posts")
        post_urn = response.headers.get("x-restli-id", "")
        return PostResult(
            platform=self.id,
            success=True,
            post_id=post_urn,
            post_url=f"https://www.linkedin.com/feed/update/{quote(post_urn)}/" if post_urn else None,
            message="Posted to LinkedIn.",
        )


def _auth_error_message(description: str, redirect: str = "the redirect URL shown in the setup guide") -> str:
    if "redirect" in description.lower():
        return ("LinkedIn rejected the sign-in because the redirect URL doesn't match. In your LinkedIn app's Auth "
                f"tab, add exactly: {redirect}")
    if "scope" in description.lower():
        return ("LinkedIn refused the permissions. In your LinkedIn app's Products tab, add “Share on LinkedIn” and "
                "“Sign In with LinkedIn using OpenID Connect”, then try again.")
    return f"LinkedIn sign-in failed: {description or 'unknown reason'}."
