# Bluesky

Auto Poster posts to your Bluesky account through the official AT Protocol API,
signing in with an **app password**.

## Connect Bluesky

1. Sign in to Bluesky (app or [bsky.app](https://bsky.app)).
2. Open **Settings → Privacy and security → App passwords**
   ([direct link](https://bsky.app/settings/app-passwords)).
3. Click **Add App Password**, name it "Auto Poster", and create it.
4. Copy the password (format `xxxx-xxxx-xxxx-xxxx`). Bluesky shows it only once.
5. In Auto Poster, open **Settings → Bluesky**, enter your handle (`yourname.bsky.social`
   or your custom domain) and paste the app password.
6. Click **Save**, then **Test connection**.

Leave **Server** empty unless your account lives on a self-hosted PDS.

### Why an app password and not "Sign in with Bluesky"?

Bluesky is moving apps to OAuth and describes app passwords as deprecated, but they
are still supported, and Bluesky's guidance allows single-purpose tools such as bots to use them.
Production OAuth needs a client-metadata file hosted at a public HTTPS address, which
would mean a project-run website. To keep Auto Poster fully local, v1 uses app passwords.
All sign-in code is in `_session()` in `backend/auto_poster/connectors/bluesky.py`, so
switching to OAuth later only touches that function.

An app password can't change your account password or delete your account, and you can
revoke it any time in Bluesky.

## How it works

| Step | Endpoint |
|---|---|
| Sign in (only when needed) | `com.atproto.server.createSession` |
| Stay signed in | `com.atproto.server.refreshSession` |
| Upload each image | `com.atproto.repo.uploadBlob` |
| Resolve @mentions | `com.atproto.identity.resolveHandle` |
| Create the post | `com.atproto.repo.createRecord` (`app.bsky.feed.post`) |

Bluesky limits logins to 30 per 5 minutes and 300 per day per account, so Auto Poster
stores the session's refresh token in the credential store and reuses it instead of logging
in for every post.

**Rich text:** Bluesky does not detect links, mentions or hashtags itself. Auto Poster finds
`https://` links, `@handle` mentions and `#hashtags` and sends them as "facets" (UTF-8 byte
ranges), so they are clickable. Bare domains (`example.com`) stay plain text. Link preview
cards are **not** created.

**Images:** up to 4 per post. Each image's description (alt text) from the post form is sent.
Images over Bluesky's size limit are **automatically compressed** (and scaled down if needed),
just like the official Bluesky app does. The post form warns you when this will happen.
Re-compressed images lose their EXIF data (including any GPS location).

## Stats

The **Stats** page reads likes, reposts/quotes and replies with `app.bsky.feed.getPosts`, and
the latest replies with `app.bsky.feed.getPostThread`, from Bluesky's public read-only API
(`public.api.bsky.app`), so no extra permission is needed. Bluesky doesn't report view counts.

## Limits (from the official lexicons)

| Rule | Value | Source |
|---|---|---|
| Text | 300 graphemes (user-visible characters) and 3000 bytes | `app.bsky.feed.post` |
| Images per post | 4 | `app.bsky.embed.images` |
| Image file size | 2,000,000 bytes (larger images are compressed) | `app.bsky.embed.images` |
| Image formats | JPEG, PNG, WEBP, GIF (first frame) | `image/*` |
| Posts | 5,000 points/hour, 35,000/day (a post costs 3) | [rate limits](https://docs.bsky.app/docs/advanced-guides/rate-limits) |

Limits live in `LIMITS`, `MAX_TEXT_BYTES` and `MAX_BLOB_BYTES` in the connector file.

## Common problems

| Message | What to do |
|---|---|
| Bluesky did not accept the handle or app password | Check the handle (no `@`). Create a new app password if you deleted the old one. |
| Bluesky asked for a sign-in code | You used your main password and have two-factor sign-in on. Use an app password. |
| Bluesky is limiting requests | Wait and try again; the result shows how long. |

## Safe testing for developers

- `pytest` mocks every Bluesky request; nothing is posted.
- For manual tests, create a **separate test account**. Publishing creates a real, public post.
  Delete test posts afterwards.
