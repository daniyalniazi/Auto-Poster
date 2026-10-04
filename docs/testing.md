# Testing

## Automated tests (safe: nothing is ever posted)

```bash
# backend
pytest

# frontend
cd frontend && npm test
```

- Every platform request is mocked with [respx](https://lundberg.github.io/respx/).
  If a test forgets to mock a request, respx raises an error instead of calling the real API.
- Tests use a temporary data folder and an in-memory secret store
  (see `backend/tests/conftest.py`). They never read or write your real settings or keyring.

Each connector's test file covers: valid and invalid credentials, missing settings,
a valid post, a too-long post, invalid and unsupported images, API errors, rate limits,
network failures and parsing a successful response.

## Manual tests (these create real posts)

Manual testing means running the app and publishing for real. **Every Publish creates a
real post.** Always use test accounts:

| Platform | Safe test setup |
|---|---|
| Telegram | A private channel with only you in it, and a separate test bot. |
| Bluesky | A separate test account. |
| Mastodon | A test account, ideally on a server that allows test accounts, posted with visibility "Only me" (direct) or "Unlisted". |
| LinkedIn | Your own profile with visibility "Connections only", deleted right after. LinkedIn has no sandbox for personal posts. |
| Facebook | A test Page that you create and leave unpublished. |

See each platform's page in `docs/platforms/` for details.

## Running the app against a throwaway data folder

```bash
AUTO_POSTER_DATA_DIR=/tmp/auto-poster-test auto-poster
```

On Windows PowerShell:

```powershell
$env:AUTO_POSTER_DATA_DIR = "$env:TEMP\auto-poster-test"; auto-poster
```

Secrets still go to your OS keyring. To keep them in the test folder instead, also set
`AUTO_POSTER_SECRET_STORE=file`.
