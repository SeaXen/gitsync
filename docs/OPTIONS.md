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
what makes a failing timer visible.

Both channels work independently. Discord and Telegram can both be set; the
test (`Enter` in the TUI, or `gitsync notify --test`) sends to whichever are
configured.

---

## Per-route options (`P` → `O`)

Every one of these defaults to "inherit" (use the global value):

- **Parallel workers** — one route single-threaded against a rate-limited
  host, while the others stay at 4.
- **Mirror mode** — one route `full` (exact mirror) while everything else
  stays `safe`.
- **Mesh conflicts** — one mesh `newest`, another `skip`.
- **Schedule** — hourly for an active project, weekly for an archive.

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
