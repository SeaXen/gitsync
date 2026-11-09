# Changelog

All notable changes to gitsync are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.2] - 2026-11-09

### Fixed

- **The Platforms tab could not scroll, and the dashboard hid sites.** There
  is no cap on how many platforms a config can hold, but the panel only drew
  what fit: on a 14-row terminal with 9 sites, the last two were invisible
  and the arrow keys did nothing. The tab now scrolls (up/dn, PgUp/PgDn,
  Home/End) with a `N-M of T sites` counter, and the dashboard appends
  `... +N more site(s) - see the Platforms tab` instead of silently
  truncating.

### Changed

- 108 unit assertions (was 103).

## [1.4.1] - 2026-11-09

### Added

- **The TUI now says how many sites you have.** The Platforms panel title
  reads `PLATFORMS (4 sites, 4 connected)`, counting only platforms that are
  enabled *and* reachable, so a disabled or failing site shows up as
  `(5 sites, 3 connected)` instead of looking healthy. The compact/mobile
  layout leads with the same number (`3/5 sites: github:OK ...`).
  `gitsync status` and the piped `gitsync tui` snapshot print the count too.

### Changed

- 103 unit assertions (was 99).

## [1.4.0] - 2026-11-09

A review pass over the TUI plus the guardrails a mesh needs now that it can
write to every peer.

### Fixed

- **The dashboard hammered the network.** `_health_strip()` ran
  `TK.check()` (an HTTP round-trip per platform) and `diskutil.mirrors()`
  (a full walk of the hub) on *every* frame, and the dashboard redraws once
  a second for the clock — measured at **474 ms per frame and ~240 API
  calls a minute** while the dashboard sat open. Both now run in a
  background thread on a 30 s timer; a frame costs 1.5 ms.
- **The sync progress bar was dead code.** It read `SYNC.progress` in the
  TUI's own process, but the sync runs in a child process, so it always
  showed `starting...` / `in flight: ...`. It now parses the child's own
  `[12/54] OK   repo` output and shows a real bar plus the current repo.
- **`sync --to X` silently crippled a mesh route.** `--to` rewrites a
  route's targets, which on a mesh dropped it to one peer (reproduced:
  `--to backup` → *"mesh needs at least two live peers — skipped"*). It
  now refuses with an explanation instead of quietly doing nothing.
- **`gitsync mirror NAME` bypassed the mesh.** It always ran the one-way
  engine, so on a mesh route it could overwrite a newer commit living on
  another peer. It now runs `mesh_sync_repo` for mesh routes.
- **The webhook receiver panel crashed the TUI.**
  `scheduler.webhooks_active()` unpacked a `CompletedProcess` as a tuple
  (*"cannot unpack non-iterable CompletedProcess"*); it had only ever been
  called inside a bare `try/except`. Now fixed and reachable from the TUI.
- **The pause screen lost the cursor.** With more rows than the screen it
  rendered `rows[:h-4]`, so moving the highlight down past the window made
  it vanish (reproduced: 11 rows on a 14-line terminal). The list now
  windows around the cursor and shows `N-M of M`.
- **The repo browser had no cursor.** Arrows scrolled the list and the top
  row was the implicit, unmarked selection. There is now a highlighted
  cursor with a `N-M of M` counter, and Space selects what it points at.
- **`flash()` was invisible in every modal screen** — it only writes
  `state["message"]`, which `draw()` renders. Pausing a target, "nothing
  selected" and "webhook saved" all vanished. Modals now share a footer
  that shows a fresh message.
- **The compact (mobile) dashboard printed `?`** for the last run's counts
  because it read `synced_ok` / `github_repos`, keys that do not exist in
  `records.json`. It now reads `ok` / `partial` / `repos`, and reports
  partial runs.
- **`L` (full log) opened at the oldest line** — `log_view()` computed a
  scroll offset that `_scroll_view()` ignored, so a 2900-line log needed
  ~30 PgDn presses to reach today. It opens 100 lines from the end, and
  `G`/`End`/`Home` jump.
