# gitsync 2.1.3 — Round 3 audit and fix plan

Audited: `gitsync-main_1.zip`, version **2.1.3** (Python 3.12.3, git 2.43.0, Linux).
This file is written to be handed to a coding agent. It is self-contained:
every item has the file, the evidence, the exact change, and a test.

## 0. Verdict (read first)

**Not 10/10 yet. It is a strong, well-tested 8/10 overall.** The sync engine is
sound and nothing found breaks everyday syncing. What keeps it from 10 is
(a) one real session-security weakness in the web UI, (b) an unsafe self-update
path, (c) a data-integrity edge case with unusual repo names, and (d) polish and
maintainability gaps. Do not claim "10/10" in any changelog or README.

| Sector | Score | Why |
|---|---|---|
| Core sync logic (one-way, safe/full, mesh) | 9 | Safe-by-default push policy, hub design, 208 passing tests. Edge cases in section P1-3 |
| Credentials handling | 9 | Tokens never in argv, `GIT_ASKPASS`, 0600 files, redaction. Webhook secret has a small race (P2-4) |
| Web UI security | 7 | Host guard, CSRF header, Origin check, throttle are good. **Session cookie is the master token (P1-1)**, no CSP |
| Installer / updater | 6 | `gitsync update` runs an unpinned script from `main` via a predictable `/tmp` path (P1-2) |
| Tests / CI | 9 | 208 unit tests + 5 Playwright, 4-check CI passes. Python 3.8 floor not exercised in GitHub CI |
| Web UI design / a11y | 8 | Skip link, aria labels, focus trap, 32px targets. Dark-only, no CSP, no light theme |
| TUI | not re-tested | Needs a real TTY; only the existing smoke tests were run (see section 7) |
| Docs and repo hygiene | 6 | README 1320 lines and CHANGELOG 1206 lines overwhelm newcomers; stale plan files in repo |
| Maintainability | 6 | `tui.py` 3822, `__main__.py` 2310, `web.py` 2447 lines, still unsplit |

## 1. Ground rules for the agent (non-negotiable)

- Python 3.8+ **standard library only**. No pip dependencies. No `match`, no `X | Y` type
  unions, no `removeprefix`, no `list[str]` generics at runtime.
- Never touch the real config or hub. Always isolate:
  ```bash
  export PYTHONPATH=$PWD GITSYNC_CONFIG_DIR=/tmp/x/config GITSYNC_DATA_DIR=/tmp/x/data
  ```
- Never print or log a token. Use `sync._redact()` / `Redactor` for anything user-visible.
- Logic assertions live **only** in `tests/test_logic.py` (no network). Add cases there.
- Run before every commit; all must pass:
  ```bash
  python3 -m py_compile gitsync/*.py
  bash -n install.sh quickinstall.sh bin/gitsync
  python3 -m unittest tests.test_logic
  python3 -m gitsync ci
  ```
- One item = one commit. Short imperative subject, body explains *why*.
- Bump `gitsync/__init__.py` to `2.1.4` and add a CHANGELOG entry at the end.
- Read `AGENTS.md` first: it documents the hub architecture and the install trap
  (the TUI runs the *installed* copy; re-run `bash install.sh` before TUI testing).

## 2. Already verified working — do not redo

- Offline end-to-end sync of local platforms: branches (including `feature/x`), tags, idempotent second run.
- `compile`, shell syntax, 208 unit tests, `gitsync ci` (4/4) all pass.
- `askpass.py` and `glab_askpass.py` are executable in the zip, and `ensure_executable_helper()` has a chmod + wrapper fallback (earlier R1 is fixed).
- Dockerfile runs unprivileged and `chmod +x`'s the helpers; has a `/healthz` healthcheck.
- Web: unauthenticated `/api/*` → 401; path traversal attempts → 404; bad `Host` on `/api/*` → 403; cross-origin POST → 403; request body cap with drain; `HttpOnly` + `SameSite=Strict` cookie; password hashing is PBKDF2 with constant-time compare; login throttling exists (8 fails/120s per user+IP, 40/120s per IP for the token).
- Round-2 items R2–R5 (unreachable ≠ critical, `n/a` level, TUI help from `TABS`, resize handling) are present in the code.

