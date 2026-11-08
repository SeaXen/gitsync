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
    for ptype in ("github", "gitlab", "forgejo", "gitea", "bitbucket",
                  "generic", "local"):
        print(PERM.format_guide(ptype))
        print()
    return 0


def cmd_routes(args):
    cfg = C.load_config()
    action = args.action or "list"

    if action == "add":
        if len(args.rest) < 3:
            print("usage: gitsync routes add NAME SOURCE t1,t2")
            print("   e.g. gitsync routes add gl-to-fj gitlab forgejo")
            print("        gitsync routes add local-to-gh backup github")
            return 2
        name, source = args.rest[0], args.rest[1]
        targets = [t.strip() for t in args.rest[2].split(",") if t.strip()]
        if any(r.get("name") == name for r in cfg.get("routes", [])):
            print("route '%s' already exists" % name)
            return 2
        cfg.setdefault("routes", []).append(
            {"name": name, "source": source, "targets": targets})
        C.save_config(cfg)
        print("route added: %s (%s -> %s)" % (name, source, ", ".join(targets)))
        return 0

    if action == "remove":
        if not args.rest:
            print("usage: gitsync routes remove NAME")
            return 2
        name = args.rest[0]
        before = len(cfg.get("routes", []))
        cfg["routes"] = [r for r in cfg.get("routes", [])
                         if r.get("name") != name]
        C.save_config(cfg)
        if len(cfg["routes"]) < before:
            print("route removed: %s" % name)
        else:
            print("route not found: %s" % name)
        return 0

    # list
    for r in C.routes(cfg):
        print("%-18s %s -> %s" % (r.get("name"), r.get("source"),
                                  ", ".join(r.get("targets", []))))
    if not C.routes(cfg):
        print("(no routes - add one: gitsync routes add main github gitlab)")
    return 0


def cmd_compare(args):
    """drift check: compare the HEAD of every repo across a route"""
    cfg = C.load_config()
    routes = C.routes(cfg)
    if args.route:
        routes = [r for r in routes if r.get("name") == args.route]
    if not routes:
        print("no routes configured")
        return 2
    problems = 0
    for route in routes:
        sp = C.platform(cfg, route["source"])
        if not sp or not sp.get("enabled"):
            print("route %s: source not enabled" % route.get("name"))
            continue
        source = P.make(route["source"], sp)
        targets = {t: P.make(t, C.platform(cfg, t))
                   for t in route.get("targets", [])
                   if C.platform(cfg, t) and C.platform(cfg, t).get("enabled")}
        print("route %s: %s vs %s" % (route.get("name"), route["source"],
                                      ", ".join(targets)))
        try:
            repos = source.list_repos()
        except Exception as e:
            print("  cannot list source: %s" % e)
            continue
        for repo in repos:
            try:
                src_head = _head_of(source, repo)
            except Exception as e:
                print("  ?? %-28s source error: %s" % (repo["name"],
                                                       str(e)[:60]))
                problems += 1
                continue
            for tname, tplat in targets.items():
                try:
                    thead = _head_of(tplat, repo)
                except Exception as e:
                    thead = "error: %s" % str(e)[:40]
                if thead and thead == src_head:
                    mark = "  "
                else:
                    mark = "!!"
                    problems += 1
                print("  %s %-28s src=%s %s=%s"
                      % (mark, repo["name"], str(src_head)[:8], tname,
                         str(thead)[:8]))
    print("\n%s" % ("all in sync" if problems == 0
                    else "%d mismatched refs" % problems))
    return 0 if problems == 0 else 1


def _head_of(plat, repo):
    """resolve a repo HEAD sha on a platform (best effort)"""
    name = repo["name"]
    if plat.ptype == "local":
        path = os.path.join(plat.path, P._fs_safe(name) + ".git")
        rc, out, _ = P.run(["git", "rev-parse", "--verify", "HEAD"],
                           cwd=path, timeout=30)
        if rc == 0 and out.strip():
            return out.strip()
        # HEAD may reference an unborn branch (git init defaults to master)
        rc, out, _ = P.run(["git", "for-each-ref", "--format=%(objectname)",
                            "refs/heads/"], cwd=path, timeout=30)
        if rc == 0 and out.strip():
            return out.strip().splitlines()[0]
        return None
    url = repo.get("clone_url") or plat.ensure_repo(repo)
    rc, out, _ = P.run(["git", "ls-remote", url, "HEAD"], timeout=120)
    return out.split("\t")[0] if out else None


