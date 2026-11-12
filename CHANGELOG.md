# Changelog

All notable changes to gitsync are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.3] - 2026-11-12

### The TUI quit instead of hanging on `Q`
- Platform probing ran in a `ThreadPoolExecutor`, and that pool registers an `atexit` hook that **joins its workers**. A probe caught in `urllib` — GitHub answers 429 and `_http` honours `Retry-After` for up to three attempts — therefore kept the process alive after `Q`, so the TUI appeared to ignore the quit (observed: still running 8 s after the keypress). The probes now run on named daemon threads (`probe-<platform>`) that feed a `queue.Queue`, and exit no longer waits for them: quit is ~1.5 s again, and `TestTUISmoke` passes.
- A regression test asserts the refresh uses daemon `probe-*` threads and no `ThreadPoolExecutor*` thread exists while it runs.

## [2.1.2] - 2026-11-12

### A skipped run no longer looks like a failed one
- A scheduled tick where **no route was due** records `skipped: 1, repos: 0`, and the dashboard summarised that as “**0 OK**, 0 failed of 0, in ?s” — a green run that appeared to have done nothing. `_public_record` dropped the field, so the UI could not tell. It is published now, the LAST RUN card says **skipped — the timer ran, no route was due yet**, and the history table shows `skipped` with `Status: SKIPPED (no route was due)` in the report (instead of `SUCCESS` with 0 repos).
- A real `0.0s` duration rendered as `?` in both views; only a missing value is `?` now.

## [2.1.1] - 2026-11-12

### Tests no longer write runs into the real data dir
- `LOG_FILE` and `RECORDS_FILE` were module constants computed once at import, so any caller that redirected `C.DATA_DIR` — the tests, or an embedded user — still appended to the **real** `gitsync.log` and `records.json`. Running the unit tests on the live box therefore recorded fake runs (route `r`, `cannot list`) and the dashboard's LAST RUN card showed a failure that never happened. Both paths are now resolved at call time via `config.log_file()` / `config.records_file()` (with `_route_state_path()` already following `DATA_DIR`), and `tests/test_logic.py` redirects `DATA_DIR`/`MIRROR_DIR` for the whole module in `setUpModule`, so no test can touch the operator's log, records or hub mirrors even if it forgets to.
- A regression test asserts the paths follow `DATA_DIR`; the previous 2.1.0 release notes stand.

## [2.1.0] - 2026-11-12

### Web UI audit fixes (36 page × viewport combinations, real browser)
- **`compare --json` printed an array with 2+ routes**, so the Repos page parsed the last line as one matrix, found no `repos` key and showed “compare produced no data”. It now prints **one JSON object per route, one per line (NDJSON)** — the shape no longer depends on how many routes exist. The page collects the matrices from the stream and the server caches them **per route**, so a single-route compare no longer discards the other routes and the picker can switch between them without a re-run.
- **Repos page gained a Route picker** when the config has more than one route (one-way + mesh); switching re-renders that route's matrix.
- **A browser mistyping a URL got `{"error": "not found"}`.** Static 404s now return a styled page (status 404, “Back to the dashboard”) when the client asks for `text/html`; API clients still get the JSON error. A `%` in the stylesheet had made the first version answer 500 — the template now substitutes the path with `str.replace`.
- **`errorBox("message")` rendered “request failed”** because the helper only understood error objects; it now passes strings through.
- **Touch targets**: `.min` (card minimise), `.linkbtn` (dashboard “repos ▾”) and `.check` rows are now at least 32 px tall/wide on phones, so every button, link button and checkbox row clears the 32 px tap minimum at 390 px width.
- **Dashboard said “not mirrored” for every repo on a local-folder platform** — a local path has no hub mirror by design, so it now says “local folder”. `/api/repos` returns the platform `type` for it.
- **`HEAD` requests returned 501** (the server only implemented GET/POST/PUT/DELETE), which reads as “broken” to uptime checks and proxies; `HEAD` now answers with GET's headers and no body. Static responses also sent `Cache-Control` twice (`no-store` from `_send`, `no-cache` from the caller) — one header now, the caller's value wins.

### Opt-in repository deletion (option C)
- **Per-target opt-in**: a route gains `"prune_repos": ["gitlab"]` — set on the Routes page (“Delete extra repos”, one checkbox per target) or in `config.json`. Only names that are actually targets of that route are accepted; a string is accepted as well as a list.
- **Four locks, all of which must be open**: the route names the target, the caller asks for it (`gitsync sync --prune-repos`, `gitsync prune --yes`, or the WebUI action), the route is in **full** mode, and the run is **not** scheduled — repo deletion is never something the timer decides.
- **`gitsync prune`** (new command): dry-run by default, lists `target/repo` per extra repository, deletes only with `--yes`. `sync --prune-repos` does the same inside a normal sync and prints `repo deletion: N deleted, N failed`.
- **WebUI**: the Repos matrix gains a **“Delete them…”** button when the route allows it and extras exist; it shows the exact list, then a second modal that requires **typing the route name** (a wrong entry deletes nothing). Deleting streams live output into its own card and re-runs the comparison afterwards. With no opt-in the button is replaced by a hint pointing at the Routes page.
- **Platforms** gained `delete_repo()`: GitHub/GitLab/Forgejo/Gitea use their DELETE API (GitLab by project path), local folders `rmtree` the bare repo. Deleting something that is already gone is not an error.
- **Refuses to delete when the source lists nothing** — a failed listing or an empty account must never be read as “delete everything”.
- Tests: not-opted-in, unknown target filtered, dry-run plans and deletes nothing, `--yes` deletes only the extra repo, safe mode refuses, empty source refuses, local delete is idempotent.

### WebUI gaps closed
- **Stop button** in the top bar while a sync runs (`POST /api/sync/cancel`) — the browser had no equivalent of the TUI's `X`.
- **Run while paused**: pausing used to be a one-way door from the browser; Sync/Dry-run now ask “Sync is paused — run anyway?” and pass `--force`.
- **`fix --dry-run`** is reachable from the Tools page (checkbox), so drift can be previewed without pushing.
- The shell polls `/api/status` every 15s (only while the tab is visible) so a run started by the timer or another browser still shows Stop.

### Docs
- README: the container's TUI command is `python3 -m gitsync tui` (`bin/` is not on `PATH`), and Docker is documented as having **no scheduler**, with a sidecar example.

### The matrix loads by default
- **Repos page auto-runs the comparison** on open: `GET /api/compare` returns the last result (rendered immediately, ~2s on a revisit) and a stale or missing one refreshes in the background (~16s for 40 repos × 4 sites). "Compare all sites" / "Re-run" still forces it.
- The server caches the last compare matrix for 5 minutes and **drops it when any sync/fix/restore/mirror job finishes**, because the answer just changed. A failed compare is never cached.

