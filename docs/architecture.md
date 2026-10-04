# Architecture

Auto Poster is a small local application. A Python process runs on the user's
computer, serves the user interface, and talks to social platforms through
their official APIs. There is no project-run server, no account, and no
telemetry.

```
 Browser (React UI)  ──HTTP, 127.0.0.1 only──▶  Local Python server (FastAPI)
                                                  │
                                                  ├── services/   posting, validation, history
                                                  ├── connectors/ one file per platform
                                                  ├── database/   SQLite (non-secret data)
                                                  └── security/   OS keyring access, log redaction
                                                         │
                                                         ▼
                                   Official platform APIs (Telegram, Bluesky, ...)
```

## Decisions

| Topic | Decision |
|---|---|
| App shape | Local web app. Launching it starts the server and opens the default browser. |
| Backend | Python 3.12+, FastAPI, `httpx` for HTTP. |
| Frontend | React + TypeScript built with Vite. Plain CSS, no UI kit. Built files are served by the backend. |
| Database | SQLite via the standard-library `sqlite3`. Never stores secrets. |
| Secrets | OS keyring via `keyring` (Windows Credential Manager, Linux Secret Service). Fallback: restricted file (see Security). |
| Images | `Pillow` for format/dimension checks and platform-required resizing. Multiple images per post, limited per platform. |
| Tests | `pytest` + `respx` (mocked HTTP). The default test suite never contacts a real platform. |
| Packaging | PyInstaller build per OS with the pre-built frontend bundled. Windows: `.exe`. Linux: tarball (AppImage later). |
| License | MIT. |

## Planned folder structure

```
README.md  LICENSE  CONTRIBUTING.md  .gitignore  pyproject.toml  .env.example
docs/
  architecture.md  connectors.md  testing.md
  platforms/telegram.md bluesky.md mastodon.md linkedin.md facebook.md
backend/auto_poster/
  main.py            app startup, opens browser
  api/               HTTP routes used by the UI
  connectors/        base.py, registry.py, one file per platform
  services/          publishing, validation, history, scheduler
  models/            shared data models (Post, PostResult, PlatformLimits, ...)
  database/          SQLite schema and queries
  security/          credential store, log redaction, local-request guard
backend/tests/
frontend/src/
  pages/  components/  services/
scripts/             build / packaging helpers
```

## Connector interface

Every platform is one file in `connectors/` implementing the same class shape:

```python
class Connector(Protocol):
    id: str                           # "telegram"
    display_name: str                 # "Telegram"
    limits: PlatformLimits            # chars, max images, image bytes, formats, dims
    settings_fields: list[FieldSpec]  # drives the Settings form (secret fields masked)
    post_fields: list[FieldSpec]      # platform-specific options shown on the post form

    async def test_connection(self, creds: Credentials) -> ConnectionStatus: ...
    def validate(self, post: Post, options: dict) -> list[Problem]: ...   # local, no network
    async def post(self, post: Post, options: dict, creds: Credentials) -> PostResult: ...
```

`PostResult` fields: `success`, `platform`, `post_id`, `post_url`,
`user_message` (plain English), `error_code` (our stable category such as
`invalid_credentials`, `rate_limited`, `too_long`), `technical_details`
(redacted, shown under "Details"), `retry_after`.

Connectors are listed in `connectors/registry.py`. The UI asks the backend for
the list of connectors and their field specs, so adding a platform normally
needs no frontend code. Shared concerns (HTTP client setup, error mapping
helpers, image checks against `PlatformLimits`) live in `connectors/base.py`
and `services/`, not in each connector.

API base URLs and version numbers live as constants at the top of each
connector file (e.g. `GRAPH_API_VERSION = "v25.0"`, `LINKEDIN_VERSION = "YYYYMM"`)
and are documented in `docs/platforms/`.

## Publishing flow

