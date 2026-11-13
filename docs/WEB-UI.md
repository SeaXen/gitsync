# Web UI

`gitsync web` serves the dashboard on `http://127.0.0.1:7765`. Every TUI
feature is there — platforms, routes, repos, logs, run history, tools and
settings — plus live progress over Server-Sent Events.

Sign-in is either the machine token (`~/.config/gitsync/web.token`, sent as
`Authorization: Bearer …`) or a username + password you set with
`gitsync web --set-password NAME`. A browser session is an opaque id in an
`HttpOnly` cookie: it expires after 12 idle hours or 30 days absolute, and you
can revoke one session or all of them from **Settings → Sessions**.

## Web UI (`gitsync web`)

Everything the TUI does, from a browser — plus live streaming. Zero new
dependencies (Python standard library on the server; plain HTML/CSS/vanilla JS
on the front end; no build step).

```bash
gitsync web                         # prints the URLs; reachable on your LAN
gitsync web --bind 127.0.0.1        # this machine only
gitsync web --port 8484             # your bind/port are remembered
```

It binds `0.0.0.0` by default (a browser dashboard is meant to be opened from
another device) and prints the exact URLs to open, e.g.
`http://192.168.0.222:7765`. **Authentication is always required.** Restrict
to this machine any time with `--bind 127.0.0.1` — the choice is saved, so you
set it once.

Open the URL, paste the **access token** printed on first run (stored at
`~/.config/gitsync/web.token`, mode `0600`; set `GITSYNC_WEB_TOKEN` to choose
your own), or sign in with the **username + password** you set, and you get a
dark, terminal-flavoured dashboard with tabs for
Dashboard, Platforms, Routes, Repos, Stars, Log, History, Tools and Settings.

Every TUI feature has a web equivalent — accounts, routes (pause a route *or*
one target), the repo browser with selective sync and save-to-file, star copy,
the full log viewer, run history, options, notifications, webhook control,
scheduler install, and the tools (doctor, compare, fix, tokens, disk, gc,
restore, mirror). **Sync now / Dry run** stream the real
`python3 -m gitsync sync` subprocess to the browser over Server-Sent Events,
with a live `[done/total]` progress bar — nothing is reimplemented, so it can
never drift from the CLI.

**Always-on (survives reboot):** run it in the foreground with `gitsync web`
(Ctrl-C to stop), or install it as a systemd service so it starts on boot:

```bash
gitsync web --install          # user service on 0.0.0.0:7765, starts on boot
loginctl enable-linger "$USER" # keep it running while logged out
gitsync web --status           # is it running?
gitsync web --uninstall        # remove it
journalctl --user -u gitsync-web -f   # logs
```

The first start prints the access token to the journal
(`journalctl --user -u gitsync-web`). For an always-on dashboard, set a
password so you don't depend on that one-time token:
`gitsync web --set-password USER`.

**Security** (the config holds platform tokens, so this is the priority):

- binds `0.0.0.0` by default so it's reachable on your LAN (it's a browser
  dashboard); **authentication is always required on every request**. Restrict
  to this machine with `gitsync web --bind 127.0.0.1` (the bind/port is saved,
  so you choose once). Behind a reverse proxy, add the public host with
  `--allowed-host` / `GITSYNC_ALLOWED_HOSTS`
- a random access token guards every `/api` call (`Authorization: Bearer` or an
  `HttpOnly, SameSite=Strict` cookie, compared with `hmac.compare_digest`)
- **optional username + password sign-in**: set one in Settings (or
  `gitsync web --set-password USER`); it is stored as a PBKDF2 hash (mode
  `0600`, never the plaintext, never returned), with a brute-force throttle.
  The access token still works for API/curl. Web/Docker only — the TUI has no
  login
- a token is **never** returned in any response, log line or page — only state
  (`set` / `missing` / `gh` / `glab`); token fields are write-only in forms; a
  redactor strips any known token from every response and streamed line
- CSRF guard (a custom header on cookie sessions) and a DNS-rebinding guard
  (Host/Origin allowlist; loopback + private IPs are allowed for LAN access —
  add a proxy domain with `--allowed-host` or `GITSYNC_ALLOWED_HOSTS`)
