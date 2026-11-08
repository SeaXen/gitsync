"""Notifications: Discord webhooks (and a generic HTTP hook) for sync results.

Config (~/.config/gitsync/config.json):
    "settings": {
        "notify": {
            "discord_webhook": "https://discord.com/api/webhooks/...",
            "on": "failure"          // failure | success | always | never
        }
    }

Environment overrides: GITSYNC_DISCORD_WEBHOOK, GITSYNC_NOTIFY_ON
"""

import json
import os
import urllib.request

TIMEOUT = 15

COLORS = {"ok": 0x2ECC71, "warn": 0xF1C40F, "fail": 0xE74C3C,
          "info": 0x3498DB}


def _settings(cfg):
    n = (cfg.get("settings", {}) or {}).get("notify", {}) or {}
    url = (n.get("discord_webhook")
           or os.environ.get("GITSYNC_DISCORD_WEBHOOK", ""))
    on = n.get("on") or os.environ.get("GITSYNC_NOTIFY_ON", "failure")
    return url, on


def enabled(cfg):
    url, on = _settings(cfg)
    return bool(url) and on != "never"


def _post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "gitsync/1.0 (+https://github.com/SeaXen/gitsync)"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status in (200, 204)
    except Exception:
        return False


def send(cfg, title, fields, color="ok", content=None):
    """send a Discord embed. fields = [(name, value, inline)]"""
    url, on = _settings(cfg)
    if not url:
        return False
    embed = {
        "title": title[:256],
        "color": COLORS.get(color, COLORS["info"]),
        "fields": [{"name": str(n)[:256], "value": str(v)[:1024],
                    "inline": bool(i)} for n, v, i in fields][:25],
        "footer": {"text": "gitsync"},
    }
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content[:2000]
    return _post(url, payload)


def notify_run(cfg, summary):
    """called at the end of a sync run; respects the 'on' setting"""
    url, on = _settings(cfg)
    if not url or on == "never":
        return False
    failed = summary.get("failed", 0)
    partial = summary.get("partial", 0)
    ok = summary.get("ok", 0)
    if failed or partial:
        if on == "success":
            return False
        color = "fail" if failed else "warn"
        title = "gitsync: sync FAILED" if failed else "gitsync: partial sync"
    else:
        if on in ("failure",):
            return False
        color = "ok"
        title = "gitsync: sync complete"

    fields = [
        ("Repos synced", "%d ok" % ok, True),
        ("Failed", str(failed), True),
        ("Partial", str(partial), True),
        ("Duration", "%ss" % summary.get("elapsed_sec", "?"), True),
        ("Finished", summary.get("finished_at", "?"), False),
    ]
    fails = summary.get("failures") or []
    if fails:
        lines = ["%s/%s — %s" % (f.get("route"), f.get("repo"),
                                 str(f.get("error", ""))[:90])
                 for f in fails[:8]]
        fields.append(("Failures", "```\n%s\n```" % "\n".join(lines), False))
    return send(cfg, title, fields, color=color)


def notify_test(cfg):
    url, on = _settings(cfg)
    if not url:
        return False, "no webhook configured"
    ok = send(cfg, "gitsync: test notification",
              [("Status", "notifications are working", False),
               ("Mode", on, True)],
              color="info")
    return ok, ("sent" if ok else "discord rejected the request")


# ---------------------------------------------------------------- progress

def progress_callback(cfg, repo, status, detail):
    """per-repo event hook (used by the TUI for live progress; also the
    place a future 'notify on first failure' would go)"""
    return None
