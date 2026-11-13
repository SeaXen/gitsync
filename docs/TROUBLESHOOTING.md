# Troubleshooting

Start with `gitsync doctor` — it checks config, tokens, git, mirrors,
disk and the scheduler, and `doctor --fix` repairs what it can.

## Requirements

- Python **3.8+** (standard library only)
- **git** 2.20+
- optional: the `gh` CLI for GitHub (gitsync can use it instead of a token)

## Troubleshooting

### A sync reports "partial"

Partial means the repo synced to some targets but not all. The notification
names the repo and the reason. The three common causes are handled
automatically now:

- **`You are not allowed to force push code to a protected branch`** — the
  target's branch protection refuses a mirror push. gitsync enables
  `allow_force_push` on that branch (access levels are left alone) and
  retries. You will see `force-push enabled on 'master'` in the log.
- **`Rate limit exceeded`** (GitLab) — the API answered 429. Requests are
  retried with backoff; a large run may simply take longer.
- **`GitLab create failed for '<name>': path: can only include ...`** —
  GitLab's path rules are stricter than GitHub's. Names are sanitised
  automatically (leading `_`, `-`, `.` are illegal there).

If a repo still fails, `gitsync sync --repos NAME --dry-run` shows the
source and target state without changing anything.

### Other problems

| Symptom | Fix |
|---|---|
| `gitsync: command not found` | `~/.local/bin` not in PATH (with `--user`), or re-run the installer |
| A platform shows FAIL | `gitsync platforms` for the reason; check token scopes with `gitsync scopes` |
| New repo missing on a target | run `gitsync sync` — it creates them automatically |
| Refs deleted on a target | that's `mode: full` (exact mirror). `mode: safe` — the default for new setups — keeps them |
| Another sync already running | a run is in progress (timer or manual); wait and retry |
| TUI looks garbled | resize the terminal; the layout adapts |

## FAQ

**Does it sync issues, PRs and wikis?**
No — code only (branches, tags, commits). Issues/PRs come over with GitLab's
one-time GitHub importer but don't update afterwards.

**Can I use it bidirectionally?**
Yes — use a **mesh** route: `gitsync routes add all github,gitlab,forgejo`.
Every peer is both source and target, and the newest commit wins per branch,
so a change made on any platform reaches all the others. See
[Two-way sync](#two-way-sync-mesh). One-way routes still exist for the
simpler case where one platform owns the truth.

**Is it safe for private repos?**
Yes — private repos are created as private on targets, and the config file
is `0600`. Tokens are embedded in the URLs the tool uses, so protect the
config file and the machine accordingly.

**Does it need root?**
No — `--user` installs entirely under your home directory.

---

[← back to the README](../README.md)

## A target looks fine but is missing commits

`gitsync sync` records what it *attempted*; it cannot know whether the other
side kept it. Ask the target directly — read-only, pushes nothing, creates
nothing:

```bash
gitsync verify                 # every branch and tag, hub vs each target
gitsync verify --route main    # one route
gitsync verify --json          # for a script; exit 1 on drift
```

```
  DRIFT      main: tradex -> gitlab: 1 ref(s) at a different commit
               differs refs/heads/main
  UNREACHABLE main: SeaVault -> forgejo: fatal: could not read Password
  NO MIRROR  main: newrepo (run: gitsync sync)

3 check(s): 1 drift, 1 in-sync, 1 unreachable
```

A `drift` row means the hub and the target disagree, which is what a rejected
push looks like after the fact. Run `gitsync sync --route ROUTE` to push the
hub's version again. For a quick HEAD-only check across platforms, `gitsync
compare` is the older, lighter command.

## A repo was skipped with "map to the same folder"

Two different repos produce the same folder name once punctuation is
sanitised (for example `a b.git` and `a_b.git`). gitsync refuses to merge two
histories into one mirror and skips both, naming them. Rename one on its
platform, or keep one out of the route:

```bash
gitsync routes edit main --exclude 'a_b'
```
