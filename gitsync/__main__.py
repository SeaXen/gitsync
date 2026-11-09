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
import glob
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
from . import tokencheck as TK
from . import webhooks as WH

WH_PORT = WH.DEFAULT_PORT


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
        # a mesh route derives its peers from its targets, so trimming them
        # would silently turn "sync everything" into a one-peer run (or into
        # nothing at all, when the requested target was paused)
        mesh = [r.get("name") for r in routes
                if r.get("direction") == "mesh"]
        if mesh:
            print("--to does not apply to mesh route(s): %s" % ", ".join(mesh))
            print("  a mesh always syncs all of its peers; use --route to")
            print("  pick a route, or pause a target to keep one out")
        else:
            for r in routes:
                r["targets"] = [t for t in r.get("targets", [])
                                if t == args.to]
        cfg["routes"] = routes
        if not any(r.get("targets") for r in routes):
            print("no route targets '%s'" % args.to)
            return 2
    only = [r for r in (args.repos or "").split(",") if r.strip()]
    t = SYNC.run_sync(cfg, route_name=args.route, quiet=args.quiet,
                      only=only or None, dry_run=args.dry_run,
                      scheduled=args.scheduled)
    if args.dry_run:
        n_upd = sum(1 for p in t.get("planned", [])
                    if p.get("verdict") == "update")
        print("\n%d of %d repos would change (nothing was modified)"
              % (n_upd, t.get("repos", 0)))
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
        tag = ""
        if r.get("direction") == "mesh":
            tag += " [mesh]"
        if r.get("enabled") is False:
            tag += " [PAUSED]"
        paused = set(r.get("paused_targets") or [])
        targets = [t + ("*" if t in paused else "")
                   for t in r.get("targets", [])]
        print("  %-10s%s %s -> %s" % (r.get("name"), tag, r.get("source"),
                                      ", ".join(targets)))
        if paused:
            print("             paused: %s" % ", ".join(sorted(paused)))
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
        # a two-argument form with a comma list is a mesh: a one-way route
        # without targets would do nothing, so it can only mean peers
        mesh = bool(getattr(args, "mesh", False)) or (
            len(args.rest) == 2 and "," in args.rest[1])
        if mesh and len(args.rest) == 2:
            # mesh form: NAME peer1,peer2,peer3
            peers = [t.strip() for t in args.rest[1].split(",") if t.strip()]
            if len(peers) < 2:
                print("mesh needs at least two peers: "
                      "gitsync routes add all --mesh github,gitlab,forgejo")
                return 2
            name, source, targets = args.rest[0], peers[0], peers[1:]
        elif len(args.rest) >= 3:
            name, source = args.rest[0], args.rest[1]
            targets = [t.strip() for t in args.rest[2].split(",") if t.strip()]
        else:
            print("usage: gitsync routes add NAME SOURCE t1,t2")
            print("       gitsync routes add NAME --mesh p1,p2,p3")
            print("   e.g. gitsync routes add gl-to-fj gitlab forgejo")
            print("        gitsync routes add all --mesh "
                  "github,gitlab,forgejo,backup")
            return 2
        if any(r.get("name") == name for r in cfg.get("routes", [])):
            print("route '%s' already exists" % name)
            return 2
        route = {"name": name, "source": source, "targets": targets}
        if mesh:
            route["direction"] = "mesh"
        if args.schedule:
            if args.schedule not in C.SCHEDULES:
                print("unknown schedule '%s' (choose from: %s)"
                      % (args.schedule, ", ".join(C.SCHEDULES)))
                return 2
            route["schedule"] = args.schedule
        if args.include:
            route["include"] = [p.strip() for p in args.include.split(",")
                                if p.strip()]
        if args.exclude:
            route["exclude"] = [p.strip() for p in args.exclude.split(",")
                                if p.strip()]
        cfg.setdefault("routes", []).append(route)
        C.save_config(cfg)
        if mesh:
            print("mesh route added: %s (%s)"
                  % (name, ", ".join([source] + targets)))
            print("  every peer is both source and target; the newest commit "
                  "wins per branch")
        else:
            print("route added: %s (%s -> %s)"
                  % (name, source, ", ".join(targets)))
        return 0

    if action in ("pause", "resume"):
        if not args.rest:
            print("usage: gitsync routes %s NAME [TARGET[,TARGET]]" % action)
            print("   no target = the whole route")
            return 2
        name = args.rest[0]
        want = []
        for chunk in args.rest[1:]:
            want += [t.strip() for t in chunk.split(",") if t.strip()]
        route = next((r for r in cfg.get("routes", [])
                      if r.get("name") == name), None)
        if not route:
            print("no such route: %s" % name)
            return 2
        if not want:
            route["enabled"] = (action == "resume")
            if action == "resume":
                route.pop("paused_targets", None)
            C.save_config(cfg)
            print("%s: %s" % (name, "resumed (whole route)"
                              if action == "resume" else "paused (whole route)"))
            return 0
        known = set(route.get("targets") or [])
        unknown = [t for t in want if t not in known]
        if unknown:
            print("%s has no target(s): %s" % (name, ", ".join(unknown)))
            print("  targets: %s" % (", ".join(sorted(known)) or "(none)"))
            return 2
        cur = [t for t in (route.get("paused_targets") or []) if t in known]
        if action == "pause":
            cur = sorted(set(cur) | set(want))
        else:
            cur = [t for t in cur if t not in want]
        if cur:
            route["paused_targets"] = cur
        else:
            route.pop("paused_targets", None)
        C.save_config(cfg)
        print("%s: %s %s" % (name, "paused" if action == "pause" else "resumed",
                             ", ".join(sorted(want))))
        if cur:
            print("  still paused: %s" % ", ".join(sorted(cur)))
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
        key = C.route_schedule(cfg, r)
        own = "" if r.get("schedule") else " (inherited)"
        paused_t = set(r.get("paused_targets") or [])
        live = [t + ("*" if t in paused_t else "")
                for t in r.get("targets", [])]
        head = r.get("name")
        if r.get("direction") == "mesh":
            head += " [mesh]"
        if r.get("enabled") is False:
            head += " [PAUSED]"
        print("%-18s %-19s %s -> %-26s next: %s"
              % (head, key + own, r.get("source"), ", ".join(live),
                 "paused" if r.get("enabled") is False
                 else SYNC.next_due_text(r, cfg)))
        if paused_t:
            print("    paused targets: %s  (resume: gitsync routes resume "
                  "%s %s)" % (", ".join(sorted(paused_t)), r.get("name"),
                              ",".join(sorted(paused_t))))
        over = []
        if r.get("workers"):
            over.append("workers=%s" % r["workers"])
        if r.get("mode"):
            over.append("mode=%s" % r["mode"])
        if r.get("mesh_conflict"):
            over.append("mesh_conflict=%s" % r["mesh_conflict"])
        if over:
            print("    overrides: %s" % ", ".join(over))
    if not C.routes(cfg):
        print("(no routes - add one: gitsync routes add main github gitlab)")
    else:
        print("\ntimer fires: %s (the most frequent route schedule)"
              % (C.finest_schedule(cfg) or "off"))
        print("per-route: gitsync route-schedule NAME hourly|6h|12h|daily|"
              "weekly|off|inherit")
        print("pause: gitsync routes pause NAME [TARGET...]   "
              "resume: gitsync routes resume NAME [TARGET...]")
    return 0


