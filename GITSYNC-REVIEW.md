# gitsync review and fix plan

Reviewed: `https://github.com/SeaXen/gitsync` branch `main`, package version **1.6.2** (2026-11-09).
Every file under `gitsync/`, `bin/`, `install.sh`, `quickinstall.sh`, `.github/workflows/ci.yml`, `.forgejo/workflows/ci.yml`, `.gitlab-ci.yml`, and the docs was read. The bugs below were confirmed by reading the code and, where marked **reproduced**, by executing the function.

This file is the only spec. Implement it in order. Do not invent extra features, do not rename commands, do not add third-party dependencies.

---

## Verdict

**Not 10/10. Not ready to hand to a newbie as "install and forget."**

The idea is sound: local bare hub, then push out; stdlib only; a real TUI; dry-run that names refs `git push --mirror` would delete. A careful user can mirror **GitHub (token or `gh`) → GitLab (personal access token) → a local folder** if they install as root and accept that the default mode force-pushes and prunes.

Several advertised paths are broken or dangerous:

- The one-click install does not survive a normal non-root user, and the scheduler it writes does not read that user's config.
- Bitbucket crashes on every call.
- Forgejo and Gitea private git operations send **no token**.
- `gitsync compare` and `gitsync fix` compare a remote to itself.
- The wizard's GitLab "leave the token blank and use `glab`" path does not exist.
- Default sync is a destructive mirror, and the wizard never offers the two-way mode the README leads with.

| Sector | Score | Why it is not 10 |
|---|---|---|
| Correctness | 4/10 | Bitbucket dead. Forgejo/Gitea git auth missing. compare/fix wrong. Schedule bookkeeping lies. Hub keyed by short name. |
| Install and scheduling | 3/10 | Non-root install aborts. Systemd unit runs as root against `/root`. `--yes` is rejected. Wizard schedule is ignored. |
| Safety of sync | 4/10 | Default `mode=full` prunes. "safe" still force-pushes. Mesh default overwrites the older side. `prepare_push` turns on force-push on protected branches. |
| Security of secrets | 5/10 | Config file is `0600` and webhook HMAC is real. Tokens are then copied into bare-repo configs and can land in logs. `doctor --fix` punches a hole in the run lock. |
| Newbie experience | 5/10 | Wizard, TUI, and getting-started exist, but they contradict the code (`glab`, prompt default, mesh, schedule). |
| Tests | 3/10 | ~137 string asserts, no install test, no adapter test, three drifting copies. Every P0 bug below is untested. |
| Docs | 6/10 | Broad and mostly honest about scopes. Several sentences describe code that is not there. |
| Structure | 6/10 | Modules are the right split. `tui.py` (2855) and `__main__.py` (1839) are too big, and version constants disagree. |

**Do not start the feature section until every P0 item is done and its test passes.**

---

## Rules for the implementing agent

1. Python 3.8+ standard library only. No `requests`, no `click`, no `rich`, no `textual`.
2. Keep the public commands and config keys that already work. Add keys only when a P0/P1 item says so.
3. Config path stays `~/.config/gitsync/config.json` (`GITSYNC_CONFIG_DIR`). Data stays `~/.local/share/gitsync` (`GITSYNC_DATA_DIR`).
4. Write config, the webhook secret, the log, and `records.json` as mode `0600`. Create parent dirs `0700`.
5. Never put a token, webhook URL, or `user:token@host` into a log line, a record, or a `ps`-visible argument if a helper or `GIT_ASKPASS` can avoid it. Where a URL must contain a token, redact it before logging (`***`).
6. Do not weaken branch protection unless the user opts in. See S3.
7. One test file, `tests/test_logic.py`, runnable as `python3 -m unittest tests.test_logic` with `PYTHONPATH=.` and **no network**. Delete the duplicated assertion blobs after that file covers them (see T1).
8. After code changes, run:
   - `python3 -m py_compile gitsync/*.py`
   - `bash -n install.sh quickinstall.sh`
   - `python3 -m unittest tests.test_logic`
   - `python3 -m gitsync ci` (offline; must pass)
9. Update `CHANGELOG.md` under a new `1.6.3` (or `1.7.0` if you ship the P1 safety defaults) and set **one** version constant. See C1.
10. If a fix would change on-disk mirror layout, keep reading old `name.git` hubs and document the move. Do not delete a user's mirrors as part of an upgrade.

---

## What is already good — do not "clean this up"

- Stdlib-only design and the `bin/gitsync` launcher (`PYTHONPATH` + `python3 -m gitsync`).
- Atomic config write (`*.tmp` + `os.replace`) and `chmod 0600` on the config file (`gitsync/config.py` `save_config`).
- Webhook listener: default bind `127.0.0.1`, 1 MiB body cap, HMAC-SHA256 for GitHub, `hmac.compare_digest`, empty secret rejected (`gitsync/webhooks.py`).
- Single-run `fcntl` lock in `run_sync` (the lock is right; `doctor --fix` breaks it — S4).
- GitLab project-path sanitiser `gitlab_path` and readable API errors `_api_error`.
- Dry-run text `WOULD DELETE` for `mode=full`, and the offline two-local-folder smoke test inside `cmd_ci`.
- Route pause / resume, include/exclude globs, per-route schedule *data model* (`route_schedule`, `finest_schedule`).
- Mesh `conflict=skip` (the policy works; the default and the wizard do not expose it safely).
- TUI token fields that use `secret=True`.
- HTTP retry on 429/502/503 (`platforms._http`).
- `LocalPlatform` as a real source and target. Keep that working; it is the only fully offline sync.