1. UI sends text, images, selected platforms and per-platform options.
2. `validate()` runs for each selected platform. Problems are shown before Publish is enabled.
3. On Publish, the backend creates a post record, then calls each selected connector independently.
4. Each result is stored separately. One failure never stops other platforms.
5. No automatic retry loops. Rate-limit responses are recorded with retry info if the API provides it.
6. The Publish button is disabled while publishing, and each publish carries a unique ID so a double-click cannot post twice.

## Security model

**Protections**

- Server binds to `127.0.0.1` only (not reachable from the network).
- A random per-launch session token is required on API calls, and the `Host`/`Origin`
  headers are checked. This stops other websites open in the user's browser
  from silently calling the local server (CSRF / DNS-rebinding).
- Secrets are stored in the OS keyring. SQLite stores only non-secret settings and history.
- The backend never returns a stored secret to the UI; it returns a mask such as `••••1234`.
- Secrets never go in URLs, logs, error messages, or the JavaScript bundle.
- A logging filter redacts known secret values and `Authorization` headers.
- `.gitignore` excludes databases, `.env`, media, build output and virtual environments.

**Keyring fallback.** If no OS keyring is available (some headless or minimal
Linux systems), secrets are stored in a file in the user's app-data directory
with permissions `0600` (readable only by the user's account). The UI and docs
show a clear warning. This is similar protection to an SSH private key.

**Realistic limits.** Malware running as the user's account can read the
keyring, the fallback file, and the browser. Anyone with full access to the
user's account can do the same. Auto Poster cannot protect against that and
does not claim to.

## Platform authentication summary (verify again before each phase)

| Platform | Method | Notes |
|---|---|---|
| Telegram | Bot token from @BotFather | Bot must be added to the channel/group with permission to post. |
| Bluesky | App password (`createSession`) | App passwords are still supported but described as deprecated in favour of OAuth. Production OAuth needs a publicly hosted client-metadata file. v1 uses app passwords and keeps the auth code isolated so OAuth can be added later. |
| Mastodon | Per-instance OAuth app registration, or a personal access token | Limits read from the instance's `/api/v2/instance` configuration. |
| LinkedIn | OAuth with the user's own developer app (Share on LinkedIn product, `w_member_social` + OpenID Connect) | Access tokens last 60 days. Refresh tokens are only for approved partners, so users must reconnect about every 60 days. 150 requests/member/day. |
| Facebook | User's own Meta developer app, Page access token | Needs `pages_manage_posts` and related permissions. Page posting only. Graph API v25.0 at time of writing. |

LinkedIn and Facebook users create their own (free) developer app once and
paste its ID and secret into Settings. This is the only way to stay fully local
without a project-run server.

## Scheduling

Scheduled posts are sent by a background task inside the app (`services/scheduler.py`),
which checks every 15 seconds. **The app must be running** for posts to go out.

- **Claiming:** a due post is switched from `scheduled` to `sending` with one atomic
  `UPDATE ... WHERE status = 'scheduled'`, so it can only be picked up once.
- **No duplicates:** it is published with request ID `scheduled-<id>-<attempt>`. History
  stores request IDs with a `UNIQUE` constraint, so the same attempt can never be posted twice.
- **Restart safety:** if the app closes while a post is `sending`, it is **not** retried on the
  next start. It is marked `failed` with a note asking the user to check the platforms, because
  some platforms may already have it.
- **Late posts:** if the app was closed at the scheduled time, posts up to 60 minutes late are
  sent on the next start. Older ones are marked `missed`, and the user can "Send now" or edit them.
- **No automatic retries.** Failures are recorded in History; the user decides what to do.
- Times are stored in UTC and shown in the computer's local time zone.
- Images of scheduled posts stay in the local media folder until the post is sent or deleted.

## Phases

0. Architecture and plan (this document)
1. Telegram + post form
2. Post history + scheduling
3. Bluesky
4. Mastodon
5. LinkedIn personal profile
6. Facebook Page
7. Polish and open-source release