### Compare must not report a failed probe as a missing repo
- **`_head_probe()` returns `(sha, err)`** instead of a bare sha. A remote `ls-remote` that fails — rate limit, timeout, DNS — returns nothing, and the first version of the matrix rendered that as `✗ missing`, which reported two healthy repos as absent. A failed probe is now `⚠ not checked` with the reason in the tooltip, counted separately as "N could not be checked", and excluded from the problem count.
- Each probe retries once with a short backoff before it is declared unknown.
- Tests: same/different/missing/extra with real bare repos, plus a fake unreachable target proving `problems == 0, unknown == 1` rather than a false missing.

### Repo × site matrix + dashboard expander fix- **Repos page: "Compare all sites"** builds a repo × platform table from `gitsync compare --json`: each row is a repo with its source commit and, per target, `✓` same commit, `⇄` different commit, or `✗` not on that site. Chips summarise `N repos · N in sync · N off` plus `+N extra` per target, and an "extra" list names the repos a target has that the source does not. Runs as a job (progress streams, non-mutating), re-runnable and filterable.
- **`compare --json`** prints one JSON object (`repos`, `extra`, `problems`) as its final line, with progress lines before it; HEADs resolve in a thread pool (40 repos × 4 sites ≈ 15s).
- **Dashboard platform expander could not be collapsed** — `.repo-list{display:flex}` overrode the `hidden` attribute, so the list stayed open. Fixed with `.repo-list[hidden]{display:none}`.
- **Answer to "why doesn't full mode match github?"**: `full` prunes *refs* inside a repo; it never deletes a whole repository from a target (gitsync creates what is missing and pushes, but deleting a repo is not something it will do). The matrix's `extra` list is exactly that gap — e.g. 40 source repos, 18 extra on gitlab, 17 on forgejo, 13 on backup.

### Web UI polish (post-2.0.3)
- **Favicon**: every page ships an inline SVG icon, so the browser stops requesting a missing `/favicon.ico` and logging a 404 on every load.
- **Version on the dashboard**: `/api/settings` now reports `version`, and the Current Settings card shows it instead of "—".
- **Card labels align**: the label row is a flex line, so "Change →" / "Manage routes →" links sit right before the minimise button and wrap tidily on phones.
- **Quick reference rows**: command and description are separate flex items, so a long description drops to its own line instead of breaking mid-command on narrow screens.

## [2.0.3] - 2026-11-11

Correctness fixes across the sync engine, the web API and the TUI: dry runs are reported as dry runs, the destructive-mode default is consistent, cron installs show a next run, and several TUI panels that read the whole hub per frame no longer do.

### Sync engine
- **One mode default**: added `config.route_mode()`; the engine used to fall back to `full` (force-push + prune) while the config template and `restore` used `safe`, so a config without `settings["mode"]` was treated as a mirror. `run_sync`, `restore`, `sync`/`fix`/`compare` CLI paths and the TUI now all resolve through it.
- **Dry runs count what would change**: `run_sync(dry_run=True)` now reports `would_change` (and the history record keeps `dry_run`), instead of an `ok` count that read like a completed sync. `notify_run` no longer sends "sync complete" for a preview.
- **`gitsync fix --dry-run`**: the flag existed in the docs but not in argparse, so `fix --dry-run` exited with an error. It now reports drift without pushing.

### Scheduler
- **Next run under cron**: added `cron_next()` for the minute/hour/list/step expressions gitsync installs; `next_run()` falls back to it when no systemd timer exists. Cron installs no longer show "next run —" in the TUI, the web dashboard or `gitsync schedule status`.

### Web API
- **Selection-file write is confined**: `platform` is validated as a platform name (it becomes part of the file name), the route that will own the file is found *before* anything is written, and the target is realpath-checked against the config dir.
- **Probe cache is invalidated**: adding, editing, deleting or toggling a platform (and settings/scheduler writes) now drop the cached `/api/status` and health probes instead of serving the previous answer for 15s.
- **`/api/settings` returns `scheduler` and `next_run`**; scheduler "off" persists so the dashboard stops offering to install the old schedule.
- **`/api/repos` mirrors** are detected with `sync.mirror_path` (the hub moved to `mirrors/<type>/…`) and each repo now carries its `id`.
- **`/api/sync` rejects an unusable tool argv with 400** instead of a 500, and route `workers` must be a number.

### TUI
- **Per-frame disk work is cached**: the Routes tab re-read config, route state and records every second (the dashboard redraws every second for the clock) — now a 3s TTL cache invalidated on any config change, with a fresh 5s read for the effective mode.
- **Dry runs read as dry runs** in the dashboard log title, the History tab and the per-route "last run" digest ("no sync yet / last was a dry run — N would change").
- **Tab bar is windowed around the active tab** with `‹ ›` hints, and a click maps to the bar that was actually drawn instead of the centred full strip.
- **The footer keeps `[?]Help [Q]uit`**: it was centred then clipped at both ends, which hid exactly those two keys at 120 columns.
- **The Log tab has a panel border and an empty state** — it used to draw straight onto the screen and looked blank with no log.
- **Mouse wheel scrolls** the active list (platforms, stars, log tail) instead of adjusting a scroll offset nothing read; Esc snaps the log back to "follow".
- **Full log viewer**: `/` opens the filter the footer has always advertised, `End` jumps to the last *page*, and a filter that shrinks the list can no longer leave the view blank.
- **Device login restores the input timeout**: `nodelay(False)` used to make the main loop block forever, freezing the clock after a cancelled GitHub browser login.
- **Stars**: `R` re-reads on the Stars tab, a failed read is remembered (with a 60s backoff) instead of retrying on every frame, and the copy streams its lines into the log panel.
- **On the Stars tab `S` means Source**, matching the footer, instead of starting a global sync behind the modal.
- **A terminal that disappears is not a crash**: curses teardown failures (`nocbreak`/`endwin`) are no longer written into the log as a TUI crash traceback.

## [2.0.2] - 2026-11-11

Credential helper reliability, network fault tolerance in token telemetry, TUI window resize resilience, and UI accessibility polish.

### Credential Helper & Container Hermeticity (R1)
- **Executable Mode in Git Index**: Explicitly marked `gitsync/askpass.py` and `gitsync/glab_askpass.py` executable (`100755`) in the repository index to prevent permission denied errors on source clones and unzips outside `install.sh`.
- **Runtime Helper Fallback**: `sync.askpass_env()` and `platforms.GitLabPlatform.git_env()` now dynamically ensure executable permissions (`os.chmod`) and automatically fall back to an isolated generated POSIX wrapper script (`0700` in private temp dir with atexit cleanup) if the underlying filesystem is read-only.
- **Docker Layer Hardening**: Ensured execute bits are preserved on askpass helpers inside container builds.

