# gitsync 2.0.1 — remaining fixes (round 2)

Audited: `gitsync-main_2.zip`, version **2.0.1**. Same rules as round 1:
Python 3.8+ stdlib only, never touch the real config/hub, never echo tokens,
logic tests only in `tests/test_logic.py` (no network), run before each commit:

```bash
export PYTHONPATH=$PWD GITSYNC_CONFIG_DIR=/tmp/x/config GITSYNC_DATA_DIR=/tmp/x/data
python3 -m py_compile gitsync/*.py
bash -n install.sh quickinstall.sh bin/gitsync
python3 -m unittest tests.test_logic
python3 -m gitsync ci
```
Bump to `2.0.2` + CHANGELOG entry. Do not claim "10/10" in the changelog.

## What was verified as fixed (do not redo)
B1, S1, S2, S3 (checked behaviorally, see below), W1 (no horizontal overflow on
all 9 pages at 375px; Playwright asserts it), W2, T1–T4, D1–D4, TUI items
5.1–5.9 present. 163 unit tests + 5 Playwright tests pass; `gitsync ci` passes
in a clean environment with no git identity and no network.

## P0 — breaks authenticated sync

### R1 — `askpass.py` is not executable, so token auth fails outside `install.sh`
- **Reproduced:** end-to-end against a local HTTP git server that requires
  Basic auth. With the files as shipped in the zip (`askpass.py` mode `0644`):
  `RESULT: ('clone-failed', "... fatal: cannot exec '.../gitsync/askpass.py':
  Permission denied ... could not read Username ...")`. After `chmod +x`: clone
  **and** push both succeed (`('ok', 'd', [])`) and the token is in no argv.
- **Who is hit:** anyone running from source (`PYTHONPATH=$PWD python3 -m
  gitsync`), the Docker image (`COPY gitsync ./gitsync`, no chmod), any
  tarball/zip install. Only `install.sh` does `chmod +x`. The old `glab_askpass.py`
  has the same exposure.
- **Fix (do all three):**
  1. Make the git index mode executable: `git update-index --chmod=+x
     gitsync/askpass.py gitsync/glab_askpass.py` and commit (this is the root
     cause — files were created on Windows).
  2. Make it robust at runtime: in `sync.askpass_env()` (and the glab helper
     path in `platforms.py` ~line 451) `os.chmod(helper, mode | 0o111)` before
     use, wrapped in try/except; if the file still isn't executable, fall back
     to a generated temp wrapper script `#!/bin/sh\nexec "<sys.executable>" "<askpass.py>" "$@"`
     created `0700` in a private temp dir and cleaned up after the run.
  3. Add `RUN chmod +x /app/gitsync/askpass.py` (or equivalent) in `Dockerfile`.
- **Test (offline, no real server needed):** unit-test `askpass_env()` that the
  helper it points to is executable (`os.access(path, os.X_OK)`) after the call,
  even when the file starts as `0644` (copy to a temp dir, strip the bit, call).
  Also add an integration test using `git http-backend` behind a tiny
  basic-auth `http.server` on `127.0.0.1` (stdlib only) that clones and pushes
  through `sync_repo`, asserts success and that the secret never appears in
  captured argv. (A working script for this was used in the audit; it is ~60
  lines.)

## P1

### R2 — an offline network is reported as a *critical* token problem
- `GitHubPlatform.token_info()` now returns `{"error": "unreachable"}` for a
  network failure (correct), but `tokencheck.check()` turns **any** `error` into
  `level="critical"`. Result: dashboard says "1 token(s) need attention" and
  alert logic that uses `tokencheck.bad()` fires when the machine is merely
  offline.
- **Fix:** in `tokencheck.check()` treat `error == "unreachable"` as
  `level="unknown"`, note `"could not reach host — not verified"`. Keep
  `"token rejected"` as critical. Same for GitLab/Forgejo/Bitbucket `token_info`
  paths (return an explicit unreachable marker when `rc is None`).
- **Test:** `token_info` with `_http` patched to `(None, "boom", {})` →
  `check()` level is `unknown`, `bad()` is empty, `summary()` says unverified.

