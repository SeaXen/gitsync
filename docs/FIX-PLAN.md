# gitsync 2.0.0 — audit and fix plan (for an implementing agent)

Audited: `gitsync-main.zip`, package version **2.0.0**.
This file is the spec. Work top to bottom. Do not invent features outside the
"Features" section, do not rename commands or config keys, do not add
third-party dependencies (Python stdlib + vanilla JS/CSS only; this is a hard
project rule, see `AGENTS.md`).

---

## 1. Verdict

**Not 10/10.** The `2.0.0` changelog claims "10/10 across all sectors"; that is
not supported by what was found. The core is solid (safe-by-default push policy,
real offline smoke test, good web auth design). But there are real bugs, a
secret-handling gap, a broken phone layout, tests that depend on the machine,
and docs that disagree with the code.

| Sector | Score | Basis |
|---|---|---|
| Core sync correctness | 8/10 | Earlier review's P0s (Bitbucket, Forgejo auth, compare/fix, hub identity) appear fixed. One crash path remains (B1). |
| Safety of sync | 8/10 | `safe` default, `push_args()` is pure and tested, mesh conflict policy exists. |
| Secrets handling | 6/10 | Tokens stay on disk in mirror configs and on `git push` argv; one unredacted error path (S1–S3). |
| Web UI — desktop | 8/10 | Clean, consistent, accessible basics. Polish items in W3. |
| Web UI — phone | 4/10 | Page is 430px wide in a 375px viewport on **every** page; Sync now clipped; Sign out unreachable (W1). |
| TUI | 7/10 (provisional) | Judged by code reading only; see "Limits of this audit". |
| Tests | 6/10 | 152 unit tests, but 3 fail on a clean machine (T1–T2), no TUI test, Playwright not in CI (T3–T4). |
| Docs | 6/10 | Counts, Python version and a stale review file contradict the code (D1–D4). |
| Structure | 6/10 | `tui.py` 3011 lines, `__main__.py` 2021, `web.py` 2029. |
| Install / scheduling | **not verified** | Needs root + systemd; not run in this audit (I1). |

**Overall: roughly 6.5–7/10.**

### Limits of this audit (read this)
- Run here: compile, shell syntax, `python3 -m unittest tests.test_logic`,
  `python3 -m gitsync ci`, and the web UI in headless Chromium (all 9 pages,
  1366×850 and 375×740).
- **Not run:** installers/systemd, any live GitHub/GitLab/Forgejo/Bitbucket
  call (no network), Windows/macOS, and the **TUI was not rendered** (no tmux or
  terminal emulator available). TUI findings below come from reading
  `gitsync/tui.py`. Step 0 of the TUI work is to look at it for real.

---

## 2. Rules for the implementing agent

1. Python 3.8+ stdlib only. No pip/npm/CDN in runtime code.
2. Never test against the real config or hub. Always isolate:
   ```bash
   export PYTHONPATH=$PWD
   export GITSYNC_CONFIG_DIR=/tmp/x/config GITSYNC_DATA_DIR=/tmp/x/data
   export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
   ```
3. Never echo a token. Never put a token in a log line, record, or test output.
4. Logic tests go in `tests/test_logic.py` only (stdlib `unittest`, **no network**).
5. The TUI and its subprocesses run the *installed* copy: after editing source,
   `bash install.sh` before TUI testing (see `AGENTS.md` "install trap").
6. Keep the design rules in `DESIGN.md` (no glow shadows, no side-tab borders,
   no native `confirm()/alert()`, tabular numerals, `text-wrap: balance`).
7. Before every commit run all four:
   ```bash
   python3 -m py_compile gitsync/*.py
   bash -n install.sh quickinstall.sh bin/gitsync
   python3 -m unittest tests.test_logic
   python3 -m gitsync ci
   ```
8. Bump `gitsync/__init__.py` to `2.0.1`, add a `CHANGELOG.md` entry. In that
   entry do not claim "10/10".

### Already good — do not "clean this up"
`push_args()` policy and its tests; atomic config write + `0600`; web auth
(Bearer/HttpOnly SameSite=Strict cookie, CSRF header, Host allowlist,
PBKDF2, login rate limit); `Redactor` in `web.py`; `LocalPlatform` offline path;
the offline two-folder smoke test in `cmd_ci`; the `G.modalConfirm` dialog;
dark palette in `DESIGN.md`.

---

# 3. Fix list

