"""Notifications: Discord webhooks and Telegram bot messages for sync results.

Config (~/.config/gitsync/config.json):
    "settings": {
        "notify": {
            "discord_webhook": "https://discord.com/api/webhooks/...",
            "telegram_token":  "123456:ABC-DEF...",
            "telegram_chat_id": "123456789",
            "on": "failure"          // failure | success | always | never
        }
    }

Environment overrides: GITSYNC_DISCORD_WEBHOOK, GITSYNC_TELEGRAM_TOKEN,
GITSYNC_TELEGRAM_CHAT_ID, GITSYNC_NOTIFY_ON

All credentials live in the user's own config file - never in the code.
"""

import json
import os

from . import __version__
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


def _telegram(cfg):
    n = (cfg.get("settings", {}) or {}).get("notify", {}) or {}
    token = (n.get("telegram_token")
             or os.environ.get("GITSYNC_TELEGRAM_TOKEN", ""))
    chat = (n.get("telegram_chat_id")
            or os.environ.get("GITSYNC_TELEGRAM_CHAT_ID", ""))
    return token, chat


def _post_raw(url, payload, headers=None):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json",
         "User-Agent": "gitsync/%s (+https://github.com/SeaXen/gitsync)"
                   % __version__}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method="POST", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status in (200, 204)
    except Exception:
        return False


def enabled(cfg):
    url, on = _settings(cfg)
    tg_token, tg_chat = _telegram(cfg)
    return bool((url or (tg_token and tg_chat))) and on != "never"


def _post(url, payload):
    return _post_raw(url, payload)


def telegram_send(cfg, text):
    """send a plain message via the Telegram Bot API"""
    token, chat = _telegram(cfg)
    if not token or not chat:
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    return _post_raw(url, {"chat_id": chat, "text": text[:4000],
                           "disable_web_page_preview": True})


def _channels(cfg):
    """(discord_url, telegram_token, telegram_chat) - what is configured"""
    url, _on = _settings(cfg)
    token, chat = _telegram(cfg)
    return url, token, chat


def send(cfg, title, fields, color="ok", content=None):
    """send to Discord (embed) and/or Telegram (plain text).

    Returns True only when something was actually sent - returning True for
    a config with no channels made callers believe alerts worked.
    """
    url, token, chat = _channels(cfg)
    if not (url or (token and chat)):
        return False
    ok = True
    if url:
        ok = _send_discord(url, title, fields, color, content) and ok
    if token and chat:
        lines = [title] + ["%s: %s" % (n, v) for n, v, _ in fields]
        ok = telegram_send(cfg, "\n".join(lines)) and ok
    return ok


def _send_discord(url, title, fields, color="ok", content=None):
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
    """called at the end of a sync run; respects the 'on' setting.

    Uses the same "Discord or Telegram" test as notify_test: the old code
    returned early when the Discord URL was empty, so a Telegram-only user
    never received sync results even though `notify --test` had passed.
    """
    url, token, chat = _channels(cfg)
    _u, on = _settings(cfg)
    if not (url or (token and chat)) or on == "never":
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
    token, chat = _telegram(cfg)
    if not url and not (token and chat):
        return False, "nothing configured"
    results = []
    if url:
        ok = _send_discord(url, "gitsync: test notification",
                           [("Status", "notifications are working", False),
                            ("Mode", on, True)], color="info")
        results.append("discord: %s" % ("sent" if ok else "FAILED"))
    if token and chat:
        ok = telegram_send(cfg, "gitsync: test notification\nmode: %s" % on)
        results.append("telegram: %s" % ("sent" if ok else "FAILED"))
    ok = "FAILED" not in " ".join(results)
    return ok, ", ".join(results)


# ---------------------------------------------------------------- progress

def progress_callback(cfg, repo, status, detail):
    """per-repo event hook (used by the TUI for live progress; also the
    place a future 'notify on first failure' would go)"""
    return None
