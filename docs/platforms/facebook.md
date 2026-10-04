# Facebook Pages

Auto Poster posts to a **Facebook Page** that you manage, using Meta's official
[Pages API](https://developers.facebook.com/docs/pages-api/posts).

**Personal Facebook profiles are not supported.** Meta's API does not allow apps to post
to personal profiles, and Auto Poster won't try to work around that.

## Before you start: why you need your own Meta app

Like LinkedIn, Facebook only lets registered apps post, and each app has a secret. With no
project server, the secret can't be shared safely, so **each user creates their own free Meta
app**. While the app stays in **Development mode**, you (as its admin) can post to Pages you
manage **without Meta's App Review**.

You must be an admin of the Page (or have a role that can create content on it).

## Connect Facebook

Meta's developer dashboard changes often; if something looks different, follow
[Meta's getting-started guide](https://developers.facebook.com/docs/pages-api/getting-started).

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps) and click **Create app**.
2. Choose the use case **Manage everything on your Page**. You can skip connecting a business portfolio.
3. In the use case's settings, make sure these permissions are added:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `pages_manage_metadata`.
4. If your app has **Facebook Login** settings, add this to **Valid OAuth Redirect URIs**:

   ```
   http://localhost:8765/oauth/facebook/callback
   ```

   (Use your port if you changed it; Settings shows the exact address.)
5. In **App settings → Basic**, copy the **App ID** and **App secret** into
   **Settings → Facebook Page**, and click **Save**.
6. Click **Connect Facebook**, choose your Page(s), and approve.
7. If you have several Pages, choose one under **Page to post to** and click **Save**.
8. Click **Test connection**.

If your app uses **Facebook Login for Business**, create a login configuration with the
permissions above and put its ID in **Login configuration ID**. Auto Poster then sends
`config_id` instead of a permission list.

### If "Connect Facebook" doesn't work

Use Meta's own token tool instead. No redirect address is needed:

1. Open the [Graph API Explorer](https://developers.facebook.com/tools/explorer).
2. Select your app, add the four permissions above, and click **Generate Access Token**.
3. Paste the token into **Access token from Graph API Explorer**, click **Save**, then
   **Load my Pages**.

Auto Poster swaps it for long-lived Page tokens and then deletes the pasted token.

## How the connection works

1. Sign-in (or the Graph API Explorer) gives a short-lived **user token**.
2. Auto Poster exchanges it for a **long-lived user token** (`grant_type=fb_exchange_token`, about 60 days).
3. `GET /me/accounts` returns the Pages you allowed, each with a **Page token**. Page tokens
   obtained from a long-lived user token **don't expire** (Meta can still invalidate them,
   for example if you change your Facebook password).
4. Only the Page tokens are kept, in the OS credential store.

All Graph API calls send the token in the `Authorization` header (never in the URL) and include
`appsecret_proof`, so they also work if your app has "Require app secret" switched on.

## What gets posted

| Post contains | API call |
|---|---|
| Text only | `POST /{page-id}/feed` with `message` |
| Text + 1 image | `POST /{page-id}/photos` with `source` (file) and `caption` |
| Text + 2–10 images | Each image to `/{page-id}/photos` with `published=false`, then `POST /{page-id}/feed` with `attached_media` |

## Limits

| Rule | Value | Source |
|---|---|---|
| Text | 63,206 characters | Facebook post limit |
| Image size | up to 10 MB (PNG works best under 1 MB) | Page Photos reference |
| Image formats | JPEG, PNG, GIF (Meta also accepts BMP/TIFF) | Page Photos reference |
| Images per post | 10 (Auto Poster's own cap; Meta documents no fixed number) | — |

## API version

Auto Poster uses Graph API **`v26.0`**, set once as `GRAPH_VERSION` in
`backend/auto_poster/connectors/facebook.py`. Meta supports each version for about two years;
check the [changelog](https://developers.facebook.com/docs/graph-api/changelog) when updating.

## Common problems

| Message | What to do |
|---|---|
| Facebook did not accept the App ID or App secret | Copy them again from App settings → Basic. |
| Facebook no longer accepts Auto Poster's access | You changed your password or removed the app. Click **Connect Facebook** again. |
| Auto Poster isn't allowed to post to this Page | Check you're a Page admin and the app has `pages_manage_posts`; connect again. |
| Your role on the Page doesn't allow creating posts | Ask a Page admin for content permissions. |
| No Facebook Pages were found | Select your Page when approving, and make sure you manage it. |
| Facebook rejected the sign-in address | Add the redirect URI from the setup guide, or use the Graph API Explorer option. |

## Safe testing for developers

- `pytest` mocks every Graph API request; nothing is posted.
- For manual tests, create a **test Page** and leave it unpublished, so test posts aren't public.
  Delete test posts afterwards.
