# gitsync options — what every setting means

A plain-language companion to `README.md`. The README has the tables; this
page explains the *choices* — what each one costs you, and when to change it.

Nothing here is required reading to use the tool. The defaults are chosen so
that a first setup cannot lose work.

---

## The two decisions that matter

### `mode`: `safe` or `full`

| | `safe` (default) | `full` |
|---|---|---|
| New commits on the source | pushed | pushed |
| A branch that exists only on the **target** | **kept** | **deleted** |
| A target commit newer than the source | reported as `refused non-fast-forward` | overwritten |
| Uses `--force` | never | yes |

**Use `safe` unless you specifically want the destination to become an exact
copy of the source.** With `safe`, if you commit on GitLab and GitHub is the
source, gitsync tells you and leaves your GitLab commit alone. With `full`,
your GitLab commit is gone after the next run.

`D` (dry run) shows you the difference before either happens: in `full` it
lists every ref that would be deleted.

### `mesh_conflict`: `newest` or `skip`

Only applies to a **mesh** route (every peer equal). It decides what happens
when two peers disagree about a branch:

- `newest` (old default) — the newer commit wins, the older one is
  overwritten. Two people committing on two forges at once means one commit
  disappears.
- `skip` (default since 1.7.0) — both are left exactly as they are, the run
  reports `partial`, and the log names the branch. Reconcile by hand; the
  next run converges.

`skip` is the safe default because a mesh can now write to every site,
including the one you consider canonical.

---

## Global settings (`O` in the TUI)

### Parallel `workers` — 1, 2, 4, 8 (default 4)

How many repos sync at once. Higher is faster until a host starts
rate-limiting you (GitLab.com answers 429 under a 50-repo burst). If you see
`rate limited` in the log, drop to 2.

### `pace` — 0, 1, 2, 5, 10 seconds (default 0)

A pause *between* repos. Costs you that many seconds × repo count (54 repos at
1s ≈ +1 minute) and in return keeps a laptop cooler and an SSD from
throttling. Logged as `pacing: 1.0s between repos`.

### `include_forks` — false (default)

Mirror repositories you forked. Off by default because most people do not
want 40 forks of other people's projects in their mirror.

### `schedule` — hourly, 6h, 12h, daily, weekly, off

How often the timer fires. **The timer actually runs at the finest schedule
any route asks for** — a route on `hourly` with a global `daily` still fires
hourly, and routes that are not due are skipped by the run itself.

`off` means manual only: press `S`, or run `gitsync sync`.

### `token_warn_days` — 7, 14, 30, 60, 90 (default 30)

How early the dashboard warns that a credential is about to expire. GitLab
tokens have an expiry date; GitHub fine-grained tokens do; Forgejo tokens
never do (they report "unknown" rather than guessing).

### `min_free_gb` — 0, 0.25, 0.5, 1, 2, 5, 10 (default 0.5)