### Token Telemetry & Health Accuracy (R2, R3)
- **Network Resilience**: `tokencheck.check()` distinguishes host unreachability (`error == "unreachable"`) from credential rejection, reporting `level="unknown"` with `"could not reach host — not verified"` instead of raising critical alert banners. Aligned `GitLabPlatform`, `ForgejoPlatform`, and `BitbucketPlatform` `token_info()` probes.
- **Local & Non-Expiring Credentials**: Assigned `level="n/a"` to local folders and CLI auth (excluding them from verification alerts), and `level="ok-no-expiry"` to verified tokens without expiration dates so healthy tokens are not counted as unverified.

### TUI Window Resize & Navigation Help (R4, R5)
- **Dynamic Tab Switch Help**: Replaced static help overlay text with dynamically computed tab list (`1 - 7 Switch tabs (...)`) sourced directly from `TABS`, preventing tab drift.
- **Interactive Window Resize Handling**: Integrated `KEY_RESIZE` / `curses.update_lines_cols()` handling across all 15 modal and full-screen loops, ensuring clean redraws upon terminal geometry changes (e.g., 120x30 to 80x24 to 40x12).

### UI Polish & Route Scoping
- **Design System Token Harmony**: Enforced unified semantic tokens across WebUI CSS and component scripts, aligning all radii (4px/9px/12px/14px/99px), type ramp steps (12px caption/label), and modal backdrop elevation to zero-drift specification.
- **Accessible Mobile Top Bar**: Mobile action buttons feature clear `aria-label` and `title` tooltips, distinct play icon for sync triggers, and responsive topbar layout.
- **Web Scheduler Install Action**: Converted raw anchor link in dashboard notice to an accessible action `<button>` wired to the scheduler install endpoint.
- **Scoped Retry Failed**: `--retry-failed` CLI option supports route isolation when combined with `--route <name>`.
- **Documentation Organization**: Relocated review and plan files to `docs/FIX-PLAN.md` with updated references.

## [2.0.1] - 2026-11-11

Security hardening, secret protection, GitHub browser login (Device Flow), live rate limits, global sync pause/resume, and UI clarity improvements.

### GitHub Device Authorization & Rate-Limit Tracking
- **Browser Login (OAuth Device Flow RFC 8628)**: Added interactive GitHub Device Flow authentication in TUI (`L` in accounts, wizard option), WebUI ("Sign in with GitHub" modal with live polling), and CLI without requiring PAT copy-pasting or open inbound ports. Standard library only (`urllib.request`).
- **Live Rate-Limit Telemetry**: Automated capture of GitHub API `x-ratelimit-*` headers across all requests; surfaced in TUI account probes (`[rate: rem/limit (reset_min)]`) and WebUI platform cards and tables.

### Global Sync Pause & Resume
- **Master Pause Switch**: Added `gitsync pause` and `gitsync resume` CLI commands, TUI `P` toggle with persistent `[PAUSED]` indicator in title and status lines, WebUI topbar Pause/Resume toggle button, and `/api/pause` endpoint.
- **Sync Guard**: `run_sync()` respects global pause setting, exiting cleanly with `skipped="paused"`, with `--force` override option.

### Repository Management & Route Filter Additions
- **Mirror Hub Status**: Repo browser in both TUI (`E`) and WebUI (`/repos`) displays real-time `mirrored` status vs unmirrored.
- **TUI Repo Browser Shortcuts**: Added `P` shortcut to quickly switch source accounts without backing out, and `+` shortcut to add selected repos directly to existing route filters.


### Security & Secret Handling
- **Scrubbed stored credentials in mirrors (S2)**: Hub mirror configs rewritten to store clean, token-less URLs in `remote.origin.url`; credentials supplied per command via `GIT_ASKPASS`.
- **Zero tokens in process arguments (S3)**: Git clone and push invocations pass clean URLs on argv; credentials delivered privately through environment variables and `gitsync/askpass.py`.
- **Full error redaction (S1)**: `sync_repo` clone-failed error path now passes through `_redact()` so remote credential echoes never leak into logs or records.

### Core & Platform Robustness
- **HTTP error handling (B1)**: `_http()` honors `want_headers` on all failure and retry branches; `GitHubPlatform.token_info()` safely classifies unreachable endpoints without unpacking crashes.
- **Unverified token telemetry (W2.1)**: `tokencheck.summary()` reports unverified/unknown tokens as unverified rather than asserting all tokens healthy.

### Web UI & Mobile Responsiveness
- **Zero mobile horizontal overflow (W1)**: Resolved 375px mobile viewport horizontal clipping; top bar buttons collapse gracefully to icon-only controls on narrow screens with accessible labels; accessible mobile sign-out button added.
- **Dashboard clarity & error handling (W2)**: Replaced truncated platform error strings with clear plain-language diagnostic messages; accurate scheduler notice displayed when route schedule is active but no timer is installed; silenced non-security 401 audit logging on unauthenticated session probes.
- **Visual polish & workflow features**: Route cards now show mode (`safe` / `full` warning badge) with proper spacing and streamlined action menus; form checkboxes styled with system theme; dirty state indicator added to settings; added "Retry failed" button and per-run execution report viewer modal linked from History.

### TUI Resilience & Enhancements
- **Crash diagnostics & logging (5.1)**: Full exception tracebacks written to `gitsync.log` with log file path surfaced to the user.
- **Dynamic modal resizing & deduplication (5.2)**: All modal loops dynamically re-query terminal dimensions (`getmaxyx()`), preventing layout clipping on terminal resize; merged duplicate confirm dialog runners into a unified `_confirm_modal`.
- **Unicode search & input (5.3)**: Search and prompt text inputs support full Unicode codepoints and multi-byte wide characters via `get_wch()`.
- **Mouse support & text selection (5.4)**: Restricted mouse mask to left clicks and scroll wheel events, preventing terminal text selection hijacking; added wheel scrolling across views.
- **NO_COLOR support (5.5)**: Honored the standard `NO_COLOR` environment variable with non-colored pairs while preserving explicit text badges (`[OK]`, `[FAIL]`, `[WARN]`).
- **Global Help (5.6)**: Added searchable global keyboard shortcuts and usage guide screen bound to `?`, with persistent footer hints.
- **Empty state guidance (5.7)**: Replaced blank panels on unconfigured installs with clear guidance to add platforms or run the setup wizard.
- **Run progress & cancellation (5.8)**: Added live per-repo breakdown (ok, failed, skipped) and safe sync cancellation via `X`.
- **Full mode safety confirmation (5.9)**: Required explicit route name or `yes` confirmation before executing any route in destructive `full` mode.
- **Zero-subprocess draw guard (5.10)**: Offloaded background scheduler status checks so terminal drawing makes no synchronous subprocess calls.

### Hermetic Testing & CI
- **Hermetic test suite (T1, T2)**: Global test environment enforces offline hermeticity with local loopback socket filtering; `TestRestore` carries explicit committer identity and asserts commit setup status.
- **Playwright suite cross-platform paths (T3, T4)**: Removed hardcoded screenshot paths; added automated mobile responsiveness assertions across all 9 pages; added optional Playwright jobs across GitHub Actions, Forgejo, and GitLab CI.
- **TUI smoke & performance tests (T5, 5.10)**: Added TUI smoke test harness and draw performance verification ensuring zero subprocess calls during UI rendering. All 163 logic tests pass.