- all subprocesses are argv lists (never a shell); names/paths/URLs are
  validated; static files and local-folder targets are confined (no traversal)
- config writes are serialized (a process lock + `flock`, so the web server and
  the TUI never clobber each other) and atomic at mode `0600`; the first-run
  token is **not** printed to the journal/pipe (read `web.token`); auth
  failures, guard rejections, 5xx and mutations are audit-logged (redacted)
- one sync at a time: the web layer respects the engine's `sync.lock` and
  reports "already running" cleanly

**Behind a reverse proxy:** terminate TLS at the proxy, forward to
`127.0.0.1:7765`, and pass the public host with `--allowed-host your.domain`
(or `GITSYNC_ALLOWED_HOSTS`). Keep the token required.

---

[← back to the README](../README.md)

## Theme (Auto / Dark / Light)

The top bar has a theme button and **Settings → Appearance** has the same
control. Three choices:

| Choice | Behaviour |
|---|---|
| **Auto** (default) | follows the operating system's light/dark preference, live |
| **Dark** | always the dark palette |
| **Light** | always the light palette |

The choice is stored in the browser (`localStorage`, key `gitsync-theme`) and
applied before first paint by `theme.js`, so there is no flash of the wrong
theme. It is per browser and is never written to the server's settings — your
phone and your desktop can differ. Every colour pair keeps at least 4.5:1
contrast, and the palette respects `prefers-reduced-motion`.

## Sessions

A sign-in is a random opaque id in an `HttpOnly`, `SameSite=Strict` cookie. The
server stores only its SHA-256, in `sessions.json` (mode 0600) next to the
config, so a copied file cannot be replayed. Sessions end after **12 idle
hours** or **30 days** absolute. **Settings → Sessions** lists them (browser,
IP, last seen) with a **Sign out everywhere** button; changing or clearing the
password revokes all of them and signs the current browser back in.

The machine token (`Authorization: Bearer …`) still works and is the way to
script the API — for example Prometheus:

```yaml
scrape_configs:
  - job_name: gitsync
    authorization:
      credentials_file: /root/.config/gitsync/web.token
    static_configs:
      - targets: ["192.168.0.222:7765"]
```

## Metrics (`/metrics`)

Prometheus text format, same authentication as every other endpoint:

```
gitsync_up 1
gitsync_info{version="2.1.4"} 1
gitsync_paused 0
gitsync_syncing 0
gitsync_stale 0
gitsync_platforms 4
gitsync_routes 2
gitsync_sessions_active 1
gitsync_hub_mirrors 53
gitsync_hub_bytes 4812345678
gitsync_last_run_timestamp_seconds 1763000000
gitsync_last_run_ok 1
gitsync_last_run_repos 53
gitsync_last_run_synced 53
gitsync_last_run_failed 0
gitsync_last_run_seconds 42.1
gitsync_last_success_timestamp_seconds 1763000000
```

Every value is cached or a small file read: a 15-second scrape never shells out
to git. `gitsync_stale 1` means no successful run within twice the schedule —
the same flag the dashboard banner uses.

A ready-to-import Grafana dashboard is not shipped; the metric names above are
the whole contract.

## Reverse proxy

Set `--bind 127.0.0.1` when nginx/Caddy is in front, and tell the server which
peers may set forwarding headers:

**Settings → Network → Trusted proxies** (or `settings.trusted_proxies` in
`config.json`) takes a comma-separated CIDR list, e.g. `127.0.0.1/32,
192.168.0.0/24`. Only when the peer address is inside that list does the
login throttle and the audit log use the right-most untrusted entry of
`X-Forwarded-For` (and honour `X-Forwarded-Proto` for cookie security). With
the list empty — the default — both headers are ignored entirely, so a client
cannot fake its address.

If the proxy serves a domain name rather than an IP, add it to the allowed
hosts, or every request gets a 403:

```bash
gitsync web --allowed-host git.example.com      # or, comma-separated:
GITSYNC_ALLOWED_HOSTS=git.example.com gitsync web
```
