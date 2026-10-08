"""Telegram connector, using the official Telegram Bot API.

Docs: https://core.telegram.org/bots/api
Methods used: getMe, getChat, getChatMember, sendMessage, sendPhoto, sendMediaGroup.
Text is sent without parse_mode, so it appears exactly as typed.
"""

from __future__ import annotations

import json

import httpx

from auto_poster.connectors.base import Config, Connector, Options, text_length
from auto_poster.models import (
    ActionResult,
    ActionSpec,
    ConnectionStatus,
    FieldSpec,
    PlatformError,
    PlatformLimits,
    Post,
    PostResult,
    SetupGuide,
)

API_BASE = "https://api.telegram.org"

# From the Bot API docs: message text 1-4096 characters, captions 0-1024,
# photos up to 10 MB with width + height <= 10000 and ratio <= 20,
# and albums (sendMediaGroup) of 2-10 items.
LIMITS = PlatformLimits(
    max_chars=4096,
    max_chars_with_images=1024,
    count_method="utf16",  # Telegram measures text in UTF-16 units; this is the safe choice
    max_images=10,
    max_image_bytes=10 * 1024 * 1024,
    image_formats=["JPEG", "PNG", "WEBP"],
    max_image_dimension_sum=10000,
    max_aspect_ratio=20,
    requires_text=False,
)


