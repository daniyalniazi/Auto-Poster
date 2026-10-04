# Auto Poster

Write once. Publish to multiple social platforms.

| Platform | Status |
|---|---|
| Telegram (channels and groups) | ✅ Available |
| Bluesky | ✅ Available |
| Mastodon (any server) | ✅ Available |
| LinkedIn (personal profile) | Planned |
| Facebook Pages | Planned |

Runs locally. No account required. No cloud backend. Open source.

> **Status:** early development (Phase 4 of 7). Telegram, Bluesky and Mastodon posting, history and scheduling work. See
> [docs/architecture.md](docs/architecture.md) for the plan.

## What it does

1. Write a post and attach images.
2. Tick the platforms that should receive it.
3. Auto Poster checks each platform's rules (length, image size, format…) before you publish.
4. Click **Publish now**, or choose **Later** to schedule it.
5. See which platforms worked and which didn't, with a plain-English reason. Everything is kept in **History**.

### Scheduling

Scheduled posts are sent by Auto Poster itself, so **it must be running** at the scheduled
time (you can minimise the window). If it was closed, posts up to an hour late are sent when you
open it again; older ones are marked **Missed** so nothing goes out unexpectedly, and you can
send or reschedule them from the **Scheduled** page.

## Your data stays with you

- Auto Poster runs on your computer and opens in your web browser. It is not a website.
- Posts and history are saved on your computer only.
- Passwords and tokens are kept in your system's secure password storage
  (Windows Credential Manager, or GNOME Keyring/KWallet on Linux).
- Images are only sent to the platforms you choose.
- No accounts, no analytics, no telemetry. The project never receives your data.

## Install and run (from source)

Ready-to-run downloads for Windows and Linux are planned. For now you need
[Python 3.11+](https://www.python.org/downloads/) and [Node.js 20+](https://nodejs.org/).

```bash
git clone https://github.com/daniyalniazi/Auto-Poster.git
cd Auto-Poster

# 1. Build the user interface (once, and after updates)
cd frontend
npm install
npm run build
cd ..

# 2. Install and start the app
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux:    source .venv/bin/activate
pip install -e .
auto-poster
```

Your browser opens at `http://127.0.0.1:8765`. Keep the terminal window open while you use
the app; press `Ctrl+C` to stop it.

Options: `auto-poster --port 9000`, `--no-browser`, `--debug` (more detailed logs; secrets stay hidden).

## Connecting platforms

Open **Settings** in the app. Each platform has a short "How to connect" guide.
More detail: [Telegram](docs/platforms/telegram.md), [Bluesky](docs/platforms/bluesky.md), [Mastodon](docs/platforms/mastodon.md).

## Not supported

Instagram, X/Twitter, TikTok, personal Facebook profiles, LinkedIn company pages,
browser automation, scraping, and unofficial APIs.

## Development

```bash
pip install -e ".[dev]"
pytest                          # backend tests (never post anything)
cd frontend && npm test         # frontend tests

# Live-reloading UI during development:
auto-poster --dev --no-browser  # terminal 1
cd frontend && npm run dev      # terminal 2, then open http://localhost:5173
```

- How it fits together: [docs/architecture.md](docs/architecture.md)
- Testing guide: [docs/testing.md](docs/testing.md)
- Adding a platform: [docs/connectors.md](docs/connectors.md)

## License

[MIT](LICENSE)
