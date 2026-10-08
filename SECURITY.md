# Security

## Reporting a vulnerability

Please report security problems privately using GitHub's
[private vulnerability reporting](https://github.com/daniyalniazi/Auto-Poster/security/advisories/new)
rather than a public issue.

## How Auto Poster protects your accounts

Auto Poster runs only on your computer and talks only to the official APIs of the platforms
you connect. There is no project server, account, analytics or telemetry.

| Risk | Protection |
|---|---|
| Other websites in your browser calling the local app | The server listens on `127.0.0.1` only. Every API request needs a random per-launch session token in a header, and `Host`/`Origin` are checked (blocks CSRF and DNS rebinding). |
| Someone on your network | Nothing listens on network interfaces, only on your own computer. |
| Credentials on disk | Stored in the OS credential store (Windows Credential Manager, or Secret Service / GNOME Keyring / KWallet on Linux). Never in the database. |
| No keyring available (some minimal Linux setups) | Falls back to a file readable only by your user account (`0600`), with a clear warning in Settings. |
| The UI / browser developer tools | The backend never sends a stored secret to the browser; it only sends a mask like `••••1234`. Secrets are never in the JavaScript bundle. |
| Secrets in URLs | Never placed in URLs: browser sign-in uses single-use `state` values, and Facebook tokens go in the `Authorization` header. (Telegram's Bot API requires the bot token in the request path; it's only ever sent to `api.telegram.org` over HTTPS and is masked in logs.) |
| Logs, error details and history | A log filter masks every known secret and token-like pattern, and `httpx` request logging is off unless `--debug`. Error "Details" shown in the UI and saved in history go through the same redaction. |
| Database dumps and backups | The SQLite database contains settings, history and post text, but no secrets. Auto Poster backups (Settings → Your data) contain that database and your images, never passwords or tokens. Restoring checks the file first and only accepts the expected contents. |
| Git leaks | Settings live outside the repository. `.gitignore` excludes `.env`, databases and media. Tests use fake values. |
| Browser sign-in hijacking | OAuth `state` is random, single-use and expires after 15 minutes. PKCE is used where supported (Mastodon). |
| Crash reports | There are none: nothing is ever sent anywhere automatically. |

## What Auto Poster cannot protect against

No local app can be perfectly secure. In particular:

- **Malware running as your user account** can read your keyring entries, the fallback file,
  and anything you can see in your browser. Keep your computer up to date and free of malware.
- **Anyone who can log in as you** has the same access you do.
- **Backups of your OS keyring or home folder** contain your tokens if they contain the keyring
  or fallback file.

To limit the damage if a token leaks, use the narrowest credentials each platform offers
(Bluesky app passwords, a dedicated Telegram bot, the minimum OAuth permissions), and revoke
them from the platform's settings if you stop using Auto Poster.

## Where your data lives

| Data | Location |
|---|---|
| Settings (non-secret), history, scheduled posts | `auto-poster.db` in your data folder |
| Images waiting to be posted | `media/` in your data folder (deleted after posting) |
| Tokens and passwords | OS keyring (service name `auto-poster`), or `secrets.json` in the data folder as fallback |

Data folder: `%APPDATA%\AutoPoster` on Windows, `~/.local/share/auto-poster` on Linux.
Delete it (and the `auto-poster` keyring entries) to remove everything.