Priority: **P0** = wrong behavior or leaks a secret, **P1** = broken UX/tests,
**P2** = polish. Each item: where, evidence, required change, how to prove it.

## P0

### B1 — token check crashes whenever the network call fails
- **Where:** `gitsync/platforms.py` `_http()` (~lines 122–124) and
  `GitHubPlatform.token_info()` (~line 265).
- **Evidence (reproduced):** `python3 -m unittest tests.test_logic` →
  `ERROR: test_token_info_reports_rejected … ValueError: not enough values to
  unpack (expected 3, got 2)`.
  `_http(..., want_headers=True)` returns `(status, body, hdrs)` on success and
  HTTP errors, but returns a **2-tuple** on the `except Exception` path
  (`return None, str(e)[:200]`) and on `"request failed after retries"`.
  `token_info()` unpacks three values, so DNS failure/offline/timeouts raise
  `ValueError`.
- **Fix:** make every return path honor `want_headers`
  (`return None, msg, {}` when `want_headers`). Grep every other
  `want_headers=True` caller and confirm none unpack differently. Treat
  `rc is None` in `token_info()` as `{"has_token": True, "error": "unreachable"}`
  (not "rejected").
- **Test:** unit-test `_http` with a monkeypatched `urllib.request.urlopen`
  raising `OSError`, for `want_headers` both False and True; assert tuple
  length 2 and 3. Test `token_info()` with `_http` patched to return
  `(None, "boom", {})`.

### S1 — unredacted git error on clone failure
- **Where:** `gitsync/sync.py` `sync_repo()` (~line 196):
  `return "clone-failed", err.strip()[:200], []` — every sibling path uses
  `_redact(...)`.
- **Why it matters:** git echoes the remote URL, and the URL carries
  `user:token@host`. This string goes to the log, records and web UI.
- **Fix:** wrap in `_redact()`. Audit every `err`/`stderr` that reaches a log,
  record, notification or HTTP response and make sure it passes through
  `_redact` / `Redactor`.
- **Test:** feed a fake stderr containing `https://u:ghp_SECRET@github.com/x.git`
  through the clone-failed path; assert `ghp_SECRET` is absent from the returned
  detail.

### S2 — tokens persist in each mirror's git config
- **Where:** `sync.py` `sync_repo()` (~line 186) runs
  `git remote set-url origin <src_url>`; `git clone --mirror <src_url>` also
  stores it. `_secure_hub_config()` only `chmod 0600`s the file.
- **Fix:** after clone/update, rewrite `origin` to the **token-less** URL; pass
  credentials per command instead (see S3). Existing mirrors must keep working:
  on the next sync, scrub the old stored URL. Do not delete or move mirrors.
- **Test:** after an offline sync, `git -C <hub> config --get remote.origin.url`
  contains no `@` credentials. (Use a fake platform whose `clone_url` embeds a
  dummy token.)

### S3 — tokens visible in process arguments
- **Where:** `sync.py` `push_args()` builds `["git","push", url, ...]` with the
  token-bearing URL; `git clone --mirror src_url` the same.
- **Why:** any local user can read argv via `ps`/`/proc`.
- **Fix:** keep the URL token-less in argv; supply credentials through the
  environment. `gitsync/glab_askpass.py` already implements a `GIT_ASKPASS`
  helper pattern — generalize it (token passed via a private env var, never
  argv). Keep `push_args()` pure and keep its existing tests passing (pass the
  token-less URL; add the env in `_push_target`).
- **Test:** unit-test that `push_args()` output and the `subprocess` argv
  captured in `_push_target` contain no `@` credentials, while an offline local
  push still succeeds.

## P1

### W1 — phone layout: horizontal overflow on every page
- **Where:** `gitsync/webui/style.css` `@media (max-width:860px)` (~line 179);
  topbar markup in `webui/app.js`.
- **Evidence (reproduced, Chromium 375×740, all 9 pages):**
  `documentElement.scrollWidth = 430` vs `clientWidth = 375`.
  Top bar contains status pill + "Dry run" + "Sync now" + sign-out icon and does
  not wrap: "Sync now" is clipped at the edge and the sign-out icon sits at
  x≈430 (off-screen). The sidebar footer (the other "Sign out") is
  `display:none` on mobile, so **a phone user cannot sign out**.
- **Fix:**
  - Under ~520px: make buttons icon-only (keep accessible name via
    `aria-label`/`title`) or let `.top` wrap (`flex-wrap:wrap; gap`).
  - Add Sign out to the mobile nav strip or the top bar's overflow.
  - Do **not** "fix" with `overflow-x:hidden` on `html/body` — that hides the bug.
  - The horizontal nav strip may keep its own `overflow-x:auto`.
