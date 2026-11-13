# gitsync 2.1.5 — Round 4 review and remaining fixes

Audited: `gitsync-main_3.zip`, version **2.1.5**. Self-contained: every item has the file, how it
was reproduced, the exact change and a test. Read section 1 first.

## 0. Verdict

**~9/10. Round 3 was implemented well; two real logic/UX bugs remain, plus accessibility and
cleanup.** Do not claim "10/10" anywhere.

| Sector | Score | Note |
|---|---|---|
| One-way sync (safe / full) | 10 | Verified: safe keeps extra branches and restores a deleted tag; full prunes |
| Mesh sync | **6** | Default setting never propagates a normal push (N1) |
| Credentials / sessions | 9.5 | Cookie is now an opaque id; revoke-all works in a real browser |
| Web security | 9.5 | CSP, Referrer-Policy, Permissions-Policy present; zero CSP violations on 9 pages |
| First-run experience | **6** | Wizard never appears on a fresh install (N2) |
| Web UI design | 9 | Clean light/dark, no horizontal scroll at 375px, no console errors |
| Accessibility | 8 | axe finds 5 rule types still open (N3) |
| TUI | 9 | 7 tabs, help, resize to 80x24 and 40x12, quit all clean |
| Tests / CI | 9.5 | 280 tests pass with `-W error::ResourceWarning`; `gitsync ci` 6/6; matrix 3.8–3.13 |
| Maintainability | 7 | `tui.py` 3820, `web.py` 2916, `__main__.py` 2819 lines, still unsplit |

## 1. Rules for the agent

- Python 3.8+ stdlib only. Never touch the real config/hub; isolate with
  `export PYTHONPATH=$PWD GITSYNC_CONFIG_DIR=/tmp/x/config GITSYNC_DATA_DIR=/tmp/x/data`.
- Never print a token. Logic tests only in `tests/test_logic.py`, no network.
- Before each commit all must pass:
  ```bash
  python3 -m py_compile gitsync/*.py
  bash -n install.sh quickinstall.sh bin/gitsync
  python3 -W error::ResourceWarning -m unittest tests.test_logic
  python3 -m gitsync ci          # also: python3 -m gitsync ci --update-sums after editing shipped files
  ```
- One item per commit. Bump to `2.1.6`, add a CHANGELOG entry. Read `AGENTS.md` first.

## 2. Verified fixed from the previous plan (do not redo)

Session cookie is a random id and revoke-all returns to the sign-in form; `gitsync update` refuses
to run anything it cannot verify; `a b` / `a_b` / `ünï` / `öñï` all map to distinct folders and
ASCII names keep their paths; empty repos sync; CSP + security headers; `watch` reloads config;
webhook secret written 0600 atomically; Dockerfile `COPY docs ./docs`; compose `scheduler`
service; docs split into `docs/`; light theme; `rmtree`; warning-clean tests; CI matrix;
`verify`, `config export`, `/metrics` (401 without auth), five notification channels, hooks,
per-route retry, free-disk floor. All present and the ones I could run behaved correctly.

## 3. P1 — real bugs

### N1. Mesh with the default `mesh_conflict: skip` never propagates an ordinary push
- **Files:** `gitsync/sync.py` `_choose()` (~line 640); default in `gitsync/config.py` line ~71
  (`"mesh_conflict": "skip"`); fallback in `sync.py` ~line 1272 (`"newest"`).
- **Reproduced (two local peers, mesh route, default settings):** both hold commit `one`; add commit
  `two` on peer m2 (a strict descendant of `one`); sync. Result:
  `PART r (m1,m2 (0 refs merged), 1 conflict(s) SKIPPED: main)` and m1 still has `one`, **every run,
  forever**. With `mesh_conflict=newest` the same scenario gives `OK … (1 refs merged)` and both peers
  have `two`. So the headline mesh feature ("change it on GitLab, see it on GitHub") does not work
  out of the box.
- **Cause:** in `skip` mode `_choose` treats *any* difference in SHA as a conflict
  (`len({s for _d, s in options}) > 1`), including a fast-forward where one SHA contains the others.
