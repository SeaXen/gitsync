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

## CI: four copies of one test block, keep them identical

`gitsync ci` runs: python syntax, shell syntax, CLI smoke tests, unit checks.
The unit-check code lives **four times** and all four must stay in sync:

| file | quote | indent | `\n` in test strings |
|---|---|---|---|
| `gitsync/__main__.py` (`UNIT_CHECKS`, a triple-quoted string) | `'` | 0 | `\\n` |
| `.gitlab-ci.yml` (shell heredoc) | `"` | 6 | `\n` |
| `.github/workflows/ci.yml` | `'` | 10 | `\n` |
| `.forgejo/workflows/ci.yml` | `'` | 10 | `\n` |

After editing any of them, verify all four:
```bash
python3 -c "import subprocess,os;from gitsync.__main__ import UNIT_CHECKS;print(subprocess.run(['python3','-c',UNIT_CHECKS],capture_output=True,text=True,env=dict(os.environ,PYTHONPATH=os.getcwd())).returncode)"
# plus, for each YAML: yaml.safe_load, extract 'import curses'..'PY',
# textwrap.dedent, run it
```
The assertion count is quoted in `gitsync/__main__.py` and `README.md`
(`unit checks (N assertions)`) — update both when it changes.

**The CLI smoke test must run a real offline sync**, not just `--help`: two
local platforms with a route between them, in a temp config. A `NameError` in
`cmd_sync` once shipped because only `--help` was exercised — and `cmd_sync`
is what the systemd timer runs, so installs silently stopped syncing.

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