def cmd_route_sched(args):
    """set or clear one route's own schedule"""
    cfg = C.load_config()
    for r in cfg.get("routes", []):
        if r.get("name") == args.name:
            if args.schedule == "inherit":
                r.pop("schedule", None)
                print("%s: schedule cleared (uses the global setting)"
                      % args.name)
            elif args.schedule in C.SCHEDULES:
                r["schedule"] = args.schedule
                print("%s: schedule set to %s" % (args.name, args.schedule))
            else:
                print("unknown schedule '%s' (choose from: %s)"
                      % (args.schedule, ", ".join(C.SCHEDULES)))
                return 2
            C.save_config(cfg)
            return 0
    print("no such route: %s" % args.name)
    return 2


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


def cmd_repos(args):
    """list the repos of a platform's logged account"""
    cfg = C.load_config()
    name = args.platform
    if not name:
        routes = C.routes(cfg)
        name = routes[0]["source"] if routes else None
    if not name:
        print("no platform configured - run: gitsync setup")
        return 2
    p = C.platform(cfg, name)
    if not p:
        print("no such platform: %s" % name)
        return 2
    plat = P.make(name, p)
    try:
        repos = plat.list_repos()
    except Exception as e:
        print("cannot list repos on %s: %s" % (name, e))
        return 1
    if not args.forks:
        repos = [r for r in repos if not r.get("fork")]
    if args.private_only:
        repos = [r for r in repos if r.get("private")]
    if args.public_only:
        repos = [r for r in repos if not r.get("private")]
    repos.sort(key=lambda r: r["name"].lower())
    print("repos on %s (%d):\n" % (name, len(repos)))
    for r in repos:
        vis = "private" if r.get("private") else "public "
        fork = " fork" if r.get("fork") else ""
        print("  %-34s %s%s" % (r["name"][:34], vis, fork))
    print("\nsync selected repos with:")
    print("  gitsync mirror %s ...            # one or more repo names"
          % (repos[0]["name"] if repos else "REPO"))
    print("  gitsync sync --repos \"a,b,c\"     # only these repos")
    return 0


