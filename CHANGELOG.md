# Changelog

All notable changes to gitsync are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