- **Fix:**
  1. In `_choose`, for `skip`: when the SHAs differ, ask git whether one candidate is a descendant of
     all the others (`git merge-base --is-ancestor OLD NEW` inside the hub; every candidate was already
     fetched into `refs/gitsync-mesh/<peer>/…`, so the objects exist). If one candidate is ahead of all
     others, choose it. Only *diverged* branches (neither is an ancestor) are skipped and reported.
     Pass `hub` into `_choose` (signature change; update both callers and `mesh_plan_repo`).
  2. Tags: a tag that points at different commits is always a conflict (tags should not move); keep skip.
  3. Make the default consistent: one constant used by `config.py`, the CLI help, the dashboard text and
     the `sync.py` fallback.
  4. Dashboard/TUI "Mesh conflicts: skip" needs a one-line explanation ("only branches that truly
     diverged are left alone; normal pushes still sync").
  5. Report wording: say "diverged" instead of "conflict" when that is what happened, and include both
     short SHAs.
- **Tests (offline, local bare repos):** (a) fast-forward on one peer → both peers equal, status `ok`,
  in skip and newest; (b) true divergence → `partial`, neither peer overwritten, message names the
  branch; (c) a moved tag → skipped; (d) three peers where one is ahead of two → ahead one wins;
  (e) default config (no setting) behaves as (a).

### N2. The first-run wizard never shows on a fresh install, and a fresh install shows a red alarm
- **Files:** `gitsync/config.py` `DEFAULT_CONFIG["routes"]` (~line 63) ships a placeholder route
  `main: github → gitlab, local`; `gitsync/webui/pages/dashboard.js` `wizardCheck()` (~line 54) only
  shows the wizard when `routes` is empty.
- **Reproduced in a real browser:** brand-new config dir → sign in → dashboard shows no wizard but a
  red **"Sync is overdue. Nothing has synced yet."** banner, a **Sync now** button, and platforms
  "gh CLI not authenticated". With `routes: []` the wizard appears and works ("First run — four steps…").
- **Fix:**
  1. Ship `"routes": []` in `DEFAULT_CONFIG` (check the wizard in `wizard.py`, `cmd_setup` and tests that
     assume a `main` route; update them).
  2. Show the wizard when there is no route that is *runnable* (enabled, source and ≥1 target enabled and
     connected), not merely when the list is empty. Existing users with a working route never see it.
  3. Do not show "Sync is overdue" or the top-bar "idle · sync overdue" until at least one runnable route
     exists and a run has been expected (use `last_run` or route creation time). Hide the "Sync now" /
     "Dry run" buttons, or disable them with a tooltip, while there is nothing to sync.
  4. Merge the two overlapping notices ("Schedule set to hourly but no timer is installed — Install timer"
     and the red overdue banner) into one calm message with one action.
- **Tests:** unit — `DEFAULT_CONFIG["routes"] == []` and the stale logic returns `False` with no runnable
  route. Browser (Playwright) — empty config shows `#wizard-card` and no overdue banner.

## 4. P2 — accessibility (axe-core, WCAG 2 A/AA, real pages)

Run with axe 4.x injected through CDP `Page.setBypassCSP` (the CSP correctly blocks inline injection).
These repeat on every page unless noted.

1. **`aria-progressbar-name` — `#top-progress`** (`app.js` ~line 667). Add `aria-label="Sync progress"`
   (or `aria-hidden="true"` while idle, and a label while running).
2. **`color-contrast` — `#side-version`** in the light theme (desktop, every page). Raise the colour to
   ≥ 4.5:1 against the sidebar background.
3. **`color-contrast` — `.ok.chip`, `.warn.chip`** (phone, dashboard and repos). Fix the light-theme chip
   text/background pairs; verify every chip variant (`ok`, `warn`, `bad`, `info`, muted).
4. **`link-in-text-block` — settings link in the dashboard notice** (`.u-mt12.u-fs125 > a`). Links inside a
   sentence need a non-colour cue: underline them (the "Install timer" link already is).
5. **`scrollable-region-focusable` — `#logtail` (dashboard) and `#l-lines` (log, phone).** Add
   `tabindex="0"` plus `aria-label` so keyboard users can scroll the log.
6. **Add the axe pass to CI** (still listed open in the changelog): in `tests/test_webapp_playwright.py`
   run axe on all 9 pages in light and dark, fail on `serious`/`critical`. Vendor `axe.min.js` under
   `tests/vendor/` (no pip/npm in the product itself).

## 5. P3 — polish and cleanup

- **Stale hub numbers right after a sync.** The dashboard showed `hub size 0B · mirrors 0` for ~5 s after a
  successful 3-repo sync while `/api/health` and `/metrics` later reported 3 mirrors. Invalidate the health
  cache when a sync/fix/restore job finishes (same hook that already drops the compare cache).
- **`gitsync update` failure message.** When it cannot verify the release it prints the manual command
  `curl -fsSL https://github.com/SeaXen/gitsync/releases/latest -o /dev/null`, which downloads nothing.
  Print the real steps: `git pull --ff-only && bash install.sh`, or the tag tarball URL plus
  `sha256sum -c`. (The refusal itself is correct.)
- **Split the big modules** (behaviour-preserving, last): `tui.py` → `gitsync/tui/`, `web.py` → `gitsync/web/`
  (server, auth/sessions, routes), `__main__.py` → `gitsync/cli/`. Keep `python3 -m gitsync` and all
  test imports working; run the full suite after each move.
- **Real-platform test lane.** Add an opt-in CI job (secrets-gated) that runs one dry-run and one real sync
  against a throw-away GitHub/GitLab/Forgejo account, so the API code paths stop depending on mocks.

## 6. Feature ideas still worth adding

1. Mesh "resolve diverged branch" action in the web UI (choose peer A, choose peer B, or open a compare).
2. A "Health" page that lists every failing check with a one-click fix (token expired, timer missing,
   disk low, hub orphan).
3. Per-route "last result" chips on the Routes page, and a diff view in Repos showing ahead/behind counts.
4. Signed releases (minisign/cosign) in addition to `SHA256SUMS`; Homebrew/AUR/Debian packages.
5. Optional read-only dashboard token for sharing status with someone without giving control.

## 7. What was tested in this audit

- Unit tests (280, `-W error::ResourceWarning`), `gitsync ci` (6/6), compile, shell syntax, py3.8-grammar parse.
- Real CLI scenarios on local folders: name collisions and Unicode, empty repo, idempotent re-run, `verify`
  (drift detected, exit 1), safe vs full, `config export`, `update` refusal, mesh fast-forward and divergence.
- Real browser (headless Chromium): sign-in (bad and good token), 9 pages × desktop and 375px phone with zero
  console/CSP errors and no horizontal scroll, running a sync from the UI, theme toggle and persistence,
  `?` help, Repos matrix, History, Settings (sessions, network, appearance, 5 notification channels),
  "sign out everywhere", empty-config wizard, axe-core.
- TUI in tmux: all 7 tabs, help, resize to 80×24 and 40×12, quit.
- Security probes: CSP/headers, opaque cookie, unauthenticated `/metrics` and `/api/logout` → 401.

**Not tested:** real GitHub/GitLab/Forgejo/Bitbucket accounts, actual Discord/Telegram/ntfy/SMTP delivery,
pre/post hooks, Git LFS handling, Docker build, Python 3.8 *runtime* (grammar only), Windows/macOS, the
project's own Playwright suite (one test skipped in this sandbox; I used my own browser script instead).

## 8. Definition of done

- [ ] N1: mesh fast-forwards propagate under the default setting; real divergence is still protected and reported.
- [ ] N2: fresh install shows the wizard and no overdue alarm; existing installs unaffected.
- [ ] Section 4: axe reports zero serious/critical on all pages, light and dark, and CI enforces it.
- [ ] Section 5 items done or explicitly deferred in the CHANGELOG.
- [ ] All four commands in section 1 pass; CHANGELOG 2.1.6 does not say "10/10".
