# Contributing to Auto Poster

Thanks for helping! Auto Poster is meant to stay **small, local and easy to understand**.
You don't need to know the whole codebase to contribute. Most changes touch one connector
file and its tests.

## Get set up

You need [Python 3.11+](https://www.python.org/downloads/), [Node.js 20+](https://nodejs.org/) and Git.

```bash
git clone https://github.com/daniyalniazi/Auto-Poster.git
cd Auto-Poster

python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux:    source .venv/bin/activate
pip install -e ".[dev]"

cd frontend
npm install
npm run build
cd ..
```

## Run the app

```bash
auto-poster
```

To work on the UI with live reload, use two terminals:

```bash
auto-poster --dev --no-browser      # backend on http://127.0.0.1:8765
cd frontend && npm run dev          # UI on http://localhost:5173 (open this one)
```

Use a throwaway data folder so you don't touch your real settings:

```bash
AUTO_POSTER_DATA_DIR=/tmp/ap-dev AUTO_POSTER_SECRET_STORE=file auto-poster
```

(PowerShell: `$env:AUTO_POSTER_DATA_DIR="$env:TEMP\ap-dev"; $env:AUTO_POSTER_SECRET_STORE="file"; auto-poster`)

## Run the tests

```bash
pytest                     # backend: every platform API call is mocked
cd frontend && npm test    # frontend
```

**The test suite never posts anything.** If you add a test that forgets to mock an HTTP
call, `respx` makes it fail instead of reaching the real API. See [docs/testing.md](docs/testing.md)
for safe manual testing with test accounts.

## Project layout

```
backend/auto_poster/
  connectors/   one file per platform + base.py (shared interface) + registry.py
  services/     publishing, validation, history, scheduler, media, browser sign-in
  security/     credential storage, log redaction, local request guard
  database/     SQLite schema (migrations are appended, never edited)
  api/          HTTP routes used by the UI
  main.py       starts the server and opens the browser
backend/tests/  pytest tests, one file per connector plus app/scheduler tests
frontend/src/   React + TypeScript UI (pages/, components/, services/)
docs/           architecture, connector guide, per-platform pages, testing
scripts/        build.py makes the Windows/Linux downloads
```

Start with [docs/architecture.md](docs/architecture.md) for the big picture.

## Adding a platform

Read **[docs/connectors.md](docs/connectors.md)**. In short:

1. Read the platform's **official** API docs (not tutorials or unofficial libraries).
2. Create `backend/auto_poster/connectors/<platform>.py` implementing `test_connection()`,
   `validate()` and `post()`, with limits from the official docs.
3. Add it to `connectors/registry.py`. The UI picks it up automatically.
4. Write `backend/tests/test_<platform>.py` with mocked responses.
5. Write `docs/platforms/<platform>.md` and add the platform to the README table.

Please open an issue first for a new platform, so we can check that the platform's API
officially allows what's needed. **Pull requests using browser automation, scraping or
unofficial APIs won't be accepted.**

## Coding expectations

- Simple, readable code over clever code. Small functions, clear names, type hints.
- Comments explain *why*, not what the code obviously does.
- User-facing messages are plain English: what happened, why, and what to do. No HTTP codes
  or jargon (put those in `technical_details`, which appears under "Details").
- Platform limits and API versions live in constants at the top of the connector file.
- Don't add a dependency for something small. If you do add one, explain why in the PR.
- No telemetry, analytics or calls to any server other than the platforms' official APIs.

## Never commit secrets

- Never put real tokens, passwords or app secrets in code, tests, docs or screenshots.
  Use obviously fake values such as `123456789:AAFakeToken...`.
- Your local settings live outside the repository (in your user data folder and OS keyring),
  so they can't be committed by accident. `.gitignore` also excludes `.env`, databases and media.
- When sharing logs or error details, check them first. Auto Poster masks secrets, but double-check.

## Pull requests

- One focused change per PR, with a short description of what and why.
- `pytest`, `npm test` and `npm run build` pass (CI checks this on Windows and Linux).
- New behaviour has tests. New platforms have docs.
- If the change affects users, update the README or docs.

## Reporting bugs

Open an [issue](https://github.com/daniyalniazi/Auto-Poster/issues) using the bug report
template. Include the message Auto Poster showed and its "Details", **after checking they
contain no secrets**.

For security problems, please don't open a public issue. See [SECURITY.md](SECURITY.md).
