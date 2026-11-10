"""Interactive setup wizard — run with: gitsync setup

Plain terminal prompts (no curses) so it works over any SSH connection.
Shows exactly which token scopes each platform needs before asking for
the token, and tests every connection before saving.
"""

import os
import sys

from . import config as C
from . import permissions as PERM
from . import platforms as P


def _tty():
    """the controlling terminal, even when stdin is a pipe (curl | bash)"""
    try:
        if sys.stdin.isatty():
            return sys.stdin
        return open("/dev/tty", "r+")
    except OSError:
        return None


def ask(prompt, default=None):
    """prompt on the real terminal so `curl ... | bash` stays interactive"""
    suffix = " [%s] " % default if default else " "
    tty = _tty()
    try:
        if tty is None:
            return default or ""
        if tty is not sys.stdin:
            tty.write(prompt + suffix)
            tty.flush()
            line = tty.readline()
        else:
            line = input(prompt + suffix)
        ans = line.strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default or ""
    finally:
        if tty is not None and tty is not sys.stdin:
            tty.close()
    return ans or (default or "")


def ask_secret(prompt):
    """read a token without echoing it (getpass on a tty).

    The old path used input(), so tokens were shown on screen and stayed in
    the terminal scrollback.
    """
    tty = _tty()
    try:
        if tty is None:
            return ""
        if tty is sys.stdin:
            import getpass
            return getpass.getpass(prompt + " ").strip()
        tty.write(prompt + " ")
        tty.flush()
        import termios
        fd = tty.fileno()
        old_attrs = termios.tcgetattr(fd)
        try:
            new = termios.tcgetattr(fd)
            new[3] = new[3] & ~termios.ECHO      # lflags
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            line = tty.readline()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            tty.write("\n")
            tty.flush()
        return line.strip()
    except Exception:
        return ""
    finally:
        if tty is not None and tty is not sys.stdin:
            try:
                tty.close()
            except OSError:
                pass


def ask_yes(prompt, default=True):
    d = "Y/n" if default else "y/N"
    ans = ask("%s (%s)" % (prompt, d)).lower()
    if not ans:
        return default
    return ans.startswith("y")


def hr(title=""):
    print("\n" + "=" * 68)
    if title:
        print(" " + title)
        print("=" * 68)


REPO_LIST_CAP = 50


def _pick_repos(cfg, src):
    """let the user choose which repos to sync. Returns [] for "all".

    Enter (blank) = every repo. Otherwise a comma-separated list of names or
    globs (`Sea*`), stored as the route's include. The printed list is capped
    so a 500-repo account does not dump 500 lines without a count first.
    """
    sp = C.platform(cfg, src)
    if not sp or not sp.get("enabled"):
        return []
    try:
        repos = P.make(src, sp).list_repos()
    except Exception as e:
        print(" cannot list repos on %s: %s" % (src, e))
        print(" syncing everything — narrow it later with: "
              "gitsync routes edit main --include 'Sea*'")
        return []
    if not repos:
        print(" (no repos on %s yet)" % src)
        return []
    names = sorted((r["name"] for r in repos), key=str.lower)
    print("\n %d repos on %s:" % (len(names), src))
    for n in names[:REPO_LIST_CAP]:
        print("   %s" % n)
    if len(names) > REPO_LIST_CAP:
        print("   ... and %d more" % (len(names) - REPO_LIST_CAP))
    print("\n Which repos? Enter = ALL, or a comma-separated list / globs")
    print("   e.g.  SeaVault,tradex      or      Sea*")
    ans = ask("Repos", "")
    if not ans.strip():
        print(" -> all %d repos" % len(names))
        return []
    picked = [p.strip() for p in ans.split(",") if p.strip()]
    print(" -> %d pattern(s): %s" % (len(picked), ", ".join(picked)))
    return picked


