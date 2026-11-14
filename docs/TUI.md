# The terminal UI

`gitsync` with no arguments opens the dashboard. It needs a real terminal
(a TTY); over SSH that is automatic.

The TUI and every subprocess it starts run the *installed* copy in
`/opt/gitsync`, so after editing source, re-run `bash install.sh`.

## The TUI

```
 gitsync 2.1.7 — repository sync control
 2026-11-14 09:14:22  syncing: no  schedule: Every hour  counts: 3s old
┌ PLATFORMS ────────────────────────────────────────────────────────────┐
│ github      github    ON   OK    repos=53    gh CLI authenticated    │
│ gitlab      gitlab    ON   OK    repos=56    token OK — user alice   │
│ forgejo     forgejo   ON   OK    repos=56    token OK — user bob     │
│ backup      local     ON   OK    repos=53    folder ready: /mnt/...  │
└───────────────────────────────────────────────────────────────────────┘
┌ ROUTES & SETTINGS ────────────────────────────────────────────────────┐
│ github-to-all: github -> gitlab, forgejo, backup                      │
│ workers=4  mode=safe  forks=False   scheduler: user timer: hourly    │
└───────────────────────────────────────────────────────────────────────┘
┌ LOG (live tail — L for full log, H for history) ──────────────────────┐
│ [52/52] OK   Sea-Wallpaper -> gitlab,forgejo,backup                   │
│ === sync done: 52 ok, 0 failed, 37.8s ===                             │
└───────────────────────────────────────────────────────────────────────┘
 [1-7]tabs  [S]ync  [P]ause  [D]ry-run  [L]og  [C]ron  [O]pt  [A]ccounts  [E]repos  [Q]uit
```

| Key | Action |
|---|---|
| `1`–`7` | Switch tabs: Dashboard (1), Platforms (2), Routes (3), Stars (4), Log (5), History (6), Settings (7) |
| `[` / `]` or `←` / `→` | Cycle previous / next tab |
| `S` | Run a sync now (live output + progress bar; confirmation required for full-mode prune) |
| `D` | **Dry run** — preview a whole run or a single route without changing data |
| `P` | **Pause / resume** global sync (or toggle route/target on Routes tab) |
| `L` | Full log viewer (scroll, `/` to filter, `G`/`End` bottom, `Home` top) |
| `H` | Interactive run history screen + duration trend sparkline |
| `C` | Schedule manager: hourly / 6h / 12h / daily / weekly / off |
| `O` | Global options: workers, mirror mode, forks, notifications, pacing, mesh policy, token warning, theme |
| `A` | Accounts manager: add / edit / remove / test / enable-disable site, `L` GitHub browser login, `V` browse repos, `?` scopes |
| `E` | **Repo browser** — see repos, mirror status, pick selection, `+` add to route, `P` switch account, `S` all / `T` one site / `L` local only |
| `T` | **Tools menu**: disk usage + orphans, doctor, compare sites, mirror one repo, fix drift, update, uninstall |
| `N` | Notifications manager: Discord, Telegram, ntfy, webhook, and email |
| `W` | Setup wizard |
| `R` | Refresh platform connection status and repo counts |
| `↑` / `↓` or `k` / `j` | Scroll logs on Dashboard & Log tab, or items in lists |
| `/` | Search filter across log lines or repository lists (`Esc` to clear) |
| `?` | Global keyboard shortcuts and help screen |
| `Q` | Quit (`Esc` or `B` = back / cancel in modal screens) |
| mouse | Click tab bar, `‹` / `›` tab indicators, or rows in list screens |

**Adaptive:** switches to a compact single-column layout on phones and narrow
terminals (≥70×18 → full dashboard; smaller → compact; <34×10 → rotate hint).

---

[← back to the README](../README.md)