---

# P0 — broken or data-losing. Fix all of these.

Each item has the current behavior, the required behavior, and a test that must fail before the fix and pass after.

## P0-1 Installer aborts or installs a scheduler that cannot see the user's config

**Files:** `quickinstall.sh`, `install.sh`, `gitsync/scheduler.py`, `gitsync/tui.py` (`apply_schedule`)

**Current**

- `quickinstall.sh` sets `SUDO` and never uses it. It then runs `bash install.sh` as the normal user.
- `install.sh` writes `/etc/systemd/system/gitsync.service` with a bare `cat >` (no sudo). A non-root one-click install dies there. `set -euo pipefail` stops the wizard too.
- `quickinstall.sh` appends `--yes` when `--yes` was passed. `install.sh` treats `--yes` as an unknown option and exits 1. **Reproduced.**
- The unit is a **system** service with no `User=`. In a system manager, `%h` is `/root`. The service runs as root and reads `/root/.config/gitsync`, while `gitsync setup` writes the invoking user's home. The timer never sees the tokens.
- `install.sh` `ExecStart` is `gitsync sync --quiet` (no `--scheduled`). `scheduler.py` `SERVICE_TEMPLATE` uses `sync --scheduled --quiet`. The two install paths disagree.
- `install.sh` cron map and the systemd calendar one-liner only know `hourly|6h|12h|daily`. `--schedule weekly` raises `KeyError`. The TUI schedule menu also omits `weekly` (`tui.py` `schedule_screen`) while `route-schedule` accepts it.
- With `--user`, `install.sh` still tries `sudo mkdir` / `sudo cp` first. If sudo works, `~/.local/share/gitsync` is owned by root and the later `chmod +x` fails.

**Required**

- Scheduler always runs as the user who owns the config.
  - Preferred: a **user** unit in `~/.config/systemd/user/gitsync.service` and `gitsync.timer`, enabled with `systemctl --user`.
  - `ExecStart` must be `{bin} sync --scheduled --quiet`.
  - Do not set `HOME=%h` on a system unit. A user unit already has the right home.
  - If `systemctl --user` is unavailable (no user session), fall back to **cron for that user** (`crontab -l`), not a root system unit.
  - Print one line if the timer will not run while logged out: `loginctl enable-linger $USER` (do not run linger unless the user passes `--linger`).
- `install.sh` accepts `--yes` (idempotent, no extra prompt; today it has no prompt, so this just must not error).
- `quickinstall.sh` either passes flags `install.sh` understands or stops passing `--yes`.
- `--user` never calls sudo. System prefix (`/opt`, `/usr/local/bin`) uses sudo only when the user is not root, and sudo covers **both** the copy and the symlink. Do not install a system unit at all.
- One function builds the unit text. `install.sh` should call `python3 -m gitsync install` after the binary is on `PATH`, instead of a second hand-written unit. Delete the duplicated unit text in `install.sh`.
- Allowlist schedules: `hourly`, `6h`, `12h`, `daily`, `weekly`, `off`. Unknown value: exit 2 with the list. Include `weekly` in the TUI schedule menu.
- `gitsync install` (no `--schedule`) must install the timer for `finest_schedule(cfg)`, not the global setting alone. A route on `hourly` and a global of `daily` must fire hourly; `sync --scheduled` skips routes that are not due.
- Changing the schedule in the wizard or the TUI must reinstall the timer (`apply_schedule` already calls `install_systemd`, but it passes the global key and ignores a finer route — use `finest_schedule`).
- Order in `quickinstall.sh`: install binary → wizard → **then** `gitsync install` so the schedule chosen in the wizard is the one that gets installed. Today the timer is installed first, at the default `hourly`, and the wizard never updates it.

**Tests**

- `bash install.sh --yes --schedule off --user` with `HOME` in a temp dir exits 0 and does not mention "unknown option".
- Rendered service file contains `sync --scheduled` and does not contain `WantedBy=multi-user.target`.
- `finest_schedule` unit test already exists; add one that `install` selects it. Pure function: extract `timer_calendar(cfg) -> str | None` and test it without systemctl.

## P0-2 Bitbucket adapter crashes on every call

**File:** `gitsync/platforms.py` `BitbucketPlatform`

**Current (reproduced):** `_api` returns the 2-tuple from `_http`, but `user`, `test`, `list_repos`, and `ensure_repo` unpack three values. `ValueError: not enough values to unpack (expected 3, got 2)`.

Also, even after the unpack is fixed:

- `repo_url` returns `https://api.bitbucket.org/{name}.git` (API host, no workspace, no token).
- `list_repos` / `ensure_repo` clone links are not rewritten to include credentials, so private git fetch/push still fails.
- `fork` is hardcoded `False`.
- Webhook auth is declared as a shared token (`webhooks.AUTH_STYLE["bitbucket"] = "token"`). Bitbucket signs with `X-Hub-Signature` HMAC-SHA256.

**Required**

- Unpack `(status, body)` everywhere.
- Git URL for Bitbucket Cloud: `https://x-bitbucket-api-token-auth:{token}@bitbucket.org/{workspace}/{slug}.git` when a token is set and protocol is not ssh. Slug stays the existing lowercased hyphenated form.
- `repo_url` must use that same URL and must not create the repo.
- Read `parent` (or the fork link) and set `fork` accordingly.
- Webhook: verify `X-Hub-Signature` the same way as GitHub's sha256 header (Bitbucket's header value is `sha256=<hex>`).
- `user()` hitting `/2.0/user` must not be required for `test()`. `test()` already has a workspace listing path; keep that, minus the bad unpack.