- **The dashboard hid routes.** The route line was one truncated string;
  with five routes the last two were invisible. It now keeps as many as
  fit and appends `+N more (3:Routes)`.
- **The Settings tab showed stale values** after a change made in Options
  or by hand, because it read a snapshot refreshed only with the platform
  counts. It now reads the config file.
- **`routes` list and the TUI showed targets whose platform no longer
  exists (or is disabled) as ordinary pausable rows.** The pause screen now
  labels them `(platform disabled)`.

### Added

- **Mesh conflict policy.** `mesh_conflict: newest` (default, unchanged)
  resolves a disagreement by date. `mesh_conflict: skip` leaves any ref the
  peers disagree about exactly as it is and reports
  `1 conflict(s) skipped: <branch>`, so a mesh can never quietly overwrite
  one platform's commit with another's. Skipped refs are excluded from the
  push as well as from the prune — otherwise the hub's stale copy would
  still have travelled. Settable globally (`O` → Mesh conflicts) or per
  route (Routes → `P` → `O`).
- **Dry run from the TUI.** `D` previews a whole run; inside the pause
  screen `D` previews just that route. Nothing is modified.
- **Per-route overrides.** A route can carry its own `workers` and `mode`,
  and (for mesh) `mesh_conflict`; all default to the global value
  ("inherit"). Editable in Routes → `P` → `O`, shown by `gitsync routes`.
- **Pacing.** `pace` (seconds between repos) slows a run to spare laptop
  heat and disk throttling. Settable in `O`, logged as
  `pacing: 1.0s between repos`.
- **Run one route from the TUI.** `S` inside the pause screen syncs just
  that route, with live output.
- **Route filters without the CLI.** `F` in the pause screen edits a
  route's include/exclude globs.
- **Webhook receiver panel.** `N` → `W` shows the secret, whether the
  receiver is live, the exact payload URL to paste into GitHub/GitLab/
  Forgejo, and installs or stops the systemd service.
- **First-run checklist** on the dashboard when no routes are configured.
- **Per-route last run** on the Routes tab: how long ago that route ran and
  whether the newest record failed for it (routes that never ran say so
  instead of a false "ok").
- **Token warning window** (`token_warn_days`) exposed in Options.
- **Mouse support:** click a tab in the tab bar, or a row in the repo
  browser and the pause screen. Needs a terminal that reports clicks
  (inside tmux: `mouse on`).

### Changed

- 99 unit assertions (was 83), identical in all three CI files.
- `snapshot()` (piped `gitsync tui`) now shows `[mesh]` / `[PAUSED]`
  markers like `routes`, `status` and `doctor`.

## [1.3.0] - 2026-11-09

Two features: sync in **both directions**, and pause any part of a route.

### Added

- **Two-way sync (mesh routes).** A route can now carry
  `"direction": "mesh"`: every peer is both a source and a target. Each run
  fetches every peer into a private namespace in the hub, keeps the **newest
  commit per branch** (and per tag), then pushes the result back to everyone.
  So a commit made on GitLab reaches GitHub, Forgejo and the local hub, and a
  commit made on GitHub flows the other way.

  ```bash
  gitsync routes add all github,gitlab,forgejo,backup
  ```

  Branches and tags are unioned: a ref is only removed from a peer when *no*
  peer still has it, so a deleted branch does not come back and a half-done
  deletion does not wipe anyone. The mesh force-pushes heads and tags only
  (never `--mirror`), leaving platform-specific refs such as GitHub's
  `refs/pull/*` alone. A fast path skips all fetching when every peer already
  holds identical refs, which keeps an idle run cheap. `sync --dry-run`
  reports whether the peers agree without touching anything.