## 3. P1 — fix these first

### P1-1. The web session cookie *is* the master access token
- **File:** `gitsync/web.py`, `h_login()` (~line 1209), cookie built at ~line 1218; `_authed()` ~line 975.
- **Evidence (reproduced):** `POST /api/session` returns
  `Set-Cookie: gitsync_session=testtoken123; Path=/; HttpOnly; SameSite=Strict` where the value is the
  literal access token. Password login returns the same cookie.
- **Why it matters:** the cookie never expires and cannot be revoked. Signing out, changing the
  password, or setting a new password does not invalidate a cookie that was copied (browser sync,
  shared machine, proxy log, backup of a browser profile). Whoever holds the cookie holds the
  permanent API/Bearer secret.
- **Fix:**
  1. Add server-side sessions: on successful login create `sid = secrets.token_urlsafe(32)`;
     store `sha256(sid) -> {created, last_seen, ip, ua}` in memory and persist to
     `CONFIG_DIR/sessions.json` (0600, written atomically like `save_config`).
  2. Cookie value is `sid`, never the token. Keep `Authorization: Bearer <token>` working for scripts.
  3. Expiry: 30 days absolute, 12 hours idle (make both constants). Add `Max-Age` to the cookie.
  4. `DELETE /api/session` (sign out) revokes that session. Changing the password or rotating the
     token revokes **all** sessions.
  5. `Secure` flag: set when the request arrived over HTTPS. Only trust `X-Forwarded-Proto` when the
     peer is loopback/private or the Host is in `GITSYNC_ALLOWED_HOSTS` (currently any client can send
     that header; harmless, but do it properly).
  6. Settings page: show active sessions (created, last seen, IP) with a "Sign out everywhere" button.
- **Test (offline):** login → cookie value != token; cookie authenticates `/api/status`; after
  logout the same cookie returns 401; after password change all earlier cookies return 401;
  Bearer token still works; expired session (patch the clock) returns 401.

### P1-2. `gitsync update` is unsafe and unpinned
- **File:** `gitsync/__main__.py`, `cmd_update()` (~line 1570).
- **Evidence (code):** when no local `quickinstall.sh` exists it downloads
  `raw.githubusercontent.com/SeaXen/gitsync/main/quickinstall.sh`, writes it to the fixed path
  `/tmp/gitsync-quickinstall.sh` with `open(script, "w")`, then runs it with `bash` (as root when
  installed system-wide).
- **Problems:** (a) predictable `/tmp` name = symlink/race attack on multi-user machines;
  (b) executes whatever is on `main` at that instant, with no version pin or integrity check;
  (c) README advertises `curl … | bash` of the same `main` file.
- **Fix:**
  1. Write with `tempfile.mkstemp(prefix="gitsync-qi-", suffix=".sh")` (mode 0600 by default), `os.chmod 0700`,
     delete in `finally`.
  2. Install from **tagged releases**, not `main`: add `--version X.Y.Z` / default to the latest tag; the
     script downloads `…/archive/refs/tags/vX.Y.Z.tar.gz`.
  3. Publish `SHA256SUMS` with each release (a CI job already tags releases); `quickinstall.sh` verifies
     the tarball hash before extracting. Document how to verify manually.
  4. If running from a git checkout, prefer `git pull --ff-only` + `bash install.sh` and say so.
  5. Print what is about to run and the target version, and ask for confirmation unless `--yes`.
- **Test:** unit-test that the script path is not `/tmp/gitsync-quickinstall.sh`, has mode 0700, and is
  removed afterwards (patch `urlopen` and `subprocess.run`). Shell-test that a wrong hash aborts.

### P1-3. Repo-name sanitising can merge two repos into one folder, and mangles non-ASCII names
- **Files:** `gitsync/platforms.py` `_fs_safe()` line 50; `gitsync/sync.py` `hub_rel()` line 199;
  `LocalPlatform.repo_url()` (~line 959) and `LocalPlatform.list_repos()` (~line 944).
