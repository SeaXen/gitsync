"""gitsync command line interface.

    gitsync setup      guided first-run configuration
    gitsync sync       run a sync now (all routes)
    gitsync status     platform + scheduler status
    gitsync log        tail the log
    gitsync records    recent run history
    gitsync tui        open the dashboard
    gitsync install    install the scheduler (systemd timer / cron)
    gitsync platforms  list platforms and their test results
    gitsync scopes     show required token scopes per platform
"""

import argparse
import os
import sys

from . import __version__
from . import config as C
from . import permissions as PERM
from . import platforms as P
from . import scheduler as S
from . import sync as SYNC


def _platforms_table(cfg):
    rows = []
    for name, p in cfg.get("platforms", {}).items():
        try:
            ok, info = P.make(name, p).test()
        except Exception as e:
            ok, info = False, str(e)
        rows.append((name, p.get("type"), "ON " if p.get("enabled") else "off",
                     "OK " if ok else "FAIL", info))
    return rows


def cmd_setup(args):
    from . import wizard
    wizard.run_wizard()


def cmd_sync(args):
    cfg = C.load_config()
    if not C.enabled_platforms(cfg):
        print("No platforms configured. Run: gitsync setup")
        return 2
    t = SYNC.run_sync(cfg, route_name=args.route, quiet=args.quiet)
    print("\n%d ok, %d partial, %d failed of %d repos in %ss"
          % (t["ok"], t["partial"], t["failed"], t["repos"],
             t["elapsed_sec"]))
    return 0 if t["failed"] == 0 else 1


def cmd_status(args):
    cfg = C.load_config()
    print("gitsync %s" % __version__)
    print("config : %s" % C.CONFIG_FILE)
    print("mirrors: %s" % C.MIRROR_DIR)
    print()
    print("platforms:")
    for name, ptype, en, ok, info in _platforms_table(cfg):
        print("  %-14s %-9s %-4s %-5s %s" % (name, ptype, en, ok, info))
    print()
    print("routes:")
    for r in C.routes(cfg):
        print("  %-10s %s -> %s" % (r.get("name"), r.get("source"),
                                    ", ".join(r.get("targets", []))))
    print()
    print("settings: workers=%s mode=%s schedule=%s forks=%s"
          % (C.setting(cfg, "workers"), C.setting(cfg, "mode"),
             C.setting(cfg, "schedule"), C.setting(cfg, "include_forks")))
    print("scheduler: %s" % S.status())
    nxt = S.next_run()
    if nxt:
        print("next run : %s" % nxt)
    recs = SYNC.last_records(1)
    if recs:
        r = recs[-1]
        print("last run : %s | %d ok, %d failed of %d in %ss"
              % (r.get("finished_at"), r.get("ok", 0), r.get("failed", 0),
                 r.get("repos", 0), r.get("elapsed_sec")))
    return 0


def cmd_log(args):
    if not os.path.exists(C.LOG_FILE):
        print("no log yet")
        return 0
    n = args.lines
    lines = open(C.LOG_FILE).read().splitlines()
    for line in lines[-n:]:
        print(line)
    return 0


def cmd_records(args):
    recs = SYNC.last_records(args.count)
    if not recs:
        print("no runs recorded yet")
        return 0
    for r in recs:
        print("%s | route(s)=%d | %d ok, %d partial, %d failed of %d | %ss"
              % (r.get("finished_at"), r.get("routes", 0), r.get("ok", 0),
                 r.get("partial", 0), r.get("failed", 0),
                 r.get("repos", 0), r.get("elapsed_sec")))
        for f in r.get("failures", [])[:5]:
            print("    failed: %s/%s — %s"
                  % (f.get("route"), f.get("repo"), f.get("error", "")[:100]))
    return 0


def cmd_tui(args):
    from . import tui
    tui.main()
    return 0


def cmd_install(args):
    cfg = C.load_config()
    key = C.setting(cfg, "schedule", "hourly")
    if args.schedule:
        key = args.schedule
        C.set_setting(cfg, "schedule", key)
    if key == "off":
        if S.systemd_installed():
            S.uninstall_systemd()
            print("systemd timer removed")
        S.install_cron("off")
        print("scheduler disabled (manual runs only)")
        return 0
    if args.cron or not S.have_systemd():
        S.install_cron(key)
        print("cron installed: %s" % C.SCHEDULES[key]["label"])
        return 0
    if S.install_systemd(key):
        print("systemd timer installed: %s (Persistent=true — catches up "
              "runs missed while the machine was off)"
              % C.SCHEDULES[key]["label"])
        print("status: %s" % S.status())
        print("logs  : journalctl -u %s" % S.SERVICE)
        return 0
    print("systemd install failed — falling back to cron")
    S.install_cron(key)
    return 0


def cmd_platforms(args):
    cfg = C.load_config()
    for name, ptype, en, ok, info in _platforms_table(cfg):
        print("%-14s %-9s %-4s %-5s %s" % (name, ptype, en, ok, info))
    return 0


def cmd_scopes(args):
    for ptype in ("github", "gitlab", "forgejo", "gitea", "local"):
        print(PERM.format_guide(ptype))
        print()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="gitsync",
        description="multi-platform git repository sync (GitHub, GitLab, "
                    "Forgejo/Gitea, local folders)")
    ap.add_argument("--version", action="version",
                    version="gitsync %s" % __version__)
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("setup", help="guided first-run configuration")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("sync", help="run a sync now")
    sp.add_argument("--route", help="only this route")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("status", help="platforms, routes, scheduler status")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("log", help="show the log")
    sp.add_argument("-n", "--lines", type=int, default=30)
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("records", help="recent run history")
    sp.add_argument("-n", "--count", type=int, default=10)
    sp.set_defaults(func=cmd_records)

    sp = sub.add_parser("tui", help="open the dashboard")
    sp.set_defaults(func=cmd_tui)

    sp = sub.add_parser("install", help="install the scheduler")
    sp.add_argument("--schedule",
                    choices=["hourly", "6h", "12h", "daily", "off"])
    sp.add_argument("--cron", action="store_true",
                    help="force cron instead of systemd")
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser("platforms", help="list platforms + test them")
    sp.set_defaults(func=cmd_platforms)

    sp = sub.add_parser("scopes", help="show required token scopes")
    sp.set_defaults(func=cmd_scopes)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