## [2.0.0] - 2026-11-10

Major 2.0 milestone release — Web UI, E2E testing, design system, and cross-platform hardening.

### Milestone Highlights
- **Impeccable Design System & Craft Floor**: Eliminated all AI design antipatterns (zero-offset chromatic glow halos, side-tab borders); added themed browser surfaces (`::selection`, custom dark scrollbars, accent caret coloring); generated canonical `DESIGN.md` tokens and `PRODUCT.md` product record.
- **End-to-End Automated Testing Suite**: Added dedicated Playwright headless browser E2E test suite (`tests/test_webapp_playwright.py`) covering authentication, all 9 navigation surfaces, keyboard-accessible in-app modal confirmations (`G.modalConfirm`), and responsive mobile viewports (375×667). 157 total automated tests passing.
- **Cross-Platform Hardening**: Fixed Windows static file path resolution in `web.py` (`.lstrip("/\\")`); silenced client keep-alive disconnect socket errors (`ConnectionAbortedError`); ensured strict lock file descriptor cleanup across CLI and background jobs.
- **Web Interface Guidelines Compliance**: Full compliance with dark-mode color-scheme metadata, tabular numeric typography (`font-variant-numeric: tabular-nums`), balanced text wrapping (`text-wrap: balance`), standard ellipsis placeholders (`…`), semantic `<button>` action controls, and touch optimization (`touch-action: manipulation`).

## [1.9.9] - 2026-11-10

Cross-platform static path resolution fix and end-to-end Playwright test suite.

### Fixed — Windows static files & socket handling
- **Windows static asset path resolution**: Fixed a bug where `os.path.normpath(path).lstrip("/")` retained leading backslashes (`\index.html`) on Windows, causing `os.path.join(root, ...)` to evaluate to `D:\index.html` (the drive root) and fail directory containment with 403 Forbidden. Now strips both forward and backslashes (`.lstrip("/\\")`).
- **Suppressed client disconnect socket noise**: Added `ConnectionAbortedError: [WinError 10053]` handling to `_send` and `_Server.handle_error` to cleanly ignore standard keep-alive teardowns and client aborts without printing tracebacks.

### Added — E2E Testing
- **Playwright E2E browser test suite**: Added `tests/test_webapp_playwright.py` exercising the full Web UI in headless Chromium across authentication, all 9 navigation pages, in-app modal confirmation dialogs, mobile responsive viewports (375×667), and session logout.
- **Cross-platform static regression guard**: Added `test_static_files_serve_200_cross_platform` to `tests/test_logic.py`.

## [1.9.8] - 2026-11-10

Web Interface Guidelines compliance pass across the entire web UI.

### Changed — Web UI guidelines & design polish
- **Accessible modal confirm dialogs**: Replaced native browser `window.confirm()` popups across Platform, Route, Tools (gc & restore), and Settings views with a non-blocking, keyboard-accessible in-app modal (`G.modalConfirm`) featuring backdrop blur, focus trapping, Escape key dismissal, and trigger focus restoration.
- **Tabular numeric typography**: Added `font-variant-numeric: tabular-nums` to `.mono`, `table`, `.status`, `.badge`, `.big`, `.chip`, and `.kv` for rock-solid visual column stability across metrics, timers, and byte counts.
- **Balanced headline wrapping**: Added `text-wrap: balance` to page headers, modal titles, and route cards to prevent orphaned words on narrow viewports.
- **Form UX & semantics**: Added proper `name` attributes, `spellcheck="false"`, `inputmode="numeric"`, `inputmode="url"`, and standardized ellipsis placeholders (`…`) across all input fields and textareas.
- **Action buttons over links**: Replaced action anchor tags (`#use-token`, `#logout`) with semantic `<button type="button">` elements.
- **Dark mode metadata**: Added `<meta name="color-scheme" content="dark">`, `<meta name="theme-color" content="#0d1117">`, and `style="color-scheme: dark"` across all 9 HTML shells.
- **Touch & mobile interaction**: Added `touch-action: manipulation` and disabled `-webkit-tap-highlight-color` to prevent tap delays and unstyled tap flashes on mobile viewports.
- **Reduced motion compliance**: Extended `prefers-reduced-motion` media queries to eliminate infinite spinner animations and modal transitions when requested by the OS.
- **Native formatting**: Implemented `Intl.DateTimeFormat` and `Intl.RelativeTimeFormat` for standardized localized timestamps and relative durations.

## [1.9.7] - 2026-11-10

Comprehensive 10/10 quality and hardening pass across all sectors.

### Fixed — correctness & platforms
- **Platform token URLs are percent-encoded**: `GitHubPlatform` and `GitLabPlatform` now properly quote tokens containing reserved characters (`@`, `:`, `/`) via `urllib.parse.quote()`, preventing malformed git push URLs.
- **GitHub HTTP URLs handled cleanly**: Local or self-hosted GitHub Enterprise instances using `http://` URLs are cleanly parsed without mangled hosts.
- **GitLab `glab` CLI option in wizard**: The setup wizard now allows selecting `glab` CLI authentication for GitLab instead of forcing manual personal access token entry.
- **Fallback in `ask_secret`**: When `termios` is unavailable, `ask_secret` falls back cleanly to `getpass.getpass()`, preventing crashes and infinite retry loops.

### Fixed — install & scheduling
- **Webhook service installation for unprivileged users**: `install_webhooks()` and `uninstall_webhooks()` now default to unprivileged systemd user units (`~/.config/systemd/user/`) and support `--system`, matching `install_systemd_web()` and eliminating non-root `PermissionError` crashes.
- **Removed hardcoded root `%h` in system units**: Webhook service template no longer sets `Environment=HOME=%h` on system units.
- **`webhooks_active()` checks both scopes**: Correctly probes user-level systemd units as well as system-level services.

### Fixed — TUI & anti-slop architecture
- **Unified progress parsing**: Eliminated duplicate `_REPO_RE` regex in `gitsync/tui.py`; TUI now imports and reuses `sync.parse_progress_line()`.
- **Deduplicated `_app_dir()`**: Replaced redundant local implementations across `tui.py`, `web.py`, and `webhooks.py` with `config.APP_DIR`.
- **Refined TUI log coloring**: Log level detection uses word-boundary matching instead of naive substring containment, preventing false positive colorings on repository or branch names.
- **Curses & POSIX graceful fallbacks**: Safe fallbacks allow module imports, testing, and informative messaging on environments where `curses` or `fcntl` are not installed.

### Fixed — Web UI LAN access
- **DNS rebinding guard supports LAN & mDNS**: When bound to `0.0.0.0`, `web.py` host validation allows `.local`, `.lan`, `.home.arpa`, and single-label local hostnames.

## [1.9.6] - 2026-11-10

