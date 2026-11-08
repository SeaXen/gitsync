# Changelog

All notable changes to gitsync are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
