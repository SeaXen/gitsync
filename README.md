<div align="center">

# ⛓️ gitsync

**Mirror your repositories between GitHub, GitLab, Forgejo, Gitea, Bitbucket and local folders — from one terminal UI.**

*One command to install. One command to run. Any direction. Self-healing. Zero dependencies.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-GitHub%20%C2%B7%20GitLab%20%C2%B7%Forgejo%20%C2%B7%20Gitea%20%C2%B7%20Bitbucket%20%C2%B7%20Local-green.svg)](#platforms--tokens)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)
[![CI](https://gitlab.com/seaxen/gitsync/badges/main/pipeline.svg)](https://gitlab.com/seaxen/gitsync/-/pipelines)

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
| **Site count at a glance** | the Platforms panel reads `PLATFORMS (4 sites, 4 connected)`, counting only reachable ones |
| **Everything in the TUI** | add / edit / remove accounts, add / remove routes, stars, disk, doctor, compare, mirror, update — no terminal needed |
| **Copy your stars** | read one account's starred repos and star the same ones on your other accounts |
| **Two-way (mesh)** | one route where every peer is source and target: edit on GitLab, it lands on GitHub, Forgejo and the local hub |
| **Pause anything** | pause one target or a whole route from the TUI (`P`) or CLI — the timer keeps running the rest |
| **Conflict-safe mesh** | a mesh can be told to *report* a disagreement instead of overwriting it (`mesh_conflict: skip`) |
| **Dry run anywhere** | `D` in the TUI previews a whole run, or one route, without touching anything |
| **Same-site too** | `github → github`, `gitlab → gitlab` — several accounts per site, side by side |
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
| **Selective sync** | browse the account's repos in the TUI (`E`) and sync only the ones you pick |
| **Webhook sync** | platforms push events → instant sync of just that repo (no polling) |
| **Dry run** | `sync --dry-run` shows exactly what would change first |
| **Per-route schedules** | hourly for active repos, weekly for archives |
| **Token health** | warns before an expiring credential kills the sync |
| **Disk analytics** | per-mirror sizes, orphan detection, safe `gc` reclaim |
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
| `gitsync notify --test` | send a Discord test message |
| `gitsync fix` | detect drift across platforms and repair it |
| `gitsync watch --interval 60` | sync every 60 s in the foreground |
| `gitsync install --schedule daily` | (re)install the scheduler |
| `gitsync doctor` | full health check |
| `gitsync ci` | run all checks locally — no CI provider needed |
| `gitsync uninstall` | remove scheduler + symlink |

---

## Selective sync

Full-account sync is the default, but you don't have to take all of it.

**From the TUI** — press `E`:

```
 REPOS - 53 repos, 2 selected
 [ ] 9router                pub  fork
 [x] AceRoute               priv
 [x] agentskills            pub
 [ ] Amazmail               priv
 ...
 space=select  a=all  n=none  /=filter  S=sync all targets  L=local only  B=back
```

| Key | Action |
|---|---|
| `space` | select / deselect the repo under the cursor |
| `a` / `n` | select **all** / **none** (respects the active filter) |
| `/` | filter the list by name |
| `S` | sync the selection to **every target** on the route |
| `L` | clone/sync the selection to the **local hub only** |
| `B` / `Esc` | back (Esc clears an active filter first) |

**From the CLI:**

```bash
gitsync repos                                  # what's in the account?
gitsync repos --forks --private-only           # filter the listing
gitsync sync --repos "tradex,SeaVault"         # only these, all targets
gitsync sync --repos tradex --to backup        # only these, one target
gitsync mirror tradex SeaVault SeaDash         # several at once
gitsync mirror tradex --to backup              # one repo, local only
gitsync mirror tradex --from gitlab            # use another platform as source
```

`--repos` matches names **exactly** (case-insensitive) and applies on top of the
route's `include`/`exclude` globs — so a repo excluded by the route stays
excluded. Names that don't exist on the source are reported, not silently
skipped.

---

## Two-way sync (mesh)

A normal route has one source and many targets: change something on a target
and it is overwritten on the next run. A **mesh** route has no single source —
every peer is equal. Each run fetches every peer, keeps the **newest commit per
branch** (and per tag), and pushes the result back to everyone:

```bash
gitsync routes add all github,gitlab,forgejo,backup
# or, explicitly:
gitsync routes add all --mesh github,gitlab,forgejo,backup
```

```
[1/1] OK   my-project -> github,gitlab,forgejo,backup (1 refs merged)
```

So editing a file on GitLab is enough — GitHub, Forgejo and the local hub pick
it up on the next run, and a commit made on GitHub flows the other way.

How conflicts are resolved:

- **Newest commit wins** per branch. Ties keep the peer listed first in the
  config (the source), so the result is deterministic.
- **Branches and tags are unioned**, never deleted on a peer unless *no* peer
  still has them. A deleted branch therefore does not come back, and a
  half-finished deletion does not wipe anyone.
- The mesh always force-pushes branches and tags (never `--mirror`), so
  platform-only refs such as GitHub's `refs/pull/*` are left alone.

If two people edit the same branch on two platforms at the same time, the
newer commit is kept and the older one is overwritten — a mesh is not a merge
engine.

When you would rather it refused, set the conflict policy to `skip`:

```bash
gitsync routes add all github,gitlab,forgejo,backup
# then, in the TUI: Routes -> P -> O -> Mesh conflicts -> skip
# or by hand:  "mesh_conflict": "skip"   in the route
```

A run then leaves any branch or tag the peers disagree about **exactly as it
is on every platform** and reports it, instead of picking a winner:

```
[1/1] OK   my-project -> github,gitlab,forgejo,backup (0 refs merged),
      1 conflict(s) skipped: release-2.0
```

Reconcile by hand, and the next run converges. `newest` (the default) is the
original behaviour.

---

## Pause a route or a target

Sometimes a target should stop receiving updates without touching the timer:
a disk is full, a Forgejo instance is down for maintenance, or you want the
local hub to stay frozen at a known-good state.

```bash
gitsync routes pause github-to-all backup     # just that target
gitsync routes pause github-to-all            # the whole route
gitsync routes resume github-to-all backup    # it catches up on the next run
```

In the TUI: **Routes tab → `P`**, move with ↑/↓, toggle with Space. Paused
entries are marked `PAUSED` on the Routes tab and on the dashboard, and every
run (manual or timer) skips them with a log line:

```
  paused target(s): backup
```

Pausing is stored in `config.json` (`"paused_targets": ["backup"]`,
`"enabled": false` for a whole route), so it survives restarts, updates and
the TUI being closed.

---

## Same-site sync (GitHub → GitHub, GitLab → GitLab, …)

Yes — and it needs no special mode. Every adapter is keyed on its **type**,
while a platform name is just a config key, so you can run as many accounts of
the same site side by side as you like and route between them.

```bash
# 1. add a second account on the same site
gitsync platforms add github-work --type github --token ghp_xxx --owner my-org

# 2. route account A -> account B
gitsync routes add gh-a-to-b github github-work

# 3. sync one repo, several, or the whole account
gitsync mirror tradex --from github --to github-work
gitsync mirror tradex SeaVault SeaDash --from github --to github-work
gitsync sync --repos "tradex,SeaVault" --route gh-a-to-b
gitsync sync --route gh-a-to-b                      # every repo on the route
```

The same three steps work for **GitLab → GitLab**, **Forgejo → Forgejo**,
**Gitea → Gitea** and **Bitbucket → Bitbucket** — just change `--type` and the
token. `gitsync platforms` lists every account you have configured.

**Three things to know:**

1. **A second GitHub account needs `--token`, not the `gh` CLI.** The `gh`
   adapter is bound to whichever account `gh` has active, so two GitHub
   platforms cannot both use it. A token (`repo` scope) makes each platform
   independent. GitLab, Forgejo, Gitea and Bitbucket are token-only already.
2. **The local hub is keyed on repo name, not on source + name.** Two routes
   that mirror the same repo name from *different* sources share one hub
   clone. The run lock stops them colliding, but the hub's `origin` follows
   whichever source ran last — so keep one direction per repo name
   (`A → B`, not also `B → A`) and this never matters.
3. **`mirror --to` finds the right route.** When several routes share a source,
   it picks the one that actually has the target you asked for instead of
   taking the first match.

---

## Dry run

Never wonder what a sync is about to do:

```bash
gitsync sync --dry-run                          # whole account
gitsync sync --repos "tradex,SeaVault" --dry-run
gitsync mirror tradex --to backup --dry-run     # not supported yet: mirror only
```

```
[09:30:06]   [1/2] PLAN gitsync
[09:30:06]            - hub up to date
[09:30:06]            - backup: up to date
[09:30:06]   [2/2] PLAN SeaVault
[09:30:06]            - hub: would clone (a1b2c3d4)
[09:30:06]            - gitlab: would CREATE

2 of 2 repos would change (nothing was modified)
```

It reads the source and each target with `git ls-remote` and **never creates a
repo and never writes to the hub** — so it is safe to run anywhere, any time.

---

## Per-route schedules

Different repos need different frequencies. Give a route its own:

```bash
gitsync routes add archives github backup --schedule weekly
gitsync route-schedule github-to-all hourly      # change an existing one
gitsync route-schedule github-to-all inherit     # back to the global setting
gitsync routes                                    # see every route + next run
```

```
github-to-all      hourly (inherited)  github -> gitlab, forgejo, backup   next: in 58 min
archives           weekly              github -> backup                    next: in 6 d

timer fires: hourly (the most frequent route schedule)
```

The systemd timer fires as often as the *most demanding* route needs; routes
with a looser schedule are skipped by `sync --scheduled` until they are due.
Manual `gitsync sync` always runs everything.

---

## Webhook sync (instant, instead of polling)

The timer polls hourly, so a push can sit unsynced for up to an hour. Let the
platforms tell you instead:

```bash
gitsync webhooks --secret --rotate      # generate the shared secret
gitsync webhooks --install              # systemd service, survives reboot
gitsync webhooks --status
```

Then point each platform's webhook at
`http://<this-box>:8791/hook/<platform-name>` and paste the same secret in.

| Platform | Where | Auth it checks |
|---|---|---|
| GitHub | repo → Settings → Webhooks | `X-Hub-Signature-256` (HMAC-SHA256) |
| GitLab | project → Settings → Webhooks | `X-Gitlab-Token` |
| Forgejo/Gitea | repo → Settings → Webhooks | `X-Gitea-Token` |

```bash
curl -X POST -H "X-Gitlab-Token: $SECRET" \
     -H "Content-Type: application/json" \
     -d '{"project":{"name":"tradex"}}' \
     http://localhost:8791/hook/gitlab
```

Only `push` events trigger anything (`ping` answers `pong`, everything else is
ignored). Bursts for the same repo collapse into one sync, the sync runs
detached so the platform never times out, and every request is authenticated
**before** any sync is spawned — an unsigned request gets a 401 and is logged.

**Security notes:** it binds to `127.0.0.1` by default (`--bind 0.0.0.0` only
if you front it with a proxy or tunnel), the secret is stored mode 0600, and
signature comparison is constant-time. An unset secret refuses to start.

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
 [S]ync  [L]og  [H]istory  [C]chedule  [O]ptions  [A]ccounts  [E]repos  [Q]uit
```

| Key | Action |
|---|---|
| `S` | run a sync now (live output) |
| `L` | full log viewer |
| `H` | run history |
| `C` | schedule: hourly / 6h / 12h / daily / OFF |
| `O` | options: workers (1–8), mirror mode, include forks |
| `A` | accounts & platforms (tokens, scopes, how to add more) |
| `E` | **repo browser** — see the account's repos, pick some, sync just those |
| `W` | re-run the setup wizard |
| `R` | refresh platform connections |
| `P` (Routes tab) | **pause / resume** a route or one of its targets |
| `P` (other tabs) | refresh platform connections |
| `D` | **dry run** — preview a whole run, or one route from the pause screen |
| `S` (pause screen) | run **just that route** now |
| `F` (pause screen) | edit that route's include/exclude filters |
| `O` (pause screen) | per-route workers / mode / mesh conflict |
| `W` (notifications) | webhook receiver: secret, URL, install/stop |
| `G` / `End` / `Home` | jump to the bottom / top of the log viewer |
| mouse | click a tab, or a row in the repo browser / pause screen |
| `T` | tools menu (disk, doctor, compare, mirror, update, uninstall) |
| `1`–`6` | switch tabs: Dashboard / Platforms / Routes / Log / History / Settings |
| `/` | search-filter the log or history |
| `Q` | quit (Esc = back inside menus) |

**Adaptive:** switches to a compact single-column layout on phones and narrow
terminals (≥70×18 → full dashboard; smaller → compact; <34×10 → rotate hint).

---

## Per-route options and pacing

Every route can override the global settings (Routes tab → `P` → `O`):

| Setting | Values | Why |
|---|---|---|
| Parallel workers | inherit, 1, 2, 4, 8 | run one route single-threaded against a rate-limited host |
| Mirror mode | inherit, full, safe | `full` prunes deleted refs, `safe` keeps them |
| Mesh conflicts | inherit, newest, skip | see [Two-way sync](#two-way-sync-mesh) |

Globally, `O` (options) also holds:

| Setting | Values | Why |
|---|---|---|
| Pause between repos | 0, 1, 2, 5, 10 s | slows a run to spare laptop heat / disk throttling |
| Mesh conflicts | newest, skip | the default for routes that do not override it |
| Token warning | 7, 14, 30, 60, 90 days | how early a credential warns before expiring |

A pace of 1 s over 54 repos adds about a minute to a run and is logged:

```
  pacing: 1.0s between repos
```

---

## What a sync would delete (and how to stop it)

`mode: full` (the default) means "make the target an exact mirror": the push
is `git push --mirror`, so **a branch or tag that exists on the target but
not on the source is deleted there**. That is what you want most of the time,
and it is the one operation in gitsync that can lose work, so it is always
visible first:

```bash
gitsync sync --dry-run          # or press D in the TUI
```

```
[1/1] PLAN dockeryaml
         - hub up to date
         - backup: up to date
         - backup: WOULD DELETE 1 ref(s): prune-preview
```

`mode: safe` (globally in `O`, or per route in `P` → `O`) pushes heads and
tags but prunes nothing, so a branch only a target has is kept and you delete
it by hand where you want it gone.

**Deleting a whole repo is different, and safer:** the repo simply stops
appearing in the source's list, so nothing is pushed to it again — it is
*not* deleted anywhere. It becomes an orphan on the other sites, listed by
Tools → Disk (or `gitsync disk --orphans`), and removing it is a separate,
explicit step (`--prune-orphans`).

In a **mesh** route, refs are unioned: deleting a branch on one platform
while another still has it means the mesh re-creates it on the next run. You
would have to delete it on every peer.

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

## Platforms & tokens

The wizard prints this before asking for anything — here it is for reference:

| Platform | Create token at | Required scopes | Notes |
|---|---|---|---|
| **GitHub** | [github.com/settings/tokens](https://github.com/settings/tokens) | `repo` (+`workflow`, `read:org` as needed) | or use the `gh` CLI (no token) |
| **GitLab** | [gitlab.com/-/user_settings/personal_access_tokens](https://gitlab.com/-/user_settings/personal_access_tokens) | `api` | self-hosted: `<host>/-/user_settings/...` |
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
- optional: the `gh` CLI for GitHub (gitsync can use it instead of a token)

## Troubleshooting

### A sync reports "partial"

Partial means the repo synced to some targets but not all. The notification
names the repo and the reason. The three common causes are handled
automatically now:

- **`You are not allowed to force push code to a protected branch`** — the
  target's branch protection refuses a mirror push. gitsync enables
  `allow_force_push` on that branch (access levels are left alone) and
  retries. You will see `force-push enabled on 'master'` in the log.
- **`Rate limit exceeded`** (GitLab) — the API answered 429. Requests are
  retried with backoff; a large run may simply take longer.
- **`GitLab create failed for '<name>': path: can only include ...`** —
  GitLab's path rules are stricter than GitHub's. Names are sanitised
  automatically (leading `_`, `-`, `.` are illegal there).

If a repo still fails, `gitsync sync --repos NAME --dry-run` shows the
source and target state without changing anything.

### Other problems

| Symptom | Fix |
|---|---|
| `gitsync: command not found` | `~/.local/bin` not in PATH (with `--user`), or re-run the installer |
| A platform shows FAIL | `gitsync platforms` for the reason; check token scopes with `gitsync scopes` |
| New repo missing on a target | run `gitsync sync` — it creates them automatically |
| Refs deleted on a target | that's `mode: full` (exact mirror). Use `mode: safe` to keep them |
| Another sync already running | a run is in progress (timer or manual); wait and retry |
| TUI looks garbled | resize the terminal; the layout adapts |

## FAQ

**Does it sync issues, PRs and wikis?**
No — code only (branches, tags, commits). Issues/PRs come over with GitLab's
one-time GitHub importer but don't update afterwards.

**Can I use it bidirectionally?**
Yes — use a **mesh** route: `gitsync routes add all github,gitlab,forgejo`.
Every peer is both source and target, and the newest commit wins per branch,
so a change made on any platform reaches all the others. See
[Two-way sync](#two-way-sync-mesh). One-way routes still exist for the
simpler case where one platform owns the truth.

**Is it safe for private repos?**
Yes — private repos are created as private on targets, and the config file
is `0600`. Tokens are embedded in the URLs the tool uses, so protect the
config file and the machine accordingly.

**Does it need root?**
No — `--user` installs entirely under your home directory.

## CI without a provider

`gitsync ci` runs the entire check suite locally in about two seconds —
no account, no runner, no verification:

```bash
gitsync ci
#   python syntax (gitsync/*.py)  PASS
#   shell syntax (installers)     PASS
#   CLI smoke tests               PASS
#   unit checks (136 assertions)   PASS
# CI: all 4 checks passed
```

The same checks are also in `.github/workflows/ci.yml`,
`.forgejo/workflows/ci.yml` and `.gitlab-ci.yml` for hosted CI, and this repo
ships a `pre-push` hook that runs them before every push
(`git push --no-verify` to bypass).

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