- **Evidence (reproduced):** source folder containing `a b.git` and `a_b.git`:
  both map to hub path `mirrors/local/a_b.git` and target `a_b.git`. Result:
  `FAIL a_b (fatal: destination path '…/mirrors/local/a_b.git' already exists…)` and
  `FAIL a b (dst: fatal: not a git repository…)`; only one folder exists at the target afterwards.
  With parallel workers it is a race; with a different ordering one repo could overwrite the other.
  Also `ünï` was written to the target as `_n_.git` (every non-ASCII character becomes `_`),
  so `ünï` and `öñï` would collide too.
- **Fix:**
  1. Make `_fs_safe(name)` allow Unicode word characters: `re.sub(r"[^\w.\-]", "_", name, flags=re.UNICODE)`.
  2. Add `_fs_unique(name)` returning `safe` when `safe == name`, otherwise `safe + "-" + sha1(name)[:6]`.
     Use it for both the hub path and the local target path, so distinct names can never share a folder
     and plain ASCII names keep their current paths (no migration needed for existing users).
  3. Add a startup/plan-time check that two repos in one route never map to the same hub path; if they do,
     skip both with a clear message (the code already does this for the legacy layout in `mirror_path`).
  4. The error `dst: fatal: not a git repository` is useless; report which names collided.
- **Test:** local platform with `a b`, `a_b`, `ünï`, `öñï` → four distinct hub dirs and four distinct
  target dirs; second run is idempotent; existing ASCII names (`alpha`) keep the same path as before.

## 4. P2 — important, not urgent

### P2-1. Add a Content-Security-Policy and friends
- **File:** `gitsync/web.py` ~line 944 (`send_header("X-Frame-Options", "DENY")`).
- **Evidence:** responses carry only `X-Content-Type-Options` and `X-Frame-Options`.
- **Fix:** send on every response `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self';
  img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy: geolocation=(), camera=(), microphone=()`.
  The pages use an inline `style="color-scheme: dark"` attribute and a `data:` favicon; move the inline style
  into `style.css` and keep `img-src data:` for the favicon. Verify with Playwright that every page loads with
  zero CSP console errors.
- **Test:** unit test asserting the headers on `/`, a static file, and an API reply; Playwright test asserting no
  `securitypolicyviolation` events on all 9 pages.

### P2-2. `gitsync watch` reads the config once and dies on any error
- **File:** `gitsync/__main__.py`, `cmd_watch()` (~line 1959).
- **Evidence (code):** `cfg = C.load_config()` is outside the loop, and `SYNC.run_sync(cfg)` is not wrapped.
  A config change made in the web UI/TUI is never picked up, and one exception ends the watcher.
- **Fix:** reload the config at the top of every iteration; wrap the run in `try/except Exception` that logs
  and continues; honour the global pause flag; use the same run lock as the timer.
- **Test:** patch `run_sync` to raise once then succeed → loop continues; change config between iterations →
  second call sees the new config.

### P2-3. Local source: empty repos are silently skipped
- **File:** `gitsync/platforms.py` `LocalPlatform.list_repos()` (~line 951): requires `out.strip()` from
  `for-each-ref`, so an empty bare repo is not listed. `sync.mirror_is_valid()` explicitly says empty repos
  are legitimate. Reproduced: a source with `empty.git` reported `src: 2 repos` instead of 3.
- **Fix:** list a folder when `git rev-parse --is-bare-repository` is true; drop the ref-count condition.
  `sync_repo` already handles empty repos.
- **Test:** an empty bare repo is listed and mirrors to the target without error and without a re-clone each run.

### P2-4. Webhook secret file is briefly world-readable
- **File:** `gitsync/webhooks.py` `set_secret()` line 75: `open(path, "w")` then `os.chmod(…, 0o600)`.
  Between the two calls the file has the umask mode. `save_config()` in `config.py` already does this right.
- **Fix:** `fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)` then write via `os.fdopen`; write to a
  temp file and `os.replace` for atomicity. Apply the same pattern anywhere else a secret is written
  (`web.token`, `sessions.json`, `auth` hash).