- **Test (add to `tests/test_webapp_playwright.py`):** at 375×667, for each of
  the 9 pages assert `scrollWidth <= clientWidth`, and that Sync now and Sign
  out are within the viewport and clickable.

### W2 — dashboard says things that are not true or not clear
Observed in the UI with a GitHub platform that could not be reached:
1. Disk & Health card shows **"tokens: all tokens healthy"** while that
   platform is red ("API failed…") and its row says "unknown". Unverified must
   read "unverified"/"unknown", never "healthy". (Look in `tokencheck.py` and
   `webui/pages/dashboard.js`.)
2. Platform error is cut mid-word: `API failed: <urlopen error [Errno -2] Na`.
   Show a plain-language message ("Can't reach github.com — check network or
   DNS") with the raw detail behind a "details" toggle or `title`, not truncated.
3. Subtitle reads "Timer: 6h, next run — · scheduler: not scheduled (manual
   runs only)" while the top pill says "idle · 6h". Config says 6h, but no
   timer is installed. Say clearly: "Schedule set to 6h but no timer is
   installed — [Install timer]".
4. Every visit to the login screen writes `web: 401 GET /api/session from
   127.0.0.1` into `gitsync.log` (visible in the dashboard log card). An
   expected unauthenticated session probe is not a security event; don't log it.
   Keep logging real failed logins.

### T1 — two tests fail on a clean machine (missing git identity)
- `TestRestore.test_restore_dry_run_touches_nothing` and
  `test_restore_pushes_every_mirror_and_skips_empty` fail with
  `0 != 1` / `0 != 2` unless a global git identity exists (reproduced: they pass
  once `GIT_AUTHOR_*`/`GIT_COMMITTER_*` are set).
- **Fix:** in the `TestRestore._hub` helper pass identity via `env=` (or
  `-c user.name=… -c user.email=…`) on every `git commit`, and **assert the seed
  commit's return code** so a setup failure is loud, not a confusing count
  mismatch. Check other tests that run `git commit`.

### T2 — a "no network" test hits the network
- `test_token_info_reports_rejected` mocks `p._api` but `token_info()` calls
  `_http` directly, so the test performs a real request to github.com
  (passes online via a real 401, errors offline). Violates "no network".
- **Fix:** patch `platforms._http` (see B1). Add a guard in the test base
  class that fails any test that opens a real socket (patch
  `socket.socket.connect`).

### T3 — Playwright suite has a Windows-only hardcoded path
- `tests/test_webapp_playwright.py` line 32:
  `ARTIFACT_DIR = r"C:\Users\Sagar-EB\.gemini\…\screenshots"`. On Linux/macOS this
  creates a junk directory named with backslashes in the CWD, and it embeds a
  local username/path in the repo.
- **Fix:** write screenshots to `tempfile.mkdtemp()` (or an env var
  `GITSYNC_SHOTS_DIR`, default off), and `unittest.skip` the whole class when
  `playwright` or Chromium is unavailable.

### T4 — Playwright tests are not run by CI
- All three CI files (`.github/…`, `.forgejo/…`, `.gitlab-ci.yml`) run only
  syntax + smoke + `unittest tests.test_logic`. The web UI has zero automated
  regression coverage in CI (which is how W1 shipped). The changelog's "157
  tests passing" includes them.
- **Fix:** add an *optional* job (allowed to be skipped if Playwright isn't
  installed — CI image with Playwright, or a separate manual job). Test-only
  deps are acceptable; runtime stays stdlib. Document it in `CONTRIBUTING.md`.

### T5 — no TUI test at all
- **Fix:** add a smoke test using stdlib `pty` + `os.fork`/`subprocess` with
  `TERM=xterm-256color`, window size set via `termios`/`fcntl` TIOCSWINSZ:
  start `python3 -m gitsync` in an isolated config, read output until a tab name
  ("Dashboard") appears, send `q`, assert exit code 0 within 5s. Also run at
  80×24 and at a too-small size and assert the "terminal too small" message path
  doesn't crash. Skip if `pty` unavailable (Windows).

### I1 — installers/scheduler not verified
- Re-test in a container as a **non-root** user: `quickinstall.sh`,
  `install.sh`, user-level systemd units, `gitsync` after install, and that the
  timer reads *that user's* config. Add a CI shell job (`docker run`) if
  feasible. If anything fails, it is P0.

