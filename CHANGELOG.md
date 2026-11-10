# Changelog

All notable changes to gitsync are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