The floor of free space on the mirror disk. Below it, a sync **refuses to
start**: the run is recorded, the notification channel gets the reason, and
the CLI exits 1 (so the timer's mail fires). A full disk turns a fetch into a
half-written mirror, and that mirror then fails every later run with a
confusing git error — stopping with a sentence is cheaper.

`0` disables the guard. An unreadable filesystem (no `statvfs`) never blocks.

### `hooks.pre` / `hooks.post` — commands, blank by default

```
settings.hooks.pre   run before a real sync (never a dry run)
settings.hooks.post  run after one
```

`shlex.split`, never a shell: the config is a file a web session can write,
so a shell would turn "save settings" into remote code execution. A hook that
needs a pipeline can call a script of its own.

- a **pre** hook that exits non-zero blocks the sync (`blocked` in the
  history, exit 1) — use it to wake a NAS or mount a disk
- a **post** hook that fails only logs: the backup itself succeeded, and a
  broken notifier must not change the exit code of a good run
- the post hook gets `GITSYNC_OK`, `GITSYNC_PARTIAL`, `GITSYNC_FAILED`,
  `GITSYNC_REPOS`, `GITSYNC_ELAPSED` and `GITSYNC_FINISHED` in its
  environment

### `theme` — default, mono, ocean, forest, sunset

TUI colours, applied immediately. `mono` is all-white, for terminals where
colour is noise or you are colour-blind: the layout and the words carry the
meaning either way.

### `relax_protection` — false (default, hand-edit only)

**Off** means: if a destination refuses a push because of branch protection,
gitsync reports `push rejected by branch protection. gitsync will not change
it.` and moves on.

**On** means gitsync will turn on force-push over that protection to make the
mirror succeed — a lasting security change on a repository you may not own.
Only set this on destinations you control.

---

## Notifications (`N`)

`notify.on`:

| Value | Sends when |
|---|---|
| `failure` (default) | anything failed **or** was partial |
| `success` | only when everything synced cleanly |
| `always` | every run |
| `never` | never |

A run is `partial` when a target refused a push, or a mesh skipped a
conflict — so `failure` catches those too. The exit code follows: `gitsync
sync` returns 0 only when nothing failed and nothing was partial, which is
what makes a failing timer visible. A **blocked** run (disk guard, pre hook)
returns 1.

Channels are Discord, Telegram, ntfy, a generic JSON webhook and SMTP mail;
see [NOTIFICATIONS.md](NOTIFICATIONS.md) for every key and the setup
commands. Each channel is independent, the test (`Enter` in the TUI, or
`gitsync notify --test`) reports them one by one, and a half-typed channel is
named as a problem rather than silently dropped.

The rule is decided **per route**: a route's own `notify_on` overrides the
global mode, so one route can page on every run while the rest stay quiet
until something breaks.

---

## Per-route options (`P` → `O`)

Every one of these defaults to "inherit" (use the global value):

- **Parallel workers** — one route single-threaded against a rate-limited
  host, while the others stay at 4.
- **Mirror mode** — one route `full` (exact mirror) while everything else
  stays `safe`.
- **Mesh conflicts** — one mesh `newest`, another `skip`.
- **Schedule** — hourly for an active project, weekly for an archive.
- **Notify on** — override the global notification mode for one route.
- **Forked repos** — include or skip forks on this route alone (blank =
  inherit the global `include_forks`).
- **Skip partners** — archived repos (default: skipped) and empty repos
  (default: kept).

An archived repository is read-only on every supported host, so syncing one
only produces push failures; an empty repo is only visible on a local folder
and is almost always a placeholder. The log line counts what was skipped
(`skipped: 1 archived, 2 fork`), so a shrinking repo count is never a
mystery.

A route can also be paused entirely (`"enabled": false`) or have individual
targets paused (`"paused_targets": [...]`). Paused items are skipped by every
run including the timer, and marked `PAUSED` in the TUI.

---

## Platform keys

### `affiliation` (GitHub)

Which repos `list_repos` returns:

| Value | Sees |
|---|---|
| `owner` (default) | repos you own |
| `owner,organization_member` | + repos in organisations you belong to |
| `owner,collaborator,organization_member` | + repos you were added to as a collaborator |

**Adding the `read:org` scope is not enough** — gitsync asks GitHub for
`affiliation=owner` until you change this key. If org repos are missing, this
is why.

### `protocol` / `ssh_port`

`http` (default) embeds the token in the git URL. `ssh` uses your SSH key and
never embeds anything — but it needs a key on the host running gitsync, and
the SSH port is usually **not** the web port (3000/3002 vs 22).

### `ns_id` (GitLab)

Which namespace new projects are created in. Blank means your personal
namespace. Set it only when you want repos created inside a group.

### `url_template` (generic)

For a git server with no API: `https://git.example.com/{owner}/{name}.git`.
A generic platform can be a **target** (push) but not a source, because there
is no API to list repos.

---

## Speed

Measured on a 4-platform, 53-mirror setup:

| | Before | After |
|---|---|---|
| TUI start, warm (within 60s) | 4.4 s | **0.21 s** |
| TUI start, cold | 4.4 s | 3.3 s |
| Key response | 15 ms | 15 ms |
| Slowest tab per frame | 2.4 ms | 2.0 ms |

The warm start comes from caching the last platform probe for 60s
(`~/.local/share/gitsync/probe_cache.json`, `0600`): the dashboard paints
real counts at once and a background refresh corrects them. `R` forces a
refresh, and the "counts: Ns old" line always shows how old the numbers
really are. The remaining cold-start cost is the `gh` CLI (~0.7s a call),
which gitsync does not control.

Per-frame work is already small: drawing is under 2.5 ms on the slowest tab,
and the log tail is cached by (mtime, size) so the Log tab costs 0.33 ms.

## Where the numbers live

| Thing | Where |
|---|---|
| Config | `~/.config/gitsync/config.json` (`0600`) |
| Mirror hub | `~/.local/share/gitsync/mirrors/` |
| Log | `~/.local/share/gitsync/gitsync.log` (`0600`) |
| Run history | `~/.local/share/gitsync/records.json` (`0600`, last 200 runs) |
| Per-route last-run times | `~/.local/share/gitsync/route_state.json` |

Override the first two with `GITSYNC_CONFIG_DIR` and `GITSYNC_DATA_DIR` —
handy for testing without touching a real setup.


---

## Picking repos: globs, or a file

Three ways to say "not everything", strongest last:

1. **In the wizard** (`gitsync setup`, step 3) — it lists your repos (capped
   at 50 with a count) and takes Enter for all, or a comma list / globs.
2. **Globs in the config** — `"include": ["Sea*", "gitsync"]`. Fine for a
   handful; unreadable at 200.
3. **A file** — `"include_file": "~/repos.txt"`, one exact repo name per
   line, `#` comments:

   ```
   # the ones I actually care about
   gitsync
   dockeryaml
   ```

   Set it with `gitsync routes edit NAME --from-file repos.txt`, or from the
   TUI: `E`, tick the repos, `W`, choose a path.

A missing or empty file means **no filtering** — it never silently syncs
nothing. `--clear-filters` (or deleting the key) goes back to "every repo".

## GitLab via the `glab` CLI

`"auth": "glab"` on a GitLab platform uses the CLI instead of a token:

- the API goes through `glab api`
- git gets a `GIT_ASKPASS` helper, so the token is read from `glab` at call
  time and **never written into a remote URL** (and so never into a hub
  `config` file or a log line)

If `glab` is missing or not logged in, `platforms test` says so plainly
rather than failing with an auth error. Requires `glab auth login` first.

## When the source host is gone

`gitsync restore --to PLATFORM` pushes every hub mirror back out. It skips
empty mirrors with a log line, creates missing repos (private unless
`--public`), and deletes nothing unless `mode: full` **and** `--prune`. See
the README section "If the source host disappears".

In the web UI the preview is not optional: **Restore always runs the dry run
first, streams it, and then asks you to type `restore`** before anything is
pushed. A checkbox you forgot to tick used to be the only thing standing
between a click and a push of ~every mirror.

## Retrying only what broke

`gitsync sync --retry-failed` re-runs the failures from the last run. It is
scoped **per route and repo** — the same repo name that failed on one route
is healthy on another, and retrying it there was wasted work (and a second
failure). Routes with no failure are skipped before anything is listed.

```bash
gitsync sync --retry-failed                 # just the failures, each on its route
gitsync sync --retry-failed --repos tradex  # those two, everywhere
```

## Git LFS and submodules

gitsync mirrors **refs**. A repository using Git LFS or submodules gets a log
line (`uses Git LFS — install git-lfs to mirror the objects`, `has
submodules`), and when `git-lfs` is installed the run also does
`git lfs fetch --all` into the hub. Without it, a clone from a target will
have the refs but not the large files — which is exactly what the log line
says, instead of a silently incomplete mirror.
