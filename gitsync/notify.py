"""Notifications: Discord, Telegram, ntfy, a generic webhook and SMTP email.

Config (~/.config/gitsync/config.json):
    "settings": {
        "notify": {
            "discord_webhook": "https://discord.com/api/webhooks/...",
            "telegram_token":  "123456:ABC-DEF...",
            "telegram_chat_id": "123456789",
            "ntfy_url":   "https://ntfy.sh/my-topic",   # or a self-hosted one
            "ntfy_token": "",                            # optional (protected)
            "webhook_url": "",                           # JSON POST receiver
            "smtp_host": "", "smtp_port": 587,
            "smtp_user": "", "smtp_password": "",
            "smtp_from": "", "smtp_to": "",
            "smtp_tls": true,
            "on": "failure"          // failure | success | always | never
        }
    }

A route may override `on` with its own "notify_on", so a chatty route can
page on every run while the rest stay quiet until something breaks.

Environment overrides: GITSYNC_DISCORD_WEBHOOK, GITSYNC_TELEGRAM_TOKEN,
GITSYNC_TELEGRAM_CHAT_ID, GITSYNC_NTFY_URL, GITSYNC_NTFY_TOKEN,
GITSYNC_WEBHOOK_URL, GITSYNC_SMTP_HOST/PORT/USER/PASSWORD/FROM/TO,
GITSYNC_NOTIFY_ON

All credentials live in the user's own config file - never in the code.
Everything here uses the standard library only: urllib for HTTP, smtplib for
mail. A send never raises - a notification channel that is down must not
turn a successful sync into a failure.
"""

import email.message
import json
import os
import smtplib
import urllib.parse
import urllib.request

from . import __version__

TIMEOUT = 15

COLORS = {"ok": 0x2ECC71, "warn": 0xF1C40F, "fail": 0xE74C3C,
          "info": 0x3498DB}

# the only hosts a Discord webhook may point at (validated on save)
DISCORD_PREFIXES = ("https://discord.com/api/webhooks/",
                    "https://discordapp.com/api/webhooks/")

NOTIFY_MODES = ("failure", "success", "always", "never")


def _notify(cfg):
    return (cfg.get("settings", {}) or {}).get("notify", {}) or {}


def _pick(cfg, key, env):
    return (_notify(cfg).get(key) or os.environ.get(env, "") or "")


def _settings(cfg):
    """(discord url, on) — kept as a pair for older callers."""
    url = _pick(cfg, "discord_webhook", "GITSYNC_DISCORD_WEBHOOK")
    on = _notify(cfg).get("on") or os.environ.get("GITSYNC_NOTIFY_ON", "failure")
    return url, on


def _telegram(cfg):
    token = _pick(cfg, "telegram_token", "GITSYNC_TELEGRAM_TOKEN")
    chat = _pick(cfg, "telegram_chat_id", "GITSYNC_TELEGRAM_CHAT_ID")
    return token, chat


def _ntfy(cfg):
    return (_pick(cfg, "ntfy_url", "GITSYNC_NTFY_URL"),
            _pick(cfg, "ntfy_token", "GITSYNC_NTFY_TOKEN"))


def _webhook(cfg):
    return _pick(cfg, "webhook_url", "GITSYNC_WEBHOOK_URL")


def _smtp(cfg):
    """{host, port, user, password, from, to, tls} or None when unconfigured.

    A mail channel needs a host and a recipient; everything else is optional
    except that a username usually implies a password.
    """
    n = _notify(cfg)
    host = (n.get("smtp_host") or os.environ.get("GITSYNC_SMTP_HOST", "") or "")
    to = (n.get("smtp_to") or os.environ.get("GITSYNC_SMTP_TO", "") or "")
    if not host or not to:
        return None
    try:
        port = int(n.get("smtp_port") or os.environ.get("GITSYNC_SMTP_PORT")
                   or 587)
    except (TypeError, ValueError):
        port = 587
    frm = (n.get("smtp_from") or os.environ.get("GITSYNC_SMTP_FROM")
           or n.get("smtp_user") or os.environ.get("GITSYNC_SMTP_USER") or "")
    tls = n.get("smtp_tls")
    if tls is None:
        tls = True
    return {"host": host, "port": port,
            "user": (n.get("smtp_user")
                     or os.environ.get("GITSYNC_SMTP_USER", "") or ""),
            "password": (n.get("smtp_password")
                         or os.environ.get("GITSYNC_SMTP_PASSWORD", "") or ""),
            "from": frm or ("gitsync@%s" % host),
            "to": to, "tls": bool(tls)}