**Test:** construct `BitbucketPlatform` with no network. Monkeypatch `_http` to return `(200, {"username": "a", "values": []})`. `user()` and `test()` must not raise. `repo_url({"name": "My Repo"})` equals the token URL above with slug `my-repo` and workspace from `owner`.

## P0-3 Forgejo and Gitea git URLs do not carry the token

**File:** `gitsync/platforms.py` `ForgejoPlatform._push_url`

**Current (reproduced):** with `token=SECRET`, `_push_url("me", "repo")` is `https://codeberg.org/me/repo.git`. `list_repos` and `ensure_repo` use that URL. Private clone and push fail (stdin is `/dev/null`, so git cannot prompt). GitHub and GitLab do embed the token. `SECURITY.md` says every platform does.

**Required**

- HTTPS: `https://{user}:{token}@{host}/{owner}/{repo}.git`.
  - User is `cfg["user"]` if set, otherwise the platform login from `user()` when already known, otherwise the literal username `oauth2` is wrong for Gitea — use the account login. Cache it on the object after the first successful `/user` call so a sync does not call `/user` once per repo.
  - Percent-encode the token (`urllib.parse.quote(token, safe="")`) so a token with `@` or `/` cannot break the URL.
- SSH: keep the current `ssh_url` behavior. Do not embed the token.
- `repo_url` and `list_repos` `clone_url` must use the same helper.

**Test:** token `a/b` produces `https://me:a%2Fb@codeberg.org/me/repo.git`. SSH config still returns `ssh://git@host:port/owner/repo.git`.

## P0-4 `compare` and `fix` do not look at the target

**File:** `gitsync/__main__.py` `_head_of`, `cmd_compare`, `cmd_fix`

**Current:** for any non-local platform, `_head_of` uses `repo["clone_url"]` if present. `list_repos()` on the **source** always sets `clone_url` to the source. Every target therefore `ls-remote`s the source URL. Compare prints a false "all in sync". `fix` never repairs remote drift. If `clone_url` is missing, `_head_of` calls `ensure_repo`, so a "read-only" compare can create repos.

`cmd_fix` also:

- ignores `route["mode"]` and uses only the global mode
- ignores `direction=mesh` and runs the one-way engine, which can overwrite a newer peer

**Required**

- `_head_of(plat, repo)` must call `plat.repo_url(repo)` for non-local platforms. Never `ensure_repo`. Never the source clone URL.
- `compare` stays read-only. Exit 1 when any target HEAD differs or is unreachable.
- `fix` on a mesh route calls `mesh_sync_repo` with that route's conflict policy. On a one-way route it uses `route["mode"] or global mode`, and only for repos that actually differ.
- `mirror` counting: `partial` is not `ok`. Print it separately. Exit 1 if any target failed. (`cmd_mirror` currently treats `partial` as success.)

**Test:** fake two platforms whose `repo_url` returns different paths. `_head_of` must request the target path (inject `P.run` and assert the URL). A unit test that `repo.get("clone_url")` is ignored when `repo_url` exists.

## P0-5 Hub identity is the short repo name

**File:** `gitsync/sync.py` `mirror_path`; every adapter's `list_repos`

**Current:** `~/.local/share/gitsync/mirrors/{name}.git`. GitLab `list_repos` stores `p["name"]`, not `path_with_namespace`. Two projects named `docs` in different groups share one mirror and overwrite each other. The same collision happens for two routes whose sources both have `app`.

**Required**

- Each listed repo has a stable `id` string:
  - GitHub: `full_name` (`owner/name`)
  - GitLab: `path_with_namespace`
  - Forgejo/Gitea: `owner/name`
  - Bitbucket: `workspace/slug`
  - Local: the directory name without `.git`
- Hub path is `mirrors/{platform_type}/{fs_safe(id)}.git` where `/` in the id becomes `__`. Example: `mirrors/gitlab/group__docs.git`.
- **Migration:** if `{id}.git` is missing and exactly one legacy `{name}.git` exists, use it (do not delete). If two live repos would claim the same legacy directory, log an error and skip both rather than mixing history. Put this in `mirror_path(repo, platform_name)`.
- Target create/push still uses the repo name (or GitLab `gitlab_path`), not the hub folder name.
- `diskutil.orphans` compares against this `id`, not the short name.

**Test:** two repos both named `docs` with ids `a/docs` and `b/docs` produce two different paths. Legacy lookup: only `docs.git` exists and only one repo wants it → that path is returned.

## P0-6 A failed run, and a dry-run, consume the schedule slot

**File:** `gitsync/sync.py` `run_sync` (the `mark_route_run(route)` call sits **before** listing repos and before the dry-run branch)

**Current:** `mark_route_run` writes "last run = now" even when:

- the command is `--dry-run`
- `list_repos` throws
- every repo fails

The next `sync --scheduled` then skips the route until the interval elapses. A newbie who previews with `--dry-run` silently delays the real backup.

**Required**

