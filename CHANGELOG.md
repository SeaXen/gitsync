# Changelog

All notable changes to gitsync are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