- **Pause a route or a single target.** `gitsync routes pause NAME [TARGET]`
  stops a whole route (`"enabled": false`) or one target
  (`"paused_targets": [...]`); `routes resume` reverses it and the target
  catches up on the next run. In the TUI: **Routes tab → `P`**, ↑/↓ to move,
  Space to toggle — it saves immediately. Paused entries are marked `PAUSED`
  in `routes`, `status`, `doctor`, the Routes tab and the dashboard, and every
  run logs `paused target(s): ...` before skipping them.

### Fixed

- **Creating a GitHub repository never worked.** `GitHubPlatform._api()` was
  GET-only, so the `POST /user/repos` call raised *"takes 2 positional
  arguments but 4 were given"* and a repo that existed only on another
  platform could not be mirrored up. `_api()` now takes a method and payload,
  and when the credential is the `gh` CLI it creates the repo with
  `gh repo create` (tolerating "already exists"), which is also the only path
  that works without a token in the config.

### Changed

- 83 unit assertions (was 76).
- README: new *Two-way sync (mesh)* and *Pause a route or a target* sections,
  plus Features/Commands/TUI-key/FAQ updates.

## [1.2.2] - 2026-11-09

### Fixed

- **`sync --dry-run` could not probe GitLab targets.** `repo_url()` built the
  URL with two `%s` placeholders and three arguments, so the plan printed
  `gitlab: cannot probe (not all arguments converted during string
  formatting)` instead of the real state. The URL is now built correctly and
  the repo name is passed through the same `gitlab_path()` sanitiser as
  creation. Regression test added.

### Changed

- 76 unit assertions (was 75).

## [1.2.1] - 2026-11-09

Fixes the three things that made a sync report "partial" on a healthy setup.

### Fixed

- **Protected branches no longer block a mirror push.** GitLab and
  Forgejo/Gitea refuse `--force` on a protected branch, so a repository
  that had drifted (or was first pushed with different history) failed
  with *"You are not allowed to force push code to a protected branch"*
  and stayed permanently out of sync. The push now detects that specific
  rejection, asks the adapter to enable force-push on the protected
  branches (`allow_force_push`, access levels untouched), and retries the
  same push once. The extra API calls happen only on failure — the happy
  path is unchanged.
- **GitLab API rate limits are retried.** A 50-repo run bursts hard, and
  GitLab.com answers 429 (or 502/503) for some repos; those looked like
  "GitLab create failed" and were counted as partial. `_http` now backs
  off and retries 429/500/502/503/504 up to twice, honouring
  `Retry-After` (capped at 30s).