def cmd_mirror(args):
    """sync one or more repos (from a platform, to its targets or --to)"""
    from . import sync as SY
    cfg = C.load_config()
    routes = C.routes(cfg)
    if args.route:
        routes = [r for r in routes if r.get("name") == args.route]
    if args.src:
        routes = [r for r in routes if r.get("source") == args.src]
    if not routes:
        print("no matching route - configure one with: gitsync routes add")
        return 2
    # several routes can share a source: prefer one that actually has the
    # requested target instead of blindly taking the first match
    if args.to:
        with_to = [r for r in routes if args.to in r.get("targets", [])]
        if with_to:
            routes = with_to
        else:
            print("no route from '%s' has target '%s' - routes with that source:"
                  % (args.src or routes[0].get("source"), args.to))
            for r in routes:
                print("  %s -> %s" % (r.get("name"),
                                      ", ".join(r.get("targets", []))))
            return 2
    route = routes[0]
    src_name = args.src or route["source"]
    sp = C.platform(cfg, src_name)
    if not sp or not sp.get("enabled"):
        print("source platform '%s' is not enabled" % src_name)
        return 2
    source = P.make(src_name, sp)
    tnames = [t for t in route.get("targets", [])
              if C.platform(cfg, t) and C.platform(cfg, t).get("enabled")]
    if args.to:
        if args.to not in tnames:
            print("route '%s' has no target '%s' (has: %s)"
                  % (route.get("name"), args.to, ", ".join(tnames) or "none"))
            return 2
        tnames = [args.to]
    targets = {t: P.make(t, C.platform(cfg, t)) for t in tnames}
    if not targets:
        print("no targets - add one with: gitsync platforms add")
        return 2
    # a mesh route resolves per ref (newest wins), so running the one-way
    # engine here could overwrite a newer commit living on another peer
    mesh = route.get("direction") == "mesh"
    if mesh:
        conflict = route.get("mesh_conflict") or \
            C.setting(cfg, "mesh_conflict", "newest")
        peers = {n: P.make(n, C.platform(cfg, n))
                 for n in SY.route_peers(cfg, route)}
        if args.to:
            keep = {route.get("source"), args.to}
            peers = {n: p for n, p in peers.items() if n in keep}
    names = [r for r in args.repos if r.strip()]
    try:
        available = source.list_repos()
    except Exception as e:
        print("cannot list repos on %s: %s" % (src_name, e))
        return 1
    by_name = {r["name"].lower(): r for r in available}
    ok = failed = 0
    for name in names:
        if args.url:
            repo = {"name": name, "private": args.private,
                    "clone_url": args.url}
        elif name.lower() in by_name:
            repo = by_name[name.lower()]
        else:
            print("%s: not found on %s" % (name, src_name))
            failed += 1
            continue
        if mesh:
            status, detail, errors = SY.mesh_sync_repo(repo, peers, conflict)
        else:
            status, detail, errors = SY.sync_repo(
                repo, source, targets,
                route.get("mode") or C.setting(cfg, "mode", "full"))
        print("%s: %s %s" % (name, status, detail))
        if status in ("ok", "partial"):
            ok += 1
        else:
            failed += 1
    print("\n%d ok, %d failed" % (ok, failed))
    return 0 if failed == 0 else 1


