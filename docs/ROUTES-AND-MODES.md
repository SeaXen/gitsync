# Routes and sync modes

A **route** is one named sync direction: a source platform, one or more
targets, and the rules for that pair. This page covers the modes, filters,
pausing, mesh, and the exact conditions under which anything is deleted.

> **Which mode do I want?**
>
> | You want… | Use | What it does |
> |---|---|---|
> | a backup that never loses anything | **one-way, `safe`** (the default) | copies heads + tags, never force-pushes, never deletes a ref or a repo |
> | an exact replica of the source | **one-way, `full`** | force-updates heads + tags and prunes refs the source no longer has. Repos are still never deleted (that needs `prune_repos`) |
> | a folder to be the single truth | **local → platform** | the folder is the source; platforms are overwritten |
> | several peers that must all agree | **mesh** | every peer is source and target; the newest commit wins per branch, refs are unioned |
>
> Unsure? Start `safe`. `gitsync compare` and `gitsync verify` tell you when a
> target has drifted, and you can switch to `full` for one run.

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

## If the source host disappears

Every repo is mirrored into the local hub first, so the hub is a complete
copy even when GitHub is not. `restore` pushes it back out:

```bash
gitsync restore --to gitlab              # every mirror, create repos as needed
gitsync restore --to backup --dry-run    # preview, change nothing
gitsync restore --to gitlab,forgejo --repos "app,docs"
```

- mirrors with **no refs** (an empty repo) are skipped with a log line, and
  nothing is created for them
- nothing is deleted on a target unless `mode: full` **and** `--prune`
- missing repos are created private unless `--public`
- exit code is non-zero if anything failed

---

## A repo list in a file

`include` globs live in `config.json`, which is fine for a handful of repos
and miserable for 200. `include_file` keeps the list in its own file (one
name per line, `#` comments), so it stays diffable:

```bash
gitsync routes edit main --from-file ~/repos.txt
```

or from the TUI: repo browser (`E`) → tick the repos you want → `W` → pick a
path. The selection is written and attached to the route(s) whose source is
the platform you were browsing. A missing or empty file means "no filtering"
— it never silently syncs nothing.

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

`mode: full` means "make the target an exact mirror": heads and tags the
source does not have are **deleted** on the target. `mode: safe` — the
default for a new setup — never deletes and never force-pushes. Either way,
the one operation in gitsync that can lose work is always visible first:

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

[← back to the README](../README.md)
