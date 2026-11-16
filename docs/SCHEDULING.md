# Scheduling

The installer sets up a systemd timer (or cron). Every run takes the same
lock as a manual sync, so they never overlap, and `Persistent=true` catches up
after downtime.

## Scheduling

Default: a **systemd timer** on Linux (hourly, `Persistent=true` — if the machine was
off when a run was due, it runs once at boot instead of being skipped), **Windows Task Scheduler**
on Windows, or **cron** where systemd is not present.

```bash
# Linux (systemd)
systemctl list-timers gitsync.timer     # next run
journalctl -u gitsync.service           # run logs
gitsync install --schedule 6h           # change frequency
gitsync install --schedule off          # manual runs only
gitsync install --cron                  # use cron instead

# Windows (Task Scheduler)
gitsync install                         # registers gitsync hourly task in Task Scheduler
gitsync install --schedule 6h           # change frequency (hourly, 6h, 12h, daily)
gitsync status                          # displays next run from Task Scheduler
gitsync uninstall                       # removes scheduled task
```

### The clock is the anchor, not your last run

A schedule is a fixed wall-clock time, never "N hours since the last sync":

| Schedule | Fires at |
|---|---|
| `hourly` | every hour at `:00` — 09:00, 10:00, 11:00 … |
| `6h` | 00:00, 06:00, 12:00, 18:00 |
| `12h` | 00:00, 12:00 |
| `daily` | 03:00 |
| `weekly` | Sunday 03:00 |
| `off` | never — manual `gitsync sync` only |

Two gates apply, which is why a tick can legitimately do nothing: the timer
fires at those times, and then **each route** separately checks its own
schedule against its last recorded run. A route last synced at 11:46 skips a
12:00 tick (`skipped — not due for 5.8h`), and a manual sync counts as a run.
The timer itself installs the **finest** schedule any route asks for, so one
hourly route next to a daily one still fires hourly.

### Custom times

There is no free-form time-of-day field, by design — a cron expression in a
config file is a config file nobody can review. If you need "09:30 and 15:30",
run the same command the timer runs, from your own scheduler:

```bash
gitsync install --schedule off        # stop gitsync's own timer
crontab -e
30 9,15 * * * /usr/local/bin/gitsync sync --scheduled --quiet
```

`--scheduled` still honours each route's own cadence, so cron gives you the
*anchor* while the routes keep their *interval*. Hand-editing
`~/.config/systemd/user/gitsync.timer` works too, but `gitsync install` (and
the web UI's **Install / Update**) regenerates that file from the preset and
would overwrite it — cron does not have that problem.

---

## Updating

```bash
gitsync update        # re-runs the one-click installer (keeps your config)
```

## Uninstalling

```bash
gitsync uninstall     # removes scheduler + symlink (config & mirrors kept)
```

---

[← back to the README](../README.md)
