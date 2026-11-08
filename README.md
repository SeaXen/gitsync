# gitsync

**Professional multi-platform git repository sync — with a TUI dashboard.**

Mirror your repositories between **GitHub**, **GitLab**, **Forgejo**, **Gitea**,
**Bitbucket**, any **generic git server** (URL template), and **local folders**
— in any direction — from one terminal UI.

Codeberg.org works too: use the `forgejo` adapter with
`url: https://codeberg.org`, or the `generic` adapter with a URL template.

```
GitHub ──┐
GitLab ──┼──▶  local mirror hub  ──▶  GitLab / Forgejo / Gitea / folder
Forgejo ─┘   (offline copy)          (any direction you configure)
```

- **One-click install**: `bash install.sh`
- **Guided setup** that shows exactly which token scopes each platform needs
- **Any direction**: GitHub → GitLab, GitLab → GitHub, local → cloud, cloud → local
- **Local instances**: Forgejo/Gitea on any host/IP/port, GitLab self-hosted
- **TUI dashboard**: live status, counts, logs, run history, scheduling
- **Self-healing**: network drops leave partial mirrors that are detected,
  removed and re-cloned on the next run
- **Scheduler**: systemd timer with `Persistent=true` (catches up runs missed
  while the machine was off) or cron — switch anytime from the TUI
- **Zero dependencies**: Python 3.8+ stdlib only. No pip installs.

---

## Install

```bash
git clone https://github.com/SeaXen/gitsync.git
cd gitsync
bash install.sh                       # installs to /opt/gitsync + systemd timer
# or, without root:
bash install.sh --user --cron
```

## Setup

```bash
gitsync setup          # guided wizard: platforms, tokens, routes, schedule
gitsync sync           # first sync
gitsync tui            # dashboard
```

The wizard asks which platforms you use, shows the required token scopes
(see [docs/PERMISSIONS.md](docs/PERMISSIONS.md)), tests every connection,
then lets you pick the **source** and **targets** — that's your sync direction.

## Commands

| Command | What it does |
|---|---|
| `gitsync` | **open the TUI** (no args needed) |
| `gitsync setup` | guided first-run configuration |
| `gitsync sync` | run a sync now (all routes) |
| `gitsync sync --route main` | sync one route |
| `gitsync mirror tradex` | sync a **single repo** now |
| `gitsync routes` | list routes |
| `gitsync routes add NAME SRC t1,t2` | add a route — **any direction** |
| `gitsync routes remove NAME` | remove a route |
| `gitsync compare` | **drift check** — compare HEADs across platforms |
| `gitsync status` | platforms, routes, scheduler, last run |
| `gitsync tui` | the dashboard |
| `gitsync log -n 50` | last 50 log lines |
| `gitsync records` | run history with failure details |
| `gitsync platforms` | test every platform connection |
| `gitsync scopes` | required token scopes per platform |
| `gitsync doctor` | full health check |
| `gitsync install --schedule daily` | (re)install the scheduler |
| `gitsync uninstall` | remove scheduler + symlink |

### Any direction, any combination

A **route** is `source -> targets`, and every platform can be either side:

```
github -> gitlab, forgejo, backup        (cloud out, keep a local copy)
gitlab -> forgejo                        (GitLab to your homelab Forgejo)
backup -> github                         (restore: local folder back to cloud)
forgejo -> gitlab, generic               (self-hosted to anywhere)
```

```bash
gitsync routes add restore backup github      # local folder -> GitHub
gitsync routes add gl-to-fj gitlab forgejo    # GitLab -> Forgejo
gitsync sync --route restore
```

## TUI keys

| Key | Action |
|---|---|
| `S` | run a sync now (live output) |
| `L` | full log viewer |
| `H` | run history |
| `C` | schedule: hourly / 6h / 12h / daily / OFF |
| `O` | options: workers (1–8), mirror mode (full/safe), include forks |
| `A` | accounts & platforms (tokens, scopes, how to add more) |
| `W` | re-run the setup wizard |
| `R` / `P` | refresh platform connections |
| `Q` | quit |

### Adaptive layout (works on phones)

The TUI picks its layout from the terminal size — no more
"terminal too small" on phone SSH sessions:

| Terminal | Layout |
|---|---|
| >= 70x18 | full dashboard (platforms / routes / log panels) |
| smaller | compact single column: last run, cron state, condensed platform list, log tail |
| < 34x10 | polite hint to rotate to landscape |

Same keys in both layouts; resizing the window re-renders instantly.

## How sync works

Each route is `source → targets`. Every repo is:

1. mirrored into the local hub (`~/.local/share/gitsync/mirrors/`) as a bare repo
2. pushed from the hub to each target (repos are auto-created if missing;
   private repos stay private)

The hub gives you a free offline copy and makes syncs resumable.

**Mirror modes**
- `full` (default): exact mirror — refs deleted on the source are pruned on targets
- `safe`: never delete anything on the targets

**Golden rule for bidirectional use:** pick one source of truth per route.
The sync overwrites targets with the source's state, so commits that exist
only on a target will be reverted by the next run.

## Scheduling

Default: systemd timer, hourly, `Persistent=true` — if the machine was off
when a run was due, it runs once at boot instead of being skipped.

```bash
systemctl list-timers gitsync.timer     # next run
journalctl -u gitsync.service           # run logs
gitsync install --schedule 6h           # change schedule
gitsync install --schedule off          # disable (manual only)
gitsync install --cron                  # use cron instead of systemd
```

## Configuration

Everything lives in `~/.config/gitsync/config.json` (mode 0600):

```json
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
    "backup":  {"type": "local",   "enabled": true,
                "path": "/mnt/backup/git"}
  },
  "routes": [
    {"name": "main", "source": "github",
     "targets": ["gitlab", "homelab", "backup"]}
  ],
  "settings": {"workers": 4, "mode": "full",
               "schedule": "hourly", "include_forks": false}
}
```

You can enable **several platforms of the same type** — e.g. two Forgejo
servers, or GitHub plus a self-hosted Gitea — each with its own token.

Environment overrides: `GITHUB_TOKEN`, `GITLAB_TOKEN`, `FORGEJO_TOKEN`,
`GITEA_TOKEN`.

## Requirements

- Python 3.8+ (stdlib only)
- git 2.20+
- optional: `gh` / `glab` CLIs (gitsync can use them instead of tokens)

## License

MIT — see [LICENSE](LICENSE)
