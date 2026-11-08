"""gitsync TUI — the dashboard. Run with: gitsync tui

Tabs (number keys):  1 Dashboard  2 Platforms  3 Routes  4 Log  5 History
                      6 Settings
Keys:  S sync now | / search (log/history) | arrows scroll | R refresh |
       C schedule | O options | A accounts | W wizard | Q quit
"""

import curses
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

from . import __version__
from . import config as C
from . import notify as NT
from . import permissions as PERM
from . import platforms as P
from . import scheduler as S
from . import sync as SYNC

TABS = ["Dashboard", "Platforms", "Routes", "Log", "History", "Settings"]

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


def _refresh_platforms_inner():
    state["counts_age"] = time.time()   # advance first: hung calls can't re-arm
    cfg = C.load_config()
    rows = []
    for name, p in cfg.get("platforms", {}).items():
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
        rows.append((name, p.get("type", "?"), bool(p.get("enabled")),
                     bool(ok), info, count))
    state["platforms"] = rows
    state["routes"] = [(r.get("name"), r.get("source"),
                        list(r.get("targets", [])),
                        r.get("include") or [], r.get("exclude") or [])
                       for r in C.routes(cfg)]
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


def run_sync():
    if state["syncing"]:
        flash("a sync is already running")
        return
    state["syncing"] = True
    state["sync_lines"] = ["--- sync started %s ---"
                           % datetime.now().strftime("%H:%M:%S")]

    def worker():
        try:
            p = subprocess.Popen(["python3", "-m", "gitsync", "sync"],
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
            threading.Thread(target=refresh_platforms, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()


def _app_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- draw

def safe_add(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w:
        try:
            win.addnstr(y, x, str(text), max(0, w - x - 1), attr)
        except curses.error:
            pass


def box(win, title):
    win.erase()
    try:
        win.box()
    except curses.error:
        pass
    safe_add(win, 0, 2, " %s " % title, curses.A_BOLD)


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
    for key in ["hourly", "6h", "12h", "daily", "off"]:
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
        if S.systemd_installed():
            S.uninstall_systemd()
        S.install_cron("off")
        flash("schedule OFF — manual syncs only (S)")
        return
    if S.systemd_installed() or S.have_systemd():
        if S.install_systemd(key):
            flash("systemd timer: %s" % C.SCHEDULES[key]["label"])
            return
    S.install_cron(key)
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


def accounts_screen(stdscr):
    cfg = C.load_config()
    lines = ["CURRENT PLATFORMS", ""]
    for name, p in cfg.get("platforms", {}).items():
        lines.append("  %-14s type=%-8s enabled=%s"
                     % (name, p.get("type"), p.get("enabled")))
        if p.get("url"):
            lines.append("      url: %s" % p["url"])
        if p.get("owner"):
            lines.append("      owner: %s" % p["owner"])
        if p.get("path"):
            lines.append("      path: %s" % p["path"])
        lines.append("      token: %s" % ("set" if p.get("token")
                                           else "(empty)"))
        lines.append("")
    lines += ["TOKEN SCOPES — what each platform needs", ""]
    for ptype in ("github", "gitlab", "forgejo", "gitea", "bitbucket",
                  "generic", "local"):
        g = PERM.guide(ptype)
        lines.append("  %-22s minimum: %s" % (g["title"], g["minimum"]))
        if g["token_url"]:
            lines.append("      create at: %s" % g["token_url"])
    lines += [
        "",
        "TO ADD / EDIT / REMOVE AN ACCOUNT (from any shell)",
        "  gitsync platforms add NAME --type gitlab --token glpat-...",
        "  gitsync platforms edit NAME --token ... --url ... --owner ...",
        "  gitsync platforms remove NAME",
        "  gitsync platforms test [NAME]",
        "",
        "  Several platforms of the same type are supported (e.g. two",
        "  Forgejo servers) - each gets its own name, token and route.",
        "",
        "  Press B to go back.",
    ]
    _scroll_view(stdscr, " ACCOUNTS & PLATFORMS ", lines)


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
        safe_add(stdscr, h - 1, 0,
                 " [D]iscord  [T]elegram  [M]ode  [Enter]test  [B]ack "
                 .center(w - 1)[:w - 1], curses.A_REVERSE)
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
        elif ch in (10, 13, curses.KEY_ENTER):
            ok, msg = NT.notify_test(cfg)
            flash("test: %s" % msg, 6)


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
    state["scroll"] = max(0, len(state["full_log"]) - 100)
    _scroll_view(stdscr, " FULL LOG ", state["full_log"])


def _scroll_view(stdscr, title, lines):
    """generic scrollable, searchable text view (log / history / accounts)"""
    scroll = 0
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
        safe_add(stdscr, h - 1, 0,
                 " up/dn=scroll PgUp/PgDn=page /=filter Esc=clear B=back "
                 .center(w - 1)[:w - 1], curses.A_REVERSE)
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
    """repaint ONLY the clock cells (19 chars at a fixed column) so the
    terminal diff — and therefore the bytes on the wire — stays tiny"""
    h, w = stdscr.getmaxyx()
    if h < 2 or w < 24:
        return
    safe_add(stdscr, 1, CLOCK_X,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), curses.A_DIM)
    curses.doupdate()


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
        draw_log_tab(stdscr, h, w, R, G)
    elif tab == 4:
        draw_history_tab(stdscr, h, w)
    elif tab == 5:
        draw_settings(stdscr, h, w, dim)
    else:
        draw_dashboard(stdscr, h, w, Cc, G, Y, R, dim)

    # tab bar + footer
    safe_add(stdscr, 2, 0, (" " + tab_bar(w) + " ").center(w - 1)[:w - 1],
             curses.A_REVERSE)
    foot = (" [1-6]tabs  [S]ync  [L]og  [C]ron  [O]pt  [A]cc  [N]otify  "
            "[W]izard  [R]efresh  [Q]uit ")
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
    """render rows [(text, attr)] inside a bordered panel"""
    pw = curses.newwin(min(h - top - 1, len(rows) + 2), w - 2, top, 1)
    box(pw, title)
    for i, (text, attr) in enumerate(rows[:h - top - 3]):
        safe_add(pw, 1 + i, 2, text[:w - 6], attr)
    pw.noutrefresh()


def draw_platforms(stdscr, h, w, G, R, dim):
    rows = []
    for name, ptype, on, ok, info, count in state["platforms"]:
        attr = (G if ok else R) if on else dim
        rows.append(("%-14s %-9s %-4s %-5s repos=%-6s %s"
                     % (name, ptype, "ON" if on else "off",
                        "OK" if ok else "FAIL", count, info), attr))
    if not rows:
        rows.append(("platforms: loading...", dim))
    _panel_rows(stdscr, h, w, 4, rows, "PLATFORMS (A = manage accounts)")
    safe_add(stdscr, h - 2, 2,
             "add/edit/remove: gitsync platforms add|edit|remove NAME",
             dim)


def draw_routes(stdscr, h, w, G, R, dim):
    rows = []
    for name, source, targets, inc, exc in state["routes"]:
        rows.append(("%s:  %s  ->  %s" % (name, source, ", ".join(targets)),
                     curses.A_BOLD))
        if inc or exc:
            rows.append(("    filters: include=%s exclude=%s"
                         % (inc or "-", exc or "-"), dim))
    if not rows:
        rows.append(("(no routes — add one: gitsync routes add main github "
                     "gitlab,forgejo)", dim))
    _panel_rows(stdscr, h, w, 4, rows, "ROUTES (sync direction)")
    safe_add(stdscr, h - 2, 2,
             "add: gitsync routes add NAME SRC t1,t2 [--include 'Sea*']",
             dim)


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
    st = state["settings"]
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


def draw_dashboard(stdscr, h, w, Cc, G, Y, R, dim):
    compact = h < 18 or w < 70      # phones / narrow splits

    if compact:
        # ---------------- compact (mobile) layout ----------------
        row = 4
        recs = state.get("records") or [{}]
        lr = recs[-1] or {}
        if lr.get("finished_at"):
            attr = R if (lr.get("failed") or lr.get("partial")) else G
            safe_add(stdscr, row, 0,
                     (" last: %s ok, %s fail of %s (%ss)"
                      % (lr.get("synced_ok", "?"), lr.get("failed", "?"),
                         lr.get("github_repos", "?"),
                         lr.get("elapsed_sec", "?")))[:w - 1], attr)
            row += 1
        safe_add(stdscr, row, 0,
                 (" cron: %s | %s" % (cached_status()[:26],
                                      "SYNCING..." if state["syncing"]
                                      else "idle"))[:w - 1], dim)
        row += 2
        plats = state["platforms"]
        if plats:
            txt = " ".join("%s:%s%s" % (n[:10], "OK" if ok else "FAIL",
                                        "" if on else "(off)")
                           for n, _t, on, ok, _i, _c in plats)
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
    top, ph = 4, min(len(state["platforms"]) + 2, 8)
    pw = curses.newwin(ph, w - 2, top, 1)
    box(pw, "PLATFORMS")
    for i, (name, ptype, on, ok, info, count) in enumerate(
            state["platforms"][:ph - 2]):
        attr = (G if ok else R) if on else dim
        safe_add(pw, 1 + i, 2,
                 ("%-14s %-9s %-4s %-5s repos=%-5s %s"
                  % (name, ptype, "ON" if on else "off", "OK" if ok else "FAIL",
                     count, info))[:w - 6], attr)
    pw.noutrefresh()

    top2 = top + ph
    rh = 3
    rw = curses.newwin(rh, w - 2, top2, 1)
    box(rw, "ROUTES & SETTINGS")
    routes_txt = "  |  ".join("%s: %s -> %s" % (n, s, ", ".join(t))
                              for n, s, t, _i, _e in state["routes"]) or "(none)"
    safe_add(rw, 1, 2, routes_txt[:w - 6], curses.A_BOLD)
    st = state["settings"]
    safe_add(rw, 2, 2, ("workers=%s  mode=%s  forks=%s   scheduler: %s"
                        % (st.get("workers"), st.get("mode"),
                           st.get("include_forks"),
                           cached_status()))[:w - 6], dim)
    rw.noutrefresh()

    # live sync progress
    top3 = top2 + rh
    prog_h = 3 if state["syncing"] else 0
    if state["syncing"]:
        prw = curses.newwin(prog_h, w - 2, top3, 1)
        box(prw, "SYNC IN PROGRESS")
        prog = SYNC.progress
        active = list(prog.get("active", {}).keys())
        done, total = prog.get("done", 0), prog.get("total", 0)
        pct = ("%d/%d" % (done, total)) if total else "starting..."
        bar_w = max(10, w - 40)
        filled = int(bar_w * done / total) if total else 0
        bar = "[%s%s] %s" % ("#" * filled, "-" * (bar_w - filled), pct)
        safe_add(prw, 1, 2, bar[:w - 6], curses.A_BOLD)
        safe_add(prw, 2, 2, ("in flight: %s"
                             % (", ".join(a[:18] for a in active[:4])
                                or "..."))[:w - 6], Y)
        prw.noutrefresh()

    top4 = top3 + prog_h
    log_h = h - top4 - 2
    lw = curses.newwin(log_h, w - 2, top4, 1)
    box(lw, "LOG (live tail - L for full log, H for history)")
    lines = state["sync_lines"] if state["syncing"] else state["log_tail"]
    for i, line in enumerate(lines[-(log_h - 2):][:log_h - 2]):
        attr = 0
        if "FAIL" in line or "failed" in line.lower():
            attr = R
        elif " OK " in line or "done" in line:
            attr = G
        elif "===" in line:
            attr = curses.A_BOLD
        safe_add(lw, 1 + i, 2, line[:w - 6], attr)
    lw.noutrefresh()


# ---------------------------------------------------------------- snapshot

def snapshot():
    cfg = C.load_config()
    print("gitsync %s — snapshot (%s)"
          % (__version__, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 68)
    print("config : %s" % C.CONFIG_FILE)
    print("mirrors: %s" % C.MIRROR_DIR)
    print()
    for name, p in cfg.get("platforms", {}).items():
        try:
            ok, info = P.make(name, p).test()
        except Exception as e:
            ok, info = False, str(e)
        print("  %-14s %-9s %-4s %s" % (name, p.get("type"),
                                        "ON" if p.get("enabled") else "off",
                                        info if ok else "FAIL: %s" % info))
    print()
    for r in C.routes(cfg):
        print("  route %-10s %s -> %s" % (r.get("name"), r.get("source"),
                                          ", ".join(r.get("targets", []))))
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
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass
    stdscr.timeout(1000)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
    read_log_tail()
    state["records"] = SYNC.last_records(10)
    _cfg = C.load_config()
    state["routes"] = [(r.get("name"), r.get("source"),
                        list(r.get("targets", [])),
                        r.get("include") or [], r.get("exclude") or [])
                       for r in C.routes(_cfg)]
    state["settings"] = dict(_cfg.get("settings", {}))
    threading.Thread(target=refresh_platforms, daemon=True).start()
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
        ch = stdscr.getch()
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
        elif ord("1") <= ch <= ord("6"):
            state["tab"] = ch - ord("1")
            state["scroll"] = 0
        elif ch in (ord("s"), ord("S")):
            run_sync()
        elif ch in (ord("l"), ord("L")):
            log_view(stdscr)
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
        elif ch in (ord("r"), ord("R"), ord("p"), ord("P")):
            threading.Thread(target=refresh_platforms, daemon=True).start()
        elif ch in (ord("n"), ord("N")):
            notifications_screen(stdscr)
        elif ch == ord("/"):
            state["search_mode"] = True
            state["search"] = ""
            flash("type to filter, Enter/Esc when done", 8)

        if now - last_draw > 10:
            read_log_tail()
            if time.time() - state["counts_age"] > 60:
                threading.Thread(target=refresh_platforms, daemon=True).start()


def launch_wizard(stdscr):
    """suspend curses, run the setup wizard, resume"""
    curses.def_prog_mode()
    curses.endwin()
    try:
        subprocess.run(["python3", "-m", "gitsync", "setup"],
                       env=dict(os.environ, PYTHONPATH=_app_dir()))
        input("\npress Enter to return to the dashboard...")
    except Exception as e:
        print("wizard error: %s" % e)
    finally:
        curses.reset_prog_mode()
        curses.curs_set(0)
        threading.Thread(target=refresh_platforms, daemon=True).start()