### R3 — "unverified token" count is wrong for local folders and for tokens with no expiry
- Reproduced: a config with two `local` platforms shows **"2 token(s)
  unverified"** on the TUI dashboard (and web says "local folder — no
  credentials"). Local folders have no token. Also a valid GitHub token with
  no expiry date gets level `unknown` forever (`level_for(None)`), so a healthy
  token reads "unverified".
- **Fix:** add a level `"n/a"` for platforms without credentials (local, `gh`
  CLI) and exclude them from `summary()`. Add level `"ok-no-expiry"` (or keep
  `ok` with note "no expiry set") for a token that authenticated successfully
  but reports no expiry. Only truly unchecked tokens are "unverified".
- **Test:** local-only config → summary `"all tokens healthy"` (or "no tokens
  to check"); authenticated-no-expiry → not counted unverified.

### R4 — TUI help text is wrong
- `gitsync/tui.py` ~line 2856: `"1 - 6  Switch tabs (Dashboard, Platforms,
  Routes, Log, History, Settings)"`. There are **7** tabs and the order is
  Dashboard, Platforms, Routes, **Stars**, Log, History, Settings (`TABS`
  constant). Build this line from `TABS` so it can't drift again.
- **Test:** assert the help text contains every name in `TABS`.

### R5 — TUI resize handling is thin
- Only one `KEY_RESIZE` reference exists in `tui.py`. The modal loops now
  re-read `getmaxyx()` (good), but verify every full-screen loop calls
  `stdscr.erase()` + redraw on `KEY_RESIZE` (and `curses.update_lines_cols()`
  where the Python version needs it). Test by resizing a pty (`TIOCSWINSZ` +
  `SIGWINCH`) from 120×30 → 80×24 → 40×12 and asserting no crash and a redraw.

## P2 — polish and structure

- **Web, "Install timer" is a raw link** in the dashboard notice. `DESIGN.md`
  says action controls are `<button>`s, not anchors. Make it a button styled as
  a link, and have it actually start the install flow or explain the command.
- **Plan file name:** the repo now contains `GITSYNC-FIX-PLAN  Claude.md`
  (double space, spaces in the name) and `GITSYNC-REVIEW.md` links to it by
  URL-encoded path. Rename to `docs/FIX-PLAN.md` (no spaces) and update the
  link. Plans for a finished round belong under `docs/` or the changelog, not
  the repo root.
- **Phone top bar** is icon-only (Dry run, Sync, Sign out). Accessible names are
  required; confirm each has `aria-label` and a visible tooltip/`title`, and
  that the three icons are visually distinguishable (the Sync icon is a plain
  refresh arrow and reads like "reload page"; consider a "play" icon).
- **File size/structure (unchanged from round 1):** `tui.py` is now 3240
  lines, `web.py` 2032, `__main__.py` 2046. Split `tui.py` into a package by
  screen only after R1–R5 are green and covered; no behavior change.
- **Retry failed** (`--retry-failed`): it re-runs failed repo names across *all*
  routes (`only` is a repo-name filter). If the same repo name exists in two
  routes and only one failed, both re-run. Scope the retry to
  `(route, repo)` pairs from the last record, or document the behavior.
- **TUI smoke test coverage:** `TestTUISmoke`/`TestTUIDrawPerformance` exist;
  add a pty-driven test that actually launches `python3 -m gitsync`, waits for
  "Dashboard", sends `?` then `Esc` then `q`, and asserts exit code 0 — plus an
  80×24 run and the "terminal too small" run (8×30 renders the message without
  crashing — verified manually).

## Definition of done (round 2)
- An authenticated HTTP clone+push works from a fresh unzip with no
  `install.sh`, in Docker, and via `python3 -m gitsync` from source.
- Offline machine ⇒ dashboard shows "not verified", never "need attention".
- Local-only config shows no "unverified token" warning.
- Help overlay lists all 7 tabs in the real order.
- All four check commands pass on a clean machine; Playwright suite passes.
