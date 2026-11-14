# gitsync 2.1.7 — Round 5: pre-release check and last fixes

Audited: `gitsync-main_4.zip`, version **2.1.7**. Self-contained for a coding agent.

## 0. Verdict

**Ready to publish after one small fix (R5-1).** Everything from round 4 is fixed and verified.
It is not honest to call it "10/10 in every sector": real GitHub/GitLab accounts, Windows, Docker and
real notification delivery were not testable here (section 5).

## 1. Rules for the agent

Python 3.8+ stdlib only. Isolate with
`export PYTHONPATH=$PWD GITSYNC_CONFIG_DIR=/tmp/x/config GITSYNC_DATA_DIR=/tmp/x/data`.
Never print tokens. Add tests in `tests/test_logic.py`. Before every commit:
```bash
python3 -m py_compile gitsync/*.py
bash -n install.sh quickinstall.sh bin/gitsync
python3 -W error::ResourceWarning -m unittest tests.test_logic
python3 -m gitsync ci --update-sums && python3 -m gitsync ci
```
One item per commit; bump to `2.1.8`; CHANGELOG entry that does not say "10/10".

## 2. Verified fixed (do not redo)

- Mesh with the default `skip`: fast-forward propagates to all peers (2 and 3 peers); true divergence is
  skipped and reported with all SHAs; a moved tag is skipped; new branches propagate; a branch deleted on
  one peer is not deleted elsewhere.
- Fresh install: empty route list, first-run wizard shown, no overdue alarm, Sync/Dry-run disabled with a tooltip.
- Browser, 9 pages x desktop+phone x light+dark: zero axe violations, zero console/CSP errors, no horizontal scroll.
- Hub size/mirror count correct right after a sync. Sync from the UI, theme persistence, `?` help,
  sign-out-everywhere, Repos matrix, History, Settings all work.
- 301 tests pass with `-W error::ResourceWarning`; `gitsync ci` 6/6; TUI: 7 tabs, `[`/`]`, dry run, 80x24, quit clean.
- Name collisions (`a b` / `a_b` / Unicode), empty repos, `--repos "alpha, a b"`, env-token override not leaked
  by `config export` / `status` / `platforms`.

## 3. P1 — fix before publishing

### R5-1. A live mirror is reported as an orphan, and `--prune-orphans` would delete it
- **Files:** `gitsync/diskutil.py` `orphans()` (line ~135); callers `gitsync/__main__.py` `cmd_disk()` (~line 1138)
  and the web "Tools → disk" action (`web.py` ~line 843).
- **Reproduced:** local source with repos `a b`, `a_b`, `alpha`, `ünï`, `öñï`, `empty`; one sync. Hub holds
  `mirrors/local/a_b-7dbde9.git` (the collision-safe name for `a b`). Then:
  `gitsync disk --orphans` → `orphaned mirrors: local/a_b-7dbde9  100.0K`, and `gitsync doctor`/`disk` count
  `orphans : 1`. `gitsync disk --orphans --prune-orphans` would delete the mirror of a repo that still exists.
- **Cause:** `orphans()` compares the *hub folder name* (`a_b-7dbde9`) with the *source repo names* (`a b`).
  Since 2.1.4 any name changed by sanitising gets a `-<sha1[:6]>` suffix, so they never match.
- **Fix:** do not compare names. Build the set of **expected hub paths** from the live repos with the same
  function the sync uses (`sync.hub_rel(repo["id"])` or `mirror_path(repo, ptype, claimants)`), then call a
  mirror an orphan only when its path is not in that set. Change `diskutil.orphans(live_names)` to take the
  expected relative paths (keep the old argument working for legacy mirrors named by plain repo name).
  Update `cmd_disk`, `cmd_doctor`, `_doctor_fix` and the web job.
- **Tests:** repos `a b`, `a_b`, `ünï`, `alpha` all synced → `orphans() == []`; delete `alpha` at the source →
  exactly `alpha` is an orphan; legacy plain-named mirror still recognised.

## 4. P2 / P3 — small, do in the same release

1. **`gitsync schedule status` does not exist.** The CHANGELOG (2.1.7, line ~32) and the docstring in
   `scheduler.py:269` mention it; the CLI has no `schedule` command (argparse: invalid choice). Either add
   `schedule {status,install,remove}` as an alias of the existing install/uninstall/status logic, or correct
   the text to the real commands (`gitsync status`, `gitsync install`, `gitsync uninstall`).
2. **Homebrew formula has no `sha256`.** `packaging/homebrew/gitsync.rb` has `url` but no `sha256`, so
   `brew install` and `brew audit` fail. Add the real checksum of the v2.1.8 tag tarball when releasing (or
   keep it clearly marked as a template and do not advertise it in the README until it works).
3. **Stale docs:** `docs/WEB-UI.md:154` shows `gitsync_info{version="2.1.4"}`; use a placeholder such as `X.Y.Z`
   and add a CI check that no doc contains a hard-coded old version.
4. **TUI log lines at 80 columns** are cut without an ellipsis; show `…` where a line is truncated.
5. **Big modules** (`tui.py` 3979, `web.py` 2927, `__main__.py` 2851 lines) are still unsplit — do after 1.0,
   behaviour-preserving, one module per PR.

## 5. Not testable in this audit (verify before claiming support)

- Real GitHub / GitLab / Forgejo / Bitbucket accounts (token scopes, rate limits, archived/fork filtering,
  private repo creation); the new opt-in `real-platform-smoke` CI job should be run once with real secrets.
- Windows Task Scheduler (`schtasks`) install/status/uninstall and the Windows file locking added in 2.1.7.
- `docker build` / `docker compose up` including the `scheduler` service; macOS `launchd`/cron.
- Delivery on Discord, Telegram, ntfy, generic webhook and SMTP; pre/post hooks; Git LFS fetch.
- Python 3.8 runtime (grammar was checked; CI matrix covers it).

## 6. Release checklist

- [ ] R5-1 fixed with tests; items 4.1–4.4 done or deferred in the CHANGELOG.
- [ ] Section 1 commands pass on Python 3.8 and 3.12/3.13.
- [ ] Tag `v2.1.8`, publish `SHA256SUMS` with the release, install it with `gitsync update` on a clean machine.
- [ ] One real sync of a throw-away GitHub → GitLab pair, safe and full mode, dry run first.
- [ ] README states the supported platforms exactly as tested (Linux/macOS yes; Windows only if section 5 passes).
