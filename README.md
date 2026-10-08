# Auto Poster

Write once. Publish to multiple social platforms.

| Platform | What you can post | What you need |
|---|---|---|
| **Telegram** | Channels and groups, text and up to 10 images | A bot (made in Telegram, 2 minutes) |
| **Bluesky** | Text with links/mentions/hashtags, up to 4 images | An app password |
| **Mastodon** | Any server; visibility and content warnings, images | Your server address, then click Connect |
| **LinkedIn** | Your personal profile, text and up to 20 images | A free LinkedIn developer app (one-time, ~10 min) |
| **Facebook Pages** | Pages you manage, text and up to 10 images | A free Meta developer app (one-time, ~15 min) |

Runs locally. No account required. No cloud backend. Open source (MIT).

## What it does

1. Write your post once and attach images. Use **Quick post** (one text box, posted as typed)
   or **Structured** (title, text, link and hashtags, which each platform arranges its own way).
2. Tick the platforms that should receive it, and fill in any platform options.
   **How it will look** shows each platform's version. Edit a version to change it for that
   platform only, or click **Use automatic version** to go back.
3. Auto Poster checks each platform's rules (length, image size, format…) **before** you publish.
4. Click **Publish now**, or choose **Later** to schedule it.
5. See which platforms worked and which didn't, with a plain-English reason. Everything is kept in **History**.

Each platform is posted to separately: if one fails, the others still go out, and you see
exactly which one failed and why.

## Your data stays with you

- Auto Poster runs on your computer and opens in your web browser. It is not a website.
- Posts, history and scheduled posts are saved on your computer only.
- Passwords and tokens are kept in your system's secure password storage
  (Windows Credential Manager, or GNOME Keyring / KWallet on Linux), never in plain files
  unless your system has no password storage (you'll see a warning if so).
- Images are only sent to the platforms you choose.
- No accounts, no analytics, no telemetry. The project never receives your data.

More detail: [SECURITY.md](SECURITY.md).

## Install

### Download (easiest)

Download the latest version from the [Releases page](https://github.com/daniyalniazi/Auto-Poster/releases):

- **Windows:** `AutoPoster-windows-x64.exe`. Double-click it. Windows may warn that the app is
  from an unknown publisher (it isn't code-signed); click **More info → Run anyway**.
- **Linux:** `AutoPoster-linux-x64`. Make it executable and run it:
  `chmod +x AutoPoster-linux-x64 && ./AutoPoster-linux-x64`

A small window shows that Auto Poster is running, and your browser opens it at
`http://127.0.0.1:8765`. **Keep that window open** while you use the app (and for scheduled posts
to be sent). Close it to stop Auto Poster. Starting it again while it's running just reopens the browser tab.

### From source

You need [Python 3.11+](https://www.python.org/downloads/) and [Node.js 20+](https://nodejs.org/).

```bash
git clone https://github.com/daniyalniazi/Auto-Poster.git
cd Auto-Poster

cd frontend && npm install && npm run build && cd ..

python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux:    source .venv/bin/activate
pip install -e .
auto-poster
```

Options: `auto-poster --port 9000`, `--no-browser`, `--debug` (more detailed logs; secrets stay hidden).

## Connecting platforms

Open **Settings** in the app. Each platform has a step-by-step "How to connect" guide. Full guides:

- [Telegram](docs/platforms/telegram.md)
- [Bluesky](docs/platforms/bluesky.md)
- [Mastodon](docs/platforms/mastodon.md)
- [LinkedIn](docs/platforms/linkedin.md): connections must be renewed every 60 days (one click)
- [Facebook Pages](docs/platforms/facebook.md)

**Why do LinkedIn and Facebook need my own developer app?** Both only allow registered apps to
post, and each app has a secret key. Sharing one key with every user would require a project-run
server, which Auto Poster deliberately doesn't have. Creating your own app is free and done once.

## Scheduling

Choose **Later** on the Create post page. Scheduled posts are sent by Auto Poster itself, so
**it must be running** at the scheduled time (you can minimise the window). If it was closed,
posts up to an hour late are sent when you open it again; older ones are marked **Missed** so
nothing goes out unexpectedly, and you can send or reschedule them from the **Scheduled** page.
Posts are never sent twice, even if the app is closed mid-send.

## Why did my post fail?

Every failure in History says what happened and what to do, for example
"Telegram did not accept the bot token…" or "The text is 412 characters, but Bluesky allows at
most 300". Click **Details** for the technical error. Common causes:

- **Not connected / expired:** open Settings, reconnect, then **Test connection**.
- **Too long or too many images:** the post form shows this before you publish; shorten the
  text or remove images for that platform.
- **Could not reach …:** your internet connection is down, or that platform is blocked on your network.
- **Limiting requests:** the platform's rate limit; wait the time shown and try again.

## Not supported

Instagram, X/Twitter, TikTok, personal Facebook profiles, LinkedIn company pages,
browser automation, scraping, and unofficial APIs.

## For developers

```bash
pip install -e ".[dev]"
pytest                          # backend tests: all platform calls are mocked, nothing is posted
cd frontend && npm test         # frontend tests

auto-poster --dev --no-browser  # terminal 1
cd frontend && npm run dev      # terminal 2, then open http://localhost:5173

pip install -e ".[build]" && python scripts/build.py   # build the download for this OS
```

- [How it fits together](docs/architecture.md)
- [Adding a platform](docs/connectors.md)
- [Testing (including safe manual testing)](docs/testing.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Releases are built automatically for Windows and Linux when a version tag (e.g. `v1.0.0`) is pushed.

## Reporting bugs

Please open an [issue](https://github.com/daniyalniazi/Auto-Poster/issues). Include the message
Auto Poster showed and its "Details", after checking they contain no passwords or tokens.

## License

[MIT](LICENSE)
