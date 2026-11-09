"""gitsync TUI — the dashboard. Run with: gitsync tui

Tabs (number keys):  1 Dashboard  2 Platforms  3 Routes  4 Log  5 History
                      6 Settings
Keys:  S sync now | E repos (browse + selective sync) | / search |
       arrows scroll | R refresh | C schedule | O options | A accounts |
       N notify | W wizard | Q quit
"""

import curses
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
from datetime import datetime

from . import __version__
from . import config as C
from . import notify as NT
from . import permissions as PERM
from . import platforms as P
from . import scheduler as S
from . import diskutil as DK
from . import sync as SYNC
from . import stars as ST
from . import tokencheck as TK
from . import webhooks as WH

TABS = ["Dashboard", "Platforms", "Routes", "Stars", "Log", "History",
        "Settings"]

state = {
    "platforms": [],       # [(name, type, on, ok, info, count)]
    "routes": [],
    "settings": {},
    "log_tail": [],
    "records": [],
    "counts_age": time.time(),
    "syncing": False,
    "sync_lines": [],
    "message": ("", 0),
    "full_log": [],
    "scroll": 0,
    "dirty": True,
    "tab": 0,
    "search": "",
    "search_mode": False,
    "repo_list": [],        # [(name, private, fork)]
    "repo_sel": set(),      # selected repo names
    "repo_scroll": 0,
    "repo_loaded": False,
    "repo_loading": False,
    "repo_src": "",          # which platform the browser listed
    "star_targets": set(),
    "star_cur": 0,
}


def sh(cmd, timeout=30, stdin_text=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=stdin_text)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def flash(text, secs=4):
    state["message"] = (text, time.time() + secs)


# ---------------------------------------------------------------- data

_refreshing = threading.Lock()


def refresh_platforms():
    # never stack refreshes: a hung API call must not spawn another thread
    if not _refreshing.acquire(blocking=False):
        return
    try:
        _refresh_platforms_inner()
    finally:
        _refreshing.release()


def _probe(name, p):
    """test one platform and count its repos (run in a worker thread)"""
    try:
        plat = P.make(name, p)
        ok, info = plat.test()
        count = "?"
        if ok and p.get("enabled"):
            try:
                count = str(len(plat.list_repos()))
            except Exception:
                count = "?"
    except Exception as e:
        ok, info, count = False, str(e)[:80], "?"
    return (name, p.get("type", "?"), bool(p.get("enabled")),
            bool(ok), info, count)


def _refresh_platforms_inner():
    state["counts_age"] = time.time()   # advance first: hung calls can't re-arm
    cfg = C.load_config()
    # probing is network-bound and used to be sequential: four sites took
    # ~8s, which is the "loading..." you see at start-up and on R. Running
    # them together cuts it to the slowest single site.
    plats = list((cfg.get("platforms") or {}).items())
    rows = [None] * len(plats)
    with ThreadPoolExecutor(max_workers=max(1, len(plats))) as ex:
        futs = {ex.submit(_probe, n, p): i for i, (n, p) in enumerate(plats)}
        for f in as_completed(futs):
            rows[futs[f]] = f.result()
    state["platforms"] = [r for r in rows if r]
    state["routes"] = [(r.get("name"), r.get("source"),
                        list(r.get("targets", [])),
                        r.get("include") or [], r.get("exclude") or [],
                        r.get("direction") or "one-way",
                        list(r.get("paused_targets") or []),
                        r.get("enabled") is not False)
                       for r in C.routes(cfg)]
    state["route_sched"] = {r.get("name"): r.get("schedule")
                            for r in C.routes(cfg)}
    state["settings"] = dict(cfg.get("settings", {}))
    state["counts_age"] = time.time()
    state["dirty"] = True


def read_log_tail(n=150):
    try:
        with open(C.LOG_FILE) as f:
            state["log_tail"] = f.read().splitlines()[-n:]
    except OSError:
        state["log_tail"] = []


def read_full_log():
    try:
        with open(C.LOG_FILE) as f:
            state["full_log"] = f.read().splitlines()
    except OSError:
        state["full_log"] = []


def run_sync(dry_run=False):
    if state["syncing"]:
        flash("a sync is already running")
        return
    state["syncing"] = True
    label = "DRY RUN" if dry_run else "sync"
    state["sync_lines"] = ["--- %s started %s ---"
                           % (label, datetime.now().strftime("%H:%M:%S"))]

    def worker():
        try:
            cmd = ["python3", "-m", "gitsync", "sync"]
            if dry_run:
                cmd.append("--dry-run")
            p = subprocess.Popen(cmd,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 env=dict(os.environ,
                                          PYTHONPATH=_app_dir()))
            for line in p.stdout:
                state["sync_lines"].append(line.rstrip())
                state["dirty"] = True
            p.wait()
            state["sync_lines"].append("--- finished (rc=%d) %s ---"
                                       % (p.returncode,
                                          datetime.now().strftime("%H:%M:%S")))
        except Exception as e:
            state["sync_lines"].append("--- error: %s ---" % e)
        finally:
            state["syncing"] = False
            read_log_tail()
            state["records"] = SYNC.last_records(10)
            state["dirty"] = True
            merged = _merged_refs(state["sync_lines"])
            if merged:
                flash("sync merged %d ref(s) across peers" % merged, 8)
            threading.Thread(target=refresh_platforms, daemon=True).start()
            threading.Thread(target=refresh_health, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()


_REPO_RE = re.compile(r"\[(\d+)\s*/\s*(\d+)\]\s+(\S+)\s+(\S+)")


def sync_progress():
    """(done, total, current_repo) read from the child sync's own output.

    The sync runs in a subprocess, so SYNC.progress in *this* process is
    always empty — the bar used to show "starting..." forever. The child
    prints "[12/54] OK   repo -> targets", which is just as good a feed.
    """
    done = total = 0
    current = ""
    for line in state.get("sync_lines") or []:
        m = _REPO_RE.search(line)
        if not m:
            continue
        d, t = int(m.group(1)), int(m.group(2))
        if t:
            total = t
        done = max(done, d)
        current = m.group(4)
    return done, total, current


def _merged_refs(lines):
    """total refs a mesh run merged, for the end-of-sync flash"""
    total = 0
    for line in lines or []:
        m = re.search(r"\((\d+) refs merged\)", line)
        if m:
            total += int(m.group(1))
    return total


def _app_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- draw

_COLOR_NAMES = {
    "black": curses.COLOR_BLACK, "red": curses.COLOR_RED,
    "green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW,
    "blue": curses.COLOR_BLUE, "magenta": curses.COLOR_MAGENTA,
    "cyan": curses.COLOR_CYAN, "white": curses.COLOR_WHITE,
}


def apply_theme(name=None):
    """(re)load the four colour pairs from the configured theme.

    Called at start-up and again the moment the setting changes, so the
    whole TUI recolours without a restart.
    """
    if name is None:
        name = C.setting(C.load_config(), "theme", "default")
    spec = C.THEMES.get(name) or C.THEMES["default"]
    try:
        for i, cname in enumerate(spec, 1):
            curses.init_pair(i, _COLOR_NAMES.get(cname, curses.COLOR_WHITE),
                             -1)
    except curses.error:
        pass


def safe_add(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w:
        try:
            win.addnstr(y, x, str(text), max(0, w - x - 1), attr)
        except curses.error:
            pass


def commit_screen(stdscr=None):
    """atomic screen update — prevents flicker (draw offscreen, flip once).

    Sub-windows are noutrefreshed where they are drawn; stdscr's addstr
    already updates the virtual screen, so a plain douupdate() flushes
    everything at once. (Calling stdscr.noutrefresh() here would copy the
    erased stdscr over the panels and blank them.)
    """
    curses.doupdate()


def menu(stdscr, title, items, note=None):
    idx = 0
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, 0, 0, (" %s " % title).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        if note:
            safe_add(stdscr, 1, 0, " %s" % note[:w - 2], curses.A_DIM)
        lbl_w = max(12, min(40, w - 16))     # adapt to narrow screens
        for i, (label, right, _) in enumerate(items):
            if 3 + i >= h - 1:
                break
            attr = curses.A_REVERSE if i == idx else 0
            safe_add(stdscr, 3 + i, 0,
                     ("  %-*s %s" % (lbl_w, label[:lbl_w],
                                     right or ""))[:w - 1], attr)
        safe_add(stdscr, h - 1, 0,
                 " up/dn=move Enter=ok B=back ".center(w - 1)[:w - 1],
                 curses.A_REVERSE)
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("b"), ord("B"), 27):
            return
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(items) - 1, idx + 1)
        elif ch in (curses.KEY_ENTER, 10, 13):
            items[idx][2]()
            return


def schedule_screen(stdscr):
    cur = C.setting(C.load_config(), "schedule", "hourly")
    items = []
    for key in ["hourly", "6h", "12h", "daily", "weekly", "off"]:
        lbl = C.SCHEDULES[key]["label"]
        items.append((lbl + ("   <-- current" if key == cur else ""),
                      C.SCHEDULES[key]["cron"] or "manual only",
                      (lambda k=key: apply_schedule(k))))
    menu(stdscr, "SCHEDULE — automatic sync", items,
         note="current: %s   | scheduler: %s"
              % (C.SCHEDULES[cur]["label"], cached_status()))


def apply_schedule(key):
    cfg = C.load_config()
    C.set_setting(cfg, "schedule", key)
    if key == "off":
        S.uninstall_systemd(system=True)
        S.uninstall_systemd()
        S.install_cron("off")
        flash("schedule OFF — manual syncs only (S)")
        return
    if S.systemd_installed() or S.have_systemd():
        # the timer must fire as often as the most demanding route needs,
        # not as often as the global setting says
        if S.install_systemd(C.finest_schedule(cfg) or key):
            flash("timer: %s" % C.SCHEDULES[key]["label"])
            return
    S.install_cron(C.finest_schedule(cfg) or key)
    flash("cron: %s" % C.SCHEDULES[key]["label"])


def options_screen(stdscr):
    cfg = C.load_config()
    items = [
        ("Parallel workers: %s" % C.setting(cfg, "workers", 4),
         "1 = slow, 8 = fast", lambda: cycle(cfg, "workers",
                                             [1, 2, 4, 8], "workers")),
        ("Mirror mode: %s" % C.setting(cfg, "mode", "full"),
         "full = prunes, safe = keeps", lambda: cycle(
             cfg, "mode", ["full", "safe"], "mirror mode")),
        ("Include forks: %s" % C.setting(cfg, "include_forks", False),
         "mirror forked repos too", lambda: cycle(
             cfg, "include_forks", [False, True], "include_forks")),
        ("Notify on: %s" % C.setting(cfg, "notify:on", "failure"),
         "failure | success | always | never", lambda: cycle(
             cfg, "notify:on", ["failure", "success", "always", "never"],
             "notify on")),
        ("Pause between repos: %ss" % (C.setting(cfg, "pace", 0) or 0),
         "0 = flat out; slows a run to spare heat/disk",
         lambda: cycle(cfg, "pace", [0, 1, 2, 5, 10], "pace")),
        ("Mesh conflicts: %s" % C.setting(cfg, "mesh_conflict", "newest"),
         "newest = overwrite; skip = leave and report",
         lambda: cycle(cfg, "mesh_conflict", ["newest", "skip"],
                       "mesh conflicts")),
        ("Token warning: %s days" % (C.setting(cfg, "token_warn_days", 30)
                                     or 30),
         "warn this long before a credential expires",
         lambda: cycle(cfg, "token_warn_days", [7, 14, 30, 60, 90],
                       "token warning")),
        ("Colour theme: %s" % C.setting(cfg, "theme", "default"),
         "mono = no colour; the rest change the accent",
         lambda: (cycle(cfg, "theme", list(C.THEMES), "theme"),
                  apply_theme())),
    ]
    menu(stdscr, "OPTIONS", items, note="saved to %s" % C.CONFIG_FILE)


def cycle(cfg, key, values, label):
    if ":" in key:                     # nested settings key (notify:on)
        parent, child = key.split(":", 1)
        cur = cfg.get("settings", {}).get(parent, {}).get(child, values[0])
        nxt = values[(values.index(cur) + 1) % len(values)] \
            if cur in values else values[0]
        cfg.setdefault("settings", {}).setdefault(parent, {})[child] = nxt
        C.save_config(cfg)
        flash("%s -> %s" % (label, nxt))
        return
    cur = C.setting(cfg, key, values[0])
    nxt = values[(values.index(cur) + 1) % len(values)] \
        if cur in values else values[0]
    C.set_setting(cfg, key, nxt)
    flash("%s -> %s" % (label, nxt))


