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


def run_wizard():
    hr("gitsync setup wizard")
    print(" This configures platforms, sync direction and schedule.")
    print(" Config file: %s" % C.CONFIG_FILE)

    cfg = C.load_config()

    # ---- platforms ----
    hr("STEP 1/4 — platforms")
    print(" Available: github, gitlab, forgejo, gitea, local")
    print(" You can enable several of each kind (e.g. two Forgejo servers).")
    chosen = ask("Which platforms to enable (comma-separated)",
                 "github,gitlab,local")
    chosen = [c.strip() for c in chosen.split(",") if c.strip()]

    cfg["platforms"] = {}
    for name in chosen:
        ptype = name
        if ptype not in ("github", "gitlab", "forgejo", "gitea", "local"):
            ptype = ask("Type for '%s' (github/gitlab/forgejo/gitea/local)"
                        % name, "forgejo")
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
                    p["token"] = ask("GitHub token (starts with ghp_/github_pat_)")
            else:
                p["token"] = ask("%s token" % ptype)
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
    cfg["routes"] = [{"name": "main", "source": src,
                      "targets": [t.strip() for t in tgt.split(",")
                                  if t.strip() and t.strip() != src]}]

    # ---- settings ----
    hr("STEP 4/4 — schedule & options")
    print(" Schedules: hourly | 6h | 12h | daily | off (manual only)")
    sched = ask("Schedule", "hourly")
    cfg["settings"]["schedule"] = sched if sched in C.SCHEDULES else "hourly"
    cfg["settings"]["workers"] = int(ask("Parallel workers", "4") or 4)
    # "Yes" = safe mode (never delete refs); "No" (default) = exact mirror
    cfg["settings"]["mode"] = "safe" if ask_yes(
            "Use safe mode? (No = exact mirror, prunes deleted refs)",
            False) else "full"

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
