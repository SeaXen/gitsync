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
import subprocess
import sys
import time

from . import __version__
from . import config as C
from . import permissions as PERM
from . import platforms as P
from . import scheduler as S
from . import notify as NT
from datetime import datetime
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
    if getattr(args, "to", None):
        # restrict every route's targets to the requested platform
        routes = C.routes(cfg)
        if args.route:
            routes = [r for r in routes if r.get("name") == args.route]
        for r in routes:
            r["targets"] = [t for t in r.get("targets", []) if t == args.to]
        cfg["routes"] = routes
        if not any(r.get("targets") for r in routes):
            print("no route targets '%s'" % args.to)
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
    """list / add / remove / edit / test platforms (accounts)"""
    cfg = C.load_config()
    plats = cfg.setdefault("platforms", {})

    if args.action == "add":
        name = args.name
        if name in plats:
            print("platform '%s' already exists (edit it instead)" % name)
            return 2
        p = {"type": args.type, "enabled": True}
        if args.type == "local":
            if not args.path:
                print("local platforms need --path")
                return 2
            p["path"] = os.path.expanduser(args.path)
        else:
            p["url"] = args.url or {
                "github": "https://github.com",
                "gitlab": "https://gitlab.com"}.get(args.type,
                                                   "http://localhost:3000")
            if args.token:
                p["token"] = args.token
            if args.owner:
                p["owner"] = args.owner
            if args.type == "github" and not args.token:
                p["auth"] = "gh"          # fall back to the gh CLI
        plats[name] = p
        C.save_config(cfg)
        print("added platform: %s (%s)" % (name, args.type))
        return _test_one(name, p)

    if args.action == "remove":
        if args.name not in plats:
            print("no such platform: %s" % args.name)
            return 2
        used = [r["name"] for r in C.routes(cfg)
                if r.get("source") == args.name
                or args.name in r.get("targets", [])]
        if used and not args.force:
            print("platform '%s' is used by route(s): %s"
                  % (args.name, ", ".join(used)))
            print("re-run with --force to remove anyway")
            return 2
        del plats[args.name]
        C.save_config(cfg)
        print("removed platform: %s" % args.name)
        return 0

    if args.action == "edit":
        if args.name not in plats:
            print("no such platform: %s" % args.name)
            return 2
        p = plats[args.name]
        changed = []
        for field, val in (("token", args.token), ("url", args.url),
                           ("owner", args.owner), ("path", args.path)):
            if val is not None:
                p[field] = os.path.expanduser(val) if field == "path" else val
                changed.append(field)
        if args.enable:
            p["enabled"] = True
            changed.append("enabled")
        if args.disable:
            p["enabled"] = False
            changed.append("disabled")
        if not changed:
            print("nothing to change (use --token/--url/--owner/--path/"
                  "--enable/--disable)")
            return 2
        C.save_config(cfg)
        print("updated %s: %s" % (args.name, ", ".join(changed)))
        return _test_one(args.name, p)

    if args.action == "test":
        if args.name:
            return _test_one(args.name, plats.get(args.name, {}))
        bad = 0
        for name, p in plats.items():
            if _test_one(name, p) != 0:
                bad += 1
        return 1 if bad else 0

    # default: list
    for name, p in plats.items():
        try:
            ok, info = P.make(name, p).test()
        except Exception as e:
            ok, info = False, str(e)[:80]
        print("%-14s %-9s %-4s %-5s %s"
              % (name, p.get("type"), "ON" if p.get("enabled") else "off",
                 "OK" if ok else "FAIL", info))
    if not plats:
        print("(no platforms - add one: gitsync platforms add gitlab "
              "--type gitlab --token glpat-...)")
    return 0


def _test_one(name, p):
    if not p:
        print("no such platform: %s" % name)
        return 2
    try:
        ok, info = P.make(name, p).test()
    except Exception as e:
        ok, info = False, str(e)[:80]
    print("%-14s %-5s %s" % (name, "OK" if ok else "FAIL", info))
    return 0 if ok else 1


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
        route = {"name": name, "source": source, "targets": targets}
        if args.include:
            route["include"] = [p.strip() for p in args.include.split(",")
                                if p.strip()]
        if args.exclude:
            route["exclude"] = [p.strip() for p in args.exclude.split(",")
                                if p.strip()]
        cfg.setdefault("routes", []).append(route)
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