- **Test:** with `umask 000`, the created file has mode `0600` at creation (check via a patched `os.chmod`
  that raises, to prove the mode was set at open time).

### P2-5. Login throttle is keyed by the proxy's IP when behind a reverse proxy
- **File:** `gitsync/web.py` `_client_ip()` line 985 returns `client_address[0]`.
  Behind nginx/Caddy/Cloudflare every user shares the proxy IP, so one attacker triggers the lockout for everyone,
  and the audit log shows only the proxy.
- **Fix:** add setting `trusted_proxies` (list of CIDRs, default empty). Only when the peer is inside it, use the
  right-most untrusted entry of `X-Forwarded-For`. Document it in the README reverse-proxy section.
- **Test:** untrusted peer sending `X-Forwarded-For` is ignored; trusted peer's header is honoured.

### P2-6. Dockerfile flattens the `docs/` folder
- **File:** `Dockerfile` line 26: `COPY README.md LICENSE docs quickinstall.sh ./`.
  A directory source copies its *contents*, so `docs/*` lands in `/opt/gitsync/` instead of `/opt/gitsync/docs/`.
- **Fix:** `COPY README.md LICENSE quickinstall.sh ./` and `COPY docs ./docs`. Build the image in CI and assert
  `/opt/gitsync/docs/GETTING-STARTED.md` exists.

### P2-7. Docker has no scheduler, so a default `docker compose up` never syncs on its own
- The changelog documents this, but a newcomer will not read it. Add an opt-in `scheduler` service to
  `docker-compose.yml` (same image, `command: ["watch", "--interval", "3600"]`, shared volumes) and mention it at the
  top of the Docker section. Show a "last sync" age and a red banner on the dashboard when nothing has run in
  2× the configured schedule.

### P2-8. Docs sprawl hurts the "simple for newbies" goal
- `README.md` is 1320 lines and `CHANGELOG.md` 1206 lines. Round plans (`docs/FIX-PLAN.md`, `FIX-PLAN-ROUND2.md`) and the
  stub `GITSYNC-REVIEW.md` sit in the repo.
- **Fix:** README ≤ 250 lines: 30-second pitch, 3-command quick start, a screenshot, links. Move the reference
  material into `docs/` (`CLI.md`, `ROUTES-AND-MODES.md`, `WEB-UI.md`, `DOCKER.md`, `SECURITY-MODEL.md`,
  `TROUBLESHOOTING.md`). Move finished plans to `docs/archive/` and delete `GITSYNC-REVIEW.md`.
  Add a "Supported platforms" table (Linux/macOS yes; Windows: state clearly whether supported — `askpass.bat`
  exists but the README never says). Add a short "Which mode do I want?" decision box (one-way safe vs full vs mesh).

## 5. P3 — polish and hygiene

- **P3-1. Dark theme only.** `style.css` hard-codes `color-scheme:dark`; there is no `prefers-color-scheme` rule.
  Add a light palette and a Settings toggle (Auto / Dark / Light) stored in `localStorage`; keep contrast ≥ 4.5:1.
- **P3-2. `subprocess.run(["rm","-rf",…])`** is used in 3 places in `sync.py`; use `shutil.rmtree(path, onerror=…)`
  so it is portable and cannot be fooled by an option-looking path.
- **P3-3. `ResourceWarning`s in the test run** (unclosed socket in the web tests, unclosed text file at exit).
  Run `python3 -W error::ResourceWarning -m unittest tests.test_logic` and close what leaks; add it to CI.
- **P3-4. Split the big modules** (behaviour-preserving, after all P1/P2 are green): `tui.py` → `gitsync/tui/` package
  by screen; `__main__.py` → `gitsync/cli/` one file per command group; `web.py` → `web/` (server, auth, routes).
  Keep `python3 -m gitsync` and every import path used by the tests working; run the full suite after each move.
- **P3-5. CI matrix.** GitHub workflow uses Python 3.11 only; GitLab uses 3.8. Add a matrix 3.8, 3.10, 3.12, 3.13 on
  GitHub (and keep a Windows/macOS smoke job if those are supported) so the "3.8+" promise is enforced.
