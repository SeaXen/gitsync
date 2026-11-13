# Notifications

Discord and Telegram, on failure or on every run, globally or per route.
A backup that silently stops working is the failure mode this exists to
prevent.

## Notifications (Discord + Telegram)

**Set them up from inside the TUI** - press `N`:

```
 NOTIFICATIONS
 Discord webhook : configured
   https://discord.com/api/webhooks/1568...wjrQ
 Telegram bot    : not set
   chat id: (missing)
 Notify on       : failure

 credentials are stored in YOUR config file
 (~/.config/gitsync/config.json) - never in the code

 [D]iscord  [T]elegram  [M]ode  [Enter]test  [B]back
```

| Key | Action |
|---|---|
| `D` | add/edit the Discord webhook (masked input) |
| `T` | add/edit the Telegram bot token + chat id |
| `M` | cycle: failure -> success -> always -> never |
| `Enter` | send a test message to both channels |

Or from the shell:

```bash
gitsync notify                        # show the current setup
gitsync notify --test                 # send a test message
```

Config reference:

```jsonc
"settings": {
    "notify": {
        "discord_webhook": "https://discord.com/api/webhooks/...",
        "telegram_token":  "123456:ABC-DEF...",
        "telegram_chat_id": "123456789",
        "on": "failure"          // failure | success | always | never
    }
}
```

Telegram: create a bot with @BotFather (https://t.me/BotFather), then get
your chat id from @userinfobot (https://t.me/userinfobot).

You get an embed/message with repos synced / failed / partial, duration and
the first few failure reasons - so a broken sync tells you instead of
waiting to be discovered.

---

[← back to the README](../README.md)
