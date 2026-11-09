"""Scheduler management: systemd timer (preferred, catches up missed runs)
with a cron fallback."""

import os
import subprocess

from . import config as C

SYSTEMD_DIR = "/etc/systemd/system"
SERVICE = "gitsync.service"
TIMER = "gitsync.timer"
WEBHOOK_SERVICE = "gitsync-webhooks.service"

WEBHOOK_TEMPLATE = """[Unit]
Description=gitsync webhook receiver (push-triggered sync)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HOME=%h
Environment=GH_CONFIG_DIR=%h/.config/gh
Environment=XDG_CONFIG_HOME=%h/.config
ExecStart={gitsync} webhooks --serve --port {port} --bind {bind}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

SERVICE_TEMPLATE = """[Unit]
Description=gitsync repository sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# gh/glab read their config from $HOME — systemd does not set it by default
Environment=HOME=%h
Environment=GH_CONFIG_DIR=%h/.config/gh
Environment=XDG_CONFIG_HOME=%h/.config
ExecStart={gitsync} sync --scheduled --quiet
StandardOutput=journal
StandardError=journal
"""

TIMER_TEMPLATE = """[Unit]
Description=Run gitsync on schedule

[Timer]
OnCalendar={calendar}
Persistent=true
AccuracySec=1min
Unit={service}

[Install]
WantedBy=timers.target
"""


def have_systemd():
    return os.path.isdir(SYSTEMD_DIR) and subprocess.run(
        ["systemctl", "--version"], capture_output=True).returncode == 0


def gitsync_bin():
    # prefer the installed command, fall back to this checkout's entry
    for p in ("/usr/local/bin/gitsync", "/usr/bin/gitsync"):
        if os.path.exists(p):
            return p
    home = os.environ.get("GITSYNC_HOME", "")
    if home:
        return os.path.join(home, "bin", "gitsync")
    return "gitsync"


def _sh(cmd, stdin_text=None, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              input=stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def systemd_installed():
    return os.path.exists(os.path.join(SYSTEMD_DIR, TIMER))


def install_systemd(schedule_key=None):
    """write + enable the systemd service/timer for the given schedule"""
    cfg = C.load_config()
    # the timer fires as often as the most demanding route needs; routes
    # with a looser schedule are skipped by `sync --scheduled` itself
    key = schedule_key or C.finest_schedule(cfg) or "hourly"
    cal = C.SCHEDULES.get(key, C.SCHEDULES["hourly"])["calendar"]
    if cal is None:
        return uninstall_systemd()      # every route is off

    with open(os.path.join(SYSTEMD_DIR, SERVICE), "w") as f:
        f.write(SERVICE_TEMPLATE.format(gitsync=gitsync_bin()))
    with open(os.path.join(SYSTEMD_DIR, TIMER), "w") as f:
        f.write(TIMER_TEMPLATE.format(calendar=cal, service=SERVICE))
    _sh(["systemctl", "daemon-reload"])
    _sh(["systemctl", "enable", "--now", TIMER])
    return True


def uninstall_systemd():
    if not systemd_installed():
        return True
    _sh(["systemctl", "disable", "--now", TIMER])
    for f in (SERVICE, TIMER):
        try:
            os.remove(os.path.join(SYSTEMD_DIR, f))
        except OSError:
            pass
    _sh(["systemctl", "daemon-reload"])
    return True


def timer_status():
    if not systemd_installed():
        return None
    r = _sh(["systemctl", "list-timers", TIMER, "--no-pager"])
    if r.returncode != 0:
        return "installed (status unavailable)"
    for line in r.stdout.splitlines():
        if TIMER in line:
            return line.strip()
    return "installed"


def next_run():
    if not systemd_installed():
        return None
    r = _sh(["systemctl", "show", TIMER, "-p", "NextElapseUSecRealtime",
             "--value"])
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return None


# ---- cron fallback ----

def _crontab():
    r = _sh(["crontab", "-l"])
    return r.stdout if r.returncode == 0 else ""


def cron_line():
    cfg = C.load_config()
    key = C.setting(cfg, "schedule", "hourly")
    expr = C.SCHEDULES.get(key, C.SCHEDULES["hourly"])["cron"]
    if expr is None:
        return None
    return "%s %s sync --quiet >/dev/null 2>&1" % (expr, gitsync_bin())


def install_cron(schedule_key=None):
    cfg = C.load_config()
    key = schedule_key or C.setting(cfg, "schedule", "hourly")
    C.set_setting(cfg, "schedule", key)
    expr = C.SCHEDULES.get(key, C.SCHEDULES["hourly"])["cron"]
    lines = [l for l in _crontab().splitlines() if "gitsync" not in l]
    if expr:
        lines.append("%s %s sync --quiet >/dev/null 2>&1"
                     % (expr, gitsync_bin()))
    _sh(["crontab", "-"], stdin_text="\n".join(lines) + "\n")
    return True



def install_webhooks(port=8791, bind="127.0.0.1"):
    """write + enable the webhook receiver service"""
    if not have_systemd():
        return False
    path = os.path.join(SYSTEMD_DIR, WEBHOOK_SERVICE)
    with open(path, "w") as f:
        f.write(WEBHOOK_TEMPLATE.format(gitsync=gitsync_bin(), port=port,
                                        bind=bind))
    _sh(["systemctl", "daemon-reload"])
    _sh(["systemctl", "enable", "--now", WEBHOOK_SERVICE])
    return True


def uninstall_webhooks():
    if not os.path.exists(os.path.join(SYSTEMD_DIR, WEBHOOK_SERVICE)):
        return False
    _sh(["systemctl", "disable", "--now", WEBHOOK_SERVICE])
    try:
        os.unlink(os.path.join(SYSTEMD_DIR, WEBHOOK_SERVICE))
    except OSError:
        pass
    _sh(["systemctl", "daemon-reload"])
    return True


def webhooks_active():
    """is the webhook receiver service running?

    _sh() returns a CompletedProcess (it is also used where only the return
    code matters), so read .returncode/.stdout rather than unpacking it —
    the old tuple unpack raised "cannot unpack non-iterable
    CompletedProcess" the moment anything asked.
    """
    try:
        r = _sh(["systemctl", "is-active", WEBHOOK_SERVICE])
        return ((r.stdout or "").strip() == "active"
                and getattr(r, "returncode", 1) == 0)
    except Exception:
        return False

def status():
    """human-readable scheduler status"""
    if systemd_installed():
        return "systemd timer: %s" % (timer_status() or "active")
    line = [l for l in _crontab().splitlines() if "gitsync" in l]
    if line:
        return "cron: %s" % line[0]
    return "not scheduled (manual runs only)"
