# AGENTS.md — gitsync

Multi-platform git repo sync tool. Python 3.8+ **standard library only**
(no pip deps — that is a hard project constraint). TUI is the product;
almost all feature work lands in `gitsync/tui.py`.

## Architecture (not obvious from filenames)

```
source platform ──> local mirror hub (~/.local/share/gitsync/mirrors/*.git)
                 ──> each target platform
```
Every repo is mirrored into the hub first, then pushed from the hub. That is
what makes a sync resumable and gives an offline copy. Consequence: **the hub
is shared state between routes** — a one-way route and a mesh route over the
same repo will fight over it. One set of peers, one mode.

Push policy lives in one pure function, `sync.push_args(mode, ptype, url)`:
`safe` (the default for new configs) never forces and never prunes; `full`
force-updates and prunes heads+tags only, and only `--mirror`s a local target.
A mesh that refuses to overwrite a disagreement reports `partial`, not `ok`.

Route kinds (`config.json` → `routes[]`):
- **one-way** (default): `source` is truth, targets are overwritten.
  `mode: full` pushes `--mirror` and **prunes refs the target has and the
  source doesn't**; `mode: safe` pushes heads+tags only, never deletes.
- **mesh** (`"direction": "mesh"`): every peer equal; newest commit wins per
  branch/tag; refs are **unioned** (dropped only when no peer has them);
  always force-push heads+tags, never `--mirror`. Mesh discovers repos from
  the route **source only** — it never invents repos from a target.
- Pause: `"paused_targets": [...]` per target, `"enabled": false` whole route.

## Commands that matter

```bash
export PYTHONPATH=$PWD                 # run from the repo without installing
python3 -m gitsync <cmd>               # bare `gitsync` opens the TUI
python3 -m gitsync ci                  # the 4 checks CI runs (run before every push)
```

Isolate the real config — never point a test at the live one:
```bash
export GITSYNC_CONFIG_DIR=/tmp/x/config GITSYNC_DATA_DIR=/tmp/x/data
```

## The install trap (costs an hour if forgotten)

`bash install.sh` copies `bin/ gitsync/ README.md LICENSE docs/
quickinstall.sh` to `/opt/gitsync` and symlinks `/usr/local/bin/gitsync`.
**The TUI and every subprocess it spawns run the *installed* copy**, so after
editing source you must re-run `bash install.sh` before TUI testing — otherwise
you are testing yesterday's code. `CHANGELOG.md` and this file are *not*
copied. Verify with `diff -r --exclude=__pycache__ gitsync /opt/gitsync/gitsync`.

## CI and tests

`python3 -m gitsync ci` runs: python syntax, shell syntax, and a CLI smoke
block. All three workflows run the same, plus `python3 -m unittest
tests.test_logic`.

- **`tests/test_logic.py` is the only home for logic assertions** (stdlib
  `unittest`, no network). The assertion blob used to be duplicated four
  times (`UNIT_CHECKS` plus a heredoc in each CI file) and drifted; it is
  gone. Add cases there, not to a workflow.
- **The smoke block must run a real offline sync**, not just `--help`: two
  local platforms with a route between them, in a temp config. It also
  performs the real pushes and asserts `safe` keeps a branch only the
  destination has while `full` prunes it. A `NameError` in `cmd_sync` once
  shipped because only `--help` was exercised — and `cmd_sync` is what the
  systemd timer runs, so installs silently stopped syncing.
- Assertion count appears in `gitsync/__main__.py` and `README.md`.
- `_plan_repo`, `_push_target` and the mesh path all need network; cover
  them through the offline local-folder smoke or a fake platform, never by
  pointing a test at a live account.

## Testing the TUI

Use **tmux**, never a hand-rolled PTY:
```bash
tmux new-session -d -s t -x 120 -y 30
tmux send-keys -t t "..."            # then capture
tmux capture-pane -p -t t            # add -e to see attributes (cursor row)
```
curses needs a real TTY, so headless `curses.wrapper` fails with
`cbreak() returned ERR`. Mouse clicks cannot be tested inside tmux (no mouse
reporting) — verify by code reading and say so.

curses gotchas already paid for:
- `safe_add()` guards bounds; `commit_screen()` only calls `doupdate()`.
  **Never** `stdscr.noutrefresh()` in it — it blanks the panels.
- Anything network/disk-bound in a draw path runs per frame, and the
  dashboard redraws every second for the clock. Cache it
  (`cached_status()`, `_health`) or run it in a thread. `TK.check()` is ~0.5s
  and `diskutil.mirrors()` walks the whole hub.
- `scheduler._sh()` returns a `CompletedProcess`, **not** a tuple.

## Release flow (all three remotes, every time)

1. Bump `gitsync/__init__.py` `__version__`, add a CHANGELOG entry.
2. `bash install.sh`; confirm `gitsync --version` and that the installed copy
   matches source.
3. Commit; push `main` to `origin`, `gitlab`, `forgejo`; **tag on all three**.
4. Confirm GitLab pipeline + Forgejo task green and identical HEADs.

Git identity: `SeaXen <SeaXen@users.noreply.github.com>` — never a personal
address. Commit style: short imperative subject, body explains *why*.

## Live environment (this box)

- Config `~/.config/gitsync/config.json`; data `~/.local/share/gitsync`
  (`mirrors/`, `records.json`, `route_state.json`, `gitsync.log`).
- **`/mnt/sg/Sea/6Repo/1git` is the user's real backup hub — never delete or
  modify it without explicit OK.** Same for live platform accounts.
- Remotes: `origin` GitHub `SeaXen/gitsync`, `gitlab` `seaxen/gitsync`,
  `forgejo` `sagar/gitsync`. A `pre-push` hook runs `gitsync ci`.
- GitHub Actions is **disabled account-wide**; GitLab + Forgejo + the local
  hook carry CI.
- `glab ci view` panics without a TTY — use
  `glab api projects/seaxen%2Fgitsync/pipelines`, `.../jobs`, `.../jobs/<id>/trace`.
- Deleting a remote tag needs a repo context:
  `git -C ~/.local/share/gitsync/mirrors/<repo>.git push <url> :refs/tags/<tag>`.
- **Never echo tokens.** Read them from the config with `python3 -c` and build
  headers in-shell. The `gh` CLI token lacks `delete_repo`, so a throwaway
  GitHub test repo cannot be deleted by the agent — ask the user, and delete
  the copies on GitLab/Forgejo/hub yourself.

## Throwaway-repo test pattern

`gstest-*` repos are the established pattern. Create on one peer, verify
propagation, then **delete from every peer** (GitLab, Forgejo, hub mirror,
`1git`) before finishing — a leftover GitHub-only repo gets re-created on all
peers by the next timer run.
