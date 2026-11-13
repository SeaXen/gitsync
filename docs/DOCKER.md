# Docker

A container with the web UI and the CLI, no host install. Data lives in
two volumes: `/data/config` and `/data/share`.

Docker has no systemd and no cron. The image runs `gitsync web` and nothing
else, so either press **Sync now** or enable the opt-in scheduler service
(`docker compose --profile sync up -d`).

## Docker

Run the same `gitsync web` as a container. The base image bundles git, so the
build needs no apt/network at build time.

```bash
docker compose up -d               # builds and runs on http://<host>:7765
docker logs gitsync                # first run prints the access token

# or by hand:
docker build -t gitsync .
docker run -d -p 7765:7765 \
  -v gitsync-config:/data/config \
  -v gitsync-share:/data/share \
  -e GITSYNC_WEB_TOKEN=choose-your-own \
  gitsync
```

**Yes — you can run gitsync's web dashboard in Docker only, with no native
install.** The container *is* the always-on process (`restart: unless-stopped`),
so there's no systemd service to set up. Configure it entirely from the browser:
open `http://<host>:7765`, sign in (token from `docker logs`, or set
`GITSYNC_WEB_TOKEN`), then add your accounts on **Platforms** and your
directions on **Routes** — the web UI does everything the setup wizard and TUI
do, so nothing else is needed. Syncs run from the dashboard (or schedule
`gitsync sync` from a host cron / sidecar; the systemd timer doesn't run inside
a container).

- **config + mirror hub live on volumes** (`/data/config`, `/data/share`), so
  they survive restarts and updates. The access token is generated on first run
  and stored in the config volume (or set `GITSYNC_WEB_TOKEN`).
- runs **unprivileged**, with a `/healthz` healthcheck.
- inside the container it binds `0.0.0.0` so the published port is reachable —
  the access token is still enforced on every request.
- the **systemd timer does not run in a container**. For a timer, enable the
  bundled scheduler service — same image, `gitsync watch` instead of the web UI:

  ```bash
  docker compose --profile sync up -d      # web + scheduler
  docker compose logs -f gitsync-scheduler
  ```

  It runs `gitsync watch --interval 3600` against the same two volumes, so it
  sees the config you edit in the browser. Change the interval with
  `GITSYNC_SYNC_INTERVAL` (seconds) or by editing `docker-compose.yml`.
  A host cron (`gitsync sync --scheduled --quiet`) or a plain sidecar also
  works.

---

## Two ways to run gitsync

| | **1 · Native** (TUI + localhost web UI) | **2 · Docker only** (web UI) |
|---|---|---|
| **Install** | `curl -fsSL https://raw.githubusercontent.com/SeaXen/gitsync/main/quickinstall.sh \| bash` | `docker compose up -d` |
| **You get** | CLI + **TUI** + **web UI** on the box | **web UI** in a container |
| **Terminal UI** | `gitsync` / `gitsync tui` | `docker exec -it gitsync python3 -m gitsync tui` |
| **Browser UI** | `gitsync web` → `http://127.0.0.1:7765` (this machine) | `http://<host>:7765` (LAN, port published) |
| **Share on the LAN** | `gitsync web --bind 0.0.0.0`, or always-on: `gitsync web --install` | already LAN via `-p 7765:7765` |
| **Configure** | `gitsync setup`, the TUI, or the web UI | the web UI (Platforms + Routes) |
| **Data** | `~/.config/gitsync`, `~/.local/share/gitsync` | volumes `/data/config`, `/data/share` |

Pick one (or run both on different ports). The native web UI binds **localhost**
by default so it's private to the machine; make it reachable from other devices
with `--bind 0.0.0.0` or the always-on service. Docker is the "just serve the
dashboard" path — configure it entirely from the browser, no host install.

**Docker does not schedule syncs.** The image has no systemd and no cron — it
runs `gitsync web` and nothing else, so nothing syncs until you press *Sync
now*. To sync on a timer, run it on the host (the native path installs one for
you) or add a sidecar:

```yaml
  gitsync-sync:
    image: gitsync:latest
    entrypoint: ["/bin/sh", "-c", "while true; do python3 -m gitsync sync --scheduled --quiet; sleep 3600; done"]
    volumes: [gitsync-config:/data/config, gitsync-share:/data/share]
```

The CLI works inside the container too (`docker exec -it gitsync python3 -m
gitsync sync`), which is handy for a one-off run.
Either way, authentication is required on every request.

---

[← back to the README](../README.md)
