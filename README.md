<div align="center">

# ⛓️ gitsync

**Mirror your repositories between GitHub, GitLab, Forgejo, Gitea, Bitbucket and local folders — from one terminal UI.**

*One command to install. One command to run. Any direction. Self-healing. Zero dependencies.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-GitHub%20%C2%B7%20GitLab%20%C2%B7%Forgejo%20%C2%B7%20Gitea%20%C2%B7%20Bitbucket%20%C2%B7%20Local-green.svg)](#platforms--tokens)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)
[![CI](https://github.com/SeaXen/gitsync/actions/workflows/ci.yml/badge.svg)](https://github.com/SeaXen/gitsync/actions/workflows/ci.yml)

</div>

---

## Install — one click

```bash
curl -fsSL https://raw.githubusercontent.com/SeaXen/gitsync/main/quickinstall.sh | bash
```

That single command **downloads gitsync, installs it, puts `gitsync` on your PATH,
runs the setup wizard** (platforms → tokens → sync direction → schedule),
**installs the scheduler**, and offers to run your first sync.

<details>
<summary><b>Options</b> (append after <code>bash -s --</code>)</summary>

```bash
# install without root
curl -fsSL .../quickinstall.sh | bash -s -- --user

# unattended with defaults, daily schedule, cron instead of systemd
curl -fsSL .../quickinstall.sh | bash -s -- --yes --schedule daily --cron

# install only (configure later with: gitsync setup)
curl -fsSL .../quickinstall.sh | bash -s -- --no-setup
```

| Flag | Effect |
|---|---|
| `--user` | install to `~/.local/share/gitsync`, no root needed |
| `--cron` | use cron instead of a systemd timer |
| `--schedule hourly\|6h\|12h\|daily\|off` | sync frequency (default `hourly`) |
| `--no-setup` | skip the wizard |
| `--no-sync` | don't offer the first sync |
| `--yes` | accept all defaults (unattended) |
| `--branch NAME` | install a different branch |

</details>

<details>
<summary><b>Manual install</b> (from a clone)</summary>

```bash
git clone https://github.com/SeaXen/gitsync.git
cd gitsync
bash install.sh                 # + --user / --cron / --schedule daily
gitsync setup                   # guided configuration
```
</details>

---

## What it does

```
                    routes (any direction you configure)
                            ┌──────────────┐
   GitHub ─────────┐        │  local mirror │        ┌─────────┐
   GitLab ─────────┼───────▶│  hub (bare)   │───────▶│ GitLab  │
   Forgejo/Gitea ──┘        │  ~/.local/    │        │ Forgejo │
   Bitbucket ──────┐        │  share/gitsync│        │ Gitea   │
   local folder ───┘        └──────────────┘        │ Bitbkt  │
                                                  │ folder  │
                                                  └─────────┘
```

Every repo is first mirrored into a **local hub** (a free offline copy), then
pushed to each target — creating missing repos automatically and preserving
private visibility. If GitHub disappears tomorrow, you still have everything.

---

## Features

| | |
|---|---|
| **6 providers** | GitHub, GitLab, Forgejo, Gitea, Bitbucket, local folders — any as source or target |
| **Any direction** | `github → gitlab`, `gitlab → forgejo`, `folder → github`, `bitbucket → everything`… |
| **One-click install** | download + install + wizard + scheduler + first sync in one command |
| **Token guidance** | the wizard shows exactly which scopes each platform needs before asking |
| **TUI dashboard** | live status, per-platform repo counts, log tail, run history |
| **Drift detection** | `gitsync compare` verifies HEADs match across every platform |
| **Self-healing** | a network drop mid-clone leaves a partial mirror that is detected, removed and re-cloned |
| **Never overlaps** | run lock — the timer and manual runs step aside instead of colliding |
| **Missed runs caught up** | systemd timer with `Persistent=true` runs after downtime (cron fallback available) |
| **Parallel** | 4 repos in flight by default (tunable 1–8) |
| **Mirror modes** | `full` (exact mirror, prunes deleted refs) or `safe` (never delete) |
| **Adaptive UI** | compact layout on phones/narrow terminals — no "terminal too small" |
| **Tabbed TUI** | 6 tabs (Dashboard/Platforms/Routes/Log/History/Settings), `/` search, sparklines, live per-repo progress |
| **Discord alerts** | webhook notifications on failure/success — a broken backup tells you |
| **Drift repair** | `gitsync fix` detects divergence across platforms and re-syncs |
| **Repo filters** | per-route `include`/`exclude` globs |
| **Self-repair** | `doctor --fix` removes broken mirror shells automatically |
| **Watch mode** | `gitsync watch --interval 60` for near-real-time sync |
| **SSH transport** | `"protocol": "ssh"` per platform (+ `ssh_port`) |
| **Zero dependencies** | Python 3.8+ standard library only. No pip. No npm. |

---

## Quick start

```bash
gitsync                 # open the TUI (also: gitsync tui)
gitsync sync            # run a sync now
gitsync status          # health check
gitsync doctor          # full diagnostics
```

**New here?** Follow the step-by-step walkthrough — copy the install
command, paste it in a terminal, log in to your accounts, pick your local
backup folder: **[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)**

---

## Commands

| Command | What it does |
|---|---|
| `gitsync` | **open the TUI** (no arguments needed) |
| `gitsync setup` | guided first-run configuration |
| `gitsync sync` | sync all routes now |
| `gitsync sync --route main` | sync one route |
| `gitsync sync --to gitlab` | push to **one target only** |
| `gitsync mirror tradex` | sync a **single repo** |
| `gitsync routes` | list routes |
| `gitsync routes add NAME SRC t1,t2` | add a route — **any direction** |
| `gitsync routes remove NAME` | remove a route |
| `gitsync compare` | **drift check** — compare HEADs across platforms |
| `gitsync status` | platforms, routes, scheduler, last run |
| `gitsync platforms` | list accounts + test every connection |
| `gitsync platforms add NAME --type gitlab --token glpat-...` | **add an account** (no wizard needed) |
| `gitsync platforms edit NAME --token ... --url ... --owner ...` | **edit an account** |
| `gitsync platforms remove NAME` | **remove an account** (warns if a route uses it) |
| `gitsync platforms test [NAME]` | test one or all connections |
| `gitsync scopes` | required token scopes per platform |
| `gitsync log -n 50` | last 50 log lines |
| `gitsync records` | run history with failure details |
| `gitsync notify --test` | send a Discord test message |
| `gitsync fix` | detect drift across platforms and repair it |
| `gitsync watch --interval 60` | sync every 60 s in the foreground |
| `gitsync sync --to gitlab` | push to one target only |
| `gitsync install --schedule daily` | (re)install the scheduler |
| `gitsync doctor` | full health check |
| `gitsync uninstall` | remove scheduler + symlink |

---

## The TUI

```
 gitsync 1.0.0 — repository sync control
 2026-11-08 09:14:22  syncing: no  schedule: Every hour  counts: 3s old
┌ PLATFORMS ────────────────────────────────────────────────────────────┐
│ github      github    ON   OK    repos=53    gh CLI authenticated    │
│ gitlab      gitlab    ON   OK    repos=56    token OK — user alice   │
│ forgejo     forgejo   ON   OK    repos=56    token OK — user bob    │
│ backup      local     ON   OK    repos=53    folder ready: /mnt/...   │
└───────────────────────────────────────────────────────────────────────┘
┌ ROUTES & SETTINGS ────────────────────────────────────────────────────┐
│ github-to-all: github -> gitlab, forgejo, backup                      │
│ workers=4  mode=full  forks=False   scheduler: systemd timer: hourly  │
└───────────────────────────────────────────────────────────────────────┘
┌ LOG (live tail — L for full log, H for history) ──────────────────────┐
│ [52/52] OK   Sea-Wallpaper -> gitlab,forgejo,backup                   │
│ === sync done: 52 ok, 0 failed, 37.8s ===                             │
└───────────────────────────────────────────────────────────────────────┘
 [S]ync  [L]og  [H]istory  [C]chedule  [O]ptions  [A]ccounts  [Q]uit
```

| Key | Action |
|---|---|
| `S` | run a sync now (live output) |
| `L` | full log viewer |
| `H` | run history |
| `C` | schedule: hourly / 6h / 12h / daily / OFF |
| `O` | options: workers (1–8), mirror mode, include forks |
| `A` | accounts & platforms (tokens, scopes, how to add more) |
| `W` | re-run the setup wizard |
| `R` / `P` | refresh platform connections |
| `1`–`6` | switch tabs: Dashboard / Platforms / Routes / Log / History / Settings |
| `/` | search-filter the log or history |
| `Q` | quit (Esc = back inside menus) |

**Adaptive:** switches to a compact single-column layout on phones and narrow
terminals (≥70×18 → full dashboard; smaller → compact; <34×10 → rotate hint).

---

## Platforms & tokens

The wizard prints this before asking for anything — here it is for reference:

| Platform | Create token at | Required scopes | Notes |
|---|---|---|---|
| **GitHub** | [github.com/settings/tokens](https://github.com/settings/tokens) | `repo` (+`workflow`, `read:org` as needed) | or use the `gh` CLI (no token) |
| **GitLab** | [gitlab.com/-/user_settings/personal_access_tokens](https://gitlab.com/-/user_settings/personal_access_tokens) | `api` | or use the `glab` CLI; self-hosted: `<host>/-/user_settings/...` |
| **Forgejo** | `<url>/user/settings/applications` | `write:repository` **+** `write:user` | `write:user` is required because repo creation goes through `/user/repos` |
| **Gitea** | `<url>/user/settings/applications` | `write:repository` + `write:user` | same model as Forgejo (1.22+) |
| **Bitbucket** | [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) | Atlassian API token, Bitbucket repo read+write | sent as Bearer; needs your workspace name |
| **Local folder** | — | none | just pick a directory |
| **Any other git server** | — | optional | use the `generic` type with a URL template |

**Codeberg.org** works too: use the `forgejo` adapter with `url: https://codeberg.org`,
or `generic` with a URL template.

Tokens are stored in `~/.config/gitsync/config.json` (mode `0600`). They can
also come from `GITHUB_TOKEN`, `GITLAB_TOKEN`, `FORGEJO_TOKEN`, `GITEA_TOKEN`,
`BITBUCKET_TOKEN`.

---

## Configuration

`~/.config/gitsync/config.json` — editable by hand or via the wizard:

```jsonc
{
  "platforms": {
    "github":  {"type": "github",  "enabled": true, "owner": "you",
                "auth": "gh", "token": ""},
    "gitlab":  {"type": "gitlab",  "enabled": true,
                "url": "https://gitlab.com", "token": "glpat-...",
                "ns_id": "12345"},
    "homelab": {"type": "forgejo", "enabled": true,
                "url": "http://192.168.0.10:3000", "token": "...",
                "owner": "you"},
    "anygit":  {"type": "generic", "enabled": true,
                "url_template": "https://git.example.com/{owner}/{name}.git",
                "owner": "me"},
    "backup":  {"type": "local",   "enabled": true,
                "path": "/mnt/backup/git"}
  },
  "routes": [
    {"name": "main", "source": "github",
     "targets": ["gitlab", "homelab", "anygit", "backup"]}
  ],
  "settings": {"workers": 4, "mode": "full",
               "schedule": "hourly", "include_forks": false}
}
```

You can enable **several platforms of the same type** — two Forgejo servers,
GitHub plus a self-hosted Gitea, each with its own token and route.

### Any account, any folder, any direction

Everything is config-driven, so the tool is fully generic:

- **Any account** - GitHub, GitLab, Forgejo, Gitea, Bitbucket, or several
  accounts of the same type side by side (`gitsync platforms add ...`)
- **Any folder** - local mirrors to any path (NAS, USB drive, another disk)
- **Any direction** - `github -> gitlab`, `gitlab -> forgejo`,
  `folder -> github`, `bitbucket -> everything`
- **Any subset** - per-route include/exclude globs, fork filtering,
  per-target pushes (`gitsync sync --to gitlab`)

---

## Notifications (Discord + Telegram)

**Set them up from inside the TUI** - press `N`:

```
 NOTIFICATIONS
 Discord webhook : configured
   https://discord.com/api/webhooks/1568...wjrQ
 Telegram bot    : not set
   chat id: (missing)
 Notify on       : failure

 credentials are stored in YOUR config file
 (~/.config/gitsync/config.json) - never in the code

 [D]iscord  [T]elegram  [M]ode  [Enter]test  [B]back
```

| Key | Action |
|---|---|
| `D` | add/edit the Discord webhook (masked input) |
| `T` | add/edit the Telegram bot token + chat id |
| `M` | cycle: failure -> success -> always -> never |
| `Enter` | send a test message to both channels |

Or from the shell:

```bash
gitsync notify                        # show the current setup
gitsync notify --test                 # send a test message
```

Config reference:

```jsonc
"settings": {
    "notify": {
        "discord_webhook": "https://discord.com/api/webhooks/...",
        "telegram_token":  "123456:ABC-DEF...",
        "telegram_chat_id": "123456789",
        "on": "failure"          // failure | success | always | never
    }
}
```

Telegram: create a bot with @BotFather (https://t.me/BotFather), then get
your chat id from @userinfobot (https://t.me/userinfobot).

You get an embed/message with repos synced / failed / partial, duration and
the first few failure reasons - so a broken sync tells you instead of
waiting to be discovered.

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

## How it works — the workflows

### First run (once)

```
curl -fsSL .../quickinstall.sh | bash
      |
      +-> preflight (python3 3.8+, sudo/--user detection)
      +-> download the repo (git clone or tarball)
      +-> install to /opt/gitsync + symlink on PATH
      |
      +-> SETUP WIZARD (interactive)
      |     +-> pick platforms (github, gitlab, forgejo, local...)
      |     +-> shows the token scopes each platform needs
      |     +-> you paste tokens / paths
      |     +-> TESTS every connection live
      |     +-> pick source + targets   <- your sync direction
      |     +-> pick schedule, workers, mirror mode
      |           +-> writes ~/.config/gitsync/config.json (0600)
      |
      +-> scheduler installed (systemd timer, Persistent=true)
      +-> first sync -> everything mirrored
```

### The sync engine (every run)

```
gitsync sync   (or the hourly timer)
      |
      +-> acquire run lock -- (another run active? step aside)
      |
      +-> for each route:  source ---> targets
      |
      |   FOR EVERY REPO (4 in parallel):
      |   +--------------------------------------------------+
      |   | 1. hub mirror: git clone --mirror from source      |  <- offline copy
      |   |    (or git remote update --prune if it exists)     |
      |   | 2. for each target:                                |
      |   |      ensure the repo exists (create if missing,    |
      |   |      private stays private)                        |
      |   |      push (full mirror; heads+tags for Forgejo)    |
      |   +--------------------------------------------------+
      |
      +-> write the run record (records.json)
      +-> notify if it failed (Discord/Telegram, per your mode)
```

Self-healing: a network drop mid-clone leaves a partial mirror; the next
run detects it (no refs), deletes it and re-clones.

### The TUI

```
gitsync                        <- bare command opens the TUI
      |
      +-> 1 Dashboard   status, platforms, routes, live log,
      |                 + progress bar while syncing
      +-> 2 Platforms   every account + connection test + repo count
      +-> 3 Routes      source -> targets, filters
      +-> 4 Log         recent lines   (/ to filter)
      +-> 5 History     past runs + duration sparkline
      +-> 6 Settings    workers/mode/schedule/notify + paths
      |
      +-> S sync now (live progress)      L full log (scroll+search)
      +-> C schedule menu                O options
      +-> A accounts/scopes              N notifications (Discord/Telegram)
      +-> W setup wizard                 Q quit
```

Rendering: redraws only when something changes (dirty flag); idle ticks
repaint just the clock (~20 bytes) so it never flickers. On phones it
switches to a compact layout automatically.

### Daily use

```
you write code in a working clone
      |
      git push                     <- GitHub only (source of truth)
      |
      v
hourly timer fires (or: gitsync sync)
      |
      +--> GitLab     (<owner>/<repo>)
      +--> Forgejo    (<owner>/<repo>)
      +--> disk       (<your-folder>/<repo>.git)
      |
want it now?        gitsync sync          (~40s for 50 repos)
something wrong?    gitsync doctor / gitsync compare
```

Golden rule: push to GitHub first. The sync mirrors GitHub's state
outward, so commits that exist only on GitLab/Forgejo get reverted by
the next run.

### Unattended automation

```
systemd timer (hourly, Persistent=true)
      |
      +-> machine was off at :00? -> runs once at boot instead of skipping
      |
      +-> gitsync sync --quiet
      |     +-> run lock (never overlaps a manual sync)
      |     +-> repos -> all targets
      |     +-> run record written
      |     +-> failure? -> Discord/Telegram message with the reasons
      |
      +-> logs: gitsync.log   |   journalctl -u gitsync.service
```

### Recovery

| Situation | Command |
|---|---|
| "Is everything in sync?" | `gitsync compare` |
| "Something drifted" | `gitsync fix` (detect + repair) |
| "A sync failed" | `gitsync records`, then `gitsync doctor` |
| "A mirror looks broken" | `gitsync doctor --fix` (auto-repair) |
| "I want it live, not hourly" | `gitsync watch --interval 60` |
| "New repo on the source" | the next sync creates it everywhere |
| "Lost the source account" | your disk + other platforms are complete copies |

## Scheduling

Default: a **systemd timer**, hourly, `Persistent=true` — if the machine was
off when a run was due, it runs once at boot instead of being skipped.

```bash
systemctl list-timers gitsync.timer     # next run
journalctl -u gitsync.service           # run logs
gitsync install --schedule 6h           # change frequency
gitsync install --schedule off          # manual runs only
gitsync install --cron                  # use cron instead
```

---

## Updating

```bash
gitsync update        # re-runs the one-click installer (keeps your config)
```

## Uninstalling

```bash
gitsync uninstall     # removes scheduler + symlink (config & mirrors kept)
```

---

## Requirements

- Python **3.8+** (standard library only)
- **git** 2.20+
- optional: `gh` / `glab` CLIs (gitsync can use them instead of tokens)

## Troubleshooting

| Symptom | Fix |
|---|---|
| `gitsync: command not found` | `~/.local/bin` not in PATH (with `--user`), or re-run the installer |
| A platform shows FAIL | `gitsync platforms` for the reason; check token scopes with `gitsync scopes` |
| `Rate limit exceeded` | wait a minute; lower `workers` |
| New repo missing on a target | run `gitsync sync` — it creates them automatically |
| Refs deleted on a target | that's `mode: full` (exact mirror). Use `mode: safe` to keep them |
| Another sync already running | a run is in progress (timer or manual); wait and retry |
| TUI looks garbled | resize the terminal; the layout adapts |

## FAQ

**Does it sync issues, PRs and wikis?**
No — code only (branches, tags, commits). Issues/PRs come over with GitLab's
one-time GitHub importer but don't update afterwards.

**Can I use it bidirectionally?**
Use two routes (`a→b` and `b→a`), but pick one source of truth per repo —
a mirror overwrites the target with the source's state.

**Is it safe for private repos?**
Yes — private repos are created as private on targets, and the config file
is `0600`. Tokens are embedded in the URLs the tool uses, so protect the
config file and the machine accordingly.

**Does it need root?**
No — `--user` installs entirely under your home directory.

## Documentation

| Document | What's in it |
|---|---|
| [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) | step-by-step walkthrough: install → log in → pick folders → first sync |
| [docs/PERMISSIONS.md](docs/PERMISSIONS.md) | exactly which token scopes every platform needs |
| [CHANGELOG.md](CHANGELOG.md) | release history |
| [SECURITY.md](SECURITY.md) | how credentials are stored, how to report issues |
| [CONTRIBUTING.md](CONTRIBUTING.md) | dev setup, conventions, how to add a platform |

## License

[MIT](LICENSE) — do whatever you want, no warranty.
