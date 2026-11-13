# The terminal UI

`gitsync` with no arguments opens the dashboard. It needs a real terminal
(a TTY); over SSH that is automatic.

The TUI and every subprocess it starts run the *installed* copy in
`/opt/gitsync`, so after editing source, re-run `bash install.sh`.

## The TUI

```
 gitsync 1.0.0 — repository sync control
 2026-11-08 09:14:22  syncing: no  schedule: Every hour  counts: 3s old
┌ PLATFORMS ────────────────────────────────────────────────────────────┐
│ github      github    ON   OK    repos=53    gh CLI authenticated    │
│ gitlab      gitlab    ON   OK    repos=56    token OK — user alice   │
│ forgejo     forgejo   ON   OK    repos=56    token OK — user bob    │
│ backup      local     ON   OK    repos=53    folder ready: /mnt/...   │
└───────────────────────────────────────────────────────────────────────┘
┌ ROUTES & SETTINGS ────────────────────────────────────────────────────┐
│ github-to-all: github -> gitlab, forgejo, backup                      │
│ workers=4  mode=safe  forks=False   scheduler: user timer: hourly    │
└───────────────────────────────────────────────────────────────────────┘
┌ LOG (live tail — L for full log, H for history) ──────────────────────┐
│ [52/52] OK   Sea-Wallpaper -> gitlab,forgejo,backup                   │
│ === sync done: 52 ok, 0 failed, 37.8s ===                             │
└───────────────────────────────────────────────────────────────────────┘
 [S]ync  [L]og  [H]istory  [C]chedule  [O]ptions  [A]ccounts  [E]repos  [Q]uit
```

| Key | Action |
|---|---|
| `S` | run a sync now (live output + progress bar) |
| `D` | **dry run** — preview a whole run, or one route from the pause screen |
| `L` | full log viewer (scroll, `/` to filter, `G`/`End` bottom, `Home` top) |
| `H` | run history + duration trend |
| `C` | schedule: hourly / 6h / 12h / daily / weekly / off |
| `O` | options: workers, mirror mode, forks, notify, **pacing**, mesh policy, token warning, **theme** |
| `A` | accounts: add / edit / remove / test / enable-disable a site, `L` GitHub browser login, `V` browse repos, `?` scopes |
| `P` (Dashboard) | **pause / resume global sync** (suspends background timer and manual syncs) |
| `P` (Routes tab) | pause/resume a target or a route · `S` run one route · `D` dry-run it · `F` filters · `A` add · `X` remove · `O` per-route options |
| `E` | **repo browser** — see repos, mirror status, pick some, `+` add to route, `P` switch account, `S` all / `T` one site / `L` local only |
| `T` | **tools**: disk + orphans, doctor, compare, mirror one repo, fix drift, update, uninstall (run in the background, output streams to the dashboard) |
| `N` | notifications: Discord, Telegram, ntfy, webhook and email — mode `M`, test Enter, webhook receiver `W` |
| `R` | refresh platform connections |
| `W` | setup wizard |
| `1`–`7` | Dashboard · Platforms · Routes · Stars · Log · History · Settings |
| mouse | click a tab, or a row in the repo browser / pause screen |
| `W` | re-run the setup wizard |
| `R` | refresh platform connections |
| `P` (Routes tab) | **pause / resume** a route or one of its targets |
| `P` (other tabs) | refresh platform connections |
| `D` | **dry run** — preview a whole run, or one route from the pause screen |
| `S` (pause screen) | run **just that route** now |
| `F` (pause screen) | edit that route's include/exclude filters |
| `O` (pause screen) | per-route workers / mode / mesh conflict |
| `W` (notifications) | webhook receiver: secret, URL, install/stop |
| `G` / `End` / `Home` | jump to the bottom / top of the log viewer |
| mouse | click a tab, or a row in the repo browser / pause screen |
| `T` | tools menu (disk, doctor, compare, mirror, update, uninstall) |
| `1`–`6` | switch tabs: Dashboard / Platforms / Routes / Log / History / Settings |
| `/` | search-filter the log or history |
| `Q` | quit (Esc = back inside menus) |

**Adaptive:** switches to a compact single-column layout on phones and narrow
terminals (≥70×18 → full dashboard; smaller → compact; <34×10 → rotate hint).

---

[← back to the README](../README.md)
