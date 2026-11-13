# Command reference

Every command, every flag. The TUI and the web UI run these same commands,
so anything you see in the UI has a CLI equivalent — handy for cron jobs,
scripts and `docker exec`.

## Commands

| Command | What it does |
|---|---|
| `gitsync` | **open the TUI** (no arguments needed) |
| `gitsync web` | **open the web dashboard in a browser** (`--port 7765`, `--bind`) |
| `gitsync web --install` | run the web dashboard **always-on** as a systemd service (survives reboot) |
| `gitsync setup` | guided first-run configuration |
| `gitsync sync` | sync all routes now |
| `gitsync sync --route main` | sync one route |
| `gitsync sync --to gitlab` | push to **one target only** |
| `gitsync sync --repos "tradex,SeaVault"` | sync **only these repos** |
| `gitsync sync --dry-run` | **preview** what would change, change nothing |
| `gitsync repos` | **browse the logged account's repo list** |
| `gitsync disk` | hub disk usage, orphaned mirrors, reclaim space |
| `gitsync tokens` | check every credential for expiry / revocation |
| `gitsync webhooks --serve` | **receive push events** and sync instantly |
| `gitsync route-schedule NAME daily` | give one route its own frequency |
| `gitsync repos --platform gitlab --forks` | list another account, forks included |
| `gitsync mirror tradex` | sync a **single repo** |
| `gitsync mirror tradex SeaVault` | sync **several repos at once** |
| `gitsync mirror tradex --to backup` | one repo, **one target only** |
| `gitsync mirror tradex --from gitlab` | pull from a **specific platform** |
| `gitsync routes` | list routes |
| `gitsync routes add NAME SRC t1,t2` | add a one-way route — **any direction** |
| `gitsync routes add all github,gitlab,forgejo` | **two-way (mesh)** route over every peer |
| `gitsync routes pause NAME [TARGET]` | pause a whole route, or just one target |
| `gitsync routes resume NAME [TARGET]` | resume it (targets catch up on the next run) |
| `gitsync pause` | **suspend all background and scheduled syncs** globally |
| `gitsync resume` | **resume** global sync |
| `gitsync routes remove NAME` | remove a route |
| `gitsync sync --to backup` | push to **one target only** (not for mesh routes) |
| `gitsync webhooks --install` | run the receiver as a service (URL shown in the TUI) |
| `gitsync stars list github` | list an account's starred repos (`--json` for scripting) |
| `gitsync stars copy github gitlab,forgejo` | star those repos on the other accounts |
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
| `gitsync notify --test` | send a test message (Discord **and** Telegram) |
| `gitsync notify --set-url https://discord.com/api/webhooks/...` | **save** a Discord webhook (validated, not just printed) |
| `gitsync notify --set-telegram BOTTOKEN --chat-id 12345` | **save** Telegram credentials |
| `gitsync fix` | detect drift across platforms and repair it |
| `gitsync fix --route main` | repair only that route |
| `gitsync install` | install the scheduler for the **finest** route schedule |
| `gitsync install --schedule weekly --user` | user timer, weekly (never writes `/etc` without `--system`) |
| `gitsync install --cron` | use cron instead of systemd |
| `gitsync ci` | run the same checks CI runs (syntax, shell, smoke, unit checks) |
| `gitsync ci --install-hook` | add a `pre-push` hook so every push runs them first |
| `gitsync restore --to gitlab` | **push the hub back out** — the source host is gone |
| `gitsync restore --to backup --dry-run` | preview a restore (nothing is modified) |
| `gitsync routes edit main --include 'Sea*'` | change a route's repo filters |
| `gitsync routes edit main --from-file repos.txt` | take the include list from a file (one repo per line, `#` comments) |
| `gitsync routes edit main --clear-filters` | back to "every repo" |
| `gitsync doctor` | health check: tokens, routes, storage, scheduler |
| `gitsync verify` | **is the backup real?** every branch + tag, hub vs each target, read-only |
| `gitsync verify --json` | the same report as JSON (exit 1 when anything drifted) |
| `gitsync compare` | quick drift check: HEADs across platforms |
| `gitsync config export` | print the config with **every credential blanked** |
| `gitsync config export --out cfg.json` | write that export to a file (mode 0600) |
| `gitsync config import cfg.json` | import a config, **keeping the secrets on this machine** |
| `gitsync ci --update-sums` | regenerate `SHA256SUMS` after changing shipped files |
| `gitsync update` | pull the newest release, verify it, reinstall |
| `gitsync watch --interval 60` | sync on a loop in the foreground (minimum 30s) |
| `gitsync uninstall` | remove the scheduler and the command (keeps config + mirrors) |

### Flags worth knowing