Web UI review pass (a fleet of correctness, security, architecture,
code-quality, simplicity, UX/a11y, telemetry and testing reviewers) plus a
TUI resource fix. Backwards-compatible; no interactive behaviour change.

### Fixed — TUI resource use
- An **orphaned TUI busy-spun at ~100% CPU**. The event loop paced itself on
  `getch()`'s 1-second timeout, but when the terminal closes, `getch()` returns
  instantly at EOF, so the `while True` loop never slept. The TUI now detects a
  dead terminal (`getch()` returning empty in <50 ms, 20× in a row — a live
  idle terminal blocks the full ~1 s, so it never false-triggers) and exits
  cleanly instead of burning a core. Interactive use is unchanged.

### Fixed — web UI correctness (dead/unsafe features)
- **Route pause/resume returned 500** — the handlers called a non-existent
  `self._pause_resolve`; now wired correctly.
- **"Doctor — auto-repair" button was dead** — the Tools page sent
  `kind:"doctorfix"`, which the backend rejected; the id is now an accepted
  alias for `doctor --fix`, with a contract test pinning every card id.
- **Repos target dropdown was ignored** — selecting a site and hitting Sync
  pushed to *every* target; the chosen target is now honoured.
- **Platforms subtitle rendered literal `<a>` markup** — the scopes link is a
  real button; the stale "0 sites" subtitle now refreshes after load.
- `save-config` selection-file accepted an arbitrary write path (could clobber
  `config.json`/`web.token`) — now confined to the config dir; returns 400 when
  no route matches instead of a false success.
