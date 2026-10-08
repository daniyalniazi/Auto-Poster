# Mastodon

Auto Poster posts to your account on **any Mastodon server**, using the official
[Mastodon API](https://docs.joinmastodon.org/client/intro/).

## Connect Mastodon

1. In **Settings → Mastodon**, type your server's address in **Server**, for example
   `mastodon.social`. It's the part after the second `@` in your full username
   (`@you@mastodon.social`).
2. Click **Connect Mastodon**. A new tab opens on your server.
3. Sign in if asked, and click **Authorize**.
4. The tab says "Mastodon connected". Close it and go back to Auto Poster.
5. Click **Test connection**. It also shows your server's character limit.

You can remove Auto Poster's access any time in Mastodon:
**Preferences → Account → Authorized apps**.

**Advanced alternative:** create an application yourself in **Preferences → Development**
with the scopes `read:accounts write:statuses write:media`, and paste its access token
into **Access token**.

### How the sign-in works (for developers)

Standard OAuth 2.0 authorization code flow with PKCE, run entirely on your computer:

1. Auto Poster registers itself on your server once (`POST /api/v1/apps`) with the redirect
   address `http://127.0.0.1:<port>/oauth/mastodon/callback`. The resulting client ID/secret
   are stored in the credential store and reused.
2. Your browser opens `/oauth/authorize` on your server with a random single-use `state`.
3. The server redirects back to the local app, which checks `state` and exchanges the code
   (`POST /oauth/token`) for an access token. Mastodon tokens don't expire until revoked.

Scopes requested: `read:accounts` (to show which account is connected), `read:statuses`
(to read likes, boosts and replies for the **Stats** page), `write:statuses`, `write:media`.
Nothing else. Connections made before Stats existed don't have `read:statuses`: stats still work
for public posts, and for other posts the app asks you to click **Connect Mastodon** again.

## Post options

| Option | API field |
|---|---|
| Who can see it: Public / Quiet public / Followers only / Only people mentioned | `visibility`: `public` / `unlisted` / `private` / `direct` |
| Content warning | `spoiler_text` |
| Mark images as sensitive | `sensitive` |

## Server-specific limits

Every server can change its limits, so Auto Poster reads them from
`GET /api/v2/instance` (falling back to `/api/v1/instance` for older servers) whenever you
connect or test the connection. Until then, Mastodon's defaults are used.

| Rule | Mastodon default | Field |
|---|---|---|
| Characters | 500 | `configuration.statuses.max_characters` |
| Images per post | 4 | `configuration.statuses.max_media_attachments` |
| Image file size | 16 MB | `configuration.media_attachments.image_size_limit` |
| Image pixels | 33,177,600 (e.g. 7680×4320) | `configuration.media_attachments.image_matrix_limit` |
| Image formats | JPEG, PNG, WEBP, GIF | `configuration.media_attachments.supported_mime_types` |

**How characters are counted (same as Mastodon):** every link counts as 23 characters
however long it is, a mention like `@alice@other.server` counts as `@alice`, and the
content warning counts together with the text.

## How posting works

1. Each image is uploaded with `POST /api/v2/media` (with its description as alt text).
   If the server is still processing it (HTTP 202), Auto Poster checks
   `GET /api/v1/media/:id` once a second for up to 30 seconds.
2. The post is created with `POST /api/v1/statuses`, sending an `Idempotency-Key` header so
   the server ignores accidental repeats.

Mastodon's default rate limits are 300 requests per 5 minutes per account, and 30 media
uploads per 30 minutes. If you hit them, the result shows when you can try again.

## Common problems

| Message | What to do |
|---|---|
| Mastodon did not accept Auto Poster's access | Access was removed or the token is wrong. Click **Connect Mastodon** again. |
| That doesn't look like a Mastodon server | Check the server address. |
| The post is longer than your Mastodon server allows | Shorten it. Click **Test connection** to refresh the server's limits. |
| Your account may be limited or suspended | Check your account on the server. |

## Safe testing for developers

- `pytest` mocks every request; nothing is posted.
- For manual tests, use a test account and set **Who can see it** to
  **Only people mentioned** (with nobody mentioned, only you can see the post). Delete test posts afterwards.
- Don't create many test accounts on big community servers; some servers offer
  dedicated test instances, or run your own.