def run_wizard():
    hr("gitsync setup wizard")
    print(" This configures platforms, sync direction and schedule.")
    print(" Config file: %s" % C.CONFIG_FILE)

    cfg = C.load_config()

    # ---- platforms ----
    hr("STEP 1/4 — platforms")
    print(" Available: %s" % ", ".join(C.PLATFORM_TYPES))
    print(" Several of the same kind are fine - give each a name:")
    print("   work=forgejo, home=forgejo")
    if cfg.get("platforms"):
        # re-running setup used to wipe every account with no warning
        if not ask_yes("Replace the existing accounts in %s?"
                       % C.CONFIG_FILE, False):
            print("Keeping the current setup - nothing written.")
            return 0
    chosen = ask("Which platforms to enable (comma-separated)",
                 "github,gitlab,local")
    chosen = [c.strip() for c in chosen.split(",") if c.strip()]

    cfg["platforms"] = {}
    for item in chosen:
        # "name=type" or just "type" (the name is then the type)
        if "=" in item:
            name, ptype = [x.strip() for x in item.split("=", 1)]
        else:
            name = ptype = item
        if ptype not in C.PLATFORM_TYPES:
            ptype = ask("Type for '%s' (%s)"
                        % (name, "/".join(C.PLATFORM_TYPES)), "forgejo")
        hr("Platform: %s (%s)" % (name, ptype))
        print(PERM.format_guide(ptype))
        p = {"type": ptype, "enabled": True}
        if ptype == "local":
            p["path"] = os.path.expanduser(ask(
                "Local mirror folder (bare repos are created here)",
                "~/git-mirrors"))
        else:
            default_url = {"github": "https://github.com",
                           "gitlab": "https://gitlab.com"}.get(
                               ptype, "http://localhost:3000")
            p["url"] = ask("Instance URL", default_url)
            if ptype == "github":
                p["auth"] = "token" if ask_yes(
                        "Use a personal access token? (No = use gh CLI)",
                        False) else "gh"
                if p["auth"] == "token":
                    p["token"] = ask_secret(
                        "GitHub token (hidden; ghp_/github_pat_):")
            else:
                # a blank token is not a saved blank: every API call would
                # fail with a confusing error. Re-prompt instead.
                while True:
                    p["token"] = ask_secret("%s token (hidden):" % ptype)
                    if p["token"]:
                        break
                    print("  a token is required for %s" % ptype)
                    if not ask_yes("Try again?", True):
                        print("  skipping %s" % name)
                        break
                if not p["token"]:
                    cfg["platforms"].pop(name, None)
                    continue
                if ptype == "gitlab":
                    p["ns_id"] = ask("Target namespace id (blank = your "
                                     "personal namespace)", "")
        cfg["platforms"][name] = p

    # ---- test connections ----
    hr("STEP 2/4 — testing connections")
    for name, p in cfg["platforms"].items():
        try:
            ok, info = P.make(name, p).test()
            print(" %-12s %s %s" % (name, "OK " if ok else "FAIL", info))
            if ok:
                u = P.make(name, p).user()
                if u:
                    p["owner"] = p.get("owner") or u
                    if p["type"] == "gitlab":
                        # resolve namespace id
                        rc, out, _ = P.run(["true"])
                        from .platforms import _http
                        st, nss = _http("GET", p["url"].rstrip("/") +
                                        "/api/v4/namespaces", token=p["token"],
                                        auth_style="private-token")
                        if isinstance(nss, list) and nss:
                            p["ns_id"] = str(nss[0]["id"])
        except Exception as e:
            print(" %-12s FAIL %s" % (name, e))

    # ---- routes ----
    hr("STEP 3/4 — sync direction")
    enabled = [n for n, p in cfg["platforms"].items() if p.get("enabled")]
    print(" Enabled platforms: %s" % ", ".join(enabled))
    src = ask("Source platform (repos are read from here)", enabled[0])
    tgt = ask("Targets (comma-separated, repos are written here)",
              ",".join([e for e in enabled if e != src]))
    route = {"name": "main", "source": src,
             "targets": [t.strip() for t in tgt.split(",")
                         if t.strip() and t.strip() != src]}

    # F2: choose which repos, here, instead of silently mirroring the whole
    # account. Enter = everything; otherwise a comma list or globs.
    include = _pick_repos(cfg, src)
    if include:
        route["include"] = include
    cfg["routes"] = [route]

    # ---- settings ----
    hr("STEP 4/4 — schedule & options")
    print(" Schedules: hourly | 6h | 12h | daily | weekly | off (manual only)")
    sched = ask("Schedule", "hourly")
    cfg["settings"]["schedule"] = sched if sched in C.SCHEDULES else "hourly"
    cfg["settings"]["workers"] = int(ask("Parallel workers", "4") or 4)
    # Default No = safe: a new setup must not delete branches on the
    # destination until the user explicitly asks for an exact mirror.
    cfg["settings"]["mode"] = "full" if ask_yes(
            "Exact mirror? This DELETES branches on the destination that "
            "are not on the source (y/N)", False) else "safe"
    cfg["settings"]["mesh_conflict"] = "skip" if ask_yes(
            "Two-way routes: report conflicts instead of overwriting the "
            "older side? (Y/n)", True) else "newest"

    C.save_config(cfg)
    hr("saved")
    print(" Config : %s" % C.CONFIG_FILE)
    print(" Mirrors: %s" % C.MIRROR_DIR)
    print(" Log    : %s" % C.LOG_FILE)
    print("\n Next steps:")
    print("   gitsync sync       # run the first sync now")
    print("   gitsync tui        # open the dashboard")
    print("   gitsync install    # install the scheduler (systemd timer)")
    return cfg