def channels(cfg):
    """names of the channels that are configured, in send order"""
    out = []
    if _settings(cfg)[0]:
        out.append("discord")
    token, chat = _telegram(cfg)
    if token and chat:
        out.append("telegram")
    if _ntfy(cfg)[0]:
        out.append("ntfy")
    if _webhook(cfg):
        out.append("webhook")
    smtp = _smtp(cfg)
    if smtp and smtp.get("to"):
        out.append("smtp")
    return out


def problem(cfg):
    """a human sentence about a *half*-configured channel, or "".

    Saving a Telegram token without a chat id, or SMTP without a recipient,
    used to look configured in the UI while nothing was ever sent.
    """
    n = _notify(cfg)
    if bool(n.get("telegram_token")) != bool(n.get("telegram_chat_id")):
        return "telegram needs both a bot token and a chat id"
    if n.get("smtp_host") and not n.get("smtp_to"):
        return "email needs a recipient (smtp_to)"
    if n.get("smtp_to") and not n.get("smtp_host"):
        return "email needs a server (smtp_host)"
    return ""


def enabled(cfg):
    url, on = _settings(cfg)
    return bool(channels(cfg)) and on != "never"


def on_for_route(cfg, route):
    """the effective 'notify on' for a route: its own, else the global one"""
    return (route or {}).get("notify_on") or _settings(cfg)[1]


def wants(cfg, route, failed, partial=0):
    """does *this route's* rule ask for a message?

    The old code decided once for the whole run, so one failing route and one
    quiet route could not coexist. 'failure' pages only for the route that
    actually broke; a route set to 'never' stays silent even when the global
    mode is 'always'.
    """
    on = on_for_route(cfg, route)
    if on == "never":
        return False
    if on == "always":
        return True
    if on == "success":
        return not (failed or partial)
    return bool(failed or partial)


def http_url_ok(url):
    """a channel URL must be http(s) with a host — anything else is a typo.

    Deliberately not a host allow-list: ntfy and a generic webhook are meant
    to point at the user's own server.
    """
    try:
        u = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return False
    return u.scheme in ("http", "https") and bool(u.netloc)


def _text(title, fields):
    return "\n".join([title] + ["%s: %s" % (n, v) for n, v, _ in fields])


def _post_raw(url, data, headers=None, method="POST"):
    h = {"User-Agent": "gitsync/%s (+https://github.com/SeaXen/gitsync)"
                   % __version__}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status in (200, 201, 202, 204)
    except Exception:
        return False