- Call `mark_route_run` only after a non-dry-run attempt that listed repos (including "0 repos", which is a real run). Do not mark when listing failed or when the route was skipped as not due / paused.
- Dry-run must not touch `route_state.json`.
- Exit code of `gitsync sync`: `0` only when `failed == 0` and `partial == 0`. Dry-run exits `1` if any repo's verdict is `error`.

**Test:** temp `GITSYNC_DATA_DIR`. Call the mark helper only through `run_sync` with a local source that raises (bad path). `last_route_run` stays `None`. A second test: dry-run on a valid local route leaves `last_route_run` as `None`.

## P0-7 "safe" still force-pushes; default mode deletes target branches

**File:** `gitsync/sync.py` `_push_target`, `PUSH_REFS`; wizard default in `gitsync/wizard.py`

**Current**

- `mode=full` (the default, and the wizard default because "Use safe mode?" defaults to No) runs `git push --mirror`, which **deletes** refs the target has and the source does not.
- `mode=safe` still runs `git push --force` with `+refs/heads/*:refs/heads/*`. A newer commit that exists only on the target is overwritten. The name "safe" is false.
- Non-fast-forward is the common case when a newbie edits a repo on the destination.
- Forgejo/Gitea always use the force refspec, even in `full`, but they do not prune. GitLab/GitHub/Bitbucket in `full` also push `refs/pull/*` and other mirror namespaces cloned from GitHub, which those hosts reject or store as junk.

**Required**

- Push only `refs/heads/*` and `refs/tags/*` to every non-local host. Do not use `--mirror` against GitHub, GitLab, Forgejo, Gitea, Bitbucket, or generic. `--mirror` remains allowed only when the target type is `local`.
- `mode=safe` (make this the **default for newly written configs**): push without `+` and without `--force`. If the remote rejects a non-fast-forward, record `partial` with the text `refused non-fast-forward on {target}` and continue. Do not retry with force.
- `mode=full`: force-update heads and tags, and delete target heads/tags that the source does not have (`--prune` on those two namespaces only, or an explicit delete refspec). Dry-run must keep reporting `WOULD DELETE`.
- Existing config files that already contain `"mode": "full"` stay `full`. Only `DEFAULT_CONFIG` and the wizard default change to `safe`. The wizard question becomes: "Exact mirror? This deletes branches on the destination that are not on the source (y/N)". Default **No**.
- One sentence in the sync log at start: `mode=safe (will not delete or force-push)` or `mode=full (force-push, prunes heads and tags on non-local targets)`.

**Test:** extend the offline local-folder smoke test. Source and dest both have `main`; dest also has `only-on-target`. After `mode=safe` real sync (not just dry-run), `only-on-target` still exists on the dest. After `mode=full` with a **local** target, it does not. A non-local fake transport is unnecessary if `_push_target` builds the argv via a function `push_args(mode, ptype, url) -> list` that the test asserts:

- `safe` + `gitlab` → no `--force`, no `+`, no `--mirror`
- `full` + `gitlab` → force heads/tags, no `--mirror`
- `full` + `local` → `--mirror`

## P0-8 Mesh "newest wins" is silent data loss, and it is the default

**File:** `gitsync/sync.py` `mesh_sync_repo`, `_choose`; wizard has no mesh step

**Current:** default `mesh_conflict` is `newest`. Committer date wins, then `git push --force`. A reset or rebase with an older committer date never propagates. Two people committing on two forges: the older commit disappears. The wizard cannot create a mesh route at all, so the README's "edit on GitLab, see it on GitHub" path is not the path a new user gets. They get one-way `full` instead, which deletes the GitLab-only work (see P0-7).

**Required**

- Default `mesh_conflict` for new configs: `skip`.
- When refs are skipped, the run status is `partial`, not `ok`, and the log lists every skipped ref (cap the line at 20 names).
- Document in the wizard, in one paragraph of plain language, the two choices. Wizard step 3 offers:
  1. One-way: read from SOURCE, write to TARGETS (default).
  2. Two-way: newest commit is copied everywhere (warn: "a force-push or an older commit can be overwritten").
  3. Two-way, report only: leave conflicts alone (`skip`).
- Do not change an existing config's `mesh_conflict` if it is already set.

**Test:** `_choose` with two SHAs and `conflict="skip"` returns the ref in the skipped list (already tested). Add: `mesh_conflict` default in `DEFAULT_CONFIG` is `skip`.

## P0-9 `glab` is documented and not implemented; wizard token entry is visible and destructive

**Files:** `gitsync/wizard.py`, `gitsync/permissions.py`, `docs/GETTING-STARTED.md`, `docs/PERMISSIONS.md`, `README.md`, `SECURITY.md`

**Current**

- GitLab text says leave the token blank and use `glab auth login`. `GitLabPlatform` never calls `glab`. A blank token fails every API call.
- Wizard `ask()` uses `input()` / a raw tty read. Tokens are shown on screen and stay in scrollback. The TUI already hides them.
- `run_wizard` does `cfg["platforms"] = {}` and replaces routes. Re-running setup deletes every account, with no "this will replace your current setup" confirmation.
- Sample transcript in `docs/GETTING-STARTED.md` shows `(Y/n)` for the GitHub token question. The code defaults to No, so the prompt is `(y/N)`. The prose under it happens to say Enter means No. The sample is wrong.
- The wizard platform list omits Bitbucket and generic. The TUI add-account screen does not. Bring the wizard up to the same type list.
- "Two Forgejo servers" cannot be entered as `forgejo,forgejo`: the dict key collides and one is dropped. Ask for a **name** when the type is repeated, or accept `name=type` (`work=forgejo, home=forgejo`).