def cmd_mirror(args):
    """sync a single repo through the first matching route"""
    cfg = C.load_config()
    routes = C.routes(cfg)
    if args.route:
        routes = [r for r in routes if r.get("name") == args.route]
    if not routes:
        print("no routes configured")
        return 2
    route = routes[0]
    sp = C.platform(cfg, route["source"])
    source = P.make(route["source"], sp)
    targets = {t: P.make(t, C.platform(cfg, t))
               for t in route.get("targets", [])
               if C.platform(cfg, t) and C.platform(cfg, t).get("enabled")}
    from . import sync as SY
    repo = {"name": args.repo, "private": args.private,
            "clone_url": args.url}
    if not repo["clone_url"]:
        for r in source.list_repos():
            if r["name"] == args.repo:
                repo = r
                break
        else:
            print("repo '%s' not found on %s" % (args.repo, route["source"]))
            return 1
    status, detail, errors = SY.sync_repo(repo, source, targets,
                                          C.setting(cfg, "mode", "full"))
    print("%s: %s %s" % (args.repo, status, detail))
    return 0 if status == "ok" else 1


def cmd_doctor(args):
    """health check: config, platforms, disk, scheduler, hub"""
    cfg = C.load_config()
    problems = []
    print("gitsync doctor")
    print("=" * 60)
    print("config file    : %s (%s)"
          % (C.CONFIG_FILE, "ok" if os.path.exists(C.CONFIG_FILE)
             else "MISSING - run gitsync setup"))
    if not os.path.exists(C.CONFIG_FILE):
        problems.append("no config")
    print("\nplatforms:")
    for name, ptype, en, ok, info in _platforms_table(cfg):
        if en and not ok:
            problems.append("platform %s failing" % name)
        print("  %-14s %-9s %-4s %-5s %s"
              % (name, ptype, en, ok, info))
    print("\nroutes:")
    if not C.routes(cfg):
        problems.append("no routes")
        print("  (none)")
    for r in C.routes(cfg):
        srcp = C.platform(cfg, r.get("source"))
        src_ok = bool(srcp) and srcp.get("enabled")
        tgts = [t for t in r.get("targets", [])
                if C.platform(cfg, t) and C.platform(cfg, t).get("enabled")]
        good = bool(src_ok) and bool(tgts)
        if not good:
            problems.append("route %s incomplete" % r.get("name"))
        print("  %-16s %s -> %s  %s"
              % (r.get("name"), r.get("source"), ", ".join(tgts) or "(none)",
                 "ok" if good else "INCOMPLETE"))
    print("\nstorage:")
    rc, out, _ = sh(["bash", "-c",
                     "du -sh %s 2>/dev/null | cut -f1" % C.MIRROR_DIR])
    print("  mirror hub    : %s (%s)" % ((out or "?").strip(), C.MIRROR_DIR))
    rc, out, _ = sh(["bash", "-c", "df -h %s | tail -1" % C.DATA_DIR])
    print("  disk          : %s" % " ".join((out or "").split()[3:5]))
    print("\nscheduler      : %s" % S.status())
    if "not scheduled" in S.status() and C.setting(cfg, "schedule") != "off":
        problems.append("scheduler not installed (run: gitsync install)")
    recs = SY.last_records(1)
    if recs:
        r = recs[-1]
        print("last run       : %s | %d ok, %d failed"
              % (r.get("finished_at"), r.get("ok", 0), r.get("failed", 0)))
        if r.get("failed"):
            problems.append("last run had %d failures" % r["failed"])
    print("\n" + ("ALL HEALTHY" if not problems
                  else "ISSUES: " + "; ".join(problems)))
    return 0 if not problems else 1


def cmd_uninstall(args):
    if not args.yes:
        ans = input("remove gitsync scheduler + symlink? (y/N) ").strip().lower()
        if not ans.startswith("y"):
            print("cancelled")
            return 1
    if S.systemd_installed():
        S.uninstall_systemd()
        print("systemd timer removed")
    S.install_cron("off")
    for p in ("/usr/local/bin/gitsync", "/usr/bin/gitsync"):
        if os.path.exists(p):
            os.remove(p)
            print("removed %s" % p)
    print("config and mirrors kept: %s , %s" % (C.CONFIG_DIR, C.DATA_DIR))
    print("to remove those too: rm -rf %s %s" % (C.CONFIG_DIR, C.DATA_DIR))
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

    sp = sub.add_parser("routes", help="list / add / remove sync routes")
    sp.add_argument("action", nargs="?",
                    choices=["list", "add", "remove"], default="list")
    sp.add_argument("rest", nargs="*",
                    help="add: NAME SOURCE t1,t2 | remove: NAME")
    sp.set_defaults(func=cmd_routes)

    sp = sub.add_parser("compare", help="drift check: HEADs across platforms")
    sp.add_argument("--route", help="only this route")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("mirror", help="sync a single repo now")
    sp.add_argument("repo")
    sp.add_argument("--route", help="use this route")
    sp.add_argument("--url", help="explicit clone URL (skip source lookup)")
    sp.add_argument("--private", action="store_true")
    sp.set_defaults(func=cmd_mirror)

    sp = sub.add_parser("doctor", help="health check: everything")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("uninstall", help="remove scheduler + symlink")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_uninstall)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        # no subcommand: open the TUI if we have a terminal, else print help
        if sys.stdout.isatty():
            return cmd_tui(args)
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