- `scheduler/install` accepted an unknown `schedule` (corrupted the setting,
  installed the wrong timer, then 500'd) — now validated.
- SSE stream could wedge every mutating job (a slow reader held the job lock
  across a blocking socket write) — the lock is now released around writes and
  the socket has a timeout; a finished run closes its EventSource (no more
  reconnect loop), and a lost stream stops retrying.

### Security
- Config read-modify-write is now serialized (process lock + `flock`) so
  concurrent web writes (or web vs TUI) don't lose updates.
- `save_config` writes its temp file `0600` before replace (tokens were briefly
  world-readable in the write window).
- Platform `url` must be http(s) with a bare host (no token relay / SSRF);
  local-folder paths are validated (no writing bare repos into `/etc` etc.).
- The first-run access token is no longer printed to the journal/pipe — read
  `web.token` (0600). `/api/auth` no longer leaks the username unauthenticated,
  and the DNS-rebinding Host guard now covers the public login/auth routes.
- Login throttle is keyed per client+account and also covers the token branch;
  `compare_digest` runs on bytes (a non-ASCII token no longer drops the
  connection); job errors are redacted and job output is bounded; a socket
  timeout reaps half-open connections; `nosniff`/`X-Frame-Options` added.
- Security-relevant web events (auth failures, guard rejections, 5xx, config
  and security mutations, job start/finish) are audit-logged (redacted) to
  `gitsync.log`.

### Architecture / quality
- The 39 HTTP handlers moved out of the 900-line `make_handler` closure to
  module level (testable); the config transaction is one `config.update()`
  helper. Shared progress parsing + `engine_busy()` live in `sync.py`; the
  redactor is a lazy singleton (no import-time read of the live config) and
  calls the now-public `sync.redact`. `web_active`/uninstall understand
  `--system`; the webhook-install bind matches the CLI; `DISCORD_PREFIXES`
  moved to `notify.py`; dead code and the Google-Fonts CDN link removed.

### UX / accessibility
- Every form control has an accessible name (labels emit `for`); toasts, status
  and progress bars are ARIA live regions; skeletons announce busy; loaders
  show their message; error toasts are never empty; buttons disable while busy.
- `<nav>` landmark + skip link; decorative icons `aria-hidden`; login is a
  `<form>` with focus management; `prefers-reduced-motion` honoured.
- Mobile: tables scroll instead of the page, 44px touch targets, status stays
  visible, sign-out moved into the top bar. Theme control labelled "TUI theme".
- Settings has one authoritative schedule control; dashboard surfaces
  per-token expiry; log viewer only auto-scrolls when already pinned.

### Tests
- 142 unit tests (was 124): a new HTTP-layer suite (auth, cookie login, CSRF,
  Host guard, traversal, 413, the pause endpoint, the doctorfix alias, setting
  validation), the tools-page contract test, locked-case `engine_busy`, token
  reuse + `0600`, `_public_settings` redaction, `_apply_setting` rejection, and
  progress-line parsing.

## [1.9.5] - 2026-11-10

### Changed — two clear run modes, and web UI fixes

- **Two ways to run gitsync**, now the documented default. The **native** install
  gives you the TUI plus a **localhost** web UI — `gitsync web` binds `127.0.0.1`
  by default (private to the machine). The **always-on service**
  (`gitsync web --install`) and **Docker** are the LAN paths and bind `0.0.0.0`.
  `--bind` still overrides either and is remembered. A "Two ways to run gitsync"
  table sits at the top of the README.

### Fixed (web UI)

- **SSE reconnect loop**: a finished run now closes its EventSource, so the
  browser stops reconnecting and re-streaming a done job forever.
- **One sync path**: the top-bar Sync/Dry-run, the Routes per-route Run, and the
  Repos selection all go through the same coordinator, so the top bar's live
  progress reflects every run, not just the global one.

CLI/TUI otherwise unchanged; 126 unit tests.

## [1.9.4] - 2026-11-10

### Added — always-on web dashboard

`gitsync web --install` installs and starts a **systemd user service** so the
dashboard is up on boot and stays up (same pattern as the sync timer / webhook
receiver). Binds `0.0.0.0:7765` by default; authentication is still required on
every request.

```bash
gitsync web --install            # always-on service, starts on boot
loginctl enable-linger "$USER"   # keep running while logged out
gitsync web --status             # active / not running
gitsync web --uninstall          # remove it
journalctl --user -u gitsync-web -f
```

`--system` installs a root system service instead. In Docker there's no systemd,
but the container itself is the always-on process (`restart: unless-stopped`) —
so the dashboard can be run **Docker-only**, configured entirely from the
browser (Platforms + Routes), with no native install. CLI/TUI otherwise
unchanged; 126 unit tests.

## [1.9.3] - 2026-11-10

### Changed

- The one-click installer's final message now lists `gitsync web`, so a fresh
  install points you at the browser dashboard (which ships in the package and
  binds `0.0.0.0` by default — see 1.9.2).

## [1.9.2] - 2026-11-10

### Changed — web UI is LAN-reachable out of the box

`gitsync web` now binds `0.0.0.0` by default, so the dashboard is reachable
from your phone/laptop the moment you start it (a browser dashboard is meant
to be opened from another device). **Authentication is still always required
on every request** — this is not an open port. On start it prints the exact
URLs to open (e.g. `http://192.168.0.222:7765`).

- The chosen `--bind` / `--port` is **remembered** in `settings.web`, so
  `gitsync web --bind 127.0.0.1 --port 7782` sticks — bare `gitsync web` reuses
  it. Nothing is written unless you pass a flag explicitly.
- Restrict to this machine any time with `gitsync web --bind 127.0.0.1`.
- Docker already binds `0.0.0.0` internally; publish the port as before.

CLI/TUI otherwise unchanged. 126 unit tests (adds bind/port resolution).

## [1.9.1] - 2026-11-10

### Changed — Web UI design polish

The web dashboard now reads as a real app, not a prototype.

- **App shell**: a sticky, blurred **top bar** (brand + live status pill + global
  **Sync now / Dry run** with an inline progress bar) and an **icon sidebar**
  (inline SVG nav, active-item accent indicator, version + sign-out footer).
  The top-bar sync drives a live progress readout everywhere; the Dashboard
  reflects the same run in its "Syncing now" card and log.
- **Refined components**: gradient primary buttons, elevated cards (12px,
  layered shadow), hover-highlighted tables, focused form controls with
  chevron selects, refined badges/chips/dots, and top-right toasts with icons.
- **Skeleton loaders** replace spinners on first paint; nicer empty/error
  states. Mobile layout tightened (sidebar → tab strip, top bar compresses).

No API or engine changes; the CLI and TUI are untouched. Still 124 unit tests.

## [1.9.0] - 2026-11-10

### Added — Web UI (`gitsync web`) and Docker

Everything the TUI does, now from a browser — plus live streaming and a
container image. Zero new dependencies: Python standard library only on the
server, plain HTML/CSS/vanilla JS on the front end, no build step.

- **`gitsync web [--port 7765] [--bind 127.0.0.1]`** opens a dark,
  terminal-flavoured dashboard (matching `docs/design/`). Tabs: Dashboard,
  Platforms, Routes, Repos, Stars, Log, History, Tools, Settings.
- **Every TUI feature has a web equivalent**: accounts (add/edit/remove/test/
  enable-disable), routes (add/edit/remove/pause/resume a route *or* one
  target, per-route workers/mode/conflict/schedule, filters), repo browser
  with selective sync + save-to-file, star copy, full log viewer, run history,
  options, notifications, webhook-receiver control, scheduler install, and the
  tools (doctor, compare, fix, tokens, disk, gc, restore, mirror).
- **Live sync over Server-Sent Events**: the same `python3 -m gitsync sync`
  subprocess the timer runs, streamed to the browser with a parsed
  `[done/total]` progress bar — nothing reimplemented, nothing can drift.
- **Security first** (the config holds platform tokens):
  - binds `127.0.0.1` by default; any other bind needs an explicit `--bind`
    and always requires auth
  - a random access token (`web.token`, 0600) guards every `/api` call via
    `Authorization: Bearer` or an `HttpOnly, SameSite=Strict` cookie
    (`hmac.compare_digest`)
  - a token is **never** returned in any response, log line or page — only
    state (`set`/`missing`/`gh`/`glab`); token fields are write-only in forms;
    a redactor strips any known token from every body and SSE line, on top of
    the engine's own `_redact()`
  - CSRF guard (custom header for cookie sessions) + DNS-rebinding guard
    (Host/Origin allowlist, private/loopback IPs allowed for LAN access)
  - **optional username + password sign-in** (`gitsync web --set-password USER`
    or the web Settings): a PBKDF2 hash at mode 0600, never the plaintext, with a
    brute-force throttle. Web/Docker only; the TUI keeps no login. The access
    token still works for API access
  - all subprocesses are argv lists (never a shell); names/paths validated;
    static files and local paths confined (no traversal)
- **One sync at a time**: respects the engine's `sync.lock` and reports
    "already running" (409) cleanly.
- **Docker**: `Dockerfile` + `docker-compose.yml`. Runs the same `gitsync web`
    on `0.0.0.0:7765`; config + mirror hub live on volumes; token auto-generated
    on first run (or set `GITSYNC_WEB_TOKEN`); unprivileged user; `/healthz`
    healthcheck. The base image bundles git, so the build needs no apt network.
- The CLI and TUI behave exactly as before (the only engine change is the
  additive `web` subcommand). 124 unit tests (was 107), including auth,
  input validation, token redaction, password hashing, atomic config write and
  the sync lock.

## [1.8.1] - 2026-11-10

### Performance

Measured before and after on the real setup (4 platforms, 53 mirrors):

- **Warm TUI start: 4.4s -> 0.21s.** The platform probe (2.7s, mostly the
  `gh` CLI at ~0.7s a call) ran on every start and the dashboard showed
  "loading..." for all of it. The last result is now cached on disk for 60s
  (`~/.local/share/gitsync/probe_cache.json`, 0600): a repeat launch paints
  real counts immediately and the background refresh corrects them. `R`
  forces a refresh, and the "counts: Ns old" line reports the data's real
  age, not the launch time.
- **Cold TUI start: 4.4s -> 3.3s.** GitHub's probe ran `gh auth status` and
  then `gh api user`; it now makes one call, which says "not authenticated"
  just as clearly.
- **Log tab: 2.2ms -> 0.33ms per frame.** `read_log_tail` re-read the whole
  log file on every frame; it is now cached by (mtime, size, n), as is the
  full-log viewer.

No behaviour change: nothing is cached across a real change, a stale cache
is ignored, and the probe still runs in the background on every start.

### Changed

- 107 unit tests (was 103), including the caches (stale ignored, age
  reported, log cache invalidated by a write).

## [1.8.0] - 2026-11-10

The features the review deferred until the fixes were in, plus the P2 polish.

### Added

- **F1 `gitsync restore`** — when the source host is gone, no route can list
  its repos, but the hub still holds every mirror. `restore --to PLATFORM`
  pushes them back out: creates missing repos (private unless `--public`),
  skips empty mirrors with a log line, deletes nothing unless `mode: full`
  and `--prune`, exits non-zero on any failure. Verified live over the real
  53-mirror hub.
- **F2 pick repos in the wizard** — step 3 lists the source's repos (capped
  at 50 with a count) and accepts Enter for all, a comma list, or globs,
  stored as the route's include. No more silently mirroring the whole
  account.
- **F3 `glab` auth, for real** — `"auth": "glab"` on a GitLab platform uses
  `glab api` for the API and a `GIT_ASKPASS` helper for git, so the token is
  read at call time and never written into a remote URL, a hub config or a
  log. If `glab` is missing or not logged in, `platforms test` says so.
  Verified end-to-end against the live GitLab account.
- **F4 `include_file`** — `gitsync routes edit NAME --from-file repos.txt`
  (one repo per line, `#` comments), or `W` in the repo browser. A missing
  file means "no filtering", never "sync nothing".
- **F5 plain-language doctor** — ends with "what to type next": at most
  three commands mapped from the problems actually found.
- **F6 activity summary** — `gitsync status` gains hub size, mirror count,
  last successful run, and next due route.