def _form(stdscr, title, fields, note=None):
    """a small form: [(key, label, value, {'secret':bool,'choices':[..]})]

    Enter edits the highlighted field (or cycles it when it has choices),
    up/dn or Tab move, S saves, B/Esc cancels. Returns {key: value} or None.
    """
    vals = {f[0]: f[2] for f in fields}
    idx = 0
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, 0, 0, (" %s " % title).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        if note:
            safe_add(stdscr, 1, 0, " %s" % note[:w - 2], curses.A_DIM)
        for i, (key, label, _v, opts) in enumerate(fields):
            if 3 + i >= h - 2:
                break
            attr = curses.A_REVERSE if i == idx else 0
            shown = ("*" * len(str(vals[key]))) if opts.get("secret") \
                else str(vals[key])
            hint = opts.get("hint") or ""
            safe_add(stdscr, 3 + i, 0,
                     ("  %-16s %-28s %s" % (label, shown[:28],
                                            hint))[:w - 1], attr)
        modal_footer(stdscr, h, w,
                     " Enter=edit/cycle  up/dn=move  S=save  B=cancel ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("b"), ord("B"), 27):
            return None
        if ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j"), 9):
            idx = min(len(fields) - 1, idx + 1)
        elif ch in (ord("s"), ord("S")):
            return vals
        elif ch in (10, 13, curses.KEY_ENTER):
            key, label, _v, opts = fields[idx]
            if opts.get("choices"):
                cur = vals[key]
                ch_o = opts["choices"]
                vals[key] = ch_o[(ch_o.index(cur) + 1) % len(ch_o)] \
                    if cur in ch_o else ch_o[0]
                continue
            new = prompt_line(stdscr, " %s: " % label, str(vals[key]))
            if new is not None and new != "":
                vals[key] = new
            elif new == "" and opts.get("clearable"):
                vals[key] = ""


def _platform_rows(cfg):
    """[(name, type, enabled, ok, info, has_token, extra)] for the manager"""
    rows = []
    tested = {r[0]: (r[3], r[4]) for r in (state.get("platforms") or [])}
    for name, p in (cfg.get("platforms") or {}).items():
        ok, info = tested.get(name, (None, ""))
        extra = p.get("path") or p.get("url") or ""
        rows.append((name, p.get("type", "?"), bool(p.get("enabled")),
                     bool(ok) if ok is not None else None, info,
                     bool(p.get("token")), extra,
                     p.get("auth") == "gh" and not p.get("token")))
    return rows


def accounts_screen(stdscr):
    """the account manager: add / edit / remove / test / enable-disable.

    Everything `gitsync platforms ...` does on the command line, so no
    terminal is needed to connect a new site.
    """
    idx = 0
    top = 0
    while True:
        cfg = C.load_config()
        rows = _platform_rows(cfg)
        idx = max(0, min(idx, len(rows) - 1)) if rows else 0

        stdscr.erase()
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, 0, 0,
                 (" ACCOUNTS — %d site(s) " % len(rows)).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        safe_add(stdscr, 1, 0,
                 " A=add  E=edit  X=remove  T=test  V=view repos  ?=scopes  "
                 "Space=on/off  R=refresh  B=back ".ljust(w - 1)[:w - 1],
                 curses.A_DIM)
        avail = max(1, h - 5)
        if idx < top:
            top = idx
        elif idx - top >= avail:
            top = idx - avail + 1
        for i, row in enumerate(rows[top:top + avail]):
            (name, ptype, on, ok, info, has_tok, extra, gh_auth) = row
            attr = curses.A_REVERSE if (top + i) == idx else 0
            if not on:
                attr |= curses.A_DIM
            mark = "[x]" if on else "[ ]"
            if ok is None:
                state_txt = "?"
            elif ok:
                state_txt = "OK"
            else:
                state_txt = "FAIL"
            tok = "gh-CLI" if gh_auth else ("token" if has_tok else "no token")
            line = " %s %-14s %-9s %-5s %-8s %s" % (
                mark, name[:14], ptype, state_txt, tok, extra[:w - 52])
            safe_add(stdscr, 3 + i, 0, line[:w - 1], attr)
        if not rows:
            safe_add(stdscr, 3, 0, " (no platforms yet - press A to add one)",
                     curses.A_DIM)
        if len(rows) > avail:
            safe_add(stdscr, 2, 0, " %d-%d of %d " % (top + 1,
                                                      min(top + avail,
                                                          len(rows)),
                                                      len(rows)),
                     curses.A_DIM)
        modal_footer(stdscr, h, w,
                     " A=add  E=edit  X=remove  T=test  V=view repos  "
                     "?=scopes  Space=on/off  up/dn=move  B=back ")
        commit_screen(stdscr)

        ch = stdscr.getch()
        if ch in (ord("b"), ord("B"), 27):
            return
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(rows) - 1, idx + 1) if rows else 0
        elif ch in (curses.KEY_PPAGE,):
            idx = max(0, idx - avail)
        elif ch in (curses.KEY_NPAGE,):
            idx = min(len(rows) - 1, idx + avail) if rows else 0
        elif ch in (ord("r"), ord("R")):
            threading.Thread(target=refresh_platforms, daemon=True).start()
        elif ch in (ord("?"),):
            _scopes_screen(stdscr)
        elif ch in (ord("v"), ord("V")) and rows:
            # browse this site's repos without leaving the manager
            repos_screen(stdscr, source=rows[idx][0])
        elif ch in (ord(" "), 10, 13, curses.KEY_ENTER):
            if not rows:
                continue
            _toggle_platform(rows[idx][0])
        elif ch in (ord("t"), ord("T")) and rows:
            _test_platform(rows[idx][0])
        elif ch in (ord("a"), ord("A")):
            made = _add_platform_screen(stdscr)
            if made:
                names = [r[0] for r in _platform_rows(C.load_config())]
                if made in names:
                    idx = names.index(made)
                    top = max(0, idx - avail + 1)
            else:
                idx = 0
                top = 0
        elif ch in (ord("e"), ord("E")) and rows:
            _edit_platform_screen(stdscr, rows[idx][0])
        elif ch in (ord("x"), ord("X")) and rows:
            _remove_platform_screen(stdscr, rows[idx][0])


def _scopes_screen(stdscr):
    """what token each kind of site needs, and where to create it"""
    lines = ["TOKEN SCOPES - what each platform needs", ""]
    for ptype in C.PLATFORM_TYPES:
        g = PERM.guide(ptype)
        lines.append("  %-24s minimum: %s" % (g["title"], g["minimum"]))
        if g.get("token_url"):
            lines.append("      create at: %s" % g["token_url"])
        lines.append("")
    lines += ["Several platforms of the same kind are supported - each gets",
              "its own name, token and routes.",
              "",
              "Press B to go back."]
    _scroll_view(stdscr, " TOKEN SCOPES ", lines)


def _toggle_platform(name):
    cfg = C.load_config()
    p = (cfg.get("platforms") or {}).get(name)
    if not p:
        return
    p["enabled"] = not p.get("enabled", True)
    C.save_config(cfg)
    _reload_routes()
    flash("%s: %s" % (name, "enabled" if p["enabled"] else "disabled"), 4)
    threading.Thread(target=refresh_platforms, daemon=True).start()


def _test_platform(name):
    cfg = C.load_config()
    p = (cfg.get("platforms") or {}).get(name)
    if not p:
        return
    flash("%s: testing ..." % name, 6)
    state["dirty"] = True

    def work():
        try:
            ok, info = P.make(name, p).test()
        except Exception as e:
            ok, info = False, str(e)[:80]
        flash("%s: %s" % (name, info[:60] if ok else "FAIL " + info[:50]), 8)
        state["dirty"] = True
    threading.Thread(target=work, daemon=True).start()


