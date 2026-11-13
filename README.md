<div align="center">

# ⛓️ gitsync

**Mirror your repositories between GitHub, GitLab, Forgejo, Gitea, Bitbucket and local folders — from one terminal UI.**

*One command to install. One command to run. Any direction. Self-healing. Zero dependencies.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](docs/TROUBLESHOOTING.md#requirements)
[![CI](https://gitlab.com/seaxen/gitsync/badges/main/pipeline.svg)](https://gitlab.com/seaxen/gitsync/-/pipelines)

</div>

---

Every repo is first mirrored into a **local hub** (a free offline copy), then
pushed to each target — creating missing repos automatically and keeping
private repos private. If GitHub disappears tomorrow, you still have everything.

```
   GitHub ─────────┐        ┌──────────────┐        ┌─────────┐
   GitLab ─────────┼───────▶│  local hub   │───────▶│ GitLab  │
   Forgejo/Gitea ──┤        │  (bare git)  │        │ Forgejo │
   Bitbucket ──────┤        │  ~/.local/   │        │ Gitea   │
   local folder ───┘        │  share/gitsync│       │ folder  │
                            └──────────────┘        └─────────┘
```

## Install — one command

```bash
curl -fsSL https://raw.githubusercontent.com/SeaXen/gitsync/main/quickinstall.sh | bash
```

That downloads gitsync, puts `gitsync` on your PATH, runs the setup wizard
(platforms → tokens → direction → schedule), installs the timer, and offers a
first sync.

```bash
# without root
curl -fsSL .../quickinstall.sh | bash -s -- --user
# unattended, daily, cron instead of systemd
curl -fsSL .../quickinstall.sh | bash -s -- --yes --schedule daily --cron
# a pinned, checksum-verified release
curl -fsSL .../quickinstall.sh | bash -s -- --tag v2.1.4
```

| Flag | Effect |
|---|---|
| `--user` | install to `~/.local/share/gitsync`, no root needed |
| `--tag vX.Y.Z` | install that released tag (verified against its `SHA256SUMS`) |
| `--sha256 HASH` | verify a tarball you downloaded yourself |
| `--schedule hourly\|6h\|12h\|daily\|off` | sync frequency (default `hourly`) |
| `--cron` | use cron instead of a systemd timer |
| `--no-setup` / `--no-sync` / `--yes` | skip the wizard / first sync / all prompts |

From a clone instead:

```bash
git clone https://github.com/SeaXen/gitsync.git && cd gitsync
bash install.sh        # + --user / --cron / --schedule daily
gitsync setup          # guided configuration
```

## Quick start

```bash
gitsync          # open the TUI (also: gitsync tui)
gitsync web      # the same dashboard in a browser, http://127.0.0.1:7765
gitsync sync     # run a sync now
gitsync status   # health check
gitsync doctor   # full diagnostics, --fix repairs what it can
```

New here? The step-by-step walkthrough — install, log in, pick a backup
folder — is in **[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)**.

## What you can do

| | |
|---|---|
| **6 providers** | GitHub, GitLab, Forgejo, Gitea, Bitbucket, local folders — any as source or target |
| **Any direction** | `github → gitlab`, `gitlab → forgejo`, `folder → github`, `bitbucket → everything` |
| **Same-site too** | `github → github`, `gitlab → gitlab` — several accounts per site, side by side |
| **Two-way (mesh)** | every peer is source and target: edit on GitLab, it lands everywhere else |
| **Safe by default** | `mode: safe` never deletes and never force-pushes; mesh reports a disagreement instead of overwriting |
| **Everything in a UI** | accounts, routes, repos, stars, disk, doctor, compare, mirror — TUI, web UI, or CLI |
| **Restore from the hub** | `gitsync restore --to gitlab` pushes every local mirror back out (the web UI previews it first) |
| **Alerts you actually get** | Discord, Telegram, ntfy, any JSON webhook, or email — globally or per route |
| **Drift detection** | `gitsync compare` (HEADs) and `gitsync verify` (every branch and tag, hub vs targets) |
| **Self-healing** | a network drop leaves a partial mirror that is detected, removed and re-cloned |
| **Zero dependencies** | Python 3.8+ standard library only. No pip. No npm. |
| **Guards** | refuses to start on a full disk, runs your own pre/post-sync commands, and retries only what failed |

The full feature list is in **[docs/CLI.md](docs/CLI.md)** and the pages below.

## Which mode do I want?

| You want… | Use | What it does |
|---|---|---|
| a backup that never loses anything | **one-way, `safe`** (default) | copies heads + tags, never force-pushes, never deletes a ref or a repo |
| an exact replica of the source | **one-way, `full`** | force-updates heads + tags and prunes refs the source dropped; repos are still never deleted (that needs `prune_repos`) |
| a folder to be the single truth | **local → platform** | the folder is the source; platforms are overwritten |
| several peers that must all agree | **mesh** | newest commit wins per branch, refs are unioned, disagreements reported |

Unsure? Start `safe`. `gitsync verify` tells you when a target has drifted, and
you can run `full` once to re-align. Details:
**[docs/ROUTES-AND-MODES.md](docs/ROUTES-AND-MODES.md)**.

## Supported platforms

| OS | Status |
|---|---|
| **Linux** | fully supported — systemd timer, or cron. The primary target. |
| **macOS** | supported — installs a launchd agent instead of a systemd timer. |
| **Windows** | **not supported.** The CLI, sync engine and web UI run under Python, and `gitsync/askpass.bat` exists for git credential prompts, but there is no installer, no scheduler integration and no test coverage. WSL2 works (it is Linux). |
| **Docker** | supported — web UI + CLI, x86-64 and arm64 base image |

## Documentation

| Page | What is in it |
|---|---|
| [GETTING-STARTED](docs/GETTING-STARTED.md) | first install, a walkthrough with real commands |
| [CLI](docs/CLI.md) | every command and flag |
| [ROUTES-AND-MODES](docs/ROUTES-AND-MODES.md) | selective sync, mesh, filters, pausing, what deletes what |
| [WEB-UI](docs/WEB-UI.md) | the browser dashboard, sessions, light/dark theme |
| [TUI](docs/TUI.md) | the terminal dashboard, keys, tabs |
| [DOCKER](docs/DOCKER.md) | container setup, volumes, the scheduler profile |
| [CONFIGURATION](docs/CONFIGURATION.md) | `config.json` field by field, export/import |
| [PLATFORMS](docs/PLATFORMS.md) | tokens and scopes per provider |
| [NOTIFICATIONS](docs/NOTIFICATIONS.md) | Discord, Telegram, ntfy, webhook and email alerts |
| [SECURITY-MODEL](docs/SECURITY-MODEL.md) | what is stored, auth, CSP, reverse proxies |
| [HOW-IT-WORKS](docs/HOW-IT-WORKS.md) | the engine and the workflows |
| [SCHEDULING](docs/SCHEDULING.md) | timers, cron, updating, uninstalling |
| [TROUBLESHOOTING](docs/TROUBLESHOOTING.md) | requirements, common failures, FAQ |
| [DEVELOPING](docs/DEVELOPING.md) | running the checks, CI |
| [CHANGELOG](CHANGELOG.md) | every release, item by item |

## License

MIT — see [LICENSE](LICENSE).
