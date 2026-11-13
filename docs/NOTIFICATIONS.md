# Notifications

Discord, Telegram, ntfy, a generic webhook and email — on failure or on every
run, globally or per route. A backup that silently stops working is the
failure mode this exists to prevent.

Five channels, all optional, all filled in independently. Anything you fill
in is used; a config with no channels sends nothing and says so.

| Channel | What it needs | Notes |
|---|---|---|
| Discord | a webhook URL | must start with `https://discord.com/api/webhooks/` |
| Telegram | bot token **and** chat id | half of the pair is reported as a problem, not silently ignored |
| ntfy | a topic URL (`https://ntfy.sh/<topic>`) | push to your phone; `ntfy_token` only for a protected topic |
| Webhook | any `http(s)` URL | receives JSON: `title`, `text`, `color`, `fields`, `version` |
| Email | SMTP host **and** recipient | STARTTLS + AUTH when a username is set; `--no-tls` for a plain relay |

Standard library only — `urllib` for HTTP, `smtplib` for mail. A channel that
is down never turns a successful sync into a failure: the message is dropped
and the run reports `ok`.

## Set them up

**From the TUI** — press `N`:

```
 NOTIFICATIONS
 Discord webhook : configured
 Telegram bot    : not set
 ntfy            : configured
 webhook         : not set
 email           : not set
 Notify on       : failure
```

**From the shell:**

```bash
gitsync notify                                  # show the current setup
gitsync notify --test                           # send a test message

gitsync notify --set-url https://discord.com/api/webhooks/...
gitsync notify --set-telegram BOTTOKEN --chat-id 12345
gitsync notify --set-ntfy https://ntfy.sh/my-topic
gitsync notify --set-webhook https://example.com/hooks/gitsync
gitsync notify --set-smtp smtp.example.com --smtp-to me@example.com \
               --smtp-user me --smtp-password ... --smtp-from gitsync@example.com
```

**From the web UI** — Settings → Notifications. Every credential field is
write-only (blank keeps the stored value), and each channel has its own
`Clear` button. The card lists which channels are active and warns when one
is half-typed.

`gitsync notify --test` reports each channel separately, so a half-configured
setup says `telegram: FAILED` instead of "sent".

## Config reference

```jsonc
"settings": {
    "notify": {
        "discord_webhook": "https://discord.com/api/webhooks/...",
        "telegram_token":  "123456:ABC-DEF...",
        "telegram_chat_id": "123456789",
        "ntfy_url": "https://ntfy.sh/my-private-topic",
        "ntfy_token": "",
        "webhook_url": "https://example.com/hooks/gitsync",
        "smtp_host": "smtp.example.com", "smtp_port": 587,
        "smtp_user": "", "smtp_password": "",
        "smtp_from": "gitsync@example.com", "smtp_to": "me@example.com",
        "smtp_tls": true,
        "on": "failure"          // failure | success | always | never
    }
}
```

`on` is the global default. A **route** may override it:

```jsonc
"routes": [
    {"name": "main", "source": "github", "targets": ["gitlab"],
     "notify_on": "failure"},
    {"name": "chatty", "source": "github", "targets": ["backup"],
     "notify_on": "always"}
]
```

| Value | Sends when |
|---|---|
| `failure` (default) | anything failed **or** was partial |
| `success` | only when everything synced cleanly |
| `always` | every run |
| `never` | never, even if the global mode is `always` |

The rule is applied **per route**, not once for the whole run: one failing
route and one quiet route can now coexist. `never` on a route wins over a
global `always`.

## What a message looks like

Repos synced / failed / partial, duration, when it finished, and the first few
failure reasons — so a broken sync tells you instead of waiting to be
discovered. A **dry run never notifies anyone**; it changes nothing, so there
is nothing to page about.

Environment overrides exist for the CI/headless case:
`GITSYNC_DISCORD_WEBHOOK`, `GITSYNC_TELEGRAM_TOKEN`,
`GITSYNC_TELEGRAM_CHAT_ID`, `GITSYNC_NTFY_URL`, `GITSYNC_NTFY_TOKEN`,
`GITSYNC_WEBHOOK_URL`, `GITSYNC_SMTP_HOST/PORT/USER/PASSWORD/FROM/TO`,
`GITSYNC_NOTIFY_ON`.

Every credential stays in your own config file (mode 0600) — never in the
code, never in a log line, and never in an export (`gitsync config export`
blanks them; `import` carries the stored values over when the incoming file
left them empty).

---

[← back to the README](../README.md)
