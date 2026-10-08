# LinkedIn (personal profile)

Auto Poster posts to **your personal LinkedIn profile** using LinkedIn's official
[Posts API](https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api).
Posting to LinkedIn **company pages is not supported**.

## Before you start: why you need your own LinkedIn app

LinkedIn only lets registered apps post, and every app has a secret key. An open-source
program can't keep a shared secret hidden without running a server, and Auto Poster has
no server. So **each user creates their own free LinkedIn app** once. It only takes about
10 minutes, and you don't need to be a developer.

LinkedIn requires the app to be linked to a **LinkedIn Page**. If you don't have one, create
a simple page first (LinkedIn → For Business → Create a Company Page). Auto Poster never
posts to that page; it's only used to register the app.

## Connect LinkedIn

1. Go to [linkedin.com/developers/apps](https://www.linkedin.com/developers/apps) and click
   **Create app**. Enter a name (e.g. "My Auto Poster"), choose your LinkedIn Page, upload any
   logo, accept the terms and create the app.
2. Open the **Products** tab and request:
   - **Share on LinkedIn** (allows posting), and
   - **Sign In with LinkedIn using OpenID Connect** (lets Auto Poster see which profile is connected).

   Both are approved immediately.
3. Open the **Auth** tab. Under **Authorized redirect URLs for your app**, add exactly:

   ```
   http://localhost:8765/oauth/linkedin/callback
   ```

   (If you start Auto Poster with a different `--port`, use that number. The Settings page
   shows the exact address for your setup.)
4. Copy the **Client ID** and **Primary Client Secret** from the Auth tab into
   **Settings → LinkedIn** and click **Save**.
5. Click **Connect LinkedIn**, sign in if asked, and click **Allow**.
6. Click **Test connection**.

### Permissions requested

| Scope | Why |
|---|---|
| `openid`, `profile` | Read your name and member ID, needed as the post's author. |
| `w_member_social` | Create posts on your behalf. |

Nothing else is requested. Auto Poster cannot read your messages, connections or feed.

## Reconnecting every 60 days

LinkedIn access tokens last **60 days**. LinkedIn only gives refresh tokens to approved
Marketing Developer Platform partners, which a personal app won't be. So:

- Settings shows when the connection expires.
- From 7 days before, the post form warns you.
- After expiry, LinkedIn posts are blocked with a clear message (including scheduled posts).
- Reconnecting is one click on **Connect LinkedIn**. If you're still signed in to LinkedIn,
  it doesn't even ask again.

## What gets posted

| Post contains | How |
|---|---|
| Text | `POST /rest/posts` with `commentary` |
| 1 image | Uploaded via the Images API, attached as `content.media` |
| 2–20 images | `content.multiImage` |

**Who can see it:** Anyone (`PUBLIC`) or Connections only (`CONNECTIONS`).

**Text formatting:** LinkedIn's text format reserves the characters
`| { } @ [ ] ( ) < > # \ * _ ~`. Auto Poster escapes them, so your text appears exactly as
typed. `#hashtags` stay as clickable hashtags. @mentions are not linked (that needs extra
LinkedIn permissions).

Link preview cards are not generated (the Posts API doesn't fetch URLs).

## Limits

| Rule | Value | Source |
|---|---|---|
| Text | 3000 characters | Posts API |
| Images per post | 1, or 2–20 as a multi-image post | MultiImage API |
| Image formats | JPEG, PNG, GIF | Images API |
| Image size | fewer than 36,152,320 pixels | Images API |
| Daily requests | about 150 per member | Share on LinkedIn rate limits |

## Stats

Reading your own post statistics needs LinkedIn permissions that are only given to approved
partner apps, so the Stats page shows LinkedIn posts as "Not available".

## API version

LinkedIn versions its API monthly (`LinkedIn-Version: YYYYMM`) and retires each version after
about a year. Auto Poster uses **`202609`**, set once as `LINKEDIN_VERSION` in
`backend/auto_poster/connectors/linkedin.py`.

**Update it at least once a year.** Check the
[versioning page](https://learn.microsoft.com/linkedin/marketing/versioning) and the Posts API
"Deprecation Notice" banner. For example, version 202510 is retired on 15 October 2026.

## Common problems

| Message | What to do |
|---|---|
| LinkedIn rejected the sign-in because the redirect URL doesn't match | Add the exact redirect URL shown in Settings to your app's Auth tab. |
| LinkedIn refused the permissions | Add both products in your app's Products tab. |
| LinkedIn did not accept the Client ID or Client Secret | Copy them again from the Auth tab. |
| Your LinkedIn connection has expired | Click **Connect LinkedIn**. |
| LinkedIn says Auto Poster isn't allowed to post | Make sure "Share on LinkedIn" is added to your app, then reconnect. |
| Same as one you posted recently | LinkedIn blocks identical repeat posts. Change the text. |

## Safe testing for developers

- `pytest` mocks every LinkedIn request; nothing is posted.
- LinkedIn has no sandbox for personal posts. For a manual test, post with
  **Who can see it: Connections only** and delete the post right after.
- Your developer app works for your own profile immediately; you don't need LinkedIn's review.
