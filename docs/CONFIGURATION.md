# Configuration

`~/.config/gitsync/config.json` holds platforms, routes and settings.
`gitsync config export` prints it with every credential blanked; `gitsync
config import FILE` reads one back and keeps the secrets already on this
machine.

## Configuration

`~/.config/gitsync/config.json` — editable by hand or via the wizard:

```jsonc
{
  "platforms": {
    "github":  {"type": "github",  "enabled": true, "owner": "you",
                "auth": "gh", "token": ""},
    "gitlab":  {"type": "gitlab",  "enabled": true,
                "url": "https://gitlab.com", "token": "glpat-...",
                "ns_id": "12345"},
    "homelab": {"type": "forgejo", "enabled": true,
                "url": "http://192.168.0.10:3000", "token": "...",
                "owner": "you"},
    "anygit":  {"type": "generic", "enabled": true,
                "url_template": "https://git.example.com/{owner}/{name}.git",
                "owner": "me"},
    "backup":  {"type": "local",   "enabled": true,
                "path": "/mnt/backup/git"}
  },
  "routes": [
    {"name": "main", "source": "github",
     "targets": ["gitlab", "homelab", "anygit", "backup"]}
  ],
  "settings": {"workers": 4, "mode": "safe", "mesh_conflict": "skip",
               "schedule": "hourly", "include_forks": false}
}
```

Every key below is optional unless the *Required* column says so; anything
missing falls back to the default shown.

### `settings` — global defaults (a route can override most of them)

| Key | Values | Default | What it does | Change it in |
|---|---|---|---|---|
| `workers` | 1, 2, 4, 8 | `4` | repos synced in parallel | `O` |
| `mode` | `safe`, `full` | `safe` | `safe`: never deletes, never force-pushes. `full`: force-update heads+tags and **delete** target refs the source doesn't have | `O` or per-route `P`→`O` |
| `mesh_conflict` | `newest`, `skip` | `skip` | when two peers disagree on a branch: `newest` overwrites the older side, `skip` leaves both alone and reports `partial` | `O` or per-route `P`→`O` |
| `schedule` | `hourly`, `6h`, `12h`, `daily`, `weekly`, `off` | `hourly` | how often the timer runs. The timer actually fires at the **finest** route schedule | `C` |
| `include_forks` | `true`, `false` | `false` | mirror forked repos too | `O` |
| `pace` | 0, 1, 2, 5, 10 (seconds) | `0` | pause between repos — slows a run to spare laptop heat / disk throttling | `O` |
| `relax_protection` | `true`, `false` | `false` | allow gitsync to turn on force-push over **branch protection** on a destination. Off means a rejected push is reported, never worked around | hand-edit only |
| `token_warn_days` | 7, 14, 30, 60, 90 | `30` | how early a credential warns before it expires | `O` |
| `theme` | `default`, `mono`, `ocean`, `forest`, `sunset` | `default` | TUI colours. `mono` is all-white | `O` |
| `trusted_proxies` | list of CIDRs, e.g. `["127.0.0.1/32","192.168.0.0/24"]` | `[]` | which peers may set `X-Forwarded-For` / `X-Forwarded-Proto`. Empty = ignore both headers (a client cannot fake its address) | web **Settings → Network** |
| `notify.on` | `failure`, `success`, `always`, `never` | `failure` | when to send Discord/Telegram alerts | `O` or `N`→`M` |
| `notify.discord_webhook` | URL | *(empty)* | Discord webhook | `N`→`D` or `notify --set-url` |
| `notify.telegram_token` / `notify.telegram_chat_id` | string | *(empty)* | Telegram bot + chat | `N`→`T` or `notify --set-telegram … --chat-id …` |

### `platforms` — one entry per site