class TelegramConnector(Connector):
    id = "telegram"
    display_name = "Telegram"
    description = "Post to a Telegram channel or group through your own bot."
    limits = LIMITS
    settings_fields = [
        FieldSpec(
            key="bot_token",
            label="Bot token",
            kind="secret",
            required=True,
            placeholder="123456789:AA...",
            help="The token BotFather gave you when you created the bot.",
        ),
        FieldSpec(
            key="chat_id",
            label="Channel or group",
            required=True,
            placeholder="@mychannel or -1001234567890",
            help="For a public channel use its @username. For a private channel or group, "
            "use its numeric ID (starts with -100).",
        ),
    ]
    post_fields = [
        FieldSpec(
            key="silent",
            label="Send silently",
            kind="checkbox",
            help="Members receive the post without a notification sound.",
        ),
    ]
    actions = [
        ActionSpec(
            id="find_chats",
            label="Find my channels and groups",
            help="Lists chats your bot was recently added to, with their IDs.",
        )
    ]
    setup_guide = SetupGuide(
        steps=[
            "Open Telegram and start a chat with @BotFather (check for the blue verified tick).",
            "Send /newbot and follow the questions to name your bot.",
            "BotFather replies with a bot token. Copy it and paste it into Bot token below.",
            "Open your channel or group, go to its settings and add your bot as an administrator.",
            "For a channel, make sure the bot has permission to post messages.",
            "Enter the channel's @username. For a private channel or group, save the bot token, then click "
            "“Find my channels and groups” to see its numeric ID.",
            "Click Save, then Test connection.",
        ],
        docs_url="https://core.telegram.org/bots/features#botfather",
        notes=[
            "Keep your bot token private. Anyone with it can control your bot.",
            "“Find my channels and groups” only sees chats the bot joined in the last 24 hours. If yours is "
            "missing, remove the bot from the chat, add it again, and click the button again.",
        ],
    )

    async def run_action(self, action_id: str, config: Config, base_url: str) -> ActionResult:
        token = config.get("bot_token", "").strip()
        if not token:
            return ActionResult(ok=False, message="Save your bot token first.")
        async with self.http() as client:
            updates = await self._call(client, token, "getUpdates", {"allowed_updates": [
                "message", "channel_post", "my_chat_member"]})
        chats = {}
        for update in updates:
            for key in ("my_chat_member", "channel_post", "message"):
                chat = (update.get(key) or {}).get("chat")
                if chat and chat.get("type") != "private":
                    chats[chat["id"]] = chat
        if not chats:
            return ActionResult(
                ok=False,
                message="No channels or groups found yet. Add the bot to your channel or group "
                "(as an administrator), then click this button again.",
            )
        found = "; ".join(
            f"“{c.get('title', 'untitled')}” ({c.get('type')}): "
            + (f"@{c['username']}" if c.get("username") else str(c["id"]))
            for c in chats.values()
        )
        return ActionResult(ok=True, message=f"Found: {found}. Copy the one you want into Channel or group.")

    # ---- API plumbing --------------------------------------------------------------------

    async def _call(
        self,
        client: httpx.AsyncClient,
        token: str,
        method: str,
        data: dict | None = None,
        files: dict | None = None,
    ) -> dict | list | bool:
        url = f"{API_BASE}/bot{token}/{method}"
        if files:
            response = await client.post(url, data=data, files=files)
        else:
            response = await client.post(url, json=data or {})
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("ok"):
            return body["result"]
        raise self._error(response.status_code, body, method)

    def _error(self, status: int, body: dict | None, method: str) -> PlatformError:
        body = body if isinstance(body, dict) else {}
        description = str(body.get("description", ""))
        code = body.get("error_code", status)
        params = body.get("parameters") or {}
        details = f"{method}: HTTP {status}, error_code={code}, description={description!r}"
        lower = description.lower()

        if code == 401 or "unauthorized" in lower:
            return PlatformError(
                "invalid_credentials",
                "Telegram did not accept the bot token. It may be mistyped, or the token was "
                "revoked in BotFather. Open Settings → Telegram, paste the current token and test again.",
                details,
            )
        if code == 429:
            retry = params.get("retry_after")
            wait = f" Telegram asked to wait {retry} seconds." if retry else ""
            return PlatformError(
                "rate_limited",
                f"Telegram is limiting how fast this bot can post.{wait} Try again later.",
                details,
                retry_after=retry,
            )
        if "migrate_to_chat_id" in params:
            return PlatformError(
                "not_found",
                "This group was upgraded by Telegram and now has a new ID: "
                f"{params['migrate_to_chat_id']}. Update Channel or group in Settings → Telegram.",
                details,
            )
        if "chat not found" in lower:
            return PlatformError(
                "not_found",
                "Telegram could not find that channel or group. Check the @username or numeric ID, "
                "and make sure the bot has been added to it.",
                details,
            )
        if code == 403 or "not enough rights" in lower or "have no rights" in lower or "not a member" in lower:
            return PlatformError(
                "missing_permission",
                "The bot is not allowed to post there. Add the bot as an administrator of the channel "
                "or group and give it permission to post messages.",
                details,
            )
        if "too long" in lower:
            return PlatformError("too_long", "Telegram says the text is too long.", details)
        if any(word in lower for word in ("photo", "image", "file", "media", "dimensions")):
            return PlatformError(
                "invalid_media",
                "Telegram could not accept one of the images. Try a different JPEG or PNG file.",
                details,
            )
        if status >= 500:
            return PlatformError(
                "platform_unavailable",
                "Telegram is having problems right now. Try again in a few minutes.",
                details,
            )
        return PlatformError(
            "validation_failed",
            f"Telegram rejected the request: {description or 'no reason given'}.",
            details,
        )

    # ---- connector interface -------------------------------------------------------------

    can_delete = True

    async def delete_post(self, post_id: str, config: Config) -> None:
        chat_id, message_ids = parse_post_id(post_id, config.get("chat_id", "").strip())
        async with self.http() as client:
            try:
                await self._call(client, config["bot_token"].strip(), "deleteMessages",
                                 {"chat_id": chat_id, "message_ids": message_ids})
            except PlatformError as err:
                if "not found" in (err.technical_details or "").lower():
                    return  # already deleted
                if "can't be deleted" in (err.technical_details or "").lower() or err.error_code == "validation_failed":
                    raise PlatformError(
                        "missing_permission",
                        "Telegram didn't allow deleting this post. Bots can only delete their posts within 48 hours, "
                        "unless the bot is an administrator with the “Delete messages” permission. You can still "
                        "delete it yourself in Telegram.",
                        err.technical_details,
                    ) from None
                raise

    async def test_connection(self, config: Config) -> ConnectionStatus:
        token, chat_id = config["bot_token"].strip(), config["chat_id"].strip()
        async with self.http() as client:
            me = await self._call(client, token, "getMe")
            bot_name = f"@{me.get('username', 'your bot')}"
            chat = await self._call(client, token, "getChat", {"chat_id": chat_id})
            title = chat.get("title") or chat.get("username") or chat_id
            member = await self._call(
                client, token, "getChatMember", {"chat_id": chat_id, "user_id": me["id"]}
            )

        status = member.get("status")
        if status in ("left", "kicked"):
            return ConnectionStatus(
                ok=False,
                error_code="missing_permission",
                account_name=bot_name,
                message=f"{bot_name} is not a member of “{title}”. Add the bot to it and test again.",
            )
        if chat.get("type") == "channel" and not (
            status == "creator" or (status == "administrator" and member.get("can_post_messages"))
        ):
            return ConnectionStatus(
                ok=False,
                error_code="missing_permission",
                account_name=bot_name,
                message=f"{bot_name} can't post in the channel “{title}”. Make the bot an administrator "
                "with permission to post messages.",
            )
        if status == "restricted" and not member.get("can_send_messages", True):
            return ConnectionStatus(
                ok=False,
                error_code="missing_permission",
                account_name=bot_name,
                message=f"{bot_name} is not allowed to send messages in “{title}”.",
            )
        return ConnectionStatus(
            ok=True, account_name=bot_name, message=f"Connected as {bot_name}. Ready to post to “{title}”."
        )

    async def post(self, post: Post, options: Options, config: Config) -> PostResult:
        token, chat_id = config["bot_token"].strip(), config["chat_id"].strip()
        silent = options.get("silent") == "true"
        base = {"chat_id": chat_id}
        if silent:
            base["disable_notification"] = "true"
        # A structured post's title is shown in bold. Entities mark text ranges (in UTF-16 units)
        # instead of using parse_mode, so the rest of the text never needs escaping.
        entities = (
            [{"type": "bold", "offset": 0, "length": text_length(post.title, "utf16")}] if post.title else []
        )

        async with self.http() as client:
            if not post.images:
                data = {**base, "text": post.text, "disable_notification": silent}
                if entities:
                    data["entities"] = entities
                message = await self._call(client, token, "sendMessage", data)
            elif len(post.images) == 1:
                image = post.images[0]
                data = {**base}
                if post.text:
                    data["caption"] = post.text
                    if entities:
                        data["caption_entities"] = json.dumps(entities)
                files = {"photo": (image.filename, image.read_bytes(), image.mime_type)}
                message = await self._call(client, token, "sendPhoto", data, files)
            else:
                media, files = [], {}
                for index, image in enumerate(post.images):
                    item = {"type": "photo", "media": f"attach://photo{index}"}
                    if index == 0 and post.text:
                        item["caption"] = post.text  # first caption becomes the album caption
                        if entities:
                            item["caption_entities"] = entities
                    media.append(item)
                    files[f"photo{index}"] = (image.filename, image.read_bytes(), image.mime_type)
                messages = await self._call(
                    client, token, "sendMediaGroup", {**base, "media": json.dumps(media)}, files
                )
                message = messages[0]

        # "chat:id1,id2" so the post can be deleted later even if the settings change.
        ids = [str(m["message_id"]) for m in messages] if post.images and len(post.images) > 1 \
            else [str(message["message_id"])]
        return PostResult(
            platform=self.id,
            success=True,
            post_id=f"{message.get('chat', {}).get('id', '')}:{','.join(ids)}",
            post_url=message_url(message),
            message="Posted to Telegram.",
        )


def parse_post_id(post_id: str, fallback_chat: str) -> tuple[str, list[int]]:
    chat, _, ids = post_id.rpartition(":")
    return (chat or fallback_chat), [int(i) for i in ids.split(",") if i.strip()]


def message_url(message: dict) -> str | None:
    chat = message.get("chat", {})
    message_id = message.get("message_id")
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{message_id}"
    chat_id = str(chat.get("id", ""))
    if chat_id.startswith("-100"):  # private supergroup/channel links work for members
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return None
