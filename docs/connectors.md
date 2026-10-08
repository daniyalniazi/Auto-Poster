# Adding a platform (connector)

Each social platform is one Python file in `backend/auto_poster/connectors/`. You don't need
to understand the rest of the app to add one: the UI, validation, history and scheduling
all work through the same small interface.

Read `connectors/telegram.py` first — it is the simplest complete example.

## 1. Research the official API

Before writing code, read the platform's **official** developer documentation and note:
authentication, permissions/scopes, text limits (and how characters are counted),
image limits (count, size, formats, dimensions), rate limits, token expiry, API version,
and anything that needs special approval. Do not copy from old tutorials or unofficial
libraries. If something we'd need is not allowed by the official API, open an issue
instead of building a workaround.

## 2. Create the connector file

```python
# backend/auto_poster/connectors/example.py
from auto_poster.connectors.base import Config, Connector, Options
from auto_poster.models import (ConnectionStatus, FieldSpec, PlatformError,
                                PlatformLimits, Post, PostResult, SetupGuide)

API_BASE = "https://api.example.com/v2"      # API URL + version in one place

LIMITS = PlatformLimits(                      # values from the official docs
    max_chars=500,
    count_method="chars",                     # "chars", "utf16" or "graphemes"
    max_images=4,
    max_image_bytes=8 * 1024 * 1024,
    image_formats=["JPEG", "PNG"],
)

class ExampleConnector(Connector):
    id = "example"
    display_name = "Example"
    description = "Post to your Example account."
    limits = LIMITS
    settings_fields = [                       # shown on the Settings page
        FieldSpec(key="token", label="Access token", kind="secret", required=True,
                  help="Where to find it, in plain words."),
    ]
    post_fields = []                          # per-post options on the Create post page
    setup_guide = SetupGuide(steps=["Step one…", "Step two…"], docs_url="https://…")

    async def test_connection(self, config: Config) -> ConnectionStatus:
        async with self.http() as client:
            ...                               # call a read-only endpoint
        return ConnectionStatus(ok=True, message="Connected as …")

    async def post(self, post: Post, options: Options, config: Config) -> PostResult:
        async with self.http() as client:
            ...                               # upload images, create the post
        return PostResult(platform=self.id, success=True, post_id=..., post_url=...,
                          message="Posted to Example.")
```

### The interface

| Member | Purpose |
|---|---|
| `settings_fields` | Settings form. `kind="secret"` fields go to the OS keyring and are shown masked. |
| `post_fields` | Options on the post form (only shown when the platform is selected). Values arrive in `options` as strings; checkboxes are `"true"`/`"false"`. |
| `limits` | Used by the shared `validate()` for text length and image checks, and by the UI's character counter. |
| `format_post(content)` | Structured mode: turns the title, body, link and hashtags into this platform's text. The default puts each in its own paragraph; override for platform style. Set `max_hashtags` to cap hashtags (the user sees a warning). The user can still hand-edit the result per platform. |
| `validate(post, options, config)` | Local checks only, no network. The base version checks `limits`; override it and call `super()` for extra rules. Return `Problem`s with `level="warning"` for things that won't block publishing. |
| `test_connection(config)` | Check the settings work **without posting**. |
| `post(post, options, config)` | Publish. Return a `PostResult` on success; **raise `PlatformError`** on failure. |
| `actions` + `run_action()` | Optional extra Settings buttons (e.g. "Connect LinkedIn" for OAuth). |
| `internal_secret_keys` + `save_secret()` | For tokens the connector stores itself (sessions, OAuth tokens). |
| `save_value()` | For non-secret values the connector learns (account IDs). |

`config` contains the saved settings **including secrets**. Never log it or put it into
messages.

### Errors

Raise `PlatformError(error_code, message, technical_details, retry_after)`:

- `message` is shown to the user. Write it in plain English: what happened, why it probably
  happened, what to do. No HTTP codes or jargon.
- `technical_details` is shown under "Details". Include the HTTP status and the platform's
  error text. It is passed through the redactor, but don't put tokens in it.
- `error_code` is one of the codes in `models.ErrorCode` (`invalid_credentials`,
  `missing_permission`, `rate_limited`, `invalid_media`, …).
- For rate limits, set `retry_after` (seconds) if the API says. **Never retry in a loop.**

Network errors, timeouts and unexpected exceptions are turned into friendly results by the
base class (`safe_post`, `safe_test_connection`), so you only handle the platform's own errors.

### Images

`post.images` is a list of `ImageFile` (already checked against your `limits`). Use
`image.read_bytes()`, `image.mime_type`, `image.alt_text`, `image.width`, `image.height`.
If the platform has a strict size limit that users will often hit, use
`services.media.fit_to_size()` to compress, and warn in `validate()` (see `bluesky.py`).

## 3. Register it

Add it to the list in `connectors/registry.py`. It now appears on the Settings and Create
post pages automatically.

## 4. Write tests

Create `backend/tests/test_<platform>.py`. Mock every HTTP call with `respx` (see
`test_telegram.py`). Cover at least: valid connection, invalid credentials, missing settings,
a valid post, a too-long post, invalid/unsupported images, an API error, rate limiting, a
network failure, and parsing a successful response. Tests must never call the real API.

## 5. Document it

Add `docs/platforms/<platform>.md`: how to connect (steps a non-technical person can follow),
what gets posted, limits with their sources, common errors, the API version, and how to test
safely. Add the platform to the table in `README.md`.

## 6. Run everything

```bash
pytest
cd frontend && npm test && npm run build
```