- **GitLab project paths starting with `_`, `-` or `.`** were rejected by
  GitLab with an opaque "create failed" (its path rules are stricter than
  GitHub's). Names are now sanitised with `gitlab_path()` — leading
  separators stripped, accents transliterated, `.git`/`.atom` suffixes
  removed — and API validation errors are flattened into one readable
  line by `_api_error()` instead of dumping a dict.
- **Shorter push errors.** A failure now reports the first meaningful
  `remote:` line instead of a truncated wall of git noise.

### Changed

- 75 unit assertions (was 58), covering path sanitisation, error
  flattening, protected-branch detection and concise error extraction.

## [1.2.0] - 2026-11-09

Five new capabilities, plus a fix for a rendering bug that had silently
broken most of the TUI since 1.0.0.

### Added

- **Webhook-driven sync** — new `gitsync/webhooks.py`: a stdlib HTTP receiver
  that authenticates GitHub (`X-Hub-Signature-256`, HMAC-SHA256), GitLab and
  Forgejo/Gitea (`X-Gitlab-Token` / `X-Gitea-Token`) push events and syncs
  just that repo. `--install` wires a systemd service; `--secret --rotate`
  manages the shared secret (stored 0600, constant-time compare). Binds to
  127.0.0.1 by default, dedupes bursts, runs the sync detached so platforms
  never time out, and refuses to start without a secret.
- **`sync --dry-run`** — plans a sync with `git ls-remote` and reports
  `would CREATE` / `N ref(s) behind` / `up to date` per target. Creates
  nothing and writes nothing to the hub. Backed by a new read-only
  `repo_url()` on every platform adapter.
- **Per-route schedules** — a route may carry its own
  `hourly|6h|12h|daily|weekly|off`, set via `routes add --schedule` or
  `gitsync route-schedule NAME`. The systemd timer fires at the finest
  schedule any route needs; `sync --scheduled` (what the timer runs) skips
  routes that are not due yet, tracked in `route_state.json`. `gitsync routes`
  now shows each route's schedule and next run.
- **`gitsync disk`** — hub and per-platform sizes, the N largest mirrors,
  orphan detection (mirrors whose repo no longer exists upstream) with
  `--prune-orphans`, and safe reclaim via `--gc`
  (`reflog expire` + `gc --prune=now`, optionally `--older-than N`).
  Sizes count `st_blocks`, so they match `du`.
- **`gitsync tokens`** + a doctor section — checks every credential for
  expiry and revocation. GitLab via `/personal_access_tokens/self`, GitHub
  via the `GitHub-Authentication-Token-Expiration` response header; Forgejo
  and Bitbucket report "not exposed" rather than guessing.
- **Dashboard health strip** — one line showing token health, hub size and
  webhook state.
- **Routes tab** now shows each route's schedule and next run.

### Fixed

- **The TUI's panel rendering was broken in every release so far.** The
  dashboard, Platforms tab and Routes tab drew their boxes into separate
  curses windows and staged them with `noutrefresh()`, but `commit_screen()`
  only calls `doupdate()`, and the clock's bare stdscr flush then overwrote
  those staged windows with blanks. Net effect: an almost-empty dashboard
  and two blank tabs, with only the Log and History tabs (which write to
  stdscr) working. Everything now draws onto stdscr, which is what the
  commit path actually flushes. Found while adding the health strip.
- `update_clock()` repainted 19 cells and called `doupdate()` to keep the
  byte count small; it now requests a full redraw, which is a slightly
  larger diff and always correct.
- Webhook event detection treated GitLab's `Push Hook` header as an unknown
  event and ignored it. GitHub (`push`), GitLab (`Push Hook`, `Tag Push
  Hook`) and Forgejo (`push`) are all recognised now.
- `subprocess.run` in the platform adapters inherited the TTY on stdin; it
  now passes `stdin=DEVNULL` so a child can never read the terminal.
- Removed the now-unused `box()` helper.

### Changed

- 58 unit assertions (was 20), covering the dry-run planner's URL probing,
  size formatting, token-expiry levels, webhook auth/event/dedupe logic and
  per-route schedule resolution.

## [1.1.1] - 2026-11-09

Fixes the route selection behind multi-repo `mirror --to`, and documents
same-site sync (GitHub → GitHub and friends).

### Fixed

- **`mirror --from SRC --to TGT` no longer picks the wrong route.** With
  several routes sharing a source, it took the first match and then failed
  if that route lacked the target. It now prefers a route that actually has
  the requested target, and when none does it prints the candidate routes
  instead of a dead end. Found while wiring up a GitHub → GitHub route.
- **The TUI repo browser names the platform it listed.** The header said
  "REPOS - 53 repos" without saying *whose*; it now reads
  "REPOS on github - 53 repos". Matters as soon as you configure a second
  account on the same site.

### Documentation

- **Same-site sync** — several accounts per site side by side
  (`github → github`, `gitlab → gitlab`, `forgejo → forgejo`), with the
  three-step setup, and the two caveats worth knowing: a second GitHub
  account must use `--token` rather than the `gh` CLI (which is bound to the
  active account), and the local hub mirror is keyed on repo name only, so
  keep one direction per repo name.

## [1.1.0] - 2026-11-09

Selective sync: browse an account's repos and sync only the ones you pick,
instead of always mirroring the whole account.

### Added

- **`gitsync repos`** — list the repos of any configured platform
  (`--platform`, `--forks`, `--private-only`, `--public-only`). Forks are
  hidden by default so you see your own repos first.
- **Repo browser in the TUI** — press `E` to open a scrollable list of the
  source account's repos with private/fork markers, live filtering (`/`),
  multi-select (`space`, `a` = all, `n` = none) and a running count.
  `S` syncs the selection to every target on the route, `L` clones it to
  the local hub only, `B`/`Esc` goes back (Esc clears an active filter
  first).
- **`gitsync sync --repos "a,b,c"`** — run a sync restricted to specific
  repos. Names match exactly (case-insensitive) and apply on top of the
  route's `include`/`exclude` globs.
- **Multi-repo `gitsync mirror`** — `mirror tradex SeaVault SeaDash` syncs
  several repos in one call, plus `--from` to pick the source platform and
  `--to` to restrict the target. Reports `N ok, M failed` at the end and
  names any repo it could not find on the source.
- **`fork` flag on every platform adapter** — `list_repos()` now reports
  whether a repo is a fork (GitHub `isFork`/`fork`, GitLab
  `forked_from_project`, Forgejo/Gitea `fork`), so forks can be filtered
  out of listings.

### Fixed

- **Arrow keys, PgUp/PgDn and Home/End now work throughout the TUI** —
  `stdscr.keypad(True)` was never enabled, so escape sequences arrived as
  raw bytes instead of `curses.KEY_UP`/`KEY_DOWN`/`KEY_NPAGE`. This
  affected every scrollable view, not just the new repo browser.

## [1.0.0] - 2026-11-08

Initial public release.

### Added

- **Six platform adapters** — GitHub, GitLab, Forgejo, Gitea, Bitbucket and
  local folders; any of them can be a sync source or target, and several
  accounts of the same type can run side by side.
- **Any-direction routes** — `github → gitlab`, `gitlab → forgejo`,
  `folder → github`, `bitbucket → everything`, with per-route
  `include`/`exclude` globs and per-target pushes (`sync --to`).
- **Sync engine** — local mirror hub (a free offline copy), parallel
  workers, run locking, self-healing partial mirrors, `full` (exact
  mirror) and `safe` (never delete) modes, fork filtering.
- **TUI dashboard** — six tabs (Dashboard / Platforms / Routes / Log /
  History / Settings), live per-repo sync progress, `/` search filtering,
  duration sparklines, adaptive compact layout for phones and narrow
  terminals, flicker-free rendering.
- **Notifications** — Discord webhooks and Telegram bot messages,
  `failure`/`success`/`always`/`never` modes, set up from the TUI (`N`).
- **Drift detection & repair** — `compare` verifies HEADs across every
  platform; `fix` re-syncs what drifted.
- **Account management** — `platforms add|edit|remove|test` without the
  wizard.
- **Diagnostics** — `doctor` health check with `--fix` auto-repair
  (broken mirror shells, stale locks).
- **Scheduling** — systemd timer with `Persistent=true` (catches up runs
  missed while the machine was off) with a cron fallback; `watch` mode for
  near-real-time foreground polling.
- **SSH transport** — per-platform `protocol: ssh` with `ssh_port`.
- **One-click install** — `quickinstall.sh` bootstraps download, install,
  wizard, scheduler and first sync; `gitsync update` upgrades in place.
- **Docs** — README, `docs/GETTING-STARTED.md` (step-by-step walkthrough),
  `docs/PERMISSIONS.md` (token scopes per platform), `SECURITY.md`.

### Security

- No credentials, tokens, webhooks or personal paths in the repository;
  everything lives in the user's own `~/.config/gitsync/config.json`
  (gitignored, mode `0600`).
- All subprocess calls are bounded by timeouts; no unbounded network or
  git operations.

[1.0.0]: https://github.com/SeaXen/gitsync/releases/tag/v1.0.0