- **P3-6. Web error copy.** The failed-collision message above and the generic `bad Host header (possible DNS
  rebinding)` should say what to do ("add this host to GITSYNC_ALLOWED_HOSTS").

## 6. Feature roadmap (suggested order)

**Must-have for a 10/10 product**
1. First-run web onboarding wizard (add platform → test token → pick repos → dry-run → first sync) — the CLI wizard exists; mirror it in the browser.
2. "Last successful sync" and "next run" always visible, with a stale-sync alert (ties to P2-7).
3. Notification on failure by default once a channel is configured, with a per-route "only on failure" switch (Discord/Telegram exist; add email/SMTP and generic webhook/ntfy).
4. Restore preview: show exactly what `restore` will overwrite before it runs.

**Strong improvements**
5. Per-repo include/exclude and "skip forks / archived / empty" toggles in the Routes UI (CLI has include files).
6. Git LFS and submodule awareness: detect LFS objects and warn or mirror them (`git lfs fetch --all` when installed).
7. Bandwidth/disk guard: warn before a first sync if the hub would exceed free disk (a `diskutil` module already exists).
8. Metrics endpoint `/metrics` (Prometheus text format: last run, ok/failed counts, duration, hub size).
9. Config export/import with secrets stripped, for backups and moving machines.
10. Retry scoping by `(route, repo)` pairs rather than repo name (still open from round 2).

**Nice to have**
11. Integrity check (`gitsync verify`): compare `git rev-parse` of every ref on hub vs targets and report drift.
12. Signed releases and a Homebrew/AUR/Debian package.
13. Plugin hook for pre/post-sync commands.

## 7. Design polish list (web UI)

Positives to keep: sidebar layout, skip link, focus trap in modals, `aria-live` regions, 32px touch targets,
no horizontal overflow at 375px, consistent button/link semantics.

Do these:
1. Light theme (P3-1) and respect `prefers-reduced-motion` on every animation (currently one rule exists).
2. Empty states with a single clear action on every page (e.g. Repos with no routes → "Create your first route").
3. Make the destructive flows visually distinct: red outline button, typed confirmation (already exists for repo
   deletion — reuse the same component for `restore` and mirror deletion).
4. Show a status chip + relative time ("12 min ago") in the top bar on every page, not only the dashboard.
5. Keyboard shortcuts overlay (`?`) in the web UI to match the TUI.
6. One-line inline help (`?` popover) next to each setting in Settings/Routes explaining the effect in plain words,
   especially `mode: full` (deletes extra refs on the target) vs `safe`.
7. Run an automated accessibility pass (axe-core in the Playwright job) and fix every serious/critical finding.

## 8. What this audit did NOT cover (be honest about it)

- The curses TUI was not driven in a real terminal (needs tmux/TTY). The existing `TestTUISmoke` passed.
- No real GitHub/GitLab/Forgejo/Bitbucket account was contacted; all sync tests used local folders.
- Python 3.8–3.11 were not run (only 3.12.3). Windows and macOS were not tested.
- The 5 Playwright tests and the browser UI were not executed (no browser in the audit sandbox).
- Mesh mode was reviewed through the existing unit tests, not stress-tested with concurrent pushes.
The agent should cover these gaps where its environment allows and report anything it cannot run.

## 9. Definition of done

- [ ] P1-1: session cookie is an opaque, revocable, expiring id; token never in a cookie.
- [ ] P1-2: `gitsync update` uses a private temp file, installs a pinned tagged release, verifies a SHA-256.
- [ ] P1-3: no two repo names can share a hub or target folder; Unicode names survive; ASCII paths unchanged.
- [ ] P2-1 … P2-8 each have a test or a CI assertion, and the docs match the behaviour.
- [ ] `python3 -m unittest tests.test_logic` and `python3 -m gitsync ci` pass on Python 3.8 and 3.12.
- [ ] Playwright suite passes with zero CSP violations and zero serious axe findings.
- [ ] CHANGELOG entry for 2.1.4 lists exactly what changed and **does not** say "10/10".
