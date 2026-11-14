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
