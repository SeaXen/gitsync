# How it works

The workflow, end to end: first run, the sync engine, the TUI loops,
daily use, unattended runs and recovery.

## How it works — the workflows

### First run (once)

```
curl -fsSL .../quickinstall.sh | bash
      |
      +-> preflight (python3 3.8+, sudo/--user detection)
      +-> download the repo (git clone or tarball)
      +-> install to /opt/gitsync + symlink on PATH
      |
      +-> SETUP WIZARD (interactive)
      |     +-> pick platforms (github, gitlab, forgejo, local...)
      |     +-> shows the token scopes each platform needs
      |     +-> you paste tokens / paths
      |     +-> TESTS every connection live
      |     +-> pick source + targets   <- your sync direction
      |     +-> pick schedule, workers, mirror mode
      |           +-> writes ~/.config/gitsync/config.json (0600)
      |
      +-> scheduler installed (systemd timer, Persistent=true)
      +-> first sync -> everything mirrored
```

### The sync engine (every run)

```
gitsync sync   (or the hourly timer)
      |
      +-> acquire run lock -- (another run active? step aside)
      |
      +-> for each route:  source ---> targets
      |
      |   FOR EVERY REPO (4 in parallel):
      |   +--------------------------------------------------+
      |   | 1. hub mirror: git clone --mirror from source      |  <- offline copy
      |   |    (or git remote update --prune if it exists)     |
      |   | 2. for each target:                                |
      |   |      ensure the repo exists (create if missing,    |
      |   |      private stays private)                        |
      |   |      push (full mirror; heads+tags for Forgejo)    |
      |   +--------------------------------------------------+
      |
      +-> write the run record (records.json)
      +-> notify if it failed (Discord, Telegram, ntfy, webhook, email — per your mode)
```

Self-healing: a network drop mid-clone leaves a partial mirror; the next
run detects it (no refs), deletes it and re-clones.

### The TUI

```
gitsync                        <- bare command opens the TUI
      |
      +-> 1 Dashboard   status, platforms, routes, live log,
      |                 + progress bar while syncing
      +-> 2 Platforms   every account + connection test + repo count
      +-> 3 Routes      source -> targets, filters
      +-> 4 Log         recent lines   (/ to filter)
      +-> 5 History     past runs + duration sparkline
      +-> 6 Settings    workers/mode/schedule/notify + paths
      |
      +-> S sync now (live progress)      L full log (scroll+search)
      +-> C schedule menu                O options
      +-> A accounts/scopes              N notifications (5 channels)
      +-> W setup wizard                 Q quit
```

Rendering: redraws only when something changes (dirty flag); idle ticks
repaint just the clock (~20 bytes) so it never flickers. On phones it
switches to a compact layout automatically.

### Daily use

```
you write code in a working clone
      |
      git push                     <- GitHub only (source of truth)
      |
      v
hourly timer fires (or: gitsync sync)
      |
      +--> GitLab     (<owner>/<repo>)
      +--> Forgejo    (<owner>/<repo>)
      +--> disk       (<your-folder>/<repo>.git)
      |
want it now?        gitsync sync          (~40s for 50 repos)
something wrong?    gitsync doctor / gitsync compare
```

Golden rule: push to GitHub first. The sync mirrors GitHub's state
outward, so commits that exist only on GitLab/Forgejo get reverted by
the next run.

### Unattended automation

```
systemd timer (hourly, Persistent=true)
      |
      +-> machine was off at :00? -> runs once at boot instead of skipping
      |
      +-> gitsync sync --quiet
      |     +-> run lock (never overlaps a manual sync)
      |     +-> repos -> all targets
      |     +-> run record written
      |     +-> failure? -> notification with the reasons (your channels)
      |
      +-> logs: gitsync.log   |   journalctl -u gitsync.service
```

### Recovery

| Situation | Command |
|---|---|
| "Is everything in sync?" | `gitsync compare` |
| "Something drifted" | `gitsync fix` (detect + repair) |
| "A sync failed" | `gitsync records`, then `gitsync doctor` |
| "A mirror looks broken" | `gitsync doctor --fix` (auto-repair) |
| "I want it live, not hourly" | `gitsync watch --interval 60` |
| "New repo on the source" | the next sync creates it everywhere |
| "Lost the source account" | your disk + other platforms are complete copies |

---

[← back to the README](../README.md)