| Flag | On | What it does |
|---|---|---|
| `--dry-run` | `sync`, `fix` | preview only. Names every ref a `full`-mode push **would delete** |
| `--quiet` | `sync` | no progress output (what the timer uses) |
| `--scheduled` | `sync` | only run routes whose own schedule says they are due |
| `--force` | `sync` | run sync even if global sync is paused |
| `--to NAME` | `sync`, `mirror` | push to one target only. **Ignored on a mesh route** (a mesh always syncs every peer) |
| `--from NAME` | `mirror` | pull from a specific platform instead of the route's source |
| `--repos "a,b"` | `sync`, `stars copy` | only these repos (exact, case-insensitive names) |
| `--route NAME` | most commands | only this route |
| `--orphans --prune-orphans` | `disk` | list mirrors no route owns, then delete them (two separate steps, on purpose) |
| `--system` | `install` | install a root system unit instead of a user unit |
| `--user` | `install` | install under `$HOME` (never uses sudo) |
| `--yes` | `install` | no prompts |
| `--json` | `stars list` | machine-readable output |
| `gitsync watch --interval 60` | sync every 60 s in the foreground |
| `gitsync install --schedule daily` | (re)install the scheduler |
| `gitsync doctor` | full health check |
| `gitsync ci` | run all checks locally — no CI provider needed |
| `gitsync uninstall` | remove scheduler + symlink |

---

## One command, everything inside

`gitsync` opens the dashboard, and from there nothing needs a terminal:

| Key | What it does |
|---|---|
| `A` | **accounts**: add, edit, remove, test, enable/disable a site, **browse its repos** (`V`), token scopes (`?`) |
| `P` (Routes tab) | **routes**: pause/resume a target or a route, run one route, dry-run it, edit its filters, per-route workers/mode/mesh-conflict/schedule, add a route **with its settings** (`A`), remove one (`X`) |
| `C` | schedule (hourly → daily → off) |
| `O` | options: workers, mirror mode, forks, notify, pacing, mesh policy, token warning, colour theme |
| `N` → `W` | webhook receiver: secret, payload URL, install/stop |
| `E` | browse repos, pick some, sync just those |
| `T` | **tools**: disk + orphans, doctor, compare sites, mirror one repo, fix drift (preview + run), update, uninstall. These run in the background and stream into the dashboard log, so the TUI never freezes (`compare` takes minutes). |
| `D` | dry run — preview a whole run, or one route. In `full` mode it names every ref the push would delete |
| `T` (repo browser) | push the selected repos to **one site** |
| `S` | sync now (or one route, from the pause screen) |

Accounts and routes are the two things that used to force you into a shell;
both are forms inside the TUI now, with the same validation the CLI uses.

---

## Disk usage and reclaim

```bash
gitsync disk                       # hub size, per-platform size, free space
gitsync disk --top 10              # the 10 largest mirrors
gitsync disk --orphans             # mirrors whose repo is gone upstream
gitsync disk --gc --dry-run        # what a gc would do
gitsync disk --gc --older-than 7   # gc only idle mirrors
```

```
mirror hub: /root/.local/share/gitsync/mirrors
  mirrors      : 53
  total size   : 7.2G
  filesystem   : 847.1G free of 916.5G
  orphans      : 2 (696.0K)

orphaned mirrors (repo gone from github):
  mdsagar-com-claude1              600.0K
  my-portable-llm-wikis             96.0K

re-run with --orphans --prune-orphans to delete them
```

`--gc` runs `git reflog expire --expire=now --all` + `git gc --prune=now`,
which reclaims loose objects **without touching any reachable commit** — a
mirror is a cache, so this is always safe. `--orphans --prune-orphans` removes
mirrors for repos that no longer exist on the source (they come back on the
next sync if the repo reappears).

---

## Copying your stars

Stars live inside one account, so there is no built-in way to say "star
everything I starred on GitHub here too". `gitsync` does it:

```bash
gitsync stars list github                     # what you have starred
gitsync stars copy github gitlab,forgejo      # star them on the others
gitsync stars copy github gitlab --dry-run    # preview first
```

Or in the TUI: **Stars tab** → `C`, tick the targets, Enter.

Two rules keep it safe:

- **Only repos that already exist on the target are starred.** Nothing is
  created, so 197 GitHub stars of other people's projects will mostly report
  "not on that site" — that is the honest answer, not a failure.
- **Matching is never by name alone.** GitHub → GitHub matches the exact
  `owner/repo`; anything else matches the repo's remote URL. A name match
  would have starred `nujovich/hermes-telemetry` when you meant
  `SeaXen/hermes-telemetry`.

---

[← back to the README](../README.md)
