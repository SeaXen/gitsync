# Contributing to gitsync

Thanks for your interest in improving gitsync! This document covers the
basics.

## Ground rules

- **Zero dependencies** — Python 3.8+ standard library only. Please don't
  add third-party packages; that constraint is a feature.
- **No credentials in the repo** — never commit tokens, webhooks, paths or
  account names. Everything user-specific belongs in the user's
  `~/.config/gitsync/config.json`.
- **Bounded operations** — every subprocess/network call must have an
  explicit timeout (see `platforms.API_TIMEOUT` / `GIT_TIMEOUT`).
- **Flicker-free TUI** — draw offscreen, `noutrefresh()` sub-windows,
  finish with a single `doupdate()`; never `stdscr.noutrefresh()` after
  the panels (it blanks them).

## Development setup

```bash
git clone https://github.com/SeaXen/gitsync.git
cd gitsync
export PYTHONPATH=$PWD          # run without installing
python3 -m gitsync --help
python3 -m gitsync tui          # the dashboard
```

Use an isolated config so you don't touch your real one:

```bash
export GITSYNC_CONFIG_DIR=/tmp/gitsync-dev/config
export GITSYNC_DATA_DIR=/tmp/gitsync-dev/data
```

## Testing your change

```bash
# syntax
python3 -m py_compile gitsync/*.py

# the CLI still answers
python3 -m gitsync --version
python3 -m gitsync scopes | head -5

# a real sync against a throwaway local folder
python3 -m gitsync platforms add devbak --type local --path /tmp/devbak
python3 -m gitsync routes add dev github devbak
python3 -m gitsync sync --route dev
python3 -m gitsync routes remove dev
python3 -m gitsync platforms remove devbak --force
```

CI runs `python3 -m gitsync ci` (python syntax, shell syntax, and a
CLI smoke block that performs a real offline sync and asserts both
push modes), plus `python3 -m unittest tests.test_logic` in all three
workflows. A `pre-push` hook runs the same locally
(`gitsync ci --install-hook`).

### Web UI testing (Playwright)

The Web UI has automated end-to-end browser tests in `tests/test_webapp_playwright.py`
covering authentication, shell navigation across all 9 pages, mobile responsiveness
(375×667 zero overflow), modal dialogs, and logout.

Test-only dependencies are allowed for running this suite:
```bash
pip install playwright
playwright install chromium
python3 -m unittest tests.test_webapp_playwright
```
In CI, Web UI testing runs as an optional job. The runtime `gitsync` application
remains strictly Python standard library only (zero runtime dependencies).

## Adding a platform

1. Add an adapter class in `gitsync/platforms.py` implementing
   `test()`, `user()`, `list_repos()` and `ensure_repo()`.
2. Register it in the `ADAPTERS` dict.
3. Add its token guidance to `gitsync/permissions.py`
   (where to create the token, required scopes, minimum).
4. Mention it in `README.md` and `docs/PERMISSIONS.md`.

## Commit style

Short, imperative subjects with a body explaining *why*:

```
feat: add codeberg as a forgejo-compatible provider

codeberg.org speaks the same API as Forgejo, so the existing adapter
works with just a different default URL.
```

## Releasing

1. Update `CHANGELOG.md`.
2. `git tag -a vX.Y.Z -m "vX.Y.Z"` and push the tag.
3. Publish the release notes.