- **P2** — log rotation at 2 MB (one backup), `platforms test` says
  `disabled` instead of `FAIL` for a disabled platform, the mesh heuristic
  in `routes add` prints "interpreted as a two-way (mesh) route",
  `CONTRIBUTING.md` describes the real CI, `import json` moved to the top of
  `sync.py`.

### Fixed

- **GitLab `star()` never actually starred.** It issued a `GET` (which only
  reports the current status) and treated a `200` as success, so an
  unstarred project looked "already ok" and nothing was starred. It now
  POSTs, and falls back to a path lookup then a POST by id.
- The GitLab star path was double-encoded (`%252F`); the earlier fix for it
  had not actually landed. Both are now covered by tests, which is how they
  were caught.

### Changed

- 103 unit tests (was 91).
- README gains "If the source host disappears" and "A repo list in a file",
  plus rows for every new command and key; `docs/OPTIONS.md` gains the repo
  selection, `glab` and restore sections.

## [1.7.1] - 2026-11-10

### Documentation

- **`docs/OPTIONS.md`** — what every setting means, in plain language, and
  when to change it: the two decisions that matter (`mode`, `mesh_conflict`),
  every global setting with its cost, per-route overrides, notifications, the
  platform keys, and where the files live.
- **README configuration reference** — tables for every `settings`,
  `platforms` and `routes` key with values, defaults and the TUI key that
  changes it. The example previously showed 4 of the 9 settings.
- **README command + flag reference** — the commands that were missing
  (`install`, `ci`, `doctor`, `update`, `watch`, `uninstall`, `notify
  --set-url/--set-telegram`, `fix --route`) and a "flags worth knowing" table
  (`--dry-run`, `--scheduled`, `--to` on a mesh route, `--orphans
  --prune-orphans`, `--system`/`--user`/`--yes`).
- **README TUI key reference** — every key on every tab, including the ones
  added since 1.5 (`D`, `T`, `V`, `?`, `W`, per-tab keys, mouse).

Every value in the new tables was checked against the code, and every flag
was checked against `--help`.

## [1.7.0] - 2026-11-09

A full review pass: 14 correctness/safety bugs, 10 honesty fixes, and one
test module instead of four drifting copies. The short version for users:

- **A new setup no longer deletes anything.** `mode: safe` is the default
  (never deletes, never force-pushes) and a mesh defaults to
  `mesh_conflict: skip`, which reports a disagreement instead of silently
  overwriting the older side. Existing configs that say `full`/`newest`
  keep their setting.
- **`compare` and `fix` now tell the truth.** They probed the source URL for
  every target, so drift was invisible; they use each platform's own URL and
  no longer create repos during a read-only check.
- **The one-click install works for a normal user.** `--yes` was rejected,
  a non-root install died writing `/etc/systemd/system`, and the timer ran
  as root against `/root`'s config. It now installs a *user* unit (or user
  cron) for the schedule the wizard chose, and previews with `--dry-run`
  before offering the first real sync.
- **No silent security changes.** Turning on force-push over branch
  protection on a destination is now opt-in
  (`settings.relax_protection`).
- **No false claims.** Every `glab` path is deleted — it never existed.
  Tokens are hidden in the wizard, and re-running setup asks before it
  replaces your accounts.
- **Secrets stay put.** Git error text is redacted before it is logged or
  stored, and the log, `records.json` and every hub `config` are `0600`.

### Fixed

- **P0-1** installer: `--yes` rejected (reproduced), non-root install died
  at the system unit, the unit ran as root against `/root`, `ExecStart`
  lacked `--scheduled`, `weekly` raised KeyError, `--user` called sudo.
  One unit builder (`gitsync install`), user unit by default,
  `finest_schedule` decides the calendar, an all-off config installs
  nothing, quickinstall installs the scheduler *after* the wizard.
- **P0-2** Bitbucket crashed on every call (`_api` returns 2 values, the
  methods unpacked 3); `repo_url` pointed at the API host with no workspace
  and no token.
- **P0-3** Forgejo/Gitea git URLs carried no token, so private clone/push
  failed; the token is percent-encoded and the login is cached.
- **P0-4** `compare`/`fix` used the source's `clone_url` for every target
  (false "all in sync") and called `ensure_repo` in a read-only path.
- **P0-5** the hub was keyed by the short repo name, so two `docs` projects
  in different groups shared one mirror. Keyed by a stable id
  (`owner/name`, `path_with_namespace`, ...) with a read-only migration of
  the existing flat layout.
- **P0-6** a dry run or a failed listing consumed the schedule slot;
  `sync` exits 0 only when nothing failed *and* nothing was partial.
- **P0-7** `safe` still `--force`-pushed and `full` used `--mirror` against
  every host. One pure `push_args()`; `safe` never forces or prunes,
  `full` force-prunes heads and tags only, `--mirror` only for a local
  target.
- **P0-8** a mesh that refused to overwrite reported `ok`; it now reports
  `partial` and lists the skipped refs.
- **P0-9** `glab` was documented and never implemented; wizard tokens were
  echoed; re-running setup wiped every account silently.
- **P0-10** tokens leaked through git error text; `bash -c "df -h %s"` was
  shell injection.
- **P0-11** `doctor --fix` unlinked `sync.lock` during a live sync (two
  syncs could push the same repo); `disk --orphans` compared against the
  first route only and `--prune-orphans` could delete live mirrors.
- **P0-12** GitHub token check crashed on 401 instead of reporting
  "token rejected".
- **P0-13** Telegram-only users never got sync alerts; `notify --set-url`
  and `ci --install-hook` were parsed and ignored; `ci` hid assertion
  failures.
- **P0-14** GitHub listing capped at 500, org repos never listed, GitHub
  Enterprise impossible.
- Plus: an unreachable target crashed the dry-run plan; Forgejo over plain
  http was forced to https; `config.VERSION` disagreed with `__version__`;
  `mirror_is_valid` re-cloned empty repos every run; `watch --interval 0`
  busy-looped; the TUI options screen always showed notify mode `failure`.

### Changed

- **One test module.** The assertion blob that lived in `UNIT_CHECKS` plus a
  heredoc in each of the three CI files (with different quoting per copy,
  and one already drifted) is now `tests/test_logic.py`, run by `gitsync ci`
  and all three workflows. The offline sync smoke also performs the real
  pushes and asserts both modes.
- 85 unit tests (was 136 loose asserts in four places).
- New config keys: `github.affiliation`, `settings.mesh_conflict`,
  `settings.relax_protection`. New defaults for *new* configs:
  `mode: safe`, `mesh_conflict: skip`.

## [1.6.2] - 2026-11-09

### Fixed

- **`gitsync status` said "4 site(s), 0 connected"** while every platform
  showed `OK`. The count compared the table's `"ON "` / `"OK "` (with their
  trailing spaces) against `"ON"` / `"OK"`, so it never matched. The TUI's
  count was correct; only the CLI line was wrong.