def sh(cmd, timeout=30, stdin_text=None, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=stdin_text, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


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
    try:
        n_mirrors = len([d for d in os.listdir(C.MIRROR_DIR)
                         if d.endswith(".git")])
    except OSError:
        n_mirrors = 0
    print("  mirror hub    : %d mirrors (%s)" % (n_mirrors, C.MIRROR_DIR))
    rc, out, _ = sh(["bash", "-c", "df -h %s | tail -1" % C.DATA_DIR],
                    timeout=30)
    parts = (out or "").split()
    if len(parts) > 4:
        print("  disk          : %s free of %s (%s used)"
              % (parts[3], parts[1], parts[4]))
    else:
        print("  disk          : (df unavailable)")
    print("\nscheduler      : %s" % S.status())
    if "not scheduled" in S.status() and C.setting(cfg, "schedule") != "off":
        problems.append("scheduler not installed (run: gitsync install)")
    recs = SYNC.last_records(1)
    if recs:
        r = recs[-1]
        print("last run       : %s | %d ok, %d failed"
              % (r.get("finished_at"), r.get("ok", 0), r.get("failed", 0)))
        if r.get("failed"):
            problems.append("last run had %d failures" % r["failed"])
    if getattr(args, "fix", False):
        fixed = _doctor_fix()
        if fixed:
            print("\nauto-repaired %d item(s):" % len(fixed))
            for f in fixed:
                print("  - %s" % f)
            print("run 'gitsync sync' to re-clone them")
        else:
            print("\nnothing to auto-repair")

    print("\n" + ("ALL HEALTHY" if not problems
                  else "ISSUES: " + "; ".join(problems)))
    return 0 if not problems else 1


def _doctor_fix():
    """remove broken mirror shells so the next sync re-clones them"""
    import shutil
    fixed = []
    hub = C.MIRROR_DIR
    if not os.path.isdir(hub):
        return fixed
    for entry in sorted(os.listdir(hub)):
        if not entry.endswith(".git"):
            continue
        path = os.path.join(hub, entry)
        # a bare mirror with no refs is a failed clone -> remove it
        rc, out, _ = sh(["git", "for-each-ref"], cwd=path, timeout=30)
        if rc != 0 or not out.strip():
            try:
                shutil.rmtree(path)
                fixed.append("removed broken mirror: %s" % entry)
            except OSError as e:
                fixed.append("could not remove %s: %s" % (entry, e))
    # stale sync lock
    lock = os.path.join(C.DATA_DIR, "sync.lock")
    if os.path.exists(lock):
        try:
            os.remove(lock)
            fixed.append("removed stale sync lock")
        except OSError:
            pass
    return fixed


def cmd_update(args):
    """re-run the one-click installer, keeping the existing configuration"""
    cfg = C.load_config()
    sched = C.setting(cfg, "schedule", "hourly")
    home = os.environ.get("GITSYNC_HOME", "")
    candidates = [os.path.join(home, "quickinstall.sh") if home else None,
                  "/opt/gitsync/quickinstall.sh",
                  os.path.expanduser("~/.local/share/gitsync/quickinstall.sh")]
    script = next((c for c in candidates if c and os.path.exists(c)), None)
    if script is None:
        # not installed from a bundle - fetch the bootstrap directly
        import urllib.request
        url = ("https://raw.githubusercontent.com/SeaXen/gitsync/main/"
               "quickinstall.sh")
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                script = "/tmp/gitsync-quickinstall.sh"
                with open(script, "w") as f:
                    f.write(r.read().decode())
        except Exception as e:
            print("could not fetch the installer: %s" % e)
            print("update manually: git pull && bash install.sh")
            return 1
    user_mode = 1 if script.startswith(os.path.expanduser("~")) else 0
    cmd = ["bash", script, "--no-setup", "--no-sync",
           "--schedule", sched]
    if user_mode:
        cmd.append("--user")
    print("updating gitsync (schedule: %s, config kept)..." % sched)
    rc = subprocess.run(cmd).returncode
    print("updated" if rc == 0 else "update failed (rc=%d)" % rc)
    return rc


def cmd_notify(args):
    cfg = C.load_config()
    if args.test:
        ok, msg = NT.notify_test(cfg)
        print("discord notification: %s" % msg)
        return 0 if ok else 1
    url, on = NT._settings(cfg)
    print("discord webhook : %s" % ("configured" if url else "NOT configured"))
    print("notify on       : %s" % on)
    if not url:
        print("\nset it with:")
        print("  gitsync notify --set-url https://discord.com/api/webhooks/...")
    return 0


def cmd_fix(args):
    """detect drift across a route and re-sync the mismatched repos"""
    cfg = C.load_config()
    routes = C.routes(cfg)
    if args.route:
        routes = [r for r in routes if r.get("name") == args.route]
    if not routes:
        print("no routes configured")
        return 2
    fixed = failed = checked = 0
    for route in routes:
        sp = C.platform(cfg, route["source"])
        if not sp or not sp.get("enabled"):
            continue
        source = P.make(route["source"], sp)
        targets = {t: P.make(t, C.platform(cfg, t))
                   for t in route.get("targets", [])
                   if C.platform(cfg, t) and C.platform(cfg, t).get("enabled")}
        if not targets:
            continue
        try:
            repos = source.list_repos()
        except Exception as e:
            print("  cannot list source: %s" % e)
            continue
        repos = SYNC._filter_repos(repos, route)
        print("route %s: checking %d repos against %s..."
              % (route.get("name"), len(repos), ", ".join(targets)))
        mode = C.setting(cfg, "mode", "full")
        for repo in repos:
            checked += 1
            try:
                src_head = _head_of(source, repo)
                drifted = []
                for tname, tplat in targets.items():
                    thead = _head_of(tplat, repo)
                    if thead != src_head:
                        drifted.append(tname)
                if not drifted:
                    continue
                print("  fixing %-28s -> %s" % (repo["name"],
                                                ", ".join(drifted)))
                status, detail, errors = SYNC.sync_repo(repo, source, targets,
                                                        mode)
                if status in ("ok", "partial"):
                    fixed += 1
                else:
                    failed += 1
                    print("    FAILED: %s" % detail)
            except Exception as e:
                failed += 1
                print("    error: %s" % str(e)[:80])
    print("\nchecked %d repos: %d fixed, %d failed" % (checked, fixed, failed))
    if NT.enabled(cfg) and (fixed or failed):
        NT.send(cfg, "gitsync: drift repair",
                [("Checked", str(checked), True),
                 ("Fixed", str(fixed), True),
                 ("Failed", str(failed), True)],
                color="ok" if not failed else "warn")
    return 0 if failed == 0 else 1


def cmd_watch(args):
    """foreground poller: sync every N seconds (faster than the hourly timer)"""
    cfg = C.load_config()
    interval = args.interval
    print("gitsync watch: syncing every %ds — Ctrl+C to stop" % interval)
    runs = 0
    try:
        while True:
            runs += 1
            t = SYNC.run_sync(cfg)
            print("[%s] run #%d: %d ok, %d failed — next in %ds"
                  % (datetime.now().strftime("%H:%M:%S"), runs, t["ok"],
                     t["failed"], interval))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped after %d runs" % runs)
        return 0


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
    sp.add_argument("--to", help="only push to this platform")
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

    sp = sub.add_parser("platforms", help="manage accounts: add/remove/edit/test")
    sp.add_argument("action", nargs="?", default="list",
                    choices=["list", "add", "remove", "edit", "test"])
    sp.add_argument("name", nargs="?", help="platform name")
    sp.add_argument("--type",
                    choices=["github", "gitlab", "forgejo", "gitea",
                             "bitbucket", "generic", "local"])
    sp.add_argument("--url", help="instance URL")
    sp.add_argument("--token", help="API token")
    sp.add_argument("--owner", help="username / workspace / namespace")
    sp.add_argument("--path", help="local folder (type=local)")
    sp.add_argument("--enable", action="store_true")
    sp.add_argument("--disable", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_platforms)

    sp = sub.add_parser("scopes", help="show required token scopes")
    sp.set_defaults(func=cmd_scopes)

    sp = sub.add_parser("routes", help="list / add / remove sync routes")
    sp.add_argument("action", nargs="?",
                    choices=["list", "add", "remove"], default="list")
    sp.add_argument("rest", nargs="*",
                    help="add: NAME SOURCE t1,t2 | remove: NAME")
    sp.add_argument("--include", help="comma-separated globs to include")
    sp.add_argument("--exclude", help="comma-separated globs to skip")
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
    sp.add_argument("--fix", action="store_true",
                    help="auto-repair: remove broken mirror shells")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("update", help="update gitsync (keeps your config)")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("notify", help="Discord notification settings")
    sp.add_argument("--test", action="store_true", help="send a test message")
    sp.add_argument("--set-url", help="set the Discord webhook URL")
    sp.set_defaults(func=cmd_notify)

    sp = sub.add_parser("fix", help="detect drift and re-sync mismatched repos")
    sp.add_argument("--route", help="only this route")
    sp.set_defaults(func=cmd_fix)

    sp = sub.add_parser("watch", help="sync every N seconds in the foreground")
    sp.add_argument("--interval", type=int, default=60)
    sp.set_defaults(func=cmd_watch)

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