def _post_json(url, payload, headers=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    return _post_raw(url, json.dumps(payload).encode(), h)


def _post(url, payload):
    return _post_json(url, payload)


def telegram_send(cfg, text):
    """send a plain message via the Telegram Bot API"""
    token, chat = _telegram(cfg)
    if not token or not chat:
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    return _post_json(url, {"chat_id": chat, "text": text[:4000],
                            "disable_web_page_preview": True})


def ntfy_send(cfg, title, text):
    """ntfy takes a plain-text body plus Title/Priority headers.

    A topic URL is add-only, so this works against ntfy.sh and any
    self-hosted instance without an account.
    """
    url, token = _ntfy(cfg)
    if not url:
        return False
    headers = {"Title": title[:100], "Priority": "default",
               "Tags": "arrows_counterclockwise"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    return _post_raw(url, text.encode("utf-8"), headers)


def webhook_send(cfg, title, fields, color="ok", text=""):
    """a generic JSON receiver (Slack-style relays, Home Assistant, n8n…)"""
    url = _webhook(cfg)
    if not url:
        return False
    return _post_json(url, {
        "source": "gitsync", "version": __version__, "title": title,
        "text": text or _text(title, fields), "color": color,
        "fields": [{"name": str(n), "value": str(v)} for n, v, _ in fields],
    })


def smtp_send(cfg, title, text):
    """plain-text mail over SMTP, with STARTTLS and AUTH when configured"""
    smtp = _smtp(cfg)
    if not smtp:
        return False
    msg = email.message.EmailMessage()
    msg["Subject"] = title[:200]
    msg["From"] = smtp["from"]
    msg["To"] = smtp["to"]
    msg.set_content(text + "\n\n--\ngitsync %s\n" % __version__)
    try:
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=TIMEOUT) as s:
            s.ehlo()
            if smtp["tls"]:
                try:
                    s.starttls()
                    s.ehlo()
                except smtplib.SMTPException:
                    # a server on 25 without STARTTLS is still usable; do not
                    # silently drop the mail just because TLS was unavailable
                    pass
            if smtp["user"]:
                s.login(smtp["user"], smtp["password"])
            s.send_message(msg)
        return True
    except Exception:
        return False


def send(cfg, title, fields, color="ok", content=None):
    """fan out to every configured channel.

    Returns True when at least one channel accepted the message: a config
    with no channels returns False, so callers can tell "sent" from
    "nothing configured" (that bug made the Settings "Send test" button
    claim success with an empty config).
    """
    text = _text(title, fields)
    results = []
    url = _settings(cfg)[0]
    if url:
        results.append(_send_discord(url, title, fields, color, content))
    token, chat = _telegram(cfg)
    if token and chat:
        results.append(telegram_send(cfg, text))
    if _ntfy(cfg)[0]:
        results.append(ntfy_send(cfg, title, text))
    if _webhook(cfg):
        results.append(webhook_send(cfg, title, fields, color, text))
    if _smtp(cfg):
        results.append(smtp_send(cfg, title, text))
    return any(results)


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


def _route_stats(summary):
    """{route: {'failed': n, 'partial': n}} from a run summary.

    Built from the failure list plus an optional per_route map, so an old
    records.json (written before per_route existed) still works.
    """
    stats = {}
    for k, v in (summary.get("per_route") or {}).items():
        stats[k] = {"failed": int(v.get("failed") or 0),
                    "partial": int(v.get("partial") or 0)}
    for f in summary.get("failures") or []:
        r = f.get("route") or "?"
        stats.setdefault(r, {"failed": 0, "partial": 0})
        if f.get("partial"):
            stats[r]["partial"] += 1
        else:
            stats[r]["failed"] += 1
    return stats


def notify_run(cfg, summary, routes=None):
    """called at the end of a sync run; decides per route, sends one message.

    `routes` is the list of route dicts for the run (their notify_on
    overrides). When it is omitted the global mode decides, which is what an
    old caller or a test expects.
    """
    if summary.get("dry_run"):
        # a preview must never page anyone with "sync complete"
        return False
    if not channels(cfg):
        return False
    failed = summary.get("failed", 0)
    partial = summary.get("partial", 0)
    stats = _route_stats(summary)
    if routes:
        wanted = []
        for r in routes:
            st = stats.get(r.get("name"), {"failed": 0, "partial": 0})
            if wants(cfg, r, st["failed"], st["partial"]):
                wanted.append(r.get("name"))
        if not wanted:
            return False
    else:
        _u, on = _settings(cfg)
        if on == "never":
            return False
        if failed or partial:
            if on == "success":
                return False
        elif on == "failure":
            return False

    ok = summary.get("ok", 0)
    if failed or partial:
        color = "fail" if failed else "warn"
        title = "gitsync: sync FAILED" if failed else "gitsync: partial sync"
    else:
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
    """send one test message per configured channel; report each result."""
    names = channels(cfg)
    if not names:
        return False, "nothing configured"
    title = "gitsync: test notification"
    fields = [("Status", "notifications are working", False),
              ("Mode", _settings(cfg)[1], True)]
    text = _text(title, fields)
    results = []
    for name in names:
        if name == "discord":
            ok = _send_discord(_settings(cfg)[0], title, fields, color="info")
        elif name == "telegram":
            ok = telegram_send(cfg, text)
        elif name == "ntfy":
            ok = ntfy_send(cfg, title, text)
        elif name == "webhook":
            ok = webhook_send(cfg, title, fields, color="info", text=text)
        else:
            ok = smtp_send(cfg, title, text)
        results.append("%s: %s" % (name, "sent" if ok else "FAILED"))
    ok = "FAILED" not in " ".join(results)
    return ok, ", ".join(results)


# ---------------------------------------------------------------- progress

def progress_callback(cfg, repo, status, detail):
    """per-repo event hook (used by the TUI for live progress)"""
    return None