def _remove_platform_screen(stdscr, name):
    cfg = C.load_config()
    used = C.platform_used_by(cfg, name)
    h, w = stdscr.getmaxyx()
    while True:
        stdscr.erase()
        safe_add(stdscr, 0, 0,
                 (" REMOVE %s " % name).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(4))
        if used:
            safe_add(stdscr, 2, 1,
                     "used by route(s): %s" % ", ".join(used)[:w - 4],
                     curses.color_pair(3))
            safe_add(stdscr, 3, 1,
                     "removing it leaves those routes incomplete",
                     curses.A_DIM)
        else:
            safe_add(stdscr, 2, 1, "no route uses this platform",
                     curses.A_DIM)
        modal_footer(stdscr, h, w,
                     " Y=remove  N=keep it  (this cannot be undone) ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("n"), ord("N"), ord("b"), ord("B"), 27):
            return
        if ch in (ord("y"), ord("Y")):
            err = C.remove_platform(cfg, name, force=True)
            if err:
                flash("cannot remove: %s" % err[:50], 6)
                return
            C.save_config(cfg)
            _reload_routes()
            flash("%s: removed" % name, 5)
            threading.Thread(target=refresh_platforms, daemon=True).start()
            return


def _add_platform_screen(stdscr):
    """pick a type, then fill in the form - the same fields the CLI asks for.

    Returns the new platform's name so the caller can keep the cursor on it
    (resetting to row 0 made the next key press hit the wrong account).
    """
    made = []

    def make(tt):
        made.append(_platform_form(stdscr, tt))

    items = []
    for t in C.PLATFORM_TYPES:
        g = PERM.guide(t)
        items.append((g["title"], g["minimum"][:40],
                      (lambda tt=t: make(tt))))
    menu(stdscr, "ADD A SITE - which kind?", items,
         note="github, gitlab, forgejo, gitea, bitbucket, generic git, "
              "local folder")
    return made[0] if made else None


def _platform_form(stdscr, ptype):
    """the add/edit form for one platform"""
    name = ""
    editing = False
    if ptype == "local":
        fields = [("name", "name", "", {"hint": "e.g. backup"}),
                  ("path", "folder", "~/git-mirrors", {})]
    else:
        fields = [("name", "name", "",
                   {"hint": "e.g. gitlab, forgejo2"}),
                  ("url", "url", C.DEFAULT_URLS.get(ptype,
                                                    "http://localhost:3000"),
                   {}),
                  ("token", "token", "",
                   {"secret": True,
                    "hint": "blank = gh CLI" if ptype == "github"
                    else "API token"}),
                  ("owner", "owner", "",
                   {"hint": "blank = auto-detect"}),
                  ("protocol", "protocol", "http",
                   {"choices": ["http", "ssh"]}),
                  ("ssh_port", "ssh port", "22",
                   {"hint": "only used with ssh"})]
    vals = _form(stdscr, "ADD %s SITE" % ptype.upper(), fields,
                 note="several of the same kind are fine - each gets its "
                      "own name and routes")
    if not vals or not vals.get("name"):
        flash("cancelled", 3)
        return None
    name = vals["name"].strip()
    if ptype == "local":
        spec = C.platform_template("local", name, path=vals.get("path"))
    else:
        spec = C.platform_template(
            ptype, name, url=vals.get("url"), token=vals.get("token"),
            owner=vals.get("owner") or None, protocol=vals.get("protocol"),
            ssh_port=vals.get("ssh_port"))
    cfg = C.load_config()
    err = C.add_platform(cfg, name, spec)
    if err:
        flash(err[:70], 6)
        return None
    C.save_config(cfg)
    flash("%s: added - testing..." % name, 6)
    _reload_routes()
    threading.Thread(target=refresh_platforms, daemon=True).start()
    _test_platform(name)
    return name


def _edit_platform_screen(stdscr, name):
    cfg = C.load_config()
    p = (cfg.get("platforms") or {}).get(name)
    if not p:
        return
    if p.get("type") == "local":
        fields = [("path", "folder", p.get("path", ""), {})]
    else:
        fields = [("url", "url", p.get("url", ""), {}),
                  ("token", "token", p.get("token", ""),
                   {"secret": True, "hint": "blank = gh CLI"
                    if p.get("type") == "github" else "API token"}),
                  ("owner", "owner", p.get("owner", ""), {}),
                  ("protocol", "protocol", p.get("protocol", "http"),
                   {"choices": ["http", "ssh"]}),
                  ("ssh_port", "ssh port", str(p.get("ssh_port", 22)),
                   {"hint": "only used with ssh"})]
    vals = _form(stdscr, "EDIT %s (%s)" % (name, p.get("type")), fields,
                 note="leave a field as it is to keep it; token is hidden")
    if not vals:
        return
    if p.get("type") != "local":
        if vals.get("url"):
            p["url"] = vals["url"]
        if vals.get("token"):
            p["token"] = vals["token"]
        if vals.get("owner"):
            p["owner"] = vals["owner"]
        if vals.get("protocol"):
            p["protocol"] = vals["protocol"]
        try:
            p["ssh_port"] = int(vals.get("ssh_port") or 22)
        except (TypeError, ValueError):
            pass
        if p.get("type") == "github" and not p.get("token"):
            p["auth"] = "gh"
    else:
        if vals.get("path"):
            p["path"] = os.path.expanduser(vals["path"])
    C.save_config(cfg)
    _reload_routes()
    flash("%s: saved" % name, 4)
    threading.Thread(target=refresh_platforms, daemon=True).start()
    _test_platform(name)


def _input_line(stdscr, prompt, secret=False):
    """single-line text input (used by the notifications setup screen)"""
    h, w = stdscr.getmaxyx()
    text = ""
    curses.curs_set(1)
    try:
        while True:
            stdscr.erase()
            safe_add(stdscr, 0, 0, (" %s " % prompt).center(w - 1)[:w - 1],
                     curses.A_BOLD | curses.color_pair(1))
            shown = ("*" * len(text)) if secret else text
            safe_add(stdscr, 2, 1, "> %s_" % shown[:w - 6])
            safe_add(stdscr, h - 1, 0,
                     " type, Enter = save, Esc = cancel ".center(w - 1)[:w - 1],
                     curses.A_REVERSE)
            commit_screen(stdscr)
            ch = stdscr.getch()
            if ch in (27,):
                return None
            if ch in (10, 13, curses.KEY_ENTER):
                return text.strip()
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                text = text[:-1]
            elif 32 <= ch < 127:
                text += chr(ch)
    finally:
        curses.curs_set(0)


def notifications_screen(stdscr):
    """add / edit / test Discord + Telegram notification channels"""
    cfg = C.load_config()
    while True:
        n = cfg.get("settings", {}).get("notify", {}) or {}
        d_url = n.get("discord_webhook", "")
        t_tok = n.get("telegram_token", "")
        t_chat = n.get("telegram_chat_id", "")
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, 0, 0, " NOTIFICATIONS ".center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        G, R, dim = (curses.color_pair(2), curses.color_pair(4),
                     curses.A_DIM)
        safe_add(stdscr, 2, 1, "Discord webhook : %s"
                 % ("configured" if d_url else "not set"),
                 G if d_url else dim)
        if d_url:
            safe_add(stdscr, 3, 1, "  %s...%s" % (d_url[:38], d_url[-12:]),
                     dim)
        safe_add(stdscr, 4, 1, "Telegram bot    : %s"
                 % ("configured" if t_tok else "not set"),
                 G if t_tok else dim)
        if t_tok:
            safe_add(stdscr, 5, 1, "  chat id: %s" % (t_chat or "(missing)"),
                     dim)
        safe_add(stdscr, 7, 1, "Notify on       : %s"
                 % n.get("on", "failure"), curses.A_BOLD)
        safe_add(stdscr, 9, 1, "credentials are stored in YOUR config file",
                 dim)
        safe_add(stdscr, 10, 1, "(%s) - never in the code" % C.CONFIG_FILE,
                 dim)
        modal_footer(stdscr, h, w,
                     " [D]iscord  [T]elegram  [M]ode  [W]ebhook  "
                     "[Enter]test  [B]ack ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("b"), ord("B"), 27):
            return
        elif ch in (ord("d"), ord("D")):
            val = _input_line(stdscr, "Discord webhook URL", secret=True)
            if val is not None:
                cfg.setdefault("settings", {}).setdefault("notify", {})
                cfg["settings"]["notify"]["discord_webhook"] = val
                C.save_config(cfg)
                flash("discord webhook saved")
        elif ch in (ord("t"), ord("T")):
            tok = _input_line(stdscr, "Telegram bot token", secret=True)
            if tok:
                chat = _input_line(stdscr, "Telegram chat id")
                if chat is not None:
                    cfg.setdefault("settings", {}).setdefault("notify", {})
                    cfg["settings"]["notify"]["telegram_token"] = tok
                    cfg["settings"]["notify"]["telegram_chat_id"] = chat
                    C.save_config(cfg)
                    flash("telegram credentials saved")
        elif ch in (ord("m"), ord("M")):
            order = ["failure", "success", "always", "never"]
            cur = n.get("on", "failure")
            cfg["settings"]["notify"]["on"] = \
                order[(order.index(cur) + 1) % len(order)] \
                if cur in order else "failure"
            C.save_config(cfg)
            flash("notify on: %s" % cfg["settings"]["notify"]["on"])
        elif ch in (ord("w"), ord("W")):
            _webhooks_screen(stdscr, cfg)
        elif ch in (10, 13, curses.KEY_ENTER):
            ok, msg = NT.notify_test(cfg)
            flash("test: %s" % msg, 6)


def _webhooks_screen(stdscr, cfg):
    """start/stop the webhook receiver and show the URL to paste upstream"""
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, 0, 0, " WEBHOOK RECEIVER ".center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        secret = WH.get_secret()
        live = S.webhooks_active()
        url = ("http://<this-host>:%d/hook/%s" % (WH.DEFAULT_PORT, secret)
               if secret else
               "run [I]nstall first - it generates the secret and the URL")
        rows = [
            ("secret      : %s" % ("set" if secret else "not set"),
             curses.color_pair(2) if secret else curses.A_DIM),
            ("receiver    : %s" % ("live" if live else "not running"),
             curses.color_pair(2) if live else curses.A_DIM),
            ("", 0),
            ("A push event on any platform then triggers a sync instantly,", 0),
            ("instead of waiting for the hourly timer.", 0),
            ("", 0),
            ("payload URL to paste into GitHub / GitLab / Forgejo:", 0),
            ("  %s" % url, curses.A_BOLD),
            ("", 0),
            ("Content type : application/json", 0),
            ("Events       : push (and tag push where supported)", 0),
        ]
        for i, (text, attr) in enumerate(rows):
            if 2 + i >= h - 1:
                break
            safe_add(stdscr, 2 + i, 1, text[:w - 2], attr)
        modal_footer(stdscr, h, w,
                     " [I]nstall service  [X]stop service  [R]efresh  "
                     "[B]ack ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("b"), ord("B"), 27):
            return
        elif ch in (ord("r"), ord("R")):
            continue
        elif ch in (ord("i"), ord("I")):
            _suspend_run(stdscr, ["python3", "-m", "gitsync", "webhooks",
                                  "--install"])
        elif ch in (ord("x"), ord("X")):
            _suspend_run(stdscr, ["python3", "-m", "gitsync", "webhooks",
                                  "--uninstall"])


def _suspend_run(stdscr, cmd):
    """drop to the shell for a command that needs the whole terminal.

    Ctrl-C inside the child must come back to the dashboard, not kill the
    TUI - that is what used to happen, because the interrupt propagated out
    of curses.wrapper().
    """
    curses.def_prog_mode()
    curses.endwin()
    try:
        try:
            subprocess.run(cmd, env=dict(os.environ, PYTHONPATH=_app_dir()))
            note = "\npress Enter to return to the dashboard..."
        except KeyboardInterrupt:
            note = "\ncancelled - press Enter to return to the dashboard..."
        except Exception as e:
            note = "\nfailed: %s - press Enter to return..." % e
        try:
            input(note)
        except (KeyboardInterrupt, EOFError):
            pass
    finally:
        curses.reset_prog_mode()
        curses.curs_set(0)
        state["dirty"] = True


def load_repos(source=None, background=True):
    """fetch a platform's repo list (cached per R / per sync)

    background=True keeps the TUI responsive: listing 52 GitHub repos takes
    ~1.5s and used to freeze the screen with no feedback at all.
    """
    cfg = C.load_config()
    if source is None:
        routes = C.routes(cfg)
        if not routes:
            flash("no routes configured", 6)
            return
        source = routes[0]["source"]
    state["repo_src"] = source

    def work():
        state["repo_loading"] = True
        state["dirty"] = True
        try:
            repos = P.make(source, C.platform(cfg, source)).list_repos()
            err = None
        except Exception as e:
            repos, err = [], str(e)[:80]
        state["repo_list"] = sorted(
            [(r["name"], bool(r.get("private")), bool(r.get("fork")))
             for r in repos], key=lambda t: t[0].lower())
        state["repo_loaded"] = True
        state["repo_loading"] = False
        if err:
            flash("cannot list repos on %s: %s" % (source, err), 8)
        state["dirty"] = True

    if background:
        if state.get("repo_loading"):
            return
        threading.Thread(target=work, daemon=True).start()
    else:
        work()


def _local_target(src=None):
    """name of a route's local-disk target (where 'L' clones to).

    Prefers the route whose source is the platform being browsed, so
    viewing GitLab's repos and pressing L uses GitLab's route.
    """
    cfg = C.load_config()
    routes = C.routes(cfg)
    if not routes:
        return None
    plats = cfg.get("platforms") or {}
    order = [r for r in routes if src is None or r.get("source") == src]
    order += [r for r in routes if r not in order]
    for r in order:
        for t in r.get("targets", []):
            p = plats.get(t) or {}
            if p.get("type") == "local" and p.get("enabled", True):
                return t
    return None


_picked_platform = {}      # what the platform picker last chose


def _pick_platform(stdscr, title):
    """choose one enabled platform; returns its name or None"""
    cfg = C.load_config()
    live = [n for n, p in (cfg.get("platforms") or {}).items()
            if p.get("enabled", True)]
    if not live:
        flash("no enabled platforms", 5)
        return None
    mesh = any(r.get("direction") == "mesh" for r in C.routes(cfg))

    def pick(n):
        _picked_platform["name"] = n

    items = [(n, "(mesh: ignored)" if mesh else "", (lambda nn=n: pick(nn)))
             for n in live]
    _picked_platform["name"] = None
    menu(stdscr, title, items,
         note="%d site(s)%s" % (len(live),
                                " - a mesh always syncs every peer, so --to "
                                "does not apply" if mesh else ""))
    return _picked_platform.get("name")


def repos_screen(stdscr, source=None):
    """browse a platform's repos, pick some, sync just those.

    source=None uses the first route's source (the E key); the accounts
    manager passes the platform you highlighted.
    """
    if source:
        state["repo_sel"] = set()
        state["repo_loaded"] = False
        state["repo_scroll"] = 0
        load_repos(source=source)
    elif not state["repo_loaded"]:
        load_repos()
    query = ""
    cur = state["repo_scroll"]
    top = 0
    while True:
        q = query.lower()
        items = [r for r in state["repo_list"] if q in r[0].lower()]
        cur = max(0, min(cur, len(items) - 1)) if items else 0
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        n_sel = len(state["repo_sel"])
        if state.get("repo_loading"):
            safe_add(stdscr, 4, 2, " listing repos on %s ..."
                     % (state.get("repo_src") or "?"), curses.A_DIM)
            modal_footer(stdscr, h, w, " reading the repo list ... ")
            commit_screen(stdscr)
            ch = stdscr.getch()
            if ch in (ord("b"), ord("B"), 27):
                return
            continue
        safe_add(stdscr, 0, 0,
                 (" REPOS on %s - %d repos, %d selected "
                  % (state.get("repo_src") or "?", len(state["repo_list"]),
                     n_sel)).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        if q:
            safe_add(stdscr, 1, 0,
                     (" filter: %s (%d match) "
                      % (query, len(items)))[:w - 1], curses.A_DIM)
        rows = max(1, h - 4)
        # keep the cursor inside the window: the list used to scroll with
        # the arrows and the top row was the implicit, unmarked selection
        if cur < top:
            top = cur
        elif cur - top >= rows:
            top = cur - rows + 1
        view = items[top:top + rows]
        for i, (name, priv, fork) in enumerate(view):
            mark = "[x]" if name in state["repo_sel"] else "[ ]"
            attr = curses.color_pair(2) if name in state["repo_sel"] else 0
            if (top + i) == cur:
                attr |= curses.A_REVERSE
            line = " %s %-30s %s%s" % (mark, name[:30],
                                       "priv" if priv else "pub ",
                                       " fork" if fork else "")
            safe_add(stdscr, 2 + i, 0, line[:w - 1], attr)
        if not view:
            safe_add(stdscr, 2, 0, " (no matching repos)", curses.A_DIM)
        if len(items) > rows:
            safe_add(stdscr, 1, 0, (" %d-%d of %d " % (top + 1,
                                                       min(top + rows,
                                                           len(items)),
                                                       len(items)))[:w - 1],
                     curses.A_DIM)
        modal_footer(stdscr, h, w,
                     " space=select  a=all  n=none  /=filter  S=all targets  "
                     "T=one site  L=local only  B=back ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch == curses.KEY_MOUSE:
            try:
                _eid, _mx, my, _bz, bstate = curses.getmouse()
            except Exception:
                bstate = 0
            if bstate & curses.BUTTON1_CLICKED and 2 <= my < 2 + rows:
                cur = top + (my - 2)
            continue
        if ch == 27:                      # Esc: clear filter first, then exit
            if query:
                query = ""
                cur = top = 0
                continue
            state["repo_scroll"] = cur
            return
        if ch in (ord("b"), ord("B")):
            state["repo_scroll"] = cur
            return
        elif ch == curses.KEY_UP:
            cur = max(0, cur - 1)
        elif ch == curses.KEY_DOWN:
            cur = min(max(0, len(items) - 1), cur + 1)
        elif ch == curses.KEY_PPAGE:
            cur = max(0, cur - rows)
        elif ch == curses.KEY_NPAGE:
            cur = min(max(0, len(items) - 1), cur + rows)
        elif ch in (curses.KEY_END, ord("g"), ord("G")):
            cur = len(items) - 1 if items else 0
        elif ch == curses.KEY_HOME:
            cur = 0
        elif ch == ord(" "):
            if items:
                name = items[cur][0]
                if name in state["repo_sel"]:
                    state["repo_sel"].discard(name)
                else:
                    state["repo_sel"].add(name)
        elif ch in (ord("a"), ord("A")):
            state["repo_sel"] = {n for n, _, _ in items}
        elif ch in (ord("n"), ord("N")):
            state["repo_sel"] = set()
        elif ch == ord("/"):
            query = prompt_line(stdscr, " filter: ")
            cur = top = 0
        elif ch in (ord("s"), ord("S"), ord("l"), ord("L"),
                    ord("t"), ord("T")):
            if not state["repo_sel"]:
                flash("nothing selected - press space on a repo", 5)
                continue
            cmd = ["python3", "-m", "gitsync", "sync", "--repos",
                   ",".join(sorted(state["repo_sel"]))]
            if ch in (ord("l"), ord("L")):
                local = _local_target(state.get("repo_src"))
                if not local:
                    flash("no local-disk target in this route", 6)
                    continue
                cmd += ["--to", local]         # local hub only
            elif ch in (ord("t"), ord("T")):
                picked = _pick_platform(stdscr, "PUSH THE SELECTED REPOS TO")
                if not picked:
                    continue
                cmd += ["--to", picked]
            state["repo_scroll"] = cur
            _suspend_run(stdscr, cmd)
            state["repo_loaded"] = False
            query = ""


def prompt_line(stdscr, label, initial=""):
    """one-line text input at the bottom of the screen"""
    curses.curs_set(1)
    buf = initial or ""
    while True:
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, h - 1, 0, (label + buf + "_").ljust(w - 1)[:w - 1],
                 curses.A_REVERSE)
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (10, 13, curses.KEY_ENTER):
            break
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif ch == 27:
            buf = ""
            break
        elif 32 <= ch < 127:
            buf += chr(ch)
    curses.curs_set(0)
    return buf


def history_screen(stdscr):
    recs = SYNC.last_records(40)
    if not recs:
        lines = ["no runs recorded yet"]
    else:
        # sparkline of recent durations
        durs = [r.get("elapsed_sec", 0) or 0 for r in recs[-30:]]
        spark = _sparkline(durs)
        lines = ["last %d runs (newest last)" % len(recs), ""]
        if spark:
            lines.append("  duration trend: %s" % spark)
            lines.append("")
        for r in recs:
            lines.append("%s | %d ok, %d partial, %d failed of %d | %ss"
                         % (r.get("finished_at"), r.get("ok", 0),
                            r.get("partial", 0), r.get("failed", 0),
                            r.get("repos", 0), r.get("elapsed_sec")))
            for f in r.get("failures", [])[:3]:
                lines.append("    ! %s/%s: %s"
                             % (f.get("route"), f.get("repo"),
                                f.get("error", "")[:90]))
    _scroll_view(stdscr, " RUN HISTORY ", lines)


def _sparkline(values):
    """tiny ascii sparkline for a list of numbers"""
    if not values:
        return ""
    blocks = " .:-=+*#%@"
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    return "".join(blocks[min(9, int((v - lo) / span * 9))] for v in values)


def log_view(stdscr):
    read_full_log()
    lines = state["full_log"]
    # open near the newest lines: a 2900-line log otherwise starts at the
    # first run from days ago and needs ~30 PgDn presses to reach today
    _scroll_view(stdscr, " FULL LOG ", lines,
                 start=max(0, len(lines) - 100))


def modal_footer(stdscr, h, w, hint):
    """footer for modal screens: a fresh flash() message wins over the hint.

    flash() only writes state["message"], which draw() renders — so inside a
    modal the user saw no confirmation at all (pausing a target, "nothing
    selected", "webhook saved" all vanished).
    """
    text = hint
    msg, until = state.get("message", ("", 0))
    if msg and time.time() < until:
        text = " %s " % msg
    safe_add(stdscr, h - 1, 0, text.center(w - 1)[:w - 1], curses.A_REVERSE)


def _scroll_view(stdscr, title, lines, start=0):
    """generic scrollable, searchable text view (log / history / accounts)"""
    scroll = max(0, start)
    while True:
        # apply the active search filter
        q = state["search"].lower()
        shown = [l for l in lines if q in l.lower()] if q else lines
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        head = title
        if q:
            head += " [filter: %s — %d/%d] " % (state["search"],
                                                len(shown), len(lines))
        safe_add(stdscr, 0, 0, head.center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        view = shown[scroll:scroll + h - 2]
        for i, line in enumerate(view):
            attr = 0
            if "FAIL" in line:
                attr = curses.color_pair(4)
            elif " OK " in line:
                attr = curses.color_pair(2)
            safe_add(stdscr, 1 + i, 0, line[:w - 1], attr)
        if not view:
            safe_add(stdscr, 1, 0, " (no matching lines)", curses.A_DIM)
        modal_footer(stdscr, h, w,
                     " up/dn=scroll PgUp/PgDn=page G/End=bottom "
                     "Home=top /=filter Esc=clear B=back ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("b"), ord("B"), 27):
            if ch == 27 and state["search"]:
                state["search"] = ""
                continue
            return
        elif ch == curses.KEY_UP:
            scroll = max(0, scroll - 1)
        elif ch == curses.KEY_DOWN:
            scroll = min(max(0, len(shown) - 1), scroll + 1)
        elif ch == curses.KEY_PPAGE:
            scroll = max(0, scroll - (h - 3))
        elif ch == curses.KEY_NPAGE:
            scroll = min(max(0, len(shown) - 1), scroll + (h - 3))
        elif ch in (curses.KEY_END, ord("g"), ord("G")):
            scroll = max(0, len(shown) - 1)
        elif ch == curses.KEY_HOME:
            scroll = 0


CLOCK_X = 1          # column of the clock inside the status line
CLOCK_LEN = 19       # len("2026-11-08 07:37:00")

_status_cache = {"txt": "checking...", "at": 0.0}


def cached_status():
    """scheduler status without shelling out on every frame"""
    now = time.time()
    if now - _status_cache["at"] > 15:
        try:
            _status_cache["txt"] = S.status()
        except Exception:
            _status_cache["txt"] = "unknown"
        _status_cache["at"] = now
    return _status_cache["txt"]


def status_line():
    """the row-1 status text (clock at CLOCK_X, sync state, schedule, age)"""
    sched = C.setting(C.load_config(), "schedule", "hourly")
    age = int(time.time() - state["counts_age"])
    age_txt = "loading..." if not state["platforms"] else (
        "%ds" % age if age < 3600 else "stale")
    return (" %s  syncing: %s  schedule: %s  counts: %s "
            % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "YES" if state["syncing"] else "no",
               C.SCHEDULES.get(sched, {}).get("label", sched), age_txt))


def update_clock(stdscr):
    """Ask for a full redraw because the clock ticked.

    This used to repaint only the 19 clock cells and call doupdate(), to
    keep the byte count on the wire tiny. That was wrong: draw() erases
    stdscr and the panels live in their own windows, so a bare stdscr
    flush overwrote the panels with blanks — which is why the Platforms
    and Routes tabs rendered empty while the Log tab (no panels) was fine.
    Redrawing everything is a slightly larger diff and always correct.
    """
    state["dirty"] = True


def _tab_at(x, w):
    """which tab (0-based) a screen column belongs to, or None"""
    parts = ["%d:%s" % (i, n) for i, n in enumerate(TABS, 1)]
    text = " " + "  ".join(parts) + " "
    start = max(0, (w - len(text)) // 2)
    if x < start:
        return None
    pos = start
    for i, p in enumerate(parts):
        if pos <= x < pos + len(p):
            return i
        pos += len(p) + 2
    return None


def tab_bar(w):
    parts = []
    for i, name in enumerate(TABS, 1):
        marker = "%d:%s" % (i, name)
        parts.append(marker)
    return "  ".join(parts)


def draw(stdscr):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    if h < 10 or w < 34:
        safe_add(stdscr, 0, 0,
                 "terminal too small (need ~34x10) - rotate to "
                 "landscape or widen the window", curses.A_BOLD)
        commit_screen(stdscr)
        return

    Cc, G, Y, R = (curses.color_pair(1), curses.color_pair(2),
                   curses.color_pair(3), curses.color_pair(4))
    dim = curses.A_DIM

    safe_add(stdscr, 0, 0,
             (" gitsync %s " % __version__).center(w - 1)[:w - 1],
             curses.A_BOLD | Cc)
    safe_add(stdscr, 1, 0, status_line().ljust(w - 1)[:w - 1], dim)

    tab = state["tab"]
    if tab == 1:
        draw_platforms(stdscr, h, w, G, R, dim)
    elif tab == 2:
        draw_routes(stdscr, h, w, G, R, dim)
    elif tab == 3:
        draw_stars(stdscr, h, w, G, R, dim)
    elif tab == 4:
        draw_log_tab(stdscr, h, w, R, G)
    elif tab == 5:
        draw_history_tab(stdscr, h, w)
    elif tab == 6:
        draw_settings(stdscr, h, w, dim)
    else:
        draw_dashboard(stdscr, h, w, Cc, G, Y, R, dim)

    # tab bar + footer
    safe_add(stdscr, 2, 0, (" " + tab_bar(w) + " ").center(w - 1)[:w - 1],
             curses.A_REVERSE)
    foot = (" [1-7]tabs  [S]ync  [D]ry-run  [L]og  [C]ron  [O]pt  "
            "[A]cc  [N]otify  [E]repos  [T]ools  [W]izard  [R]efresh  "
            "[Q]uit ")
    if tab == 1:
        foot = (" [1-7]tabs  [S]ync  [D]ry-run  up/dn=scroll sites  "
                "[A]cc  [R]efresh  [Q]uit ")
    if tab == 2:
        foot = (" [1-7]tabs  [S]ync  [P]ause/resume  [O]ptions  "
                "[R]efresh  [Q]uit ")
    if tab == 3:
        foot = (" [1-7]tabs  [S]ource  [C]opy stars  up/dn=scroll  "
                "[R]e-read  [Q]uit ")
    if state["search_mode"]:
        foot = " filter: %s_   (Enter = apply, Esc = clear) " % state["search"]
    elif state["search"]:
        foot = " filter: %s   (/ = edit, Esc = clear) " % state["search"]
    elif state["message"][0] and time.time() < state["message"][1]:
        foot = " %s " % state["message"][0]
    safe_add(stdscr, h - 1, 0, foot.center(w - 1)[:w - 1],
             curses.A_REVERSE | curses.A_BOLD)
    commit_screen(stdscr)


def _panel_rows(stdscr, h, w, top, rows, title, colors=None):
    """render rows [(text, attr)] inside a bordered panel.

    Drawn straight onto stdscr instead of into a separate curses window.
    commit_screen() only calls doupdate(), which flushes what was staged
    with noutrefresh(); a separate panel window was staged correctly but
    then wiped by the clock's stdscr flush, which is why the Platforms
    and Routes tabs rendered an empty panel for every release so far.
    Drawing on stdscr makes the panel exactly as reliable as the header,
    tab bar and footer, which always worked.
    """
    avail = h - top - 2          # leave the hint line + footer alone
    if avail < 1 or w < 8:
        return
    inner = w - 4
    n = min(len(rows), avail - 1)
    label = " %s " % title
    if len(label) > inner:
        label = label[:inner]
    rule = "+" + label + "-" * max(0, inner - len(label)) + "+"
    safe_add(stdscr, top, 1, rule[:w - 2], curses.A_DIM)
    for i in range(n):
        text, attr = rows[i]
        body = "| " + str(text)
        safe_add(stdscr, top + 1 + i, 1,
                 body.ljust(inner + 1)[:inner + 1] + "+", attr)
    if n < len(rows):
        safe_add(stdscr, top + 1 + n, 1,
                 ("| ... %d more" % (len(rows) - n)).ljust(inner + 1)[:inner + 1]
                 + "+", curses.A_DIM)


def platforms_title():
    """'PLATFORMS (4 sites, 4 connected)' - the count the panel used to omit"""
    rows = state["platforms"]
    if not rows:
        return "PLATFORMS (0 sites - loading...)"
    live = sum(1 for r in rows if r[2] and r[3])      # enabled and reachable
    return "PLATFORMS (%d sites, %d connected)" % (len(rows), live)


def draw_platforms(stdscr, h, w, G, R, dim):
    """the Platforms tab. Scrolls, because there is no cap on how many
    sites a config can hold and a short terminal cannot show them all."""
    rows = []
    for name, ptype, on, ok, info, count in state["platforms"]:
        attr = (G if ok else R) if on else dim
        rows.append(("%-14s %-9s %-4s %-5s repos=%-6s %s"
                     % (name, ptype, "ON" if on else "off",
                        "OK" if ok else "FAIL", count, info), attr))
    if not rows:
        rows.append(("platforms: loading...", dim))
    avail = max(1, h - 4 - 2 - 1)          # panel body rows available
    top = state.get("plat_scroll", 0)
    top = max(0, min(top, max(0, len(rows) - 1)))
    if top + avail < len(rows) and top > 0:
        pass
    state["plat_scroll"] = top
    _panel_rows(stdscr, h, w, 4, rows[top:top + avail], platforms_title())
    if len(rows) > avail:
        safe_add(stdscr, 3, 0, " %d-%d of %d sites " % (
            top + 1, min(top + avail, len(rows)), len(rows)), curses.A_DIM)
    safe_add(stdscr, h - 2, 2,
             "up/dn=scroll  |  add/edit/remove: gitsync platforms "
             "add|edit|remove NAME", dim)


def platforms_key(ch):
    """arrow / page / jump keys for the Platforms tab"""
    n = len(state["platforms"])
    if not n:
        return
    top = state.get("plat_scroll", 0)
    if ch in (curses.KEY_UP, ord("k")):
        top = max(0, top - 1)
    elif ch in (curses.KEY_DOWN, ord("j")):
        top = min(max(0, n - 1), top + 1)
    elif ch == curses.KEY_PPAGE:
        top = max(0, top - 8)
    elif ch == curses.KEY_NPAGE:
        top = min(max(0, n - 1), top + 8)
    elif ch in (curses.KEY_END, ord("g"), ord("G")):
        top = max(0, n - 1)
    elif ch == curses.KEY_HOME:
        top = 0
    state["plat_scroll"] = top
    state["dirty"] = True


def _route_last(name):
    """'last run' digest for one route: failures from the newest record plus
    when this route itself last ran (route_state.json), so a route that was
    not part of the last run says 'never' rather than a false 'ok'."""
    bits = []
    try:
        last = SYNC.last_route_run(name)
    except Exception:
        last = None
    if last is None:
        bits.append("never run")
    else:
        age = (time.time() - last) / 60.0
        bits.append("ran %s ago" % ("%dm" % age if age < 1440
                                    else "%.1fd" % (age / 1440.0)))
    recs = SYNC.last_records(1)
    if recs:
        fails = [f for f in (recs[-1].get("failures") or [])
                 if f.get("route") == name]
        if fails:
            bits.append("%d failed (%s)" % (len(fails),
                                            fails[0].get("repo", "?")[:16]))
    return "last run: " + ", ".join(bits)


def draw_routes(stdscr, h, w, G, R, dim):
    rows = []
    cfg = C.load_config()
    for (name, source, targets, inc, exc, direction, paused,
         route_on) in state["routes"]:
        sched = C.route_schedule(cfg, {
            "name": name, "schedule": state["route_sched"].get(name)})
        nxt = SYNC.next_due_text({
            "name": name, "schedule": state["route_sched"].get(name)}, cfg)
        mesh = direction == "mesh"
        parts = []
        for t in targets:
            mark = ""
            if t in paused:
                mark = " (paused)"
            parts.append(t + mark)
        arrow = (", ".join(["*" + source] + parts) if mesh
                 else "%s -> %s" % (source, ", ".join(parts) or "(none)"))
        meta = "mesh" if mesh else "one-way"
        when = "PAUSED" if not route_on else "next: " + nxt
        attr = dim if not route_on else curses.A_BOLD
        rows.append(("%s:  %s   [%s, %s, %s]" % (name, arrow, meta, sched,
                                                 when), attr))
        extra = []
        last = _route_last(name)
        if last:
            extra.append(last)
        if inc or exc:
            extra.append("include=%s exclude=%s" % (inc or "-", exc or "-"))
        if extra:
            rows.append(("    " + "  |  ".join(extra), dim))
    if not rows:
        rows.append(("(no routes — add one: gitsync routes add main github "
                     "gitlab,forgejo)", dim))
    inner = w - 6
    for i, (text, attr) in enumerate(rows):
        if len(text) > inner:
            rows[i] = (text[:inner - 18] + " ... (P for details)", attr)
    _panel_rows(stdscr, h, w, 4, rows, "ROUTES (direction + pause)")
    safe_add(stdscr, h - 2, 2,
             "P = pause/resume a route or target   |   "
             "add: gitsync routes add all github,gitlab,forgejo", dim)



def _route_rows(cfg):
    """flat [name] list in the same order the pause screen draws them"""
    out = []
    for r in C.routes(cfg):
        out.append(r.get("name"))
        out += ["%s/%s" % (r.get("name"), t) for t in r.get("targets", [])]
    return out


def _route_targets_row(r):
    """the pause screen's rows for one route: the route, then its targets"""
    paused = set(r.get("paused_targets") or [])
    on = r.get("enabled") is not False
    mesh = r.get("direction") == "mesh"
    rows = [{
        "label": "%s%s" % (r.get("name"), "  [mesh]" if mesh else ""),
        "sub": "%s -> %s" % (r.get("source"),
                             ", ".join(r.get("targets", [])) or "(none)"),
        "on": on, "kind": "route", "route": r, "target": None}]
    for t in r.get("targets", []):
        plat = C.platform(C.load_config(), t)
        rows.append({
            "label": "    %s" % t,
            "sub": "target of %s%s" % (
                r.get("name"),
                "" if (plat and plat.get("enabled", True))
                else "  (platform disabled)"),
            "on": (on and t not in paused),
            "kind": "target", "route": r, "target": t})
    return rows


def _toggle_route_row(row):
    """apply one pause/resume toggle and save; returns the flash text"""
    cfg = C.load_config()
    name = row["route"].get("name")
    target = None
    for r in cfg.get("routes", []):
        if r.get("name") == name:
            target = r
            break
    if target is None:
        return None
    if row["kind"] == "route":
        target["enabled"] = not (target.get("enabled") is not False)
        if target["enabled"]:
            # resuming the whole route also clears per-target pauses
            target.pop("paused_targets", None)
        msg = "%s: %s" % (name, "resumed" if target["enabled"]
                          else "paused (whole route)")
    else:
        t = row["target"]
        paused = [x for x in (target.get("paused_targets") or [])]
        if t in paused:
            paused.remove(t)
            msg = "%s / %s: resumed" % (name, t)
        else:
            paused.append(t)
            msg = "%s / %s: paused" % (name, t)
        if paused:
            target["paused_targets"] = sorted(paused)
        else:
            target.pop("paused_targets", None)
    C.save_config(cfg)
    _reload_routes()
    return msg


def _reload_routes():
    """re-read routes from disk into TUI state (after any config edit)"""
    cfg = C.load_config()
    state["routes"] = [(r.get("name"), r.get("source"),
                        list(r.get("targets", [])),
                        r.get("include") or [], r.get("exclude") or [],
                        r.get("direction") or "one-way",
                        list(r.get("paused_targets") or []),
                        r.get("enabled") is not False)
                       for r in C.routes(cfg)]
    state["route_sched"] = {r.get("name"): r.get("schedule")
                            for r in C.routes(cfg)}
    state["settings"] = dict(cfg.get("settings", {}))
    state["dirty"] = True


def _run_route(name, dry_run=False):
    """run one route now, in a child process, with live output in the TUI"""
    if state["syncing"]:
        flash("a sync is already running")
        return
    state["syncing"] = True
    state["sync_lines"] = ["--- route '%s'%s started %s ---"
                           % (name, " (dry run)" if dry_run else "",
                              datetime.now().strftime("%H:%M:%S"))]

    def worker():
        try:
            cmd = ["python3", "-m", "gitsync", "sync", "--route", name]
            if dry_run:
                cmd.append("--dry-run")
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=dict(os.environ, PYTHONPATH=_app_dir()))
            for line in p.stdout:
                state["sync_lines"].append(line.rstrip())
                state["dirty"] = True
            p.wait()
            state["sync_lines"].append("--- finished (rc=%d) %s ---"
                                       % (p.returncode,
                                          datetime.now().strftime("%H:%M:%S")))
            merged = _merged_refs(state["sync_lines"])
            if merged:
                flash("%s: merged %d ref(s) across peers" % (name, merged), 8)
        except Exception as e:
            state["sync_lines"].append("--- error: %s ---" % e)
        finally:
            state["syncing"] = False
            read_log_tail()
            state["records"] = SYNC.last_records(10)
            state["dirty"] = True
            threading.Thread(target=refresh_platforms, daemon=True).start()
            threading.Thread(target=refresh_health, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()


def _edit_route_filters(stdscr, route):
    """edit a route's include/exclude globs without leaving the TUI"""
    inc = ",".join(route.get("include") or [])
    exc = ",".join(route.get("exclude") or [])
    inc = prompt_line(stdscr, " include globs (blank = all): ", inc)
    exc = prompt_line(stdscr, " exclude globs (blank = none): ", exc)
    cfg = C.load_config()
    for r in cfg.get("routes", []):
        if r.get("name") == route.get("name"):
            r["include"] = [p.strip() for p in inc.split(",") if p.strip()]
            r["exclude"] = [p.strip() for p in exc.split(",") if p.strip()]
            break
    C.save_config(cfg)
    _reload_routes()
    flash("%s: filters saved" % route.get("name"), 5)


def _route_options_screen(stdscr, route):
    """per-route overrides: workers, mirror mode, mesh conflict policy.

    Everything defaults to the global setting ("inherit"), so one route can
    run single-threaded against a rate-limited host, keep deleted refs, or
    refuse to overwrite a peer's newer commit.
    """
    name = route.get("name")
    idx = 0
    while True:
        cfg = C.load_config()
        cur = next((r for r in cfg.get("routes", [])
                    if r.get("name") == name), None)
        if cur is None:
            return
        mesh = cur.get("direction") == "mesh"
        gw = C.setting(cfg, "workers", 4)
        gm = C.setting(cfg, "mode", "full")
        gc = C.setting(cfg, "mesh_conflict", "newest")
        rows = [
            ("Parallel workers",
             "%s (global %s)" % (cur.get("workers") or "inherit", gw),
             "inherit | 1 | 2 | 4 | 8"),
            ("Mirror mode",
             "%s (global %s)" % (cur.get("mode") or "inherit", gm),
             "inherit | full = prunes | safe = keeps"),
        ]
        if mesh:
            rows.append(("Mesh conflicts",
                         "%s (global %s)" % (cur.get("mesh_conflict")
                                             or "inherit", gc),
                         "inherit | newest = overwrite | skip = report only"))
        sched = C.route_schedule(cfg, cur)
        own = " (own)" if cur.get("schedule") else " (inherited)"
        rows.append(("Schedule", sched + own,
                     "Enter cycles: hourly/6h/12h/daily/weekly/off/inherit"))
        idx = max(0, min(idx, len(rows) - 1))

        stdscr.erase()
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, 0, 0,
                 (" ROUTE OPTIONS - %s " % name).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        safe_add(stdscr, 1, 0,
                 " Enter cycles the highlighted value ".ljust(w - 1)[:w - 1],
                 curses.A_DIM)
        for i, (label, value, hint) in enumerate(rows):
            if 3 + i >= h - 2:
                break
            attr = curses.A_REVERSE if i == idx else 0
            safe_add(stdscr, 3 + i, 0,
                     ("  %-18s %-24s %s" % (label, value, hint))[:w - 1],
                     attr)
        modal_footer(stdscr, h, w, " up/dn=move  Enter=cycle  B=back ")
        commit_screen(stdscr)

        ch = stdscr.getch()
        if ch in (ord("b"), ord("B"), 27):
            return
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(rows) - 1, idx + 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            _cycle_route_option(cfg, cur, rows[idx][0])


def _cycle_route_option(cfg, route, label):
    """cycle one per-route override, wrapping back to 'inherit'"""
    name = route.get("name")
    target = next((r for r in cfg.get("routes", [])
                   if r.get("name") == name), None)
    if target is None:
        return
    if label == "Parallel workers":
        vals = ["inherit", 1, 2, 4, 8]
        cur = target.get("workers") or "inherit"
        nxt = vals[(vals.index(cur) + 1) % len(vals)] \
            if cur in vals else vals[0]
        if nxt == "inherit":
            target.pop("workers", None)
        else:
            target["workers"] = nxt
        msg = "workers -> %s" % nxt
    elif label == "Mirror mode":
        vals = ["inherit", "full", "safe"]
        cur = target.get("mode") or "inherit"
        nxt = vals[(vals.index(cur) + 1) % len(vals)] \
            if cur in vals else vals[0]
        if nxt == "inherit":
            target.pop("mode", None)
        else:
            target["mode"] = nxt
        msg = "mirror mode -> %s" % nxt
    elif label == "Schedule":
        order = ["inherit", "hourly", "6h", "12h", "daily", "weekly", "off"]
        cur_s = target.get("schedule") or "inherit"
        nxt = order[(order.index(cur_s) + 1) % len(order)] \
            if cur_s in order else order[0]
        if nxt == "inherit":
            target.pop("schedule", None)
        else:
            target["schedule"] = nxt
        C.save_config(cfg)
        _reload_routes()
        flash("schedule -> %s" % nxt, 4)
        return
    elif label == "Mesh conflicts":
        vals = ["inherit", "newest", "skip"]
        cur = target.get("mesh_conflict") or "inherit"
        nxt = vals[(vals.index(cur) + 1) % len(vals)] \
            if cur in vals else vals[0]
        if nxt == "inherit":
            target.pop("mesh_conflict", None)
        else:
            target["mesh_conflict"] = nxt
        msg = "mesh conflicts -> %s" % nxt
    else:
        return
    C.save_config(cfg)
    _reload_routes()
    flash(msg, 4)


def _add_route_screen(stdscr):
    """add a route without leaving the TUI: name, source, targets, direction
    and its per-route settings, all in one form"""
    cfg = C.load_config()
    names = [r[0] for r in _platform_rows(cfg)]
    live = [n for n, p in (cfg.get("platforms") or {}).items()
            if p.get("enabled", True)]
    if len(live) < 2:
        flash("need at least two enabled sites", 6)
        return
    fields = [("name", "name", "", {"hint": "e.g. all, gh-to-gl"}),
              ("source", "source", live[0], {"choices": live}),
              ("targets", "targets", ",".join([n for n in live
                                               if n != live[0]]),
               {"hint": "comma-separated"}),
              ("direction", "direction", "one-way",
               {"choices": ["one-way", "mesh"],
                "hint": "mesh = every site syncs to every other"}),
              ("workers", "workers", "inherit",
               {"choices": ["inherit", "1", "2", "4", "8"],
                "hint": "parallel jobs for this route"}),
              ("mode", "mirror mode", "inherit",
               {"choices": ["inherit", "full", "safe"],
                "hint": "full prunes deleted refs"}),
              ("mesh_conflict", "conflicts", "inherit",
               {"choices": ["inherit", "newest", "skip"],
                "hint": "mesh only: skip = never overwrite"}),
              ("schedule", "schedule", "inherit",
               {"choices": ["inherit", "hourly", "6h", "12h", "daily",
                            "weekly", "off"],
                "hint": "this route's own frequency"})]
    vals = _form(stdscr, "ADD A ROUTE", fields,
                 note="one-way: the source wins. mesh: newest commit wins "
                      "per branch. 'inherit' = use the global setting.")
    if not vals or not vals.get("name"):
        flash("cancelled", 3)
        return
    name = vals["name"].strip()
    src_name = vals.get("source") or live[0]
    targets = [t.strip() for t in str(vals.get("targets") or "").split(",")
               if t.strip() and t.strip() != src_name]
    if not targets:
        flash("a route needs at least one target", 6)
        return
    cfg = C.load_config()
    if any(r.get("name") == name for r in cfg.get("routes", [])):
        flash("route '%s' already exists" % name, 6)
        return
    route = {"name": name, "source": src_name, "targets": targets}
    if vals.get("direction") == "mesh":
        route["direction"] = "mesh"
    # per-route settings; "inherit" means "leave it to the global value"
    for key in ("workers", "mode", "mesh_conflict", "schedule"):
        v = vals.get(key)
        if v and v != "inherit":
            route[key] = int(v) if key == "workers" else v
    cfg.setdefault("routes", []).append(route)
    C.save_config(cfg)
    _reload_routes()
    extra = ", ".join("%s=%s" % (k, route[k]) for k in
                      ("workers", "mode", "mesh_conflict", "schedule")
                      if k in route)
    flash("route added: %s%s%s" % (name,
                                   " (mesh)" if route.get("direction")
                                   == "mesh" else "",
                                   " [%s]" % extra if extra else ""), 6)
    return name


def _remove_route_screen(stdscr, name):
    cfg = C.load_config()
    routes = [r for r in cfg.get("routes", []) if r.get("name") != name]
    if len(routes) == len(cfg.get("routes", [])):
        flash("no such route: %s" % name, 5)
        return
    cfg["routes"] = routes
    C.save_config(cfg)
    _reload_routes()
    flash("route removed: %s" % name, 5)


def routes_screen(stdscr):
    """pause/resume a whole route or one of its targets, live.

    Space/Enter toggles the highlighted row and saves immediately, so a
    target can be paused without editing config.json or stopping the timer.
    S runs this one route, F edits its include/exclude filters.
    """
    idx = 0
    top = 0
    while True:
        cfg = C.load_config()
        rows = []
        for r in C.routes(cfg):
            rows += _route_targets_row(r)
        idx = max(0, min(idx, len(rows) - 1)) if rows else 0

        stdscr.erase()
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, 0, 0,
                 " ROUTES — pause / resume ".center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        safe_add(stdscr, 1, 0,
                 " paused items are skipped by every run, including the timer "
                 .ljust(w - 1)[:w - 1], curses.A_DIM)
        avail = max(1, h - 5)
        # window the list around the cursor: with several routes the old
        # rows[:h-4] slice let the highlight scroll off the screen entirely
        top = max(0, min(top, idx))
        if idx - top >= avail:
            top = idx - avail + 1
        if idx < top:
            top = idx
        for i, row in enumerate(rows[top:top + avail]):
            attr = curses.A_REVERSE if (top + i) == idx else 0
            if not row["on"] and (top + i) != idx:
                attr |= curses.A_DIM
            box = "[x]" if row["on"] else "[ ]"
            line = " %s %-24s %-34s %s" % (
                box, row["label"][:24], row["sub"][:34],
                "" if row["on"] else "PAUSED")
            safe_add(stdscr, 3 + i, 0, line[:w - 1], attr)
        if not rows:
            safe_add(stdscr, 3, 0, " (no routes configured)", curses.A_DIM)
        if len(rows) > avail:
            safe_add(stdscr, 2, 0, " %d-%d of %d " % (top + 1,
                                                      min(top + avail,
                                                          len(rows)),
                                                      len(rows)),
                     curses.A_DIM)
        modal_footer(stdscr, h, w,
                     " space/Enter=toggle  S=run  D= dry-run  O=options  "
                     "F=filters  A=add  X=remove  up/dn=move  B=back ")
        commit_screen(stdscr)

        ch = stdscr.getch()
        if ch == curses.KEY_MOUSE:
            try:
                _eid, _mx, my, _bz, bstate = curses.getmouse()
            except Exception:
                bstate = 0
            if bstate & curses.BUTTON1_CLICKED and 3 <= my < 3 + avail:
                idx = top + (my - 3)
            continue
        if ch in (ord("b"), ord("B"), 27):
            return
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(rows) - 1, idx + 1) if rows else 0
        elif ch in (curses.KEY_PPAGE,):
            idx = max(0, idx - avail)
        elif ch in (curses.KEY_NPAGE,):
            idx = min(len(rows) - 1, idx + avail) if rows else 0
        elif ch in (curses.KEY_END, ord("g"), ord("G")):
            idx = len(rows) - 1 if rows else 0
        elif ch == curses.KEY_HOME:
            idx = 0
        elif ch in (ord("s"), ord("S")) and rows:
            _run_route(rows[idx]["route"].get("name"))
        elif ch in (ord("d"), ord("D")) and rows:
            _run_route(rows[idx]["route"].get("name"), dry_run=True)
        elif ch in (ord("o"), ord("O")) and rows:
            _route_options_screen(stdscr, rows[idx]["route"])
        elif ch in (ord("a"), ord("A")):
            made = _add_route_screen(stdscr)
            if made:
                names = _route_rows(C.load_config())
                if made in names:
                    idx = names.index(made)
        elif ch in (ord("f"), ord("F")) and rows:
            _edit_route_filters(stdscr, rows[idx]["route"])
        elif ch in (ord("x"), ord("X")) and rows:
            _remove_route_screen(stdscr, rows[idx]["route"].get("name"))
        elif ch in (ord(" "), 10, 13, curses.KEY_ENTER):
            if not rows:
                continue
            msg = _toggle_route_row(rows[idx])
            if msg:
                flash(msg, 5)


STAR_STATE = {"src": "", "rows": [], "at": 0.0, "busy": False,
              "scroll": 0}


def _stars_load(source):
    """read one account's stars in the background (network)"""
    if STAR_STATE["busy"]:
        return
    STAR_STATE["busy"] = True
    STAR_STATE["src"] = source

    def worker():
        try:
            cfg = C.load_config()
            p = P.make(source, cfg["platforms"][source])
            STAR_STATE["rows"] = ST.starred(p)
            STAR_STATE["at"] = time.time()
        except Exception as e:
            STAR_STATE["rows"] = []
            flash("cannot read stars on %s: %s" % (source, str(e)[:50]), 8)
        finally:
            STAR_STATE["busy"] = False
            state["dirty"] = True
    threading.Thread(target=worker, daemon=True).start()


def _stars_copy(targets):
    """apply the loaded star list to other accounts"""
    src_name = STAR_STATE["src"]
    if not src_name or not targets:
        flash("pick a source and at least one target", 5)
        return
    if state["syncing"]:
        flash("a sync is already running", 5)
        return
    state["syncing"] = True
    lines = ["--- copying stars %s -> %s ---" % (src_name, ",".join(targets))]

    def worker():
        try:
            cfg = C.load_config()
            t = ST.copy_stars(cfg, src_name, targets, log=lines.append)
            lines.append("--- done: %d starred, %d already, %d not there, "
                         "%d failed ---" % (t["starred"], t["already"],
                                            t["missing"], t["failed"]))
            state["sync_lines"] = lines
            flash("stars: %d new, %d already, %d not on that site"
                  % (t["starred"], t["already"], t["missing"]), 10)
        except Exception as e:
            lines.append("--- error: %s ---" % e)
            state["sync_lines"] = lines
        finally:
            state["syncing"] = False
            state["dirty"] = True
    threading.Thread(target=worker, daemon=True).start()


def draw_stars(stdscr, h, w, G, R, dim):
    cfg = C.load_config()
    live = [n for n, p in (cfg.get("platforms") or {}).items()
            if p.get("enabled", True)]
    if not STAR_STATE["src"]:
        STAR_STATE["src"] = (C.routes(cfg)[0].get("source")
                             if C.routes(cfg) else (live[0] if live else ""))
    src = STAR_STATE["src"]
    rows = STAR_STATE["rows"]
    if not STAR_STATE["at"] and src and not STAR_STATE["busy"]:
        _stars_load(src)

    body = []
    body.append(("read stars from : %s%s" % (src or "(none)",
                                             "  [%d]" % len(rows)
                 if STAR_STATE["at"] else ""), curses.A_BOLD))
    if STAR_STATE["busy"]:
        body.append(("  reading...", dim))
    elif STAR_STATE["at"]:
        age = int(time.time() - STAR_STATE["at"])
        body.append(("  %d starred repo(s), read %ds ago" % (len(rows), age),
                     dim))
    body.append(("", 0))
    if rows:
        avail = max(1, h - 12)
        top = STAR_STATE["scroll"]
        top = max(0, min(top, max(0, len(rows) - 1)))
        STAR_STATE["scroll"] = top
        for name, _u in rows[top:top + avail]:
            body.append(("  %s" % name[:w - 8], 0))
        if len(rows) > avail:
            body.append(("  ... %d-%d of %d (up/dn to scroll)"
                         % (top + 1, min(top + avail, len(rows)), len(rows)),
                         dim))
    elif STAR_STATE["at"]:
        body.append(("  (no stars, or this site does not expose them)", dim))
    _panel_rows(stdscr, h, w, 4, body, "STARS")

    hint = (" S = choose source   C = copy these stars to other sites   "
            "up/dn = scroll   R = re-read ")
    if state["syncing"]:
        hint = " copying stars... see the log below "
    safe_add(stdscr, h - 2, 2, hint[:w - 4], dim)


def stars_screen(stdscr):
    """source picker + target picker for the star copy"""
    cfg = C.load_config()
    live = [n for n, p in (cfg.get("platforms") or {}).items()
            if p.get("enabled", True)]
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        safe_add(stdscr, 0, 0,
                 (" STARS — copy %s's list " % (STAR_STATE["src"] or "?")
                  ).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(1))
        safe_add(stdscr, 1, 0,
                 " only repos that already exist on the target are starred - "
                 .ljust(w - 1)[:w - 1], curses.A_DIM)
        safe_add(stdscr, 2, 0,
                 " nothing is created and no name is guessed "
                 .ljust(w - 1)[:w - 1], curses.A_DIM)
        row = 4
        safe_add(stdscr, row, 1, "copy to:", curses.A_BOLD)
        row += 1
        for i, n in enumerate(live):
            if row + i >= h - 2:
                break
            mark = "[x]" if n in state.get("star_targets", set()) else "[ ]"
            attr = curses.A_REVERSE if i == state.get("star_cur", 0) else 0
            safe_add(stdscr, row + i, 1,
                     (" %s %-16s %s" % (mark, n, "(source)"
                                        if n == STAR_STATE["src"] else ""))[:w - 2],
                     attr)
        modal_footer(stdscr, h, w,
                     " space=toggle  Enter=copy now  S=change source  "
                     "B=back ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("b"), ord("B"), 27):
            return
        elif ch in (curses.KEY_UP, ord("k")):
            state["star_cur"] = max(0, state.get("star_cur", 0) - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            state["star_cur"] = min(max(0, len(live) - 1),
                                    state.get("star_cur", 0) + 1)
        elif ch == ord(" "):
            if live:
                n = live[state.get("star_cur", 0)]
                if n == STAR_STATE["src"]:
                    flash("that is the source", 4)
                    continue
                t = state.setdefault("star_targets", set())
                t.discard(n) if n in t else t.add(n)
        elif ch in (ord("s"), ord("S")):
            items = [(n, "read stars from here",
                      (lambda nn=n: _stars_load(nn))) for n in live]
            menu(stdscr, "READ STARS FROM", items)
        elif ch in (10, 13, curses.KEY_ENTER):
            targets = sorted(state.get("star_targets") or set())
            if not targets:
                flash("pick at least one target (space)", 5)
                continue
            _stars_copy(targets)
            return


def draw_log_tab(stdscr, h, w, R, G):
    read_log_tail(400)
    lines = state["log_tail"]
    q = state["search"].lower()
    if q:
        lines = [l for l in lines if q in l.lower()]
    start = max(0, len(lines) - (h - 6))
    for i, line in enumerate(lines[start:start + h - 6]):
        attr = 0
        if "FAIL" in line:
            attr = R
        elif " OK " in line:
            attr = G
        safe_add(stdscr, 4 + i, 1, line[:w - 2], attr)
    hint = "L = full log viewer (scroll+search)   %d lines" % len(state["log_tail"])
    if q:
        hint = "filter '%s' -> %d/%d lines   (Esc = clear)" % (
            state["search"], len(lines), len(state["log_tail"]))
    safe_add(stdscr, h - 2, 1, hint, curses.A_DIM)


def draw_history_tab(stdscr, h, w):
    recs = SYNC.last_records(40)
    durs = [r.get("elapsed_sec", 0) or 0 for r in recs[-30:]]
    spark = _sparkline(durs)
    if spark:
        safe_add(stdscr, 4, 1, "duration trend (last %d runs): %s"
                 % (len(durs), spark), curses.A_BOLD)
    row = 5
    for r in reversed(recs[-(h - 8):]):
        attr = curses.color_pair(4) if r.get("failed") else \
            curses.color_pair(2)
        safe_add(stdscr, row, 1,
                 "%s | %d ok, %d failed of %d | %ss"
                 % (r.get("finished_at"), r.get("ok", 0),
                    r.get("failed", 0), r.get("repos", 0),
                    r.get("elapsed_sec")), attr)
        row += 1
    if not recs:
        safe_add(stdscr, 4, 1, "no runs recorded yet", curses.A_DIM)


def draw_settings(stdscr, h, w, dim):
    # read the file rather than state["settings"]: that snapshot only moves
    # when the platform refresh runs, so a change made in Options (or by
    # hand) looked like it had not been saved
    st = C.load_config().get("settings", {}) or {}
    notify = st.get("notify", {}) or {}
    rows = [
        ("Parallel workers   : %s" % st.get("workers", 4), 0),
        ("Mirror mode        : %s" % st.get("mode", "full"), 0),
        ("Include forks      : %s" % st.get("include_forks", False), 0),
        ("Schedule           : %s" % st.get("schedule", "hourly"), 0),
        ("Discord webhook    : %s"
         % ("configured" if notify.get("discord_webhook") else "not set"), 0),
        ("Telegram bot       : %s"
         % ("configured" if notify.get("telegram_token") else "not set"), 0),
        ("Notify on          : %s" % notify.get("on", "failure"), 0),
        ("Pause between repos : %ss" % (st.get("pace", 0) or 0), 0),
        ("Mesh conflicts     : %s" % st.get("mesh_conflict", "newest"), 0),
        ("Token warning      : %s days" % (st.get("token_warn_days", 30)
                                           or 30), 0),
        ("Colour theme       : %s" % st.get("theme", "default"), 0),
        ("Scheduler          : %s" % cached_status(), 0),
        ("Config file        : %s" % C.CONFIG_FILE, dim),
        ("Mirror hub         : %s" % C.MIRROR_DIR, dim),
        ("Log file           : %s" % C.LOG_FILE, dim),
        ("", 0),
        ("change with: O (options)  C (schedule)  A (accounts)  "
         "W (wizard)", dim),
        ("notify test  : gitsync notify --test", dim),
    ]
    for i, (text, attr) in enumerate(rows[:h - 6]):
        safe_add(stdscr, 4 + i, 1, text[:w - 2], attr)


HEALTH_TTL = 30          # seconds between health refreshes (network + disk walk)

_health = {"tok_txt": "tokens: checking...", "tok_attr": 0,
           "disk_txt": "hub: checking...", "disk_attr": 0,
           "wh_txt": "webhooks: checking...", "wh_attr": 0,
           "at": 0.0}
_health_lock = threading.Lock()
_health_busy = threading.Lock()


def refresh_health(force=False):
    """token expiry + hub size + webhook state, off the draw path.

    This used to run inside _health_strip(), i.e. on every dashboard frame —
    and the dashboard redraws once a second for the clock. TK.check() is a
    network round-trip per platform and diskutil.mirrors() walks the whole
    hub, so an open dashboard issued ~240 API calls a minute and spent
    ~0.5s per frame in the health line. It now runs in a thread on a timer.
    """
    if not _health_busy.acquire(blocking=False):
        return
    try:
        with _health_lock:
            if not force and time.time() - _health["at"] < HEALTH_TTL:
                return
            _health["at"] = time.time()     # advance first: never stack
        try:
            cfg = C.load_config()
            tok = TK.check(cfg)
            tok_txt = TK.summary(tok)
            tok_attr = curses.color_pair(2) if not TK.bad(tok) else (
                curses.color_pair(4)
                if any(t["level"] in ("expired", "critical") for t in tok)
                else curses.color_pair(3))
        except Exception:
            tok_txt, tok_attr = "tokens: unknown", 0

        disk_txt, disk_attr = "hub: ?", 0
        try:
            ms = DK.mirrors()
            free, tot = DK.fs_free(C.MIRROR_DIR)
            pct = (100.0 * (tot - free) / tot) if tot else 0
            disk_txt = "hub: %d mirrors, %s, disk %s%% full" % (
                len(ms), DK.human(sum(m[2] for m in ms)), int(pct))
            disk_attr = (curses.color_pair(4) if pct > 90 else
                         (curses.color_pair(3) if pct > 75 else
                          curses.color_pair(2)))
        except Exception:
            pass

        wh_txt, wh_attr = "webhooks: not set up", 0
        try:
            if WH.get_secret():
                live = S.webhooks_active()
                wh_attr = curses.color_pair(2) if live else 0
                wh_txt = ("webhooks: live" if live
                          else "webhooks: configured, not running")
        except Exception:
            pass

        with _health_lock:
            _health.update(tok_txt=tok_txt, tok_attr=tok_attr,
                           disk_txt=disk_txt, disk_attr=disk_attr,
                           wh_txt=wh_txt, wh_attr=wh_attr)
        state["dirty"] = True
    finally:
        _health_busy.release()


def _health_strip(stdscr, row, w, G, Y, R, dim):
    """one line: token health + hub disk + webhook receiver state (cached)"""
    if row >= stdscr.getmaxyx()[0] - 2:
        return
    with _health_lock:
        tok_txt = _health["tok_txt"]
        tok_attr = _health["tok_attr"]
        disk_txt = _health["disk_txt"]
        wh_txt = _health["wh_txt"]
    safe_add(stdscr, row, 1, (" %-34s %-46s %s"
                              % (tok_txt, disk_txt, wh_txt))[:w - 3], dim)
    # recolour just the token segment
    safe_add(stdscr, row, 2, tok_txt[:32], tok_attr)


def draw_dashboard(stdscr, h, w, Cc, G, Y, R, dim):
    compact = h < 18 or w < 70      # phones / narrow splits

    if compact:
        # ---------------- compact (mobile) layout ----------------
        row = 4
        recs = state.get("records") or [{}]
        lr = recs[-1] or {}
        if lr.get("finished_at"):
            bad = (lr.get("failed") or lr.get("partial"))
            attr = R if bad else G
            extra = ""
            if lr.get("partial"):
                extra = ", %s partial" % lr.get("partial")
            safe_add(stdscr, row, 0,
                     (" last: %s ok%s, %s failed of %s (%ss)"
                      % (lr.get("ok", "?"), extra, lr.get("failed", "?"),
                         lr.get("repos", "?"),
                         lr.get("elapsed_sec", "?")))[:w - 1], attr)
            row += 1
        safe_add(stdscr, row, 0,
                 (" cron: %s | %s" % (cached_status()[:26],
                                      "SYNCING..." if state["syncing"]
                                      else "idle"))[:w - 1], dim)
        row += 2
        plats = state["platforms"]
        if plats:
            live = sum(1 for r in plats if r[2] and r[3])
            txt = "%d/%d sites: %s" % (
                live, len(plats),
                " ".join("%s:%s%s" % (n[:10], "OK" if ok else "FAIL",
                                      "" if on else "(off)")
                         for n, _t, on, ok, _i, _c in plats))
            while txt and row < h - 3:
                safe_add(stdscr, row, 0, txt[:w - 1],
                         G if "FAIL" not in txt else R)
                txt = txt[w - 1:]
                row += 1
        else:
            safe_add(stdscr, row, 0, " platforms: loading...", dim)
            row += 1
        row += 1
        lines = state["sync_lines"] if state["syncing"] else state["log_tail"]
        avail = max(0, h - row - 1)
        for i, line in enumerate(lines[-avail:][:avail] if avail else []):
            attr = 0
            if "FAIL" in line or "failed" in line.lower():
                attr = R
            elif " OK " in line or "done" in line:
                attr = G
            safe_add(stdscr, row + i, 0, line[:w - 1], attr)
        return

    # ---------------- full (desktop) layout ----------------
    # Every section is drawn straight onto stdscr via _panel_rows: the
    # old per-section curses windows were noutrefresh'd correctly but then
    # wiped by the clock's stdscr flush, so the dashboard rendered empty.
    top = 4
    plat_rows = []
    shown_plats = state["platforms"][:6]
    for name, ptype, on, ok, info, count in shown_plats:
        attr = (G if ok else R) if on else dim
        plat_rows.append(("%-14s %-9s %-4s %-5s repos=%-5s %s"
                          % (name, ptype, "ON" if on else "off",
                             "OK" if ok else "FAIL", count, info), attr))
    if not plat_rows:
        plat_rows.append(("platforms: loading...", dim))
    hidden_plats = len(state["platforms"]) - len(shown_plats)
    if hidden_plats > 0:
        plat_rows.append(("... +%d more site(s) - see the Platforms tab"
                          % hidden_plats, dim))
    _panel_rows(stdscr, h, w, top, plat_rows,
                platforms_title() + "  (A = manage accounts)")
    top2 = top + min(len(plat_rows) + 2, h - top - 6)

    if not state["routes"]:
        _panel_rows(stdscr, h, w, top2, [
            ("1. add your accounts:  A  (or: gitsync platforms add ...)", 0),
            ("2. add a route:        gitsync routes add main github "
             "gitlab,forgejo,backup", 0),
            ("   two-way instead:    gitsync routes add all "
             "github,gitlab,forgejo", 0),
            ("3. run it:             S  (or: gitsync sync)", 0),
        ], "GETTING STARTED - no routes yet")
        top2 += 6

    bits = []
    for (n, s, t, _i, _e, direction, paused, route_on) in state["routes"]:
        if direction == "mesh":
            bits.append("%s: mesh %s" % (n, ", ".join([s] + t)))
        else:
            shown = [x + ("*" if x in paused else "") for x in t]
            bits.append("%s: %s -> %s" % (n, s, ", ".join(shown) or "(none)"))
        if not route_on:
            bits[-1] += " [PAUSED]"
    routes_txt = "  |  ".join(bits) or "(none)"
    # one long line used to be truncated at the screen edge with no hint,
    # so with several routes the dashboard silently hid the last ones
    if len(routes_txt) > w - 10 and bits:
        keep, used = [], 0
        for b in bits:
            if used + len(b) + 4 > w - 26:
                break
            keep.append(b)
            used += len(b) + 4
        hidden = len(bits) - len(keep)
        routes_txt = "  |  ".join(keep)
        if hidden > 0:
            routes_txt += "  |  +%d more (3:Routes)" % hidden
    st = state["settings"]
    route_rows = [
        (routes_txt[:w - 8], curses.A_BOLD),
        ("workers=%s  mode=%s  forks=%s   scheduler: %s"
         % (st.get("workers"), st.get("mode"), st.get("include_forks"),
            cached_status()), dim),
    ]
    _panel_rows(stdscr, h, w, top2, route_rows, "ROUTES & SETTINGS")

    # ---- health strip: tokens, disk, webhooks ----
    _health_strip(stdscr, top2 + len(route_rows) + 2, w, G, Y, R, dim)

    top3 = top2 + len(route_rows) + 3
    if state["syncing"]:
        done, total, current = sync_progress()
        pct = ("%d/%d" % (done, total)) if total else "starting..."
        bar_w = max(10, w - 44)
        filled = int(bar_w * done / total) if total else 0
        _panel_rows(stdscr, h, w, top3, [
            ("[%s%s] %s" % ("#" * filled, "-" * (bar_w - filled), pct),
             curses.A_BOLD),
            ("current: %s" % (current[:w - 20] or "..."), Y),
        ], "SYNC IN PROGRESS")
        top3 += 4

    lines = state["sync_lines"] if state["syncing"] else state["log_tail"]
    log_rows = []
    for line in lines[-(h - top3 - 3):][:h - top3 - 3]:
        attr = 0
        if "FAIL" in line or "failed" in line.lower():
            attr = R
        elif " OK " in line or "done" in line:
            attr = G
        elif "===" in line:
            attr = curses.A_BOLD
        log_rows.append((line[:w - 8], attr))
    if not log_rows:
        log_rows.append(("(no log lines yet)", dim))
    _panel_rows(stdscr, h, w, top3, log_rows,
                "LOG (live tail - L for full log, H for history)")


# ---------------------------------------------------------------- snapshot

def snapshot():
    cfg = C.load_config()
    print("gitsync %s — snapshot (%s)"
          % (__version__, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 68)
    print("config : %s" % C.CONFIG_FILE)
    print("mirrors: %s" % C.MIRROR_DIR)
    print()
    plats = list(cfg.get("platforms", {}).items())
    print("platforms: %d site(s)" % len(plats))
    for name, p in plats:
        try:
            ok, info = P.make(name, p).test()
        except Exception as e:
            ok, info = False, str(e)
        print("  %-14s %-9s %-4s %s" % (name, p.get("type"),
                                        "ON" if p.get("enabled") else "off",
                                        info if ok else "FAIL: %s" % info))
    print()
    for r in C.routes(cfg):
        tag = " [mesh]" if r.get("direction") == "mesh" else ""
        if r.get("enabled") is False:
            tag += " [PAUSED]"
        paused = set(r.get("paused_targets") or [])
        tgts = [t + ("*" if t in paused else "")
                for t in r.get("targets", [])]
        print("  route %-10s%s %s -> %s"
              % (r.get("name"), tag, r.get("source"),
                 ", ".join(tgts) or "(none)"))
        if paused:
            print("             paused: %s" % ", ".join(sorted(paused)))
    print()
    print("settings : workers=%s mode=%s schedule=%s"
          % (C.setting(cfg, "workers"), C.setting(cfg, "mode"),
             C.setting(cfg, "schedule")))
    print("scheduler: %s" % S.status())
    recs = SYNC.last_records(1)
    if recs:
        r = recs[-1]
        print("last run : %s | %d ok, %d failed of %d in %ss"
              % (r.get("finished_at"), r.get("ok", 0), r.get("failed", 0),
                 r.get("repos", 0), r.get("elapsed_sec")))
    print("=" * 68)
    print("run 'gitsync tui' in an interactive terminal for the dashboard")


# ---------------------------------------------------------------- main

def main():
    if not sys.stdout.isatty():
        snapshot()
        return
    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print("TUI error: %s" % e)
        snapshot()


def _run(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)          # decode arrows/PgUp/PgDn as curses.KEY_*
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass
    stdscr.timeout(1000)
    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
    except (AttributeError, curses.error):
        pass
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        apply_theme()
    read_log_tail()
    state["records"] = SYNC.last_records(10)
    _cfg = C.load_config()
    state["routes"] = [(r.get("name"), r.get("source"),
                        list(r.get("targets", [])),
                        r.get("include") or [], r.get("exclude") or [],
                        r.get("direction") or "one-way",
                        list(r.get("paused_targets") or []),
                        r.get("enabled") is not False)
                       for r in C.routes(_cfg)]
    state["route_sched"] = {r.get("name"): r.get("schedule")
                            for r in C.routes(_cfg)}
    state["settings"] = dict(_cfg.get("settings", {}))
    threading.Thread(target=refresh_platforms, daemon=True).start()
    threading.Thread(target=refresh_health, daemon=True).start()
    last_draw = 0.0
    last_clock = 0.0
    dirty = True
    while True:
        now = time.time()
        # threads raise state["dirty"] when new data arrives; merge it in
        dirty = dirty or state.get("dirty")
        if dirty or (state["syncing"] and now - last_draw > 2.0):
            draw(stdscr)
            last_draw = now
            last_clock = now
            dirty = False
            state["dirty"] = False
        elif now - last_clock >= 1.0:
            update_clock(stdscr)
            last_clock = now
            dirty = True
        ch = stdscr.getch()
        if ch == curses.KEY_MOUSE:
            # a malformed event must not spin the loop: fall through as "no
            # key" rather than re-reading the same queued event
            ch = -1
            try:
                _eid, mx, my, _bz, bstate = curses.getmouse()
            except Exception:
                bstate = 0
            if bstate & curses.BUTTON1_CLICKED:
                if my == 2:                     # the tab bar
                    hit = _tab_at(mx, stdscr.getmaxyx()[1])
                    if hit is not None:
                        state["tab"] = hit
                        state["scroll"] = 0
        if ch != -1:
            dirty = True

        # ---- search mode: capture typed characters ----
        if state["search_mode"]:
            if ch in (27,):                       # Esc: clear & exit
                state["search"] = ""
                state["search_mode"] = False
            elif ch in (10, 13, curses.KEY_ENTER):
                state["search_mode"] = False
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                state["search"] = state["search"][:-1]
            elif 32 <= ch < 127:
                state["search"] += chr(ch)
            continue

        if ch in (ord("q"), ord("Q")):
            return
        elif ord("1") <= ch <= ord(str(len(TABS))):
            state["tab"] = ch - ord("1")
            state["scroll"] = 0
            STAR_STATE["scroll"] = 0
            state["plat_scroll"] = 0
        elif ch in (ord("s"), ord("S")):
            run_sync()
        elif ch in (ord("d"), ord("D")):
            run_sync(dry_run=True)
        elif ch in (ord("l"), ord("L")):
            log_view(stdscr)
        elif state["tab"] == 3 and ch in (ord("c"), ord("C"), ord("s"),
                                          ord("S")):
            stars_screen(stdscr)
        elif ch in (ord("h"), ord("H")):
            history_screen(stdscr)
        elif ch in (ord("c"), ord("C")):
            schedule_screen(stdscr)
        elif ch in (ord("o"), ord("O")):
            options_screen(stdscr)
        elif ch in (ord("a"), ord("A")):
            accounts_screen(stdscr)
        elif ch in (ord("w"), ord("W")):
            launch_wizard(stdscr)
        elif state["tab"] == 2 and ch in (ord("p"), ord("P"), 10, 13,
                                          curses.KEY_ENTER):
            routes_screen(stdscr)
        elif state["tab"] == 1 and ch in (curses.KEY_UP, curses.KEY_DOWN,
                                          curses.KEY_PPAGE, curses.KEY_NPAGE,
                                          curses.KEY_HOME, curses.KEY_END,
                                          ord("k"), ord("j"), ord("g"),
                                          ord("G")):
            platforms_key(ch)
        elif state["tab"] == 3 and ch in (curses.KEY_UP, curses.KEY_DOWN,
                                          curses.KEY_PPAGE, curses.KEY_NPAGE,
                                          curses.KEY_HOME, curses.KEY_END):
            n = len(STAR_STATE["rows"])
            if ch == curses.KEY_UP:
                STAR_STATE["scroll"] = max(0, STAR_STATE["scroll"] - 1)
            elif ch == curses.KEY_DOWN:
                STAR_STATE["scroll"] = min(max(0, n - 1),
                                           STAR_STATE["scroll"] + 1)
            elif ch == curses.KEY_PPAGE:
                STAR_STATE["scroll"] = max(0, STAR_STATE["scroll"] - 8)
            elif ch == curses.KEY_NPAGE:
                STAR_STATE["scroll"] = min(max(0, n - 1),
                                           STAR_STATE["scroll"] + 8)
            elif ch == curses.KEY_END:
                STAR_STATE["scroll"] = max(0, n - 1)
            else:
                STAR_STATE["scroll"] = 0
        elif ch in (ord("r"), ord("R"), ord("p"), ord("P")):
            threading.Thread(target=refresh_platforms, daemon=True).start()
        elif ch in (ord("n"), ord("N")):
            notifications_screen(stdscr)
        elif ch in (ord("t"), ord("T")):
            _tools_screen(stdscr)
        elif ch in (ord("e"), ord("E")):
            repos_screen(stdscr)
        elif ch == ord("/"):
            state["search_mode"] = True
            state["search"] = ""
            flash("type to filter, Enter/Esc when done", 8)

        if now - last_draw > 10:
            read_log_tail()
            if time.time() - state["counts_age"] > 60:
                threading.Thread(target=refresh_platforms, daemon=True).start()
            if time.time() - _health["at"] > HEALTH_TTL:
                threading.Thread(target=refresh_health, daemon=True).start()


def _run_capture(cmd, timeout=300):
    """run a CLI command and return its output lines (blocking - only for
    commands that finish in well under a second)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, PYTHONPATH=_app_dir()))
        return (r.stdout + r.stderr).splitlines()
    except Exception as e:
        return ["failed: %s" % e]


def _run_bg(title, cmd):
    """run a command in the background and stream its output to the log panel.

    `gitsync compare` takes minutes and `disk` takes ~12s; running them on
    the main thread froze the whole TUI for that long, which is what "the
    TUI feels slow" was. The dashboard keeps responding and the output
    appears live, exactly like a sync does.
    """
    if state["syncing"]:
        flash("something is already running", 5)
        return
    state["syncing"] = True
    state["sync_lines"] = ["--- %s started %s ---"
                           % (title, datetime.now().strftime("%H:%M:%S"))]
    state["tab"] = 0                     # the log panel is on the dashboard
    state["dirty"] = True

    def worker():
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 env=dict(os.environ, PYTHONPATH=_app_dir()))
            for line in p.stdout:
                state["sync_lines"].append(line.rstrip())
                state["dirty"] = True
            p.wait()
            state["sync_lines"].append(
                "--- %s finished (rc=%d) %s ---"
                % (title, p.returncode,
                   datetime.now().strftime("%H:%M:%S")))
        except Exception as e:
            state["sync_lines"].append("--- error: %s ---" % e)
        finally:
            state["syncing"] = False
            # keep the tool's output on screen instead of snapping back to
            # the log tail the moment it finishes
            state["log_tail"] = list(state["sync_lines"])
            state["dirty"] = True
    threading.Thread(target=worker, daemon=True).start()


def _confirm_run(stdscr, title, cmd, warning):
    """ask before running something that changes every repo"""
    h, w = stdscr.getmaxyx()
    while True:
        stdscr.erase()
        safe_add(stdscr, 0, 0, (" %s " % title).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(4))
        safe_add(stdscr, 2, 1, warning[:w - 4], curses.A_DIM)
        safe_add(stdscr, 3, 1, "Dry-run first if you are unsure.",
                 curses.A_DIM)
        modal_footer(stdscr, h, w, " Y = run it   N = cancel ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("n"), ord("N"), ord("b"), ord("B"), 27):
            return
        if ch in (ord("y"), ord("Y")):
            _suspend_run(stdscr, cmd)
            return


def _confirm_bg(stdscr, title, cmd, warning):
    """ask before running something that changes every repo, then run it in
    the background so the TUI keeps responding"""
    h, w = stdscr.getmaxyx()
    while True:
        stdscr.erase()
        safe_add(stdscr, 0, 0, (" %s " % title).center(w - 1)[:w - 1],
                 curses.A_BOLD | curses.color_pair(4))
        safe_add(stdscr, 2, 1, warning[:w - 4], curses.A_DIM)
        safe_add(stdscr, 3, 1, "Dry-run first if you are unsure.",
                 curses.A_DIM)
        modal_footer(stdscr, h, w, " Y = run it   N = cancel ")
        commit_screen(stdscr)
        ch = stdscr.getch()
        if ch in (ord("n"), ord("N"), ord("b"), ord("B"), 27):
            return
        if ch in (ord("y"), ord("Y")):
            _run_bg(title, cmd)
            return


def _tools_screen(stdscr):
    """everything that still needed a terminal, in one menu.

    Read-only checks (disk, doctor, compare) render inside the TUI; the
    ones that change something (mirror, update, uninstall) drop to the
    shell and come back.
    """
    def bg(title, cmd):
        _run_bg(title, cmd)

    def ask_repo():
        name = prompt_line(stdscr, " repo name: ")
        if not name:
            return
        _run_bg("MIRROR %s" % name,
                ["python3", "-m", "gitsync", "mirror", name])

    items = [
        ("Disk usage + orphans", "hub size, what can be reclaimed",
         lambda: bg("DISK", ["python3", "-m", "gitsync", "disk",
                             "--orphans"])),
        ("Doctor", "health check: tokens, routes, storage",
         lambda: bg("DOCTOR", ["python3", "-m", "gitsync", "doctor"])),
        ("Compare sites", "which repos differ across a route",
         lambda: bg("COMPARE", ["python3", "-m", "gitsync", "compare"])),
        ("Mirror one repo", "sync a single repo now", ask_repo),
        ("Fix drift (preview)", "see which repos disagree, change nothing",
         lambda: bg("FIX DRIFT", ["python3", "-m", "gitsync", "fix",
                                  "--dry-run"])),
        ("Fix drift (run it)", "re-sync every repo whose sites disagree",
         lambda: _confirm_bg(stdscr, "FIX DRIFT",
                             ["python3", "-m", "gitsync", "fix"],
                             "This re-pushes every mismatched repo to all "
                             "of its targets.")),
        ("Update gitsync", "pull the newest release and reinstall",
         lambda: _suspend_run(stdscr, ["python3", "-m", "gitsync",
                                       "update"])),
        ("Uninstall", "remove the scheduler and the command",
         lambda: _suspend_run(stdscr, ["python3", "-m", "gitsync",
                                       "uninstall", "--yes"])),
    ]
    menu(stdscr, "TOOLS", items,
         note="output streams on the dashboard log while the TUI stays "
              "responsive")


def launch_wizard(stdscr):
    """suspend curses, run the setup wizard, resume"""
    _suspend_run(stdscr, ["python3", "-m", "gitsync", "setup"])
    threading.Thread(target=refresh_platforms, daemon=True).start()