## P2 — docs and structure

- **D1 README numbers drift:** README says "142 unit tests" (line ~221) and
  "136 assertions" (line ~1279); real count is **152** tests. Generate the
  number (e.g. `gitsync ci` prints it) or remove it. `AGENTS.md` says the count
  also lives in `__main__.py` — make it a single source.
- **D2 PRODUCT.md contradicts the project:** says "Python 3.10+" and mentions
  `sqlite3`; everything else says 3.8+, and no `sqlite3` import exists. Make it
  match (and keep the 3.8 syntax check meaningful: run `py_compile` on 3.8 in CI
  if an image is available).
- **D3 Stale `GITSYNC-REVIEW.md`:** describes version 1.6.2 and lists many bugs
  already fixed; an agent reading it will "fix" things that are fine. Delete it
  or replace it with a short pointer to this plan.
- **D4 CHANGELOG 2.0.0** claims "10/10 quality standard across all sectors".
  Reword to factual changes.
- **D5 Structure:** after P0/P1 are green, split `tui.py` (3011 lines) into
  a `gitsync/tui/` package by screen, with **no behavior change** (the
  installer copies the whole `gitsync/` dir; keep `python3 -m gitsync` entry
  working; re-run `bash install.sh`). Same idea for `web.py`/`__main__.py`
  only if time permits. Do this last — it is pure risk without tests.

---

# 4. Web UI — design polish (P2, after W1–W2)

Observed on desktop (1366×850) unless noted.

1. **Routes page:** route cards touch with no gap (stacked with a hairline).
   Add vertical spacing. Show the route's **mode (`safe` / `full`)** as a badge
   — it is the most safety-relevant fact and is missing; render `full` in the
   warning color.
2. **Action hierarchy:** each route has six equal buttons (Run, Dry Run, Pause,
   Targets, Edit, Delete). Keep **Run** and **Dry run** visible, move the rest
   into a "⋯" menu, keep Delete visually separate/danger.
3. **Label consistency:** "Dry run" (top bar) vs "Dry Run" (route) vs "Set
   Password". Pick sentence case everywhere.
4. **Settings form:** checkbox rows inherit the uppercase field-label style and
   use the native grey checkbox; style them like the selects (custom
   `accent-color`/box), sentence-case labels. "hand-edit territory" is jargon —
   write "Advanced: allows overwriting protected branches. Off by default." Give
   **Relax protection** warning styling and a `G.modalConfirm` when turned on.
5. **Save UX:** Save button is top-right, not sticky, no "unsaved changes"
   indicator, no success state besides a toast. Add dirty-state and a sticky
   action bar.
6. **Dashboard empty space:** below the log card the page is empty. Use the
   space for the data users actually come for: per-route status, last result
   per repo, failures needing attention.
7. **Disk "96% full"** is shown in neutral text; color it warn/bad over
   thresholds (e.g. 85/95%) and add `role`/text so it isn't color-only.
8. **Duplicate sign-out** (sidebar footer + top-right icon): keep one on
   desktop; ensure one exists on phone (W1).
9. **Loading / error states:** verify every page has skeleton/spinner, empty
   state with a next action ("No routes yet — Add route"), and an inline retry on
   API failure.