| Key | Applies to | What it does |
|---|---|---|
| `type` | **required** | `github`, `gitlab`, `forgejo`, `gitea`, `bitbucket`, `generic`, `local` |
| `enabled` | all | `false` = never synced, still listed and testable |
| `url` | all but `local` | instance URL. Defaults per type (`https://github.com`, `https://gitlab.com`, `https://bitbucket.org`, `http://localhost:3000`) |
| `token` | all but `local` | API token. **Empty on GitHub** means "use the `gh` CLI" |
| `owner` | all but `local` | your username / workspace / namespace. Blank = auto-detected |
| `auth` | github, gitlab | `gh` (GitHub CLI) or `token`; `glab` (GitLab CLI) or `token`. Set automatically when no token is given. With `glab`/`gh`, git gets a `GIT_ASKPASS` helper instead of a token in the URL |
| `affiliation` | github | which repos to list: `owner` (default) or `owner,organization_member`. **Org repos stay invisible until this is set**, even with the `read:org` scope |
| `ns_id` | gitlab | target namespace id. Blank = your personal namespace (never a group, unless you set it) |
| `protocol` | all but `local` | `http` (default) or `ssh`. SSH needs a key on the host |
| `ssh_port` | all but `local` | SSH port when `protocol: ssh` (default 22) |
| `user` | forgejo, gitea, generic | git username inside token URLs (default: the account login) |
| `url_template` | generic | URL pattern with `{owner}` and `{name}`, e.g. `https://git.example.com/{owner}/{name}.git` |
| `path` | **local (required)** | folder that holds the bare `*.git` mirrors |

### `routes` — one entry per sync direction

| Key | What it does |
|---|---|
| `name` | any label; used by `--route`, `route-schedule`, `pause`/`resume` |
| `source` | platform repos are read from |
| `targets` | list of platforms repos are written to |
| `direction` | `one-way` (default) or `mesh`. **Mesh = every peer is equal**: newest commit wins per branch, refs are unioned, nothing is deleted unless *no* peer has it |
| `enabled` | `false` pauses the whole route — every run, including the timer, skips it |
| `paused_targets` | list of targets to skip while the rest keep syncing |
| `workers` / `mode` / `mesh_conflict` | per-route override of the global value |
| `schedule` | per-route frequency; the timer fires at the finest one |
| `include` / `exclude` | glob lists, e.g. `["Sea*"]` / `["tradex"]` |
| `include_file` | path to a file of exact repo names (one per line, `#` comments) — replaces `include`. A missing file means "no filtering" |

You can enable **several platforms of the same type** — two Forgejo servers,
GitHub plus a self-hosted Gitea, each with its own token and route.

### Any account, any folder, any direction

Everything is config-driven, so the tool is fully generic:

- **Any account** - GitHub, GitLab, Forgejo, Gitea, Bitbucket, or several
  accounts of the same type side by side (`gitsync platforms add ...`)
- **Any folder** - local mirrors to any path (NAS, USB drive, another disk)
- **Any direction** - `github -> gitlab`, `gitlab -> forgejo`,
  `folder -> github`, `bitbucket -> everything`
- **Any subset** - per-route include/exclude globs, fork filtering,
  per-target pushes (`gitsync sync --to gitlab`)

---

[← back to the README](../README.md)

## Export / import

```bash
gitsync config export                    # print it, credentials blanked
gitsync config export --out cfg.json     # write it (mode 0600)
gitsync config import cfg.json           # read one back
```

A gitsync config is the whole install: platforms, routes, schedules, filters
and notification settings. `export` writes a perfect copy **except that every
credential field is emptied** — `token`, `password`, `api_key`,
`webhook_secret`, anything ending in `_token` / `_secret` / `_password` /
`_webhook`, plus `settings.web_token` and `settings.password_hash`. That makes
the file safe to paste into an issue, commit to a dotfiles repo, or hand to a
teammate.

`import` is deliberately conservative:

- credentials already on this machine are **kept**, so importing a redacted
  export does not wipe a working setup;
- the current config is backed up first (`config.json.bak-<timestamp>`);
- platforms that arrive with no credential are listed as needing one
  (`gitsync platforms edit NAME --token …`);
- `--replace` imports verbatim instead.

Anything that is not a gitsync config is rejected before a byte is written.
