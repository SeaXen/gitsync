# gitsync — Step-by-Step Walkthrough

*From "I have a terminal" to "all my repos are mirrored everywhere" — in
order, with exactly what you'll see and what to type.*

---

## Step 0 — What you need

- A terminal (SSH into your server, or a local shell)
- Python 3.8+ (the installer checks and tells you if it's missing)
- Accounts you want to mirror between — you'll log in to them during setup

---

## Step 1 — Copy the install command

```bash
curl -fsSL https://raw.githubusercontent.com/SeaXen/gitsync/main/quickinstall.sh | bash
```

---

## Step 2 — Open a terminal and paste it

Paste the command and press Enter. You'll see:

```
==> gitsync one-click installer
  ✓ python3 3.13.5
==> downloading SeaXen/gitsync@main ...
  ✓ downloaded to /tmp/tmp.XXXX/gitsync
==> gitsync installer
    source : /tmp/tmp.XXXX/gitsync
    target : /opt/gitsync
    command: /usr/local/bin/gitsync
==> systemd timer installed: hourly (Persistent=true)
==> starting the setup wizard (platforms, tokens, sync direction, schedule)
```

That's the download + install + scheduler done. Now the wizard takes over.

---

## Step 3 — Log in to your accounts

### 3.1 Choose your platforms

```
====================================================================
 STEP 1/4 — platforms
====================================================================
 Available: github, gitlab, forgejo, gitea, local
 You can enable several of each kind (e.g. two Forgejo servers).

Which platforms to enable (comma-separated) [github,gitlab,local]
```

Type the ones you use, comma-separated. For example:

```
github,gitlab,forgejo,local
```

`local` = a folder on your own disk for backup.

### 3.2 GitHub — pick your login method

For each platform the wizard prints what it needs **before** asking:

```
====================================================================
 Platform: github (github)
====================================================================
GitHub
  Create token : https://github.com/settings/tokens (classic)
  Required     :
    - repo      Full control of private repositories (required)
    - workflow  Only if .github/workflows files must be mirrored
  Minimum      : repo
  Alternative  : If the gh CLI is installed and logged in (gh auth login),
                 gitsync can use it instead of a token.

Instance URL [https://github.com]
```

Press **Enter** to accept `github.com`, then:

```
Use a personal access token? (No = use gh CLI) (y/N)
```

- **Press Enter (No)** → gitsync uses the `gh` CLI. If `gh` isn't logged
  in yet, run `gh auth login` in another terminal first — it's the same
  device-code flow (open the URL, enter the code, done).
- **Y** → paste a classic token with the `repo` scope.

### 3.3 GitLab

```
Instance URL [https://gitlab.com]
```
Enter. Then:

```
gitlab token
```

Create one at <https://gitlab.com/-/user_settings/personal_access_tokens>
with the **`api`** scope and paste it. gitsync reads the token directly —
there is no `glab` CLI path. The prompt hides what you type.

```
Target namespace id (blank = your personal namespace) []
```
Press **Enter** — your personal namespace is used automatically.

### 3.4 Forgejo / Gitea (self-hosted)

```
Instance URL [http://localhost:3000]
```
Enter your instance (e.g. `http://192.168.0.222:3002`), then:

```
forgejo token
```

Create it at `<your-url>/user/settings/applications` with
**`write:repository` + `write:user`** (both are required) and paste it.

### 3.5 Local backup — pick your folder

```
Local mirror folder (bare repos are created here) [~/git-mirrors]
```

Type your path, for example:

```
/mnt/sg/Sea/6Repo/1git
```

or `/mnt/backup/git`, or just press Enter for `~/git-mirrors`.

---

## Step 4 — Watch it test everything

```
====================================================================
 STEP 2/4 — testing connections
====================================================================
 github       OK  gh CLI authenticated as yourname
 gitlab       OK  token OK — user yourname
 forgejo      OK  token OK — user yourname
 local        OK  folder ready: /mnt/sg/Sea/6Repo/1git
```

Every line must say **OK**. If one says FAIL, the message tells you why
(wrong token, missing scope, unreachable URL) — fix it and re-run
`gitsync setup` when you're done.

---

## Step 5 — Choose the sync direction

```
====================================================================
 STEP 3/4 — sync direction
====================================================================
 Enabled platforms: github, gitlab, forgejo, local

Source platform (repos are read from here) [github]
```
Press **Enter** (or type a different one, e.g. `gitlab`).

```
Targets (comma-separated, repos are written here) [gitlab,forgejo,local]
```
Press **Enter** to mirror to all of them, or pick a subset like `gitlab,local`.

That's it — `github → gitlab + forgejo + local folder`. Any direction works;
this is just yours.

---

## Step 6 — Schedule and options

```
 Schedules: hourly | 6h | 12h | daily | off (manual only)
Schedule [hourly]
```
Press **Enter** for hourly (or type `6h`, `daily`, …).

```
Parallel workers [4]
```
Enter.

```
Exact mirror? This DELETES branches on the destination that are not on
the source (y/N)
```
Press **Enter (No)** → `safe` mode: nothing is ever deleted or force-pushed
on a destination, so your first setup cannot lose work. Answer **y** only
when you want the destination to become an exact copy of the source.

---

## Step 7 — First sync

```
====================================================================
 saved
====================================================================
 Config : /root/.config/gitsync/config.json
 Mirrors: /root/.local/share/gitsync/mirrors
 Log    : /root/.local/share/gitsync/gitsync.log

==> done. next steps:
    gitsync setup     # guided configuration
    ...

Run your first sync now? [Y/n]
```

Press **Enter**. You'll watch every repo mirror out:

```
[2026-11-08 15:19:06] === sync start (mode=full, workers=4) ===
[2026-11-08 15:19:07]   github: 53 repos
[2026-11-08 15:19:12]   [1/53] OK   tradex -> gitlab,forgejo,local
[2026-11-08 15:19:14]   [2/53] OK   agentskills -> gitlab,forgejo,local
...
[2026-11-08 15:19:46] === sync done: 53 ok, 0 failed, 41.6s ===

53 ok, 0 partial, 0 failed of 53 repos in 41.6s
```

The first run is the slow one (it downloads everything). Every later run
takes seconds.

---

## Step 8 — Verify and open the dashboard

```bash
gitsync status      # platforms, routes, scheduler, last run
gitsync doctor      # full health check
gitsync             # open the TUI dashboard
```

---

## Your daily workflow after this

```bash
# work in any clone, then:
git push                          # to your source (e.g. GitHub)

# either wait for the hourly timer, or push it out now:
gitsync sync                      # ~40s for 50 repos

# check anything:
gitsync compare                   # is everything in sync?
gitsync records                   # what happened in past runs?
```

**Golden rule:** always push to your *source* first. The sync mirrors the
source's state outward — commits that exist only on GitLab/Forgejo get
reverted by the next run.

---

## Changing things later

| I want to… | Do this |
|---|---|
| Add another account | `gitsync platforms add work --type gitlab --token glpat-...` |
| Change a token | `gitsync platforms edit gitlab --token glpat-NEW` |
| Pause an account | `gitsync platforms edit gitlab --disable` |
| Remove an account | `gitsync platforms remove oldserver` |
| Add another route | `gitsync routes add restore local github` |
| Only mirror some repos | `gitsync routes add sea github backup --include "Sea*"` |
| Change the schedule | `gitsync install --schedule 6h` (or `C` in the TUI) |
| Get Discord/Telegram alerts | press `N` in the TUI, or `gitsync notify --test` |
| Sync only to one target | `gitsync sync --to gitlab` |
| Sync one repo | `gitsync mirror tradex` |
| Repair drift | `gitsync fix` |
| Fix a broken mirror | `gitsync doctor --fix` |
| Re-run the whole wizard | `gitsync setup` (or `W` in the TUI) |

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| A platform shows FAIL | `gitsync platforms` shows why; check scopes with `gitsync scopes` |
| `gh` isn't logged in | `gh auth login` in another terminal, then `gitsync platforms test github` |
| Install says "python3 not found" | `apt install python3` (Debian) / `brew install python3` |
| Wanted to install without root | re-run with `--user` |
| TUI looks garbled | resize the terminal; it adapts (phones get a compact layout) |