**Required**

- Either implement GitLab `auth=glab` for real (`glab api` for HTTP, and `glab auth status` for git credentials via `glab auth git-credential` as `GIT_ASKPASS` / a one-shot credential helper) **or** delete every `glab` claim from code, README, SECURITY.md, PERMISSIONS.md, and the getting-started guide. Preferred: **delete the claim** in this release. Do not pretend. A follow-up feature can add it (F3).
- Wizard secrets go through `getpass.getpass` when a tty exists. Empty token on GitLab/Forgejo/Gitea/Bitbucket is an error that re-prompts, not a saved blank, unless type is `github` and they chose the `gh` CLI.
- Before wiping platforms, if `config.json` already has platforms, ask "Replace existing accounts? (y/N)". Default No: exit without writing.
- Fix the getting-started transcript to `(y/N)` and remove the `glab` sentence.

**Test:** `format_guide("gitlab")` does not contain `glab` after the doc fix. Wizard wipe is a function `prepare_cfg(existing, replace: bool)` that keeps platforms when `replace` is false.

## P0-10 Tokens leak out of the 0600 config file

**Files:** `gitsync/sync.py` (remote URL stored in the bare repo), `gitsync/platforms.py` (token embedded in URL), log and records writers

**Current:** `git remote set-url origin <url-with-token>` stores the token in `mirrors/*.git/config`, which is created with the process umask (usually world-readable). `gitsync.log` and `records.json` are opened the same way. Git error text can include the remote URL. `doctor` runs `bash -c "df -h %s"` with `DATA_DIR` interpolated — a crafted `GITSYNC_DATA_DIR` is shell injection. Low on a personal machine, still wrong.

**Required**

