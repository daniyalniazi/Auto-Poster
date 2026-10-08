# Telegram

Auto Poster posts to a Telegram **channel or group** through a bot that you create
and control. It uses the official [Telegram Bot API](https://core.telegram.org/bots/api).

## Connect Telegram

1. In Telegram, open a chat with [@BotFather](https://t.me/BotFather) (check for the verified tick).
2. Send `/newbot` and answer the questions. BotFather replies with a **bot token**
   that looks like `123456789:AAH...`.
3. Add the bot to your channel or group **as an administrator**.
   For channels, the bot needs the **Post messages** permission.
4. In Auto Poster, open **Settings → Telegram**:
   - Paste the bot token.
   - In **Channel or group**, enter:
     - `@yourchannel` for a public channel or group, or
     - the numeric ID (for example `-1001234567890`) for a private one.
       Save the token first, then click **Find my channels and groups** to see the ID.
5. Click **Save**, then **Test connection**.

The connection test checks the token (`getMe`), that the chat exists (`getChat`), and
that the bot is allowed to post there (`getChatMember`). It never posts anything.

## What gets posted

| Post contains | API method | Notes |
|---|---|---|
| Text only | `sendMessage` | Up to 4096 characters. |
| Text + 1 image | `sendPhoto` | Text becomes the caption: up to 1024 characters. |
| Text + 2–10 images | `sendMediaGroup` | Sent as an album. Text is the album caption: up to 1024 characters. |

Text is sent **without** formatting (`parse_mode` is not set), so characters like `*`
and `_` appear exactly as typed.

**Send silently** (post option) delivers the post without a notification sound.

## Limits (from the official Bot API docs)

| Rule | Value |
|---|---|
| Text | 1–4096 characters (counted in UTF-16 units, so some emoji count as 2) |
| Caption (when images are attached) | 0–1024 characters |
| Images per post | 1–10 |
| Image size | up to 10 MB |
| Image dimensions | width + height ≤ 10000, aspect ratio ≤ 20 |
| Image formats accepted by Auto Poster | JPEG, PNG, WEBP |

These live in `LIMITS` in `backend/auto_poster/connectors/telegram.py`.

Telegram also limits how fast a bot can post (roughly 20 messages per minute in one
group). If Telegram answers "Too Many Requests", Auto Poster records the failure with the
waiting time Telegram gives and does **not** retry automatically.

## Stats

Telegram's Bot API has no way for a bot to read a post's views or reactions, so the Stats page
shows Telegram posts as "Not available".

## Deleting posts

**Delete everywhere** in History uses `deleteMessages` (all photos of an album are removed).
Telegram only lets bots delete their messages **within 48 hours**, unless the bot is an
administrator with the **Delete messages** permission in that channel or group. Older posts
must then be deleted in Telegram itself; Auto Poster says so if it happens.

## Common problems

| Message | What to do |
|---|---|
| Telegram did not accept the bot token | Copy the token again from BotFather (`/mybots` → your bot → API Token). |
| Telegram could not find that channel or group | Check the @username or ID, and that the bot was added to the chat. |
| The bot is not allowed to post there | Make the bot an administrator with permission to post messages. |
| This group was upgraded … new ID | Telegram changed the group's ID. Paste the new ID into Settings. |
| Could not reach Telegram | Your internet connection is down, or Telegram is blocked on your network. |

## Safe testing for developers

- Automated tests (`pytest`) mock every Telegram request. They never post.
- For a manual test, create a **private test channel** with only yourself in it, and a
  separate test bot. **Publishing from the app creates a real message** in that channel.
- Never test against a channel other people follow.

## API version

The Bot API is not versioned in URLs. Auto Poster uses only long-standing methods
(`getMe`, `getChat`, `getChatMember`, `getUpdates`, `sendMessage`, `sendPhoto`,
`sendMediaGroup`). Check the [changelog](https://core.telegram.org/bots/api-changelog)
when updating the connector.