def sh(cmd, timeout=30, stdin_text=None, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=stdin_text, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def cmd_disk(args):
    """mirror-hub disk usage, orphan detection and safe space reclaim"""
    from . import diskutil as D
    cfg = C.load_config()

    # ---- orphan detection (mirrors whose repo is gone upstream) ----
    src_name = args.route_source
    if not src_name:
        routes = C.routes(cfg)
        src_name = routes[0].get("source") if routes else None
    orphans = []
    if src_name:
        sp = C.platform(cfg, src_name)
        if sp and sp.get("enabled"):
            try:
                live = [r["name"] for r in P.make(src_name, sp).list_repos()]
                orphans = D.orphans(live)
            except Exception as e:
                print("cannot list %s to check for orphans: %s"
                      % (src_name, e))

    ms = D.mirrors()
    total = sum(m[2] for m in ms)
    free, fstot = D.fs_free(C.MIRROR_DIR)

    print("mirror hub: %s" % C.MIRROR_DIR)
    print("  mirrors      : %d" % len(ms))
    print("  total size   : %s" % D.human(total))
    if free:
        print("  filesystem   : %s free of %s" % (D.human(free),
                                                  D.human(fstot)))
    print("  orphans      : %d (%s)"
          % (len(orphans),
             D.human(sum(o[2] for o in orphans)) if orphans else "nothing to reclaim"))

    for name, path in D.local_platform_paths(cfg):
        if os.path.realpath(path) == os.path.realpath(C.MIRROR_DIR):
            continue
        if not os.path.isdir(path):
            continue
        sz = D.dir_size(path)
        n = len([e for e in os.listdir(path) if e.endswith(".git")])
        print("\nlocal target '%s': %s" % (name, path))
        print("  mirrors      : %d" % n)
        print("  total size   : %s" % D.human(sz))

    if args.orphans:
        print("\norphaned mirrors (repo gone from %s):" % src_name)
        if not orphans:
            print("  none — hub matches the source")
        for name, path, size in orphans:
            print("  %-30s %8s" % (name, D.human(size)))
        if orphans and args.prune_orphans:
            print("\nremoving %d orphaned mirror(s)..." % len(orphans))
            ok = 0
            for name, path, size in orphans:
                if D.delete_mirror(path):
                    print("  removed %-24s %s" % (name, D.human(size)))
                    ok += 1
                else:
                    print("  FAILED  %s" % name)
            print("\n%d removed, %s reclaimed" % (ok, D.human(
                sum(o[2] for o in orphans[:ok]))))
        elif orphans:
            print("\nre-run with --orphans --prune-orphans to delete them")

    if args.top:
        print("\nlargest mirrors:")
        for name, path, size, mt in ms[:args.top]:
            age = (time.time() - mt) / 86400
            print("  %-30s %8s  last update %5.1f d ago"
                  % (name, D.human(size), age))

    if args.gc:
        older = args.older_than
        todo = ms
        if older:
            cutoff = time.time() - older * 86400
            todo = [m for m in ms if m[3] < cutoff]
        if not todo:
            print("\nnothing to gc (no mirror older than %s d)" % older)
            return 0
        if args.dry_run:
            print("\ndry run: would gc %d mirror(s)%s"
                  % (len(todo), " (idle > %s d)" % older if older else ""))
            return 0
        print("\ngc-ing %d mirror(s)%s — this can take a while..."
              % (len(todo), " (idle > %s d)" % older if older else ""))
        reclaimed = 0
        for i, (name, path, size, mt) in enumerate(todo, 1):
            got = D.gc_mirror(path, aggressive=args.aggressive)
            reclaimed += got
            print("  [%d/%d] %-28s %s -> %s"
                  % (i, len(todo), name, D.human(size),
                     D.human(max(0, size - got))))
        print("\nreclaimed %s" % D.human(reclaimed))
    return 0


def cmd_webhooks(args):
    """run / install / configure the webhook receiver"""
    from . import webhooks as WH

    if args.secret:
        if args.rotate:
            val = WH.rotate_secret()
            print("new webhook secret generated (%d chars):" % len(val))
            print("  %s" % val)
            print("\nstored in %s (mode 600)" % WH._secret_path())
            print("put this same value in each platform's webhook settings.")
            return 0
        if args.show:
            s = WH.get_secret()
            if not s:
                print("no secret set — generate one with:")
                print("  gitsync webhooks --secret --rotate")
                return 2
            print(s)
            return 0
        print("use --rotate to generate a secret, or --show to print it")
        return 2

    if args.uninstall:
        ok = S.uninstall_webhooks()
        print("webhook service removed" if ok else
              "webhook service not installed")
        return 0

    if args.install:
        ok = S.install_webhooks(port=args.port, bind=args.bind)
        if not ok:
            print("install failed (needs root + systemd)")
            return 1
        print("webhook receiver installed and started")
        print("  port : %d" % args.port)
        print("  bind : %s" % args.bind)
        print("  log  : journalctl -u gitsync-webhooks -f")
        print("\nconfigure each platform to POST to:")
        print("  http://<this-box>:%d/hook/<platform-name>" % args.port)
        print("\nset the shared secret first if you have not:")
        print("  gitsync webhooks --secret --rotate")
        return 0

    if args.status:
        rc, out, _ = sh(["systemctl", "is-active", "gitsync-webhooks"],
                        timeout=20)
        active = (out or "").strip()
        print("webhook service : %s" % (active or "not installed"))
        if active == "active":
            rc2, out2, _ = sh(["systemctl", "status", "gitsync-webhooks",
                               "--no-pager", "-n", "0"], timeout=20)
            for line in (out2 or "").splitlines():
                if "Main PID" in line or "Active:" in line:
                    print("  " + line.strip())
        print("secret          : %s"
              % ("set" if WH.get_secret() else "NOT SET"))
        print("port            : %d" % args.port)
        cfg = C.load_config()
        for name, p in (cfg.get("platforms") or {}).items():
            if p.get("enabled") and p.get("type") in WH.AUTH_STYLE:
                print("  endpoint       : /hook/%s (%s)"
                      % (name, p.get("type")))
        return 0

    return WH.serve(port=args.port, bind=args.bind, allow=args.allow)


def cmd_tokens(args):
    """check every platform's credential for expiry / revocation"""
    cfg = C.load_config()
    res = TK.check(cfg)
    if not res:
        print("no platforms configured")
        return 2
    print("token health\n")
    bad = 0
    for t in res:
        mark = {"ok": "  ok  ", "warning": " WARN ",
                "critical": " CRIT ", "expired": " DEAD ",
                "unknown": "  ?   "}.get(t["level"], "  ?   ")
        print("  %s %-14s %-9s %s" % (mark, t["platform"], t["type"],
                                     t.get("note", "")))
        if t["level"] in ("expired", "critical", "warning"):
            bad += 1
    print("\n%s" % TK.summary(res))
    if bad:
        print("\nGitHub note: expiry is only visible when a token is set.")
        print("  gh-CLI auth (the default) does not expose it — set one with:")
        print("  gitsync platforms edit github --token ghp_...")
    return 1 if bad else 0


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
    print("\ntokens:")
    tok = TK.check(cfg)
    if not tok:
        print("  (none)")
    for t in tok:
        mark = {"ok": "  ok ", "warning": "WARN", "critical": "CRIT",
                "expired": "DEAD", "unknown": "  ? "}.get(t["level"], "  ? ")
        print("  %-14s %-5s %s" % (t["platform"], mark, t.get("note", "")))
        if t["level"] in ("expired", "critical"):
            problems.append("token %s: %s" % (t["platform"],
                                              t.get("note", "problem")))
        elif t["level"] == "warning":
            problems.append("token %s expiring soon" % t["platform"])
    print("\nroutes:")
    if not C.routes(cfg):
        problems.append("no routes")
        print("  (none)")
    for r in C.routes(cfg):
        paused_t = set(r.get("paused_targets") or [])
        srcp = C.platform(cfg, r.get("source"))
        src_ok = bool(srcp) and srcp.get("enabled")
        tgts = [t for t in r.get("targets", [])
                if C.platform(cfg, t) and C.platform(cfg, t).get("enabled")]
        live = [t for t in tgts if t not in paused_t]
        if r.get("enabled") is False:
            state = "PAUSED"
        elif r.get("direction") == "mesh":
            state = "mesh (%d peers)" % len(live)
        else:
            state = "ok" if (src_ok and live) else "INCOMPLETE"
            if not (src_ok and live):
                problems.append("route %s incomplete" % r.get("name"))
        shown = ", ".join(t + ("*" if t in paused_t else "") for t in tgts)
        print("  %-16s %s -> %s  %s"
              % (r.get("name"), r.get("source"), shown or "(none)", state))
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


def cmd_ci(args):
    """run the full check suite locally (same checks as the CI workflows)"""
    import py_compile
    import tempfile

    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(app_dir)
    checks = []

    def check(name, fn):
        try:
            fn()
            checks.append((name, True, ""))
        except Exception as e:
            checks.append((name, False, str(e)[:200]))

    def c_syntax():
        for f in sorted(glob.glob(os.path.join(app_dir, "gitsync", "*.py"))):
            py_compile.compile(f, doraise=True)

    def c_shell():
        for s in ("install.sh", "quickinstall.sh"):
            p = os.path.join(repo_root, s)
            if os.path.exists(p):
                rc, _, err = sh(["bash", "-n", p])
                if rc != 0:
                    raise RuntimeError("%s: %s" % (s, err[:120]))

    def c_cli():
        env = dict(os.environ, PYTHONPATH=app_dir)
        for argv in (["--version"], ["--help"], ["scopes"],
                     ["platforms", "--help"], ["routes", "--help"],
                     ["doctor", "--help"]):
            rc, out, err = subprocess.run(
                ["python3", "-m", "gitsync"] + argv, capture_output=True,
                text=True, env=env, timeout=60).returncode, "", ""
            if rc != 0:
                raise RuntimeError("gitsync %s failed" % " ".join(argv))
        rc, out, _ = subprocess.run(
            ["python3", "-m", "gitsync", "scopes"], capture_output=True,
            text=True, env=env, timeout=60).returncode if False else (0, "", ""), None, None

    def c_units():
        env = dict(os.environ, PYTHONPATH=app_dir)
        code = UNIT_CHECKS
        rc, out, err = subprocess.run(["python3", "-c", code],
                                      capture_output=True, text=True,
                                      env=env, timeout=60).returncode, "", ""
        if rc != 0:
            raise RuntimeError((err or out).strip().splitlines()[-1]
                               if (err or out) else "unit checks failed")

    check("python syntax (gitsync/*.py)", c_syntax)
    check("shell syntax (installers)", c_shell)
    check("CLI smoke tests", c_cli)
    check("unit checks (99 assertions)", c_units)

    width = max(len(n) for n, _, _ in checks)
    failed = 0
    for name, ok, err in checks:
        print("  %-*s  %s" % (width, name, "PASS" if ok else "FAIL"))
        if not ok:
            failed += 1
            print("      %s" % err)
    print()
    if failed:
        print("CI: %d of %d checks FAILED" % (failed, len(checks)))
        return 1
    print("CI: all %d checks passed" % len(checks))
    return 0


UNIT_CHECKS = """
from gitsync import platforms as P
from gitsync import config as C
from gitsync import notify as NT
from gitsync import sync as SY
from gitsync import tui

assert P.ssh_url('https://github.com', 'me', 'repo') == 'git@github.com:me/repo.git'
assert P.ssh_url('git.example.com', 'me', 'repo', 2222) == 'ssh://git@git.example.com:2222/me/repo.git'
assert P.ssh_url('https://codeberg.org', 'me', 'repo') == 'git@codeberg.org:me/repo.git'
assert P._norm('My-Repo.Name') == 'myreponame'
assert P._fs_safe('a b/c') == 'a_b_c'
assert set(C.SCHEDULES) == {'hourly', '6h', '12h', 'daily', 'weekly', 'off'}
assert C.SCHEDULES['hourly']['cron'] == '0 * * * *'
assert C.SCHEDULES['daily']['calendar'] == '*-*-* 03:00:00'
cfg = {'settings': {'notify': {}}}
assert NT.enabled(cfg) is False
assert NT.send(cfg, 't', [], color='info') is True
assert isinstance(P.make('gh', {'type': 'github', 'url': 'https://github.com'}), P.GitHubPlatform)
p = P.make('fj', {'type': 'forgejo', 'url': 'http://localhost:3002', 'protocol': 'ssh', 'ssh_port': 2222, 'owner': 'me'})
assert p._push_url('me', 'repo') == 'ssh://git@localhost:2222/me/repo.git'
repos = [{'name': 'SeaVault'}, {'name': 'tradex'}, {'name': 'Sea-Wallpaper'}]
assert [r['name'] for r in SY._filter_repos(repos, {'include': ['Sea*']})] == ['SeaVault', 'Sea-Wallpaper']
assert [r['name'] for r in SY._filter_repos(repos, {'exclude': ['trade*']})] == ['SeaVault', 'Sea-Wallpaper']
# selective sync: 'only' is an exact, case-insensitive name list
only = SY._filter_repos(repos, {}, ['SEAVAULT', ' tradex '])
assert [r['name'] for r in only] == ['SeaVault', 'tradex'], only
# 'only' narrows whatever the route globs already kept
both = SY._filter_repos(repos, {'include': ['Sea*']}, ['SeaVault', 'tradex'])
assert [r['name'] for r in both] == ['SeaVault'], both
# a name that isn't there is dropped, an empty list filters nothing
assert SY._filter_repos(repos, {}, ['nope']) == []
assert len(SY._filter_repos(repos, {}, [])) == 3
assert len(SY._filter_repos(repos, {}, None)) == 3
spark = tui._sparkline([0, 5, 10])
assert len(spark) == 3 and spark[0] == ' ' and spark[-1] == '@'

# ---- dry-run planner: repo_url must not create anything ----
from gitsync import diskutil as D
from gitsync import tokencheck as TK
from gitsync import webhooks as WH

assert D.human(0) == '0B'
assert D.human(1023) == '1023B'
assert D.human(1024) == '1.0K'
assert D.human(1536) == '1.5K'
assert D.human(1024 ** 3) == '1.0G'

# token expiry levels
assert TK.level_for(200) == 'ok'
assert TK.level_for(20) == 'warning'
assert TK.level_for(5) == 'critical'
assert TK.level_for(0) == 'critical'
assert TK.level_for(-1) == 'expired'
assert TK.level_for(None) == 'unknown'
assert TK._parse('2026-12-01') is not None
assert TK._parse('2026-12-01T10:00:00Z') is not None
assert TK._parse('2026-12-01 10:00:00 UTC') is not None
assert TK._parse('garbage') is None
assert TK.days_left(None) is None

# webhook event mapping (GitLab uses 'Push Hook', GitHub uses 'push')
assert WH._event({}, 'push') == 'push'
assert WH._event({}, 'Push Hook') == 'push'
assert WH._event({}, 'Tag Push Hook') == 'push'
assert WH._event({}, 'ping') == 'ping'
assert WH._event({}, 'Merge Request Hook') == 'other'
assert WH._event({'commits': [1]}, None) == 'push'
assert WH._event({'zen': 'x'}, None) == 'ping'
assert WH._event({}, None) == 'other'
# payload repo-name extraction
assert WH._repo_name({'repository': {'name': 'tradex'}}) == 'tradex'
assert WH._repo_name({'project': {'path': 'SeaVault'}}) == 'SeaVault'
assert WH._repo_name({}) is None

# an unset secret must never authenticate
import hmac as _hmac, hashlib as _hl, json as _js
_body = _js.dumps({'repository': {'name': 'x'}}).encode()
_sig = 'sha256=' + _hmac.new(b'k', _body, _hl.sha256).hexdigest()
ok, _ = WH._verify('hmac-sha256', '', {'X-Hub-Signature-256': _sig}, _body)
assert ok is False
ok, _ = WH._verify('hmac-sha256', 'k', {'X-Hub-Signature-256': _sig}, _body)
assert ok is True
ok, _ = WH._verify('hmac-sha256', 'k', {'X-Hub-Signature-256': 'sha256=bad'}, _body)
assert ok is False
ok, _ = WH._verify('token', 'k', {'X-Gitlab-Token': 'k'}, _body)
assert ok is True
ok, _ = WH._verify('token', 'k', {'X-Gitlab-Token': 'wrong'}, _body)
assert ok is False

# per-route schedule resolution + finest
assert C.route_schedule(cfg, {}) == 'hourly'
assert C.route_schedule(cfg, {'schedule': 'daily'}) == 'daily'
assert C.SCHEDULE_HOURS['hourly'] == 1
assert C.SCHEDULE_HOURS['weekly'] == 168
_c2 = {'routes': [{'name': 'a', 'schedule': 'weekly'},
                  {'name': 'b', 'schedule': '6h'},
                  {'name': 'c', 'schedule': 'daily'}]}
assert C.finest_schedule(_c2) == '6h'
_c3 = {'routes': [{'name': 'a', 'schedule': 'off'}]}
assert C.finest_schedule(_c3) is None

    # gitlab project-path sanitiser (GitLab rejects a leading _/-/.)
assert P.gitlab_path('_gstest_src') == 'gstest_src'
assert P.gitlab_path('-leading') == 'leading'
assert P.gitlab_path('my repo') == 'my-repo'
assert P.gitlab_path('ok-name') == 'ok-name'
assert P.gitlab_path('trailing.git') == 'trailing'
assert P.gitlab_path('...') == 'repo'
# readable API errors instead of a raw dict
assert P._api_error({'message': {'path': ['bad']}}) == 'path: bad'
assert P._api_error({'message': 'nope'}) == 'nope'
assert P._api_error({'message': ['a', 'b']}) == 'a; b'
assert P._api_error('plain') == 'plain'
# protected-branch detection + concise git errors
assert SY._protection_error('remote: You are not allowed to force push code to a protected branch') is True
assert SY._protection_error('some other failure') is False
assert SY._protection_error('') is False
assert SY._short_error('remote: nope\\nTo example') == 'nope'
assert SY._short_error('hint: x\\nremote: real reason') == 'real reason'
assert SY._short_error('') == ''
# every adapter exposes the prepare_push hook
for _cls in (P.GitHubPlatform, P.GitLabPlatform, P.ForgejoPlatform,
             P.LocalPlatform):
    assert hasattr(_cls, 'prepare_push')
# repo_url must format every URL part (regression: one % arg too many)
_gp = P.GitLabPlatform('gl', {'url': 'https://gitlab.com',
                      'owner': 'me', 'token': 'tok'})
assert _gp.repo_url({'name': '_weird name'}) == 'https://oauth2:tok@gitlab.com/me/weird-name.git'


# mesh: newest commit wins; ties keep the earlier peer
assert SY._newest([(10, 'a'), (20, 'b'), (20, 'c')]) == (20, 'b')
assert SY._newest([(7, 'only')]) == (7, 'only')
# paused targets are skipped, and a mesh treats the source as a peer
_cfg = {'platforms': {'a': {'type': 'github', 'enabled': True},
                      'b': {'type': 'gitlab', 'enabled': True},
                      'c': {'type': 'local', 'enabled': True}}}
_r = {'name': 'r', 'source': 'a', 'targets': ['b', 'c'],
      'paused_targets': ['c']}
assert set(SY.active_targets(_cfg, _r)) == {'b'}
assert set(SY.route_peers(_cfg, _r)) == {'a', 'b'}
_cfg['platforms']['b']['enabled'] = False
assert SY.active_targets(_cfg, _r) == {}
assert set(SY.route_peers(_cfg, _r)) == {'a'}
# CLI error text: last meaningful line only
assert P._short_text('line one\\n\\nthe real reason') == 'the real reason'

# ---- mesh conflict policy: "skip" refuses to overwrite a disagreement ----
_c = {'branch': [(10, 'sha_old'), (20, 'sha_new')]}
assert SY._choose(_c, 'newest') == ({'branch': (20, 'sha_new')}, [])
assert SY._choose(_c, 'skip') == ({}, ['branch'])
# every peer agrees -> nothing is skipped, even under "skip"
_ok = {'branch': [(10, 'same'), (30, 'same')]}
assert SY._choose(_ok, 'skip') == ({'branch': (30, 'same')}, [])
assert SY._choose({'b': [(1, 'x')]}, 'newest')[0]['b'] == (1, 'x')
# last_route_run() reads the per-route timestamp file
assert SY.last_route_run('no-such-route') is None
# TUI progress parsing: "[12/54] OK   repo -> targets" -> (12, 54, repo)
tui.state["sync_lines"] = ["=== route 'x' ===", "  github: 3 repos",
                           "  [1/3] OK   nine-router -> a,b"]
assert tui.sync_progress() == (1, 3, 'nine-router')
tui.state["sync_lines"].append("  [2/3] OK   gitsync -> a,b")
assert tui.sync_progress() == (2, 3, 'gitsync')
assert tui.sync_progress.__doc__
# merged-ref counter for the end-of-sync flash
assert tui._merged_refs(["  x (3 refs merged)", "  y (1 refs merged)"]) == 4
assert tui._merged_refs(["  x"]) == 0
assert tui._merged_refs([]) == 0
# tab bar hit-testing: the same arithmetic draw() uses
assert tui._tab_at(0, 120) is None
assert tui._tab_at(28, 120) == 0
assert tui._tab_at(57, 120) == 2
assert tui._tab_at(200, 120) is None
# modal footer prefers a fresh flash over the static hint
tui.state['message'] = ('paused: backup',
                       __import__('time').time() + 5)
assert tui.state['message'][0] == 'paused: backup'
tui.state['message'] = ('x' * 0, 0)

print('all unit checks passed')
"""


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
    sp.add_argument("--repos", help="comma-separated repo names to sync")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what would change, change nothing")
    sp.add_argument("--scheduled", action="store_true",
                    help="skip routes whose own schedule is not due yet")
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
                    choices=["list", "add", "remove", "pause", "resume"],
                    default="list")
    sp.add_argument("rest", nargs="*",
                    help="add: NAME SOURCE t1,t2 | remove: NAME | "
                         "pause/resume: NAME [TARGET...]")
    sp.add_argument("--include", help="comma-separated globs to include")
    sp.add_argument("--exclude", help="comma-separated globs to skip")
    sp.add_argument("--mesh", action="store_true",
                    help="all-direction route: NAME --mesh p1,p2,p3")
    sp.add_argument("--schedule", choices=sorted(C.SCHEDULES),
                    help="this route's own sync frequency")
    sp.set_defaults(func=cmd_routes)

    sp = sub.add_parser("route-schedule",
                        help="set one route's sync frequency")
    sp.add_argument("name", help="route name")
    sp.add_argument("schedule",
                    help="hourly|6h|12h|daily|weekly|off|inherit")
    sp.set_defaults(func=cmd_route_sched)

    sp = sub.add_parser("compare", help="drift check: HEADs across platforms")
    sp.add_argument("--route", help="only this route")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("mirror", help="sync one or more repos now")
    sp.add_argument("repos", nargs="+", help="repo names")
    sp.add_argument("--route", help="use this route")
    sp.add_argument("--from", dest="src", help="source platform")
    sp.add_argument("--to", help="only this target platform")
    sp.add_argument("--url", help="explicit clone URL (single repo)")
    sp.add_argument("--private", action="store_true")
    sp.set_defaults(func=cmd_mirror)

    sp = sub.add_parser("repos", help="list the repos of a platform")
    sp.add_argument("--platform", help="platform name (default: route source)")
    sp.add_argument("--forks", action="store_true", help="include forks")
    sp.add_argument("--private-only", action="store_true")
    sp.add_argument("--public-only", action="store_true")
    sp.set_defaults(func=cmd_repos)

    sp = sub.add_parser("disk", help="hub disk usage, orphans, reclaim")
    sp.add_argument("--top", type=int, default=0, metavar="N",
                    help="list the N largest mirrors")
    sp.add_argument("--gc", action="store_true",
                    help="reflog-expire + gc mirrors to reclaim space")
    sp.add_argument("--aggressive", action="store_true",
                    help="slower, smaller result (repacks everything)")
    sp.add_argument("--older-than", type=int, default=0, metavar="DAYS",
                    help="only gc mirrors idle longer than this")
    sp.add_argument("--dry-run", action="store_true",
                    help="with --gc: report, change nothing")
    sp.add_argument("--orphans", action="store_true",
                    help="list hub mirrors whose repo is gone upstream")
    sp.add_argument("--prune-orphans", action="store_true",
                    help="with --orphans: delete those mirrors")
    sp.add_argument("--route-source", help="platform to compare orphans against")
    sp.set_defaults(func=cmd_disk)

    sp = sub.add_parser("webhooks", help="receive push events, sync instantly")
    sp.add_argument("--serve", action="store_true",
                    help="run the receiver in the foreground (default)")
    sp.add_argument("--install", action="store_true",
                    help="install + start a systemd service")
    sp.add_argument("--uninstall", action="store_true",
                    help="remove the systemd service")
    sp.add_argument("--status", action="store_true",
                    help="is the receiver running?")
    sp.add_argument("--port", type=int, default=WH_PORT,
                    help="listen port (default %d)" % WH_PORT)
    sp.add_argument("--bind", default="127.0.0.1",
                    help="bind address (default 127.0.0.1)")
    sp.add_argument("--allow",
                    help="comma-separated source IPs to accept")
    sp.add_argument("--secret", action="store_true",
                    help="manage the shared secret")
    sp.add_argument("--rotate", action="store_true",
                    help="generate a new shared secret")
    sp.add_argument("--show", action="store_true",
                    help="print the current shared secret")
    sp.set_defaults(func=cmd_webhooks)

    sp = sub.add_parser("tokens", help="check credentials for expiry")
    sp.set_defaults(func=cmd_tokens)

    sp = sub.add_parser("doctor", help="health check: everything")
    sp.add_argument("--fix", action="store_true",
                    help="auto-repair: remove broken mirror shells")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("update", help="update gitsync (keeps your config)")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("ci", help="run all checks locally (no provider needed)")
    sp.add_argument("--install-hook", action="store_true",
                    help="install a pre-push hook in this repo")
    sp.set_defaults(func=cmd_ci)

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