### Changed

- 136 unit assertions (was 135).

## [1.6.1] - 2026-11-09

The TUI felt slow. It was not the drawing (0.4-2.8 ms a frame) - it was
blocking calls on the main thread.

### Fixed

- **Tools no longer freeze the TUI.** `compare` walks every repo on every
  site and took **3.5 minutes**, `disk` ~12s, `doctor` ~3s - all on the main
  thread, so the dashboard was dead for that long. Disk, doctor, compare,
  mirror and fix now run in the background and stream their output into the
  dashboard's log panel, and the output stays on screen when they finish.
  Verified: switching tabs mid-run works.
- **Start-up and `R` are ~45% faster.** The platform probe was sequential
  (4 sites, ~7.8s, most of it the `gh` CLI at ~0.7s a call). It now probes
  every site in parallel: 4.5s, bounded by the slowest single site.
- **Listing repos no longer blocks.** `E` (and `V` below) froze the screen
  for ~1.5s with no feedback; it now shows "listing repos on github ..."
  and fills in.
- **Testing a platform no longer blocks** the accounts screen.
- **`V` in the accounts manager loaded the wrong platform's repos** - the
  repo screen re-listed from the first route's source. It now takes the
  platform you highlighted.

### Added

- **`V` in the accounts manager browses that site's repos** - so selecting a
  platform does show its repos, with the same select/sync keys as `E`.
  `L` (local only) now follows the route whose source is the platform you
  are browsing.
- **Adding a route includes its settings**: workers, mirror mode, mesh
  conflict policy and schedule are all in the same form, each defaulting to
  "inherit" (use the global value).

## [1.6.0] - 2026-11-09

Three things: the risky part of a sync is now visible before it happens, the
last sync option moved into the TUI, and the TUI takes a theme.

### Added

- **A dry run now names every ref a `full`-mode push would delete.** The
  push in full mode is `git push --mirror`, which prunes refs the target has
  and the source does not — the one part of a sync that can lose work. The
  plan now lists them per target:

  ```
  [1/1] PLAN dockeryaml
           - backup: up to date
           - backup: WOULD DELETE 1 ref(s): prune-preview
  ```

  `mode: safe` never reports one, because it never prunes. The CI smoke test
  covers both directions with an offline two-folder repo.
- **`T` in the repo browser** pushes the selected repos to **one site** of
  your choosing (the last sync option that needed the terminal). If every
  route is a mesh, the picker says so, because `--to` does not apply.
- **Colour theme** (`O` → Colour theme): `default`, `mono`, `ocean`,
  `forest`, `sunset`. `mono` is all-white for terminals where colour is
  noise. The change applies immediately, no restart, and shows on the
  Settings tab.

### Changed

- `_plan_repo()` reads each remote's full ref map once instead of issuing
  extra `ls-remote` calls per branch, so a dry run of 52 repos is not
  slower than before despite doing more work.
- 135 unit assertions (was 129).

## [1.5.2] - 2026-11-09

### Fixed

- **`gitsync sync` crashed with `NameError: name 'SYNC' is not defined`.**
  Shipped in 1.5.0: the module is imported as `sync as SY` while every call
  site says `SYNC`, so `cmd_sync` died before doing any work. That is the
  command the systemd timer runs, so a 1.5.0/1.5.1 install silently stopped
  syncing (`gitsync.service` shows `failed`, status 1). `mirror` still
  worked, which is why it was not obvious. The import is renamed to match
  the call sites.
- **CI could not have caught it**: the smoke test only ran `--help`. It now
  also runs a real offline sync (two local folders, one route) plus
  `status`, `routes` and `stars list`, so a broken `cmd_sync` fails the
  build. Verified by reintroducing the bug in a scratch copy and watching
  the new check fail.

## [1.5.1] - 2026-11-09

### Fixed

- **Ctrl-C inside a suspended run killed the whole TUI.** Pressing Ctrl-C in
  the wizard, in `mirror`, or in any Tools entry that drops to the shell
  propagated out of `curses.wrapper()` and printed the snapshot before
  exiting. It now catches the interrupt, says "cancelled", and returns to
  the dashboard. The wizard, the repo browser's inline sync and every Tools
  entry share one helper, so they all behave the same.
- **A route line wider than the panel was cut with no hint.** It now ends
  with `... (P for details)`.

### Added

- **Fix drift can now actually be run** from Tools (preview stays the
  default; the real run asks for confirmation first, because it re-pushes
  every mismatched repo).
- **The token-scope guide is back in the TUI**: `?` on the accounts screen
  lists what each platform needs and where to create the token. It had been
  lost when the read-only accounts screen became the manager.

## [1.5.0] - 2026-11-09

The TUI is now the whole app: no command needs the terminal any more, and
stars can be copied between accounts.

### Added

- **Add / edit / remove / test / enable-disable an account in the TUI**
  (`A`). Pick the type, fill the form (name, URL, token, owner, protocol,
  ssh port - token masked), and it is tested on save. Removing warns when a
  route still uses the platform. This replaces the old read-only screen
  that told you to run `gitsync platforms ...` in a shell.
- **Add / remove a route in the TUI** (Routes → `P` → `A` / `X`), with a
  source picker, a target list and a one-way/mesh switch. Per-route
  schedules now cycle in place instead of printing a command.
- **Stars tab**: read one account's starred repos, scroll the list, and copy
  them to other accounts (`C`). GitHub, GitLab and Forgejo/Gitea are
  supported. Only repos that already exist on the target are starred -
  nothing is created - and matching is by exact `owner/repo` (GitHub →
  GitHub) or by the repo's remote URL, never by name alone:
  `SeaXen/hermes-telemetry` and `nujovich/hermes-telemetry` are different
  projects.
- **Tools menu** (`T`): disk usage + orphans, doctor, compare, mirror one
  repo, fix drift (preview), update, uninstall. Read-only checks render
  inside the TUI; the rest drop to the shell and come back.
- CLI: `gitsync stars list [PLATFORM] [--json]` and
  `gitsync stars copy SOURCE t1,t2 [--dry-run] [--repos a,b]`.
- `gitsync platforms add --protocol ssh --ssh-port N`.

### Fixed

- **`stars._norm_url()` mangled `host:port`.** It replaced every colon with
  a slash, so `http://localhost:3002/sagar/gitsync.git` became
  `localhost/3002/...`. Only the scp-style `host:owner/repo` colon is now
  converted; a port is kept, which is what distinguishes two servers on one
  machine. Found by the new unit assertions.
- **The account manager's cursor reset after adding a platform**, so the
  next key press hit the first row (it disabled `github` instead of the new
  site). The cursor now stays on what was just added.
- **`_form()` accepted an empty text field** as "keep the old value" without
  saying so; a field can now be cleared explicitly where that is allowed.

### Changed

- 7 tabs (Stars added between Routes and Log); every footer is tab-aware.
- 129 unit assertions (was 108).

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
