"""Facebook connector: posts to a Facebook **Page** the user manages. Personal profiles are not
supported (Meta's API does not allow apps to post to personal profiles).

Docs:
  Pages API:     https://developers.facebook.com/docs/pages-api/posts
  Page photos:   https://developers.facebook.com/docs/graph-api/reference/page/photos/
  Login flow:    https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow
  Token lengths: https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived

Like LinkedIn, each user creates their own Meta app (the app secret can't be shared in
open-source code). While the app is in development mode, the people with a role on the app
(the user themselves) can use its Page permissions without Meta's App Review.

Token chain: short-lived user token -> long-lived user token (~60 days) -> Page token.
Page tokens obtained from a long-lived user token don't expire, so users normally connect once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

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
from auto_poster.security.redact import register_secret
from auto_poster.services import oauth

# Graph API version, used for every call. Meta supports each version for about two years.
GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,pages_manage_metadata"

LIMITS = PlatformLimits(
    max_chars=63206,  # Facebook's post text limit
    max_images=10,  # Auto Poster's cap for multi-photo posts (Meta documents no fixed number)
    max_image_bytes=10 * 1024 * 1024,  # Page photos: up to 10MB
    image_formats=["JPEG", "PNG", "GIF"],  # Meta also accepts BMP and TIFF, which Auto Poster doesn't upload
    requires_text=False,
)

# Graph API error codes: https://developers.facebook.com/docs/graph-api/guides/error-handling
AUTH_CODES = {102, 190}
PERMISSION_CODES = {3, 10} | set(range(200, 300))
RATE_LIMIT_CODES = {4, 17, 32, 613, 80001}
TEMPORARY_CODES = {1, 2}


class FacebookConnector(Connector):
    id = "facebook"
    display_name = "Facebook Page"
    description = "Post to a Facebook Page you manage. Posting to personal profiles is not possible."
    limits = LIMITS
    max_hashtags = 3  # a few hashtags work best on Facebook
    settings_fields = [
        FieldSpec(key="app_id", label="App ID", required=True,
                  help="From your Meta app: App settings → Basic."),
        FieldSpec(key="app_secret", label="App secret", kind="secret", required=True,
                  help="From your Meta app: App settings → Basic → App secret (click Show)."),
        FieldSpec(key="config_id", label="Login configuration ID",
                  help="Only if your app uses “Facebook Login for Business”: the configuration's ID. "
                  "Leave empty otherwise."),
        FieldSpec(key="user_token", label="Access token from Graph API Explorer", kind="secret",
                  help="Alternative to “Connect Facebook”: paste a User token from Meta's Graph API Explorer, "
                  "click Save, then “Load my Pages”."),
    ]
    actions = [
        ActionSpec(id="connect", label="Connect Facebook", help="Opens Facebook so you can choose your Pages."),
        ActionSpec(id="load_pages", label="Load my Pages",
                   help="Uses the pasted Graph API Explorer token to find your Pages."),
    ]
    internal_secret_keys = {"page_tokens"}
    setup_guide = SetupGuide(
        steps=[
            "Facebook requires every app that posts to have its own Meta app. It's free and takes about 15 "
            "minutes, once. You need to be an admin of the Facebook Page you want to post to.",
            "Go to developers.facebook.com/apps, click “Create app”, and give it a name.",
            "When asked for a use case, choose “Manage everything on your Page”. If asked about a business "
            "portfolio, you can skip it.",
            "Open the use case's settings and make sure these permissions are added: pages_show_list, "
            "pages_read_engagement, pages_manage_posts, pages_manage_metadata.",
            "If your app has Facebook Login settings, add this to “Valid OAuth Redirect URIs”: "
            "http://localhost:{port}/oauth/facebook/callback",
            "In App settings → Basic, copy the App ID and App secret into the fields below and click Save.",
            "Click “Connect Facebook”, choose your Page(s) and approve. Then choose the Page below and click Save.",
        ],
        docs_url="https://developers.facebook.com/docs/pages-api/getting-started",
        notes=[
            "Keep the Meta app in Development mode. You (as the app's admin) can post to your own Pages without "
            "Meta's App Review.",
            "If “Connect Facebook” doesn't work: open Meta's Graph API Explorer "
            "(developers.facebook.com/tools/explorer), select your app, add the four permissions above, click "
            "“Generate Access Token”, paste it into “Access token from Graph API Explorer”, Save, then click "
            "“Load my Pages”.",
            "Meta's dashboard changes often. If a step looks different, follow Meta's guide linked below.",
        ],
    )

    # ---- settings depend on the Pages found ------------------------------------------------

    @staticmethod
    def _pages(config: Config) -> list[dict]:
        try:
            return json.loads(config.get("pages") or "[]")
        except ValueError:
            return []

    def settings_fields_for(self, config: Config) -> list[FieldSpec]:
        pages = self._pages(config)
        if not pages:
            return self.settings_fields
        page_field = FieldSpec(
            key="page_id",
            label="Page to post to",
            kind="select",
            required=True,
            options=[FieldOption(value="", label="Choose a Page…")]
            + [FieldOption(value=p["id"], label=p["name"]) for p in pages],
            help="Only Pages you allowed when connecting are listed.",
        )
        return [*self.settings_fields, page_field]

    def is_configured(self, config: Config) -> bool:
        return bool(config.get("app_id") and config.get("app_secret") and config.get("page_id")
                    and self._page_token(config, config.get("page_id", "")))

    @staticmethod
    def _page_token(config: Config, page_id: str) -> str | None:
        try:
            return json.loads(config.get("page_tokens") or "{}").get(page_id)
        except ValueError:
            return None

    def validate(self, post: Post, options: Options, config: Config) -> list[Problem]:
        problems = super().validate(post, options, config)
        page = next((p for p in self._pages(config) if p["id"] == config.get("page_id")), None)
        if page and page.get("tasks") and "CREATE_CONTENT" not in page["tasks"]:
            problems.append(self.problem(
                f"Your role on the Page “{page['name']}” doesn't allow creating posts. Ask a Page admin for full "
                "control or content permissions."))
        return problems

    # ---- Graph API plumbing ----------------------------------------------------------------

    @staticmethod
    def _proof(token: str, app_secret: str) -> str:
        """appsecret_proof: proves calls come from the app's owner (required if the app enables it)."""
        return hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()

    async def _graph(self, client, method: str, path: str, token: str, config: Config,
                     data: dict | None = None, files: dict | None = None) -> dict:
        register_secret(token)
        # The token goes in the Authorization header, never in the URL. appsecret_proof is a hash, not a secret.
        headers = {"Authorization": f"Bearer {token}"}
        proof = {"appsecret_proof": self._proof(token, config["app_secret"].strip())}
        if method == "GET":
            response = await client.get(f"{GRAPH}/{path}", params={**proof, **(data or {})}, headers=headers)
        elif method == "DELETE":
            response = await client.delete(f"{GRAPH}/{path}", params=proof, headers=headers)
        else:
            response = await client.post(f"{GRAPH}/{path}", data={**proof, **(data or {})}, files=files,
                                         headers=headers)
        if response.status_code != 200:
            raise self._error(response, path)
        return response.json()

    def _error(self, response: httpx.Response, what: str) -> PlatformError:
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        code = error.get("code")
        sub = error.get("error_subcode")
        message = str(error.get("message", ""))
        details = f"{what}: HTTP {response.status_code}, code={code}, subcode={sub}, message={message!r}"

        if code in AUTH_CODES:
            return PlatformError(
                "expired_credentials",
                "Facebook no longer accepts Auto Poster's access. This happens if you changed your Facebook "
                "password or removed the app. Open Settings → Facebook Page and click “Connect Facebook” again.",
                details,
            )
        if code == 101 or "client secret" in message.lower() or "client_id" in message.lower():
            return PlatformError("invalid_credentials",
                                 "Facebook did not accept the App ID or App secret. Copy them again from your Meta "
                                 "app's settings.", details)
        if code in PERMISSION_CODES:
            return PlatformError(
                "missing_permission",
                "Facebook says Auto Poster isn't allowed to post to this Page. Make sure you're an admin of the "
                "Page, that your Meta app has the pages_manage_posts permission, and connect again.",
                details,
            )
        if code in RATE_LIMIT_CODES:
            return PlatformError("rate_limited", "Facebook is limiting requests right now. Try again later.", details)
        if code == 368:
            return PlatformError("missing_permission",
                                 "Facebook has temporarily blocked posting from this account or Page.", details)
        if code == 506:
            return PlatformError("validation_failed",
                                 "Facebook rejected this post because it's the same as a recent one.", details)
        if code == 324 or "image" in message.lower() or "photo" in message.lower():
            return PlatformError("invalid_media", "Facebook could not accept one of the images.", details)
        if code in TEMPORARY_CODES or response.status_code >= 500:
            return PlatformError("platform_unavailable",
                                 "Facebook is having problems right now. Try again in a few minutes.", details)
        if "redirect_uri" in message.lower():
            return PlatformError("invalid_credentials",
                                 "Facebook rejected the sign-in address. Add the redirect URI from the setup guide "
                                 "to your Meta app's Facebook Login settings, or use the Graph API Explorer option.",
                                 details)
        return PlatformError("validation_failed", f"Facebook rejected the request: {message or 'no reason given'}.",
                             details)

    # ---- connecting -------------------------------------------------------------------------

    async def run_action(self, action_id: str, config: Config, base_url: str) -> ActionResult:
        if not (config.get("app_id") and config.get("app_secret")):
            return ActionResult(ok=False, message="Enter your App ID and App secret and click Save first.")
        if action_id == "load_pages":
            if not config.get("user_token"):
                return ActionResult(ok=False, message="Paste a token from Graph API Explorer and click Save first.")
            async with self.http() as client:
                return await self._finish_with_user_token(client, config, config["user_token"].strip())

        state, pending = oauth.start(self.id, base_url, host="localhost")
        params = {
            "client_id": config["app_id"].strip(),
            "redirect_uri": pending.redirect_uri,
            "state": state,
            "response_type": "code",
        }
        if config.get("config_id"):
            params["config_id"] = config["config_id"].strip()
        else:
            params["scope"] = SCOPES
        return ActionResult(ok=True, open_url=f"{DIALOG}?{urlencode(params)}",
                            message="A new tab opened on Facebook. Choose your Page(s) and approve, then come back here.")

    async def handle_oauth_callback(self, params: dict[str, str], config: Config) -> ActionResult:
        pending = oauth.finish(self.id, params.get("state"))
        if pending is None:
            return ActionResult(ok=False, message="This sign-in link has expired. Click “Connect Facebook” again.")
        if params.get("error"):
            return ActionResult(ok=False, message="Access was not approved on Facebook, so nothing was connected.")
        async with self.http() as client:
            response = await client.post(f"{GRAPH}/oauth/access_token", data={
                "client_id": config["app_id"].strip(),
                "client_secret": config["app_secret"].strip(),
                "redirect_uri": pending.redirect_uri,
                "code": params.get("code", ""),
            })
            if response.status_code != 200:
                raise self._error(response, "oauth/access_token")
            return await self._finish_with_user_token(client, config, response.json()["access_token"])

    async def _finish_with_user_token(self, client, config: Config, short_token: str) -> ActionResult:
        register_secret(short_token)
        # Long-lived user token, so the Page tokens derived from it don't expire.
        response = await client.post(f"{GRAPH}/oauth/access_token", data={
            "grant_type": "fb_exchange_token",
            "client_id": config["app_id"].strip(),
            "client_secret": config["app_secret"].strip(),
            "fb_exchange_token": short_token,
        })
        if response.status_code != 200:
            raise self._error(response, "fb_exchange_token")
        user_token = response.json()["access_token"]
        accounts = await self._graph(client, "GET", "me/accounts", user_token, config,
                                     {"fields": "id,name,access_token,tasks", "limit": "100"})
        pages = accounts.get("data", [])
        if not pages:
            return ActionResult(ok=False, message="No Facebook Pages were found. Make sure you're an admin of a Page "
                                "and that you selected it when approving Auto Poster.")
        self.save_secret("page_tokens", json.dumps({p["id"]: p["access_token"] for p in pages}))
        self.save_value("pages", json.dumps([{"id": p["id"], "name": p["name"], "tasks": p.get("tasks", [])}
                                             for p in pages]))
        self.save_secret("user_token", None)  # no longer needed
        if len(pages) == 1 or config.get("page_id") not in {p["id"] for p in pages}:
            self.save_value("page_id", pages[0]["id"] if len(pages) == 1 else "")
        names = ", ".join(p["name"] for p in pages)
        if len(pages) == 1:
            return ActionResult(ok=True, message=f"Connected to the Page “{pages[0]['name']}”.")
        return ActionResult(ok=True, message=f"Found {len(pages)} Pages ({names}). Choose one under “Page to post "
                            "to” and click Save.")

    # ---- connector interface -----------------------------------------------------------------

    async def test_connection(self, config: Config) -> ConnectionStatus:
        page_id = config["page_id"]
        token = self._page_token(config, page_id)
        async with self.http() as client:
            page = await self._graph(client, "GET", page_id, token, config, {"fields": "id,name,link"})
        return ConnectionStatus(ok=True, account_name=page.get("name"),
                                message=f"Connected. Posts will go to the Page “{page.get('name')}”.")

    can_delete = True

    async def delete_post(self, post_id: str, config: Config) -> None:
        page_id = post_id.split("_", 1)[0]
        token = self._page_token(config, page_id)
        if not token:
            raise PlatformError("not_configured", "This post was made on a Facebook Page that is no longer connected.")
        async with self.http() as client:
            try:
                await self._graph(client, "DELETE", post_id, token, config)
            except PlatformError as err:
                if "does not exist" in (err.technical_details or ""):
                    return  # already deleted
                raise

    async def post(self, post: Post, options: Options, config: Config) -> PostResult:
        page_id = config["page_id"]
        token = self._page_token(config, page_id)
        async with self.http() as client:
            if len(post.images) == 1:
                image = post.images[0]
                data = {"caption": post.text} if post.text else {}
                result = await self._graph(client, "POST", f"{page_id}/photos", token, config, data,
                                           files={"source": (image.filename, image.read_bytes(), image.mime_type)})
                post_id = result.get("post_id") or result["id"]
            else:
                data: dict = {}
                if post.text:
                    data["message"] = post.text
                for index, image in enumerate(post.images):
                    # Upload unpublished, then attach all photos to one post.
                    photo = await self._graph(
                        client, "POST", f"{page_id}/photos", token, config, {"published": "false"},
                        files={"source": (image.filename, image.read_bytes(), image.mime_type)},
                    )
                    data[f"attached_media[{index}]"] = json.dumps({"media_fbid": photo["id"]})
                result = await self._graph(client, "POST", f"{page_id}/feed", token, config, data)
                post_id = result["id"]
        return PostResult(platform=self.id, success=True, post_id=post_id,
                          post_url=f"https://www.facebook.com/{post_id}", message="Posted to your Facebook Page.")