- After creating a mirror, `chmod` the hub git `config` to `0600`. Also `chmod 0600` the log and `records.json` on every write.
- Redact `https://<user>:<secret>@` and `x-access-token:<secret>@` to `https://***@` / `x-access-token:***@` inside `_short_error` before it is logged or stored.
- `doctor` disk line: `subprocess.run(["df", "-h", path])` with a list, no shell.
- Do not print tokens in `gitsync platforms` output. `platforms edit --token` is fine (it is the user's argv; mention in SECURITY.md that shell history captures it, and that `gitsync platforms edit NAME` with a prompt is preferable). Optional in this item: if `--token` is omitted and stdin is a tty, prompt with `getpass`.

**Test:** `_short_error("fatal: https://oauth2:glpat-SECRET@gitlab.com/x")` does not contain `glpat-SECRET`.

## P0-11 `doctor --fix` lets two syncs run, and orphan prune can delete live mirrors

**Files:** `gitsync/__main__.py` `_doctor_fix`; `gitsync/diskutil.py` `orphans`; `cmd_disk`

**Current**

- A live sync holds `fcntl` on `sync.lock` but the file can be unlinked. `_doctor_fix` **always** deletes `sync.lock` if the path exists, including during a sync. The next sync creates a new inode and locks that. Two syncs then push the same repo.
- `gitsync disk --orphans` compares the hub to `list_repos()` of **the first route's source only**, with no include/exclude. Mirrors that belong to another route, or that were excluded by a glob, look orphaned. `--prune-orphans` deletes them.

**Required**

- Never unlink `sync.lock`. Stale-lock detection: try `LOCK_EX | LOCK_NB` on the existing file. If the lock is acquired, the previous process is dead — then it is safe to continue (the new sync will lock it anyway). `doctor --fix` should only remove broken mirror directories (empty / failed clones), not the lock.
- Orphans are hub directories whose `id` is not in the **union of every enabled route's filtered repo list**. If listing any source fails, abort prune (print the error, delete nothing).
- `--prune-orphans` without `--orphans` stays a no-op (already true). Keep it.
- `gc` must take the same sync lock (shared is not enough — use the exclusive lock, and if it cannot, exit 2 with "a sync is running").

**Test:** `_doctor_fix` source no longer contains `sync.lock`. Orphan helper given live ids `["a"]` and mirrors `a`, `b` returns only `b`.

## P0-12 GitHub token-health check crashes when the token is rejected

**File:** `gitsync/platforms.py` `_http` and `GitHubPlatform.token_info`

**Current (reproduced):** `want_headers=True` still returns a 2-tuple on `HTTPError`. `token_info` unpacks three values and raises. `tokencheck` turns that into "check failed" instead of "token rejected".

**Required:** on HTTP error with `want_headers=True`, return `(code, body, headers)`. `token_info` maps 401/403 to `{has_token: True, error: "token rejected"}`.

**Test:** monkeypatch is unnecessary if you call the error branch via a local `HTTPServer` returning 401, the same way this review reproduced it.

## P0-13 Notifications and a few CLI flags do not do what they say

**Files:** `gitsync/notify.py` `notify_run`; `gitsync/__main__.py` `cmd_notify`, `cmd_ci`

**Current**

- `enabled()` is true for Telegram-only config. `notify_run` returns immediately when the Discord URL is empty, so Telegram never gets sync results. **Reproduced** (`_settings` discord url is `""`). `notify_test` does send Telegram. Users think alerts work because the test passed.
- `gitsync notify --set-url` is parsed and ignored. The command only prints an example.
- `gitsync ci --install-hook` is parsed and ignored.
- `gitsync install --schedule` choices omit `weekly`.
- `cmd_ci` captures unit-test output with this expression:

```python
rc, out, err = subprocess.run(...).returncode, "", ""
```

  Operator precedence assigns `out` and `err` to `""`. A failed unit check reports "unit checks failed" with no assertion text.
- `NT.send` returns `True` when nothing is configured. The unit test asserts that. Change the function to return `False` when it sent nothing, and update the test. Do not keep a test that locks in the bug.

**Required**

- `notify_run` uses the same "Discord or Telegram" check as `notify_test`. Honor `on` for both.
- `--set-url` writes `settings.notify.discord_webhook` and saves. Reject URLs that do not start with `https://discord.com/api/webhooks/` or `https://discordapp.com/api/webhooks/`.
- Implement `--install-hook` as a pre-push hook that runs `python3 -m gitsync ci`, or remove the flag and its help. Prefer implement: write `.git/hooks/pre-push` only when that file does not already exist; print the path.
- Fix the `subprocess.run` unpacking so stderr is shown.
- `install --schedule` choices equal `C.SCHEDULES` keys.

**Test:** `notify_run` with only telegram and `on=always` calls the telegram sender (monkeypatch `telegram_send` to record the call) and does not return before that. `--set-url` round-trip via `cmd_notify` against a temp config dir.

## P0-14 GitHub listing is capped and ignores the documented org scope

**File:** `gitsync/platforms.py` `GitHubPlatform.list_repos`

**Current**

- `gh repo list --limit 500` silently drops repos beyond 500.
- REST uses `affiliation=owner` only. `docs/PERMISSIONS.md` says `read:org` is how you mirror organization repos. Those repos are never listed. GitHub Enterprise Server is also broken: `_api` always calls `https://api.github.com`, ignoring `cfg["url"]`.

**Required**

- Paginate until a short page. For `gh`, use `--limit 1000` in a loop with `--source` is not enough — shell out to `gh api user/repos?per_page=100&page=N` or follow Link headers with `_http`. Do not stop at 500.
- Add config `github.affiliation` default `"owner"`. Document the other legal values (`owner,organization_member`, `owner,collaborator,organization_member`). The permissions doc must say org mirroring is **off until this is set**, not "add the scope and it works".
- If `url` host is not `github.com`, API base is `{url}/api/v3` (GitHub Enterprise). `github.com` stays `https://api.github.com`.

**Test:** a fake paginator that returns 100, then 100, then 1 item is fully consumed (inject `_api`). GHE URL `https://git.example.com` produces API base `https://git.example.com/api/v3`.

---

# P1 — do these immediately after P0. They are what make the tool honest for a new user.

## P1-1 First sync must be a dry-run unless the user says go

`quickinstall.sh` asks "Run your first sync now?" and then runs a real sync. Change the default answer to No, and run `gitsync sync --dry-run` first always, printing the plan. Only run the real sync if they answer yes **after** seeing the plan.

## P1-2 Stop changing branch protection behind the user's back

`GitLabPlatform.prepare_push` and `ForgejoPlatform.prepare_push` PATCH `allow_force_push` / recreate protection. That is a lasting security change on the destination, done to make a mirror succeed.

**Required:** do not call this unless `settings.relax_protection` is true (default **false**). On a protected-branch error, log: `push rejected by branch protection on {target}. gitsync will not change it. Set settings.relax_protection=true only if you mean to.`

Delete the automatic retry in `_push_target` when the flag is false.

## P1-3 Webhooks and mesh

`webhooks._run_sync` only selects routes whose `source` equals the platform that sent the event. A mesh route therefore ignores pushes from every peer except the first.

**Required:** also select mesh routes where the platform is the source **or** a target. Pass `--repos` the same way. Reload config from disk on each request (it is loaded once today, so new routes never appear until restart).

## P1-4 GitLab namespace

Wizard and `ensure_repo` take `/namespaces` item `[0]`, which may be a group, not the user. Use the namespace whose path equals `user()` (the user's personal namespace) unless `ns_id` or `owner` is set. Do not guess.

`star()` for GitLab double-encodes the path (`quote` of a string that already contains `%2F` → `%252F`). Star with `quote(path, safe="")` once. **Test:** the path segment for `group/sub/repo` is `group%2Fsub%2Frepo`.

GitHub `starred()` treats HTTP status `200` as failure because it checks `rc != 0`. Check `rc == 200`. **Test** with a fake `_api` returning `(200, [{"full_name": "a/b"}])` → `[("a/b", "")]`.

## P1-5 Dry-run must compare every branch, not only HEAD

`_plan_repo`: if HEAD matches, it reports "up to date" and skips branch drift. Compare `refs/heads/*` and `refs/tags/*` even when HEAD is equal. Keep the existing `WOULD DELETE` block.

## P1-6 Platform CLI gaps

- `platforms edit` accepts `--protocol` and `--ssh-port` (add already does).
- `platforms add` for `generic` accepts `--url-template` and `--user`.
- `uninstall` also removes `~/.local/bin/gitsync` and the user systemd unit, and calls `uninstall_webhooks`. Do not delete config or mirrors (current behavior, keep it). If removing a root-owned symlink fails, print the command to run, exit 1. Do not traceback.
- `gitsync update` must pass `--user` when the running binary lives under `$HOME`, and must not re-run setup (`--no-setup` is already there). Reinstall the user timer at the end via `gitsync install`.

## P1-7 Version and notify display

- `gitsync/__init__.py` `__version__` is `1.6.2`. `gitsync/config.py` `VERSION` is `1.0.0`. Nothing should read `config.VERSION`. Delete it or set it from `__version__`. `--version`, the log user-agent in `notify.py` (`gitsync/1.0`), and the changelog must match.
- TUI options screen reads notify with `C.setting(cfg, "notify:on")`, but `setting()` does not split on `:`. The menu always shows `failure`. Read the nested dict the same way `cycle()` writes it.

## P1-8 Empty repos and `mirror_is_valid`

`mirror_is_valid` requires at least one ref. An empty source is deleted and re-cloned every run. Treat a bare repo with a valid `HEAD` file (or `git rev-parse --is-bare-repository`) as valid even with zero refs. Do not `rm -rf` it.

## P1-9 `watch` minimum interval

`--interval` below 30 is almost always an accident (`0` busy-loops `run_sync`). Clamp to 30 and print the clamp. Document it.

## P1-10 Tests live in one place

See T1 below. This is P1 because the P0 tests should be born inside that file, not pasted into a fourth copy.

---

# P2 — polish after the above is green. No new product surface.

- Split `tui.py` only along the existing screen functions (`tui_dashboard.py`, `tui_routes.py`, `tui_accounts.py`) if you are already editing it. Do not rewrite the draw path. Keep off-screen `noutrefresh` + one `doupdate` (see `CONTRIBUTING.md`).
- `import json` at the top of `sync.py`, not at the bottom.
- `cmd_routes` mesh heuristic (`comma in the second argument means mesh`) surprises people. Keep it, but when it triggers, print `interpreted as a two-way route`.
- Redact tokens in `git remote -v` inside the hub: store the authenticated URL in the remote, but that is inherent; P0-10's chmod is the fix. Do not invent a credential helper framework in P2.
- `CONTRIBUTING.md` says CI is "syntax plus a smoke test". Update it to the unittest command.
- README command table: delete or correct any row that P0 changed (glab, compare, safe mode, default mesh).
- `gitsync platforms test` on a disabled platform should say `disabled`, not fail the connection.
- Log rotation: cap `gitsync.log` at 2 MB by renaming to `gitsync.log.1` (one backup). Records already cap at 200 runs.
- Shell injection leftovers: grep for `bash -c` and `%s` inside shell strings. The doctor `df` line is the one found; fix any others the same way.
- GitHub `gh` auth path: `list_repos` currently stores the public `url` with no credential. When `auth=gh` and there is no token, set `GIT_CONFIG_COUNT` / `credential.helper` to `gh auth git-credential` for those subprocesses only, or run `gh auth setup-git` is too global — do **not** modify the user's gitconfig. Pass the helper in the command environment.

---

# Tests (T1) — do this as part of P0, not after

Create `tests/test_logic.py` (stdlib `unittest`, no network).

Move every assertion that is now in:

- `gitsync/__main__.py` `UNIT_CHECKS`
- `.github/workflows/ci.yml`
- `.forgejo/workflows/ci.yml`
- `.gitlab-ci.yml`

into that module. Those four copies already disagree (GitLab file has fewer asserts than the others).

Then:

- `cmd_ci` runs `python3 -m unittest tests.test_logic` and the existing offline sync smoke (keep that smoke, it is the only end-to-end test).
- Both workflows and GitLab CI become: install nothing, `PYTHONPATH=. python3 -m unittest tests.test_logic`, `bash -n`, `python3 -m gitsync ci`.
- Add the new tests named in each P0 item. A test that only asserts the happy helper and not the bug is not done.
- Do not mock so broadly that a regression in argument order still passes. Assert exact argv or exact URL strings.

Minimum new cases (names to use):

| Test | Covers |
|---|---|
| `test_bitbucket_no_unpack_crash` | P0-2 |
| `test_bitbucket_repo_url_has_workspace_and_token` | P0-2 |
| `test_forgejo_https_url_encodes_token` | P0-3 |
| `test_head_of_uses_target_repo_url` | P0-4 |
| `test_mirror_path_disambiguates_same_name` | P0-5 |
| `test_dry_run_does_not_mark_route` | P0-6 |
| `test_push_args_safe_does_not_force` | P0-7 |
| `test_default_mesh_conflict_is_skip` | P0-8 |
| `test_short_error_redacts_token` | P0-10 |
| `test_http_error_returns_headers_when_asked` | P0-12 |
| `test_notify_run_telegram_only` | P0-13 |
| `test_github_pagination_does_not_stop_at_500` | P0-14 |
| `test_gitlab_star_path_encoded_once` | P1-4 |
| `test_github_starred_accepts_status_200` | P1-4 |

---

# Documentation edits (same change set as the code they describe)

Touch these only to remove lies or document a behavior you changed. Do not rewrite them for style.

| File | Change |
|---|---|
| `README.md` | Remove `glab` as a working auth path. State default mode is `safe` for new setups. State mesh default is `skip`. State the scheduler is a user systemd timer or user cron. State compare/fix use each platform's own URL. State Bitbucket needs a workspace (`--owner`) and an API token. State org repos are off unless `affiliation` is set. |
| `docs/GETTING-STARTED.md` | Fix the `(y/N)` transcript. Remove `glab`. Add a short "your first sync will not delete branches" note matching P0-7. Mention two accounts of the same type need two names. |
| `docs/PERMISSIONS.md` | Remove the GitLab `glab` alternative. Qualify `read:org`. |
| `SECURITY.md` | Say which adapters embed tokens (all HTTPS adapters after P0-3). Say hub `config` files and the log are `0600`. Say `doctor --fix` does not touch the lock. Delete the `glab` row unless F3 is implemented. Note shell history if `--token` is passed on the command line. |
| `CHANGELOG.md` | One entry that lists user-visible fixes, especially the default mode change. |

---

# Feature plans — only after P0 and P1 are merged

Each feature is optional. Ship them as separate changes. None of them excuse skipping a P0 fix.

## F1 — `gitsync restore` (makes the "GitHub could disappear" claim true)

**Problem:** the hub is a pile of bare repos. If the source host is gone, `list_repos` on that source fails, so no route can push the hub anywhere. There is no restore command.

**Behavior**

```text
gitsync restore --to backup
gitsync restore --to gitlab --repos "app,docs"
```

- Source of truth is the hub directory, not a platform.
- For each mirror that has refs, push to `--to` with the current mode (`safe` default).
- Create missing repos via that platform's `ensure_repo`.
- Do not delete anything on the target unless global mode is `full` and the user also passed `--prune`.
- Exit 1 if any repo failed.

**Not in F1:** a UI wizard, incremental rsync, or LFS.

**Test:** two bare repos in a temp hub, local target, both appear on the target with the same HEAD. A third empty mirror is skipped with a log line, not a failure.

## F2 — Pick repositories in the wizard, not after

The TUI already has a repo browser (`E`). The wizard syncs the whole account, which is frightening.

**Behavior:** after the connection test, list repos (name, private/public) and accept:

- Enter = all
- `all` = all
- a comma-separated list or a glob (`Sea*`) stored as the route's `include`

Cap the printed list at 50 names and print the count. Never dump thousands of lines without a count first.

## F3 — `glab` auth, for real, or not at all

Do not do this unless P0-9 deleted the false claim first (so the docs stay true in the gap).

**Behavior:** `auth: "glab"` on a GitLab platform.

- `test()` runs `glab auth status` and requires exit 0.
- API calls: `glab api <path>`.
- Git calls get `GIT_ASKPASS` pointing at a 20-line stdlib helper that prints the token from `glab auth status -t` (do not write the token into the remote URL in this mode).

If `glab` is missing, `test()` returns a clear "install glab or paste a token".

## F4 — Per-repo include file

**Behavior:** `gitsync routes edit NAME --from-file repos.txt` sets `include` to the exact names in the file (one per line, `#` comments). The TUI `E` screen writes this file when the user saves a selection. Stored in the config as `include_file`, not as a 500-element JSON list, so the file stays diffable.

## F5 — Plain-language doctor

`gitsync doctor` already exits non-zero. Add a final section "what to type" with at most three commands, chosen from the problems actually found:

- no config → `gitsync setup`
- platform fail → the adapter's existing error string plus `gitsync platforms edit NAME`
- scheduler missing → `gitsync install`
- last run failed → `gitsync records -n 1` and `gitsync log -n 40`

No new analyzer. Map the existing `problems` list to those sentences.

## F6 — Activity summary, not a new product

`gitsync status` gains four lines: last success time, number of mirrors, hub size (`diskutil` already computes this), next due route. No charts, no web UI. The TUI dashboard already shows a health strip; keep them consistent.

## Explicitly not recommended

Do not add these. They make a backup tool worse for a newbie:

- A hosted SaaS account, telemetry, or phone-home version check (the current update curl is enough; pin it to a tag rather than adding analytics).
- Two-way sync of issues, wikis, releases, or stars as part of the default sync. `stars copy` can stay a separate command.
- A database, a daemon other than the existing webhook receiver, or a web dashboard.
- Rewriting the TUI in another toolkit.
- Automatic force-push as a silent fallback.

---

# Suggested order of work

Do not parallelize P0-5 (hub paths) with other sync edits. One person owns `sync.py` at a time.

1. **T1 skeleton** — move existing asserts to `tests/test_logic.py`, make CI call it, fix `cmd_ci` stderr (P0-13's unpack bug) so you can see failures.
2. **P0-2, P0-3, P0-12, P0-14** — `platforms.py` only. Adapters become safe to call.
3. **P0-7, P0-8, P0-6, P0-10** — `sync.py` push policy, schedule stamp, redaction.
4. **P0-4, P0-11, P0-13** — CLI commands that depend on the new helpers.
5. **P0-5** — hub path migration. Re-run the offline sync smoke after this.
6. **P0-1 and P0-9** — installer and wizard last, so they call the fixed `gitsync install`.
7. **P1** in the numbered order.
8. Docs and changelog.
9. Features F1 then F2. Stop. F3–F6 only if the user asks.

# Definition of done

- Every P0 test in the table passes with no network.
- `python3 -m gitsync ci` passes.
- A manual offline scenario still passes: two temp folders, one commit on the source, one extra branch on the dest, `mode=safe`, extra branch survives; `mode=full` with a local dest removes it and the dry-run said `WOULD DELETE` first.
- `bash install.sh --user --schedule daily --yes` against a temp `HOME` exits 0, writes a user unit or a user crontab containing `sync --scheduled`, and does not write `/etc/systemd/system/gitsync.service`.
- README does not mention `glab` as something gitsync can use.
- `__version__` is the only version, and it matches the changelog heading.
- A new user who only reads `docs/GETTING-STARTED.md` is told that the first setup will not delete branches on the destination.