10. **Accessibility:** run a pass for `aria-live` on sync progress, visible focus
    rings on every control, contrast of `--muted` (#8b949e) on `--panel2`,
    tap targets ≥ 40px (the tab strip is fine; check small icon buttons).
11. **Mobile (375px):** after W1, review each page; tables must scroll inside
    their own container; modals must fit with the on-screen keyboard.

# 5. TUI — polish and fixes (code-read; verify visually first)

**Step 0:** `bash install.sh`, then screenshot every tab and modal at 80×24,
120×30 and 200×50 (tmux: `tmux new -d -s t -x 120 -y 30`, `send-keys`,
`capture-pane -p -e`). Fix what you *see* first; the list below is from code.

1. **Crash is hidden** — `tui.py` `main()` (~line 2687): `except Exception as e:
   print("TUI error: %s" % e)` then falls back to a text snapshot. Write the full
   traceback to `gitsync.log` and print its path.
2. **Modals ignore resize** — `_confirm_run` / `_confirm_bg` (~line 2923) read
   `getmaxyx()` once *before* the `while` loop. Re-read inside the loop. These two
   functions are near-duplicates: merge them. Audit every other modal loop for
   the same stale `h, w`.
3. **Search is ASCII-only** — main loop (~line 2755): `32 <= ch < 127`. Non-ASCII
   repo names can't be searched. Use `get_wch()` or decode multibyte input.
4. **Mouse support is thin** — only a click on the tab bar is handled (~line
   2765), yet `ALL_MOUSE_EVENTS` is enabled (this also steals text selection from
   the terminal). Either add row click/scroll-wheel support or enable only what
   is used, and document Shift+drag for selection.
5. **No `NO_COLOR` / low-color fallback** — nothing in `tui.py` reads `NO_COLOR`;
   `apply_theme()` (~line 334) assumes color pairs. Honor `NO_COLOR`, and make
   state readable without color (use `OK`/`FAIL` text or symbols, not color
   alone).
6. **No global help** — the only `?` handler found is in the accounts screen
   (~line 626). Add a `?` overlay on every screen listing that screen's keys, and
   make the footer hint line consistent across screens.
7. **Empty states:** a fresh install with no platforms should show "Press `a` to
   add a platform / run the wizard", not blank panels.
8. **Run progress:** while syncing, show per-repo results (ok / failed / skipped)
   and a way to cancel safely, not only a spinner/counter.
9. **Danger confirmations:** for `full` mode (prunes target branches) require
   typing the route name (or `yes`), not a single `Y`.
10. **Performance guard:** the dashboard redraws every second; keep network and
    disk work out of draw paths (`AGENTS.md` already warns). Add a regression
    test that `draw()` makes no subprocess/network call.

---

# 6. Features to add or improve (only after P0/P1 are green)

Keep each one small and stdlib-only. Both UIs should offer the same capability
where it makes sense.

**Both TUI and Web**
- **Retry failed only** — re-run just the repos that failed in the last run.
- **Per-run report** — list every repo with result and error (redacted); link
  from History.
- **Dry-run diff view** — show what *would* be created/updated/**deleted** per
  repo, highlight deletions in the warning color (data exists:
  `WOULD DELETE`).
- **Test connection** for each platform with plain-language errors and the exact
  token scopes missing.
- **Guided first run** — empty install → wizard (platform → route → dry-run
  → schedule) in the web UI too (CLI wizard exists in `wizard.py`).
- **Health summary** — one line: "All 12 repos in sync / 2 failing / token expires
  in 9 days", same text in both UIs.

**Web only**
- Repo list: search, sort, filter by status; click a repo for last sync, refs
  count, last error.
- Log page: level filter, text search, follow/pause, download (redacted).
- History: small trend of success/failure per day (inline SVG, no libs).
- Route editor: schedule, mode, mesh conflict policy with inline explanations of
  each option.
- Config export/import with tokens stripped.
- Notice when served over plain HTTP on a non-loopback bind (recommend a reverse
  proxy/TLS).

**TUI only**
- `/` search on every list (already on some) with highlighted matches.
- Command palette (`:`) for rarely used actions.
- Resizable/responsive layout: collapse columns on narrow terminals instead of
  truncating.

*Not recommended:* a plugin system, a database, a JS framework, auto-updating
branch protection, anything that adds a runtime dependency.

---

# 7. Suggested order of work

1. B1, S1 (small, isolated, tested) → commit.
2. T1, T2 (make the suite green and hermetic) → commit.
3. S2, S3 (secret handling; careful, keep old mirrors working) → commit.
4. W1, W2 + the new Playwright assertions (T3, T4) → commit.
5. I1 installer verification (fix anything found as P0).
6. TUI Step 0 screenshots, then section 5 items 1–6, T5.
7. D1–D4 docs, then section 4 polish.
8. Section 6 features, one per commit.
9. D5 split `tui.py` last.

# 8. Definition of done

- The four commands in Rule 7 pass on a **clean machine with no global git
  identity and no network**.
- No token appears in: any log/record/notification, any hub `config`, or any
  captured `git` argv (tests prove it).
- All 9 web pages have no horizontal overflow at 375px; Sync now and Sign out
  are reachable on a phone.
- Dashboard never shows "healthy" for something it could not check.
- README/PRODUCT/CHANGELOG numbers and requirements match the code;
  `GITSYNC-REVIEW.md` no longer misleads.
- `CHANGELOG.md` has a `2.0.1` entry; `__version__` matches; installed copy
  matches source (`diff -r --exclude=__pycache__ gitsync /opt/gitsync/gitsync`).
