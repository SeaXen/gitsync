# Security model

What gitsync stores, what it never stores, how the web UI authenticates,
and which network guards are on by default.

Short version: credentials live in `config.json` (mode 0600) and in the hub's
git config; they are redacted from every log line, job output and API
response. Nothing is sent anywhere except the platforms you configure.

## Privacy: nothing personal in the code

This repository contains **no credentials, no tokens, no webhooks and no
personal paths** - everything lives in your own
`~/.config/gitsync/config.json` (gitignored, mode 0600).

A fresh clone is completely generic - default config
`~/.config/gitsync/config.json`, default mirror hub
`~/.local/share/gitsync/mirrors`, github.com/gitlab.com URLs, no accounts,
no webhooks. Your own accounts, folders and directions are supplied during
`gitsync setup` and stored only on your machine.

The tool is generic by design: **any account, any folder, any direction.**

---

[← back to the README](../README.md)

## Web authentication

- **Token:** a random 32-byte value in `~/.config/gitsync/web.token` (0600),
  sent as `Authorization: Bearer …`. Rotate it by deleting the file and
  restarting, or by setting `GITSYNC_WEB_TOKEN`.
- **Password:** `gitsync web --set-password NAME` stores a PBKDF2-SHA256 hash
  with a per-user salt in `config.json`; the plaintext is never stored.
- **Sessions:** a random opaque id in an `HttpOnly`, `SameSite=Strict` cookie.
  Only `sha256(id)` is persisted (`sessions.json`, 0600). 12 idle hours, 30
  days absolute, revocable one at a time or all at once.
- **Cookies are never used to carry the token**, so an XSS bug cannot read a
  credential out of `document.cookie`.

Every request — including `/metrics`, and including the login route — passes a
Host allow-list first (DNS-rebinding guard). Failures read
`add it to GITSYNC_ALLOWED_HOSTS`.

## Guards on by default

| Guard | Behaviour |
|---|---|
| Host allow-list | loopback, private IPs, the bind host, `*.local`/`*.lan` and `--allowed-host` names are accepted; nothing else |
| CSRF | state-changing requests need an `Origin`/`Sec-Fetch-Site` that matches, or a Bearer token (which is not ambient) |
| Login throttle | repeated failures lock that source address out for a while |
| Body limit | oversized bodies are rejected with 413, not buffered |
| CSP | `script-src 'self'` — no inline JavaScript, no eval. `style-src 'self'` too: no markup carries a `style=""` attribute (one-off tweaks are utility classes in `style.css`, the 404 page links its own stylesheet). The only CSS applied at runtime is a progress bar's width through the CSSOM, which CSP does not police |
| Redaction | tokens, webhook URLs and passwords are scrubbed from logs, job output and API responses |

Logs live at `~/.local/share/gitsync/gitsync.log` (0600). Audit lines (sign-in,
revoked sessions, mutating jobs) are written there too.
