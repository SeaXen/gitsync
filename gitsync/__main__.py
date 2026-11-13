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
import hashlib
import hmac
import json
import os
import re
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
from . import stars as ST
from . import tokencheck as TK
from . import webhooks as WH

WH_PORT = WH.DEFAULT_PORT
WH_PORT_WEB = 7765


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


def cmd_prune(args):
    """delete, on opt-in targets, repos the source does not have.

    Dry-run by default: `gitsync prune --route X` only lists. Deleting
    needs --yes, a target named in the route's prune_repos, full mode, and
    is refused from the timer.
    """
    cfg = C.load_config()
    routes = C.routes(cfg)
    if args.route:
        routes = [r for r in routes if r.get("name") == args.route]
    if not routes:
        print("no routes configured")
        return 2
    rc = 0
    for route in routes:
        name = route.get("name")
        if not C.prune_targets(cfg, route):
            print("route %s: no target is opted in — add \"prune_repos\": "
                  "[\"target\"] to the route first" % name)
            rc = 2
            continue
        if C.route_mode(cfg, route) != "full":
            print("route %s: repo deletion needs full mode (it is %s)"
                  % (name, C.route_mode(cfg, route)))
            rc = 2
            continue
        gone = SYNC.prune_repos(cfg, route)
        if not gone:
            print("route %s: nothing to delete" % name)
            continue
        n = sum(len(v) for v in gone.values())
        if not args.yes:
            print("route %s: %d repo(s) exist on a target but not on the "
                  "source:" % (name, n))
            for tname, items in sorted(gone.items()):
                for it in items:
                    print("  %s/%s" % (tname, it["name"]))
            print("\nre-run with --yes to delete them permanently.")
            continue
        deleted = failed = 0
        for tname, items in sorted(gone.items()):
            plat = P.make(tname, C.platform(cfg, tname))
            for it in items:
                try:
                    ok, err = plat.delete_repo({"name": it["name"]})
                except Exception as e:
                    ok, err = False, str(e)[:120]
                if ok:
                    deleted += 1
                    print("  deleted %s/%s" % (tname, it["name"]))
                else:
                    failed += 1
                    print("  FAILED %s/%s: %s" % (tname, it["name"], err))
        print("route %s: %d deleted, %d failed" % (name, deleted, failed))
        if failed:
            rc = 1
    return rc


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
    if getattr(args, "retry_failed", False):
        recs = SYNC.last_records(1)
        failed = []
        if recs:
            for f in recs[-1].get("failures", []):
                r = f.get("repo")
                rt = f.get("route")
                if r and r != "-":
                    if not args.route or rt == args.route:
                        failed.append(r)
        if not failed:
            print("no failed repos in last run to retry")
            return 0
        only = list(set(only + failed))
    t = SYNC.run_sync(cfg, route_name=args.route, quiet=args.quiet,
                      only=only or None, dry_run=args.dry_run,
                      scheduled=args.scheduled,
                      force=getattr(args, "force", False),
                      prune_repos=getattr(args, "prune_repos", False))
    if t.get("paused"):
        return 0
    if args.dry_run:
        n_upd = sum(1 for p in t.get("planned", [])
                    if p.get("verdict") == "update")
        n_err = sum(1 for p in t.get("planned", [])
                    if p.get("verdict") == "error")
        print("\n%d of %d repos would change (nothing was modified)"
              % (n_upd, t.get("repos", 0)))
        for p in t.get("prune_planned", []):
            print("  would delete %s/%s (not on the source)"
                  % (p["target"], p["name"]))
        print("\n%d ok, %d partial, %d failed of %d repos in %ss"
              % (t["ok"], t["partial"], t["failed"], t["repos"],
                 t["elapsed_sec"]))
        # a dry run that could not read a repo is a failure, not a preview
        return 1 if n_err else 0
    print("\n%d ok, %d partial, %d failed of %d repos in %ss"
          % (t["ok"], t["partial"], t["failed"], t["repos"],
             t["elapsed_sec"]))
    if t.get("pruned") or t.get("prune_failed"):
        print("repo deletion: %d deleted, %d failed"
              % (t.get("pruned", 0), t.get("prune_failed", 0)))
    # partial is not success: a target that refused the push, or a mesh that
    # skipped a conflict, must be visible in the exit code (the timer mails
    # on non-zero)
    return 0 if (t["failed"] == 0 and t["partial"] == 0) else 1


def cmd_status(args):
    cfg = C.load_config()
    print("gitsync %s" % __version__)
    print("config : %s" % C.CONFIG_FILE)
    print("mirrors: %s" % C.MIRROR_DIR)
    if C.setting(cfg, "paused", False):
        print("status : [PAUSED] background sync is suspended")
    print()
    table = _platforms_table(cfg)
    live = sum(1 for r in table if r[2].strip() == "ON" and r[3].strip() == "OK")
    print("platforms: %d site(s), %d connected" % (len(table), live))
    for name, ptype, en, ok, info in table:
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
    # F6: four lines of activity, no new analyzer - diskutil and the run
    # records already compute all of it.
    try:
        from . import diskutil as D
        ms = D.mirrors()
        free, tot = D.fs_free(C.MIRROR_DIR)
        print("hub      : %d mirrors, %s used%s"
              % (len(ms), D.human(sum(m[2] for m in ms)),
                 ", disk %s free" % D.human(free) if free else ""))
    except Exception:
        pass
    recs = SYNC.last_records(1)
    if recs:
        r = recs[-1]
        when = r.get("finished_at") or "?"
        clean = (not (r.get("failed") or r.get("partial"))
                 and (r.get("ok") or 0) > 0)
        if clean:
            print("last ok  : %s (%d repos, %ss)"
                  % (when, r.get("ok", 0), r.get("elapsed_sec", "?")))
        else:
            # a 0-repo run is not a success - say what actually happened
            print("last run : %s - %d ok, %d partial, %d failed of %d"
                  % (when, r.get("ok", 0), r.get("partial", 0),
                     r.get("failed", 0), r.get("repos", 0)))
    else:
        print("last ok  : never (run: gitsync sync)")
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
    if not os.path.exists(C.log_file()):
        print("no log yet")
        return 0
    n = args.lines
    with open(C.log_file(), "r", encoding="utf-8", errors="replace") as lf:
        lines = lf.read().splitlines()
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


def cmd_web(args):
    from . import web
    if getattr(args, "set_password", None):
        import getpass
        user = args.set_password
        p1 = getpass.getpass("New password for '%s': " % user)
        p2 = getpass.getpass("Confirm password: ")
        if p1 != p2:
            print("passwords do not match")
            return 2
        try:
            web.set_password(user, p1)
        except ValueError as e:
            print(e)
            return 2
        print("password set for '%s' (stored as a PBKDF2 hash, mode 0600)." % user)
        print("sign in with it at the web UI, or disable with "
              "gitsync web --disable-password.")
        return 0
    if getattr(args, "disable_password", False):
        web.clear_password()
        print("password sign-in disabled (access-token sign-in still works).")
        return 0
    cfg = C.load_config()
    if getattr(args, "install", False):
        # the always-on service is the "share it on the LAN" path -> 0.0.0.0
        bind, port = web.resolve_bind_port(getattr(args, "bind", None),
                                           getattr(args, "port", None), cfg,
                                           default_bind=web.LAN_BIND)
        web.remember_bind_port(cfg, getattr(args, "bind", None),
                               getattr(args, "port", None))
        ok = S.install_systemd_web(port=port, bind=bind, system=args.system)
        if ok:
            print("web dashboard service installed and started (always-on).")
            print("  open : http://<this-box>:%d   (http://127.0.0.1:%d)"
                  % (port, port))
            print("  stop : gitsync web --uninstall   (or: systemctl --user "
                  "disable --now gitsync-web)")
            print("  to survive logout/reboot: loginctl enable-linger $USER")
            return 0
        print("could not install the service (needs systemd). Run "
              "'gitsync web' in the foreground instead.")
        return 1
    if getattr(args, "uninstall", False):
        ok = S.uninstall_systemd_web(system=args.system)
        print("web service removed" if ok else "web service not installed")
        return 0
    if getattr(args, "status", False):
        print("web service: %s" % ("active" if S.web_active()
                                   else "not running"))
        return 0
    # foreground `gitsync web` is for this machine -> localhost by default
    bind, port = web.resolve_bind_port(getattr(args, "bind", None),
                                       getattr(args, "port", None), cfg)
    web.remember_bind_port(cfg, getattr(args, "bind", None),
                           getattr(args, "port", None))
    return web.serve(port=port, bind=bind,
                     allowed_hosts=getattr(args, "allowed_host", None))


def cmd_install(args):
    cfg = C.load_config()
    # no --schedule means "the most demanding route wins", so a route on
    # hourly with a global daily still fires hourly. Every route off means
    # there is nothing to schedule.
    key = args.schedule or C.finest_schedule(cfg)
    if key is None and not C.routes(cfg):
        key = C.setting(cfg, "schedule", "hourly")
    if args.schedule:
        C.set_setting(cfg, "schedule", key)
    if not key:
        S.uninstall_systemd(system=args.system)
        S.uninstall_systemd()
        print("scheduler disabled (every route is off)")
        return 0
    if key == "off":
        S.uninstall_systemd(system=args.system)
        S.uninstall_systemd()
        print("scheduler disabled (manual runs only)")
        return 0
    if args.cron or not S.have_systemd():
        S.install_cron(key)
        print("cron installed: %s" % C.SCHEDULES[key]["label"])
        return 0
    if S.install_systemd(key, system=args.system):
        print("timer installed: %s (Persistent=true — catches up runs "
              "missed while the machine was off)"
              % C.SCHEDULES[key]["label"])
        print("status: %s" % S.status())
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
        if args.type == "local" and not args.path:
            print("local platforms need --path")
            return 2
        p = C.platform_template(args.type, name, url=args.url,
                                token=args.token, owner=args.owner,
                                path=args.path, protocol=args.protocol,
                                ssh_port=args.ssh_port)
        if getattr(args, "url_template", None):
            p["url_template"] = args.url_template
        if getattr(args, "user", None):
            p["user"] = args.user
        err = C.add_platform(cfg, name, p)
        if err:
            print(err)
            return 2
        C.save_config(cfg)
        print("added platform: %s (%s)" % (name, args.type))
        return _test_one(name, p)

    if args.action == "remove":
        if args.name not in plats:
            print("no such platform: %s" % args.name)
            return 2
        err = C.remove_platform(cfg, args.name, force=args.force)
        if err:
            if err.startswith("used by route"):
                print("platform '%s' is %s" % (args.name, err))
                print("re-run with --force to remove anyway")
            else:
                print(err)
            return 2
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
                           ("owner", args.owner), ("path", args.path),
                           ("protocol", getattr(args, "protocol", None)),
                           ("ssh_port", getattr(args, "ssh_port", None))):
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
    if not p.get("enabled", True):
        # a disabled platform is not a broken one - say so instead of FAIL
        print("%-14s %-5s disabled (enable: gitsync platforms edit %s "
              "--enable)" % (name, "off", name))
        return 0
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
        if mesh and not getattr(args, "mesh", False):
            # say what the heuristic decided, instead of surprising people
            print("interpreted as a two-way (mesh) route")
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

    if action == "edit":
        if not args.rest:
            print("usage: gitsync routes edit NAME [--include a,b] "
                  "[--exclude a,b] [--from-file repos.txt] [--clear-filters]")
            return 2
        name = args.rest[0]
        route = next((r for r in cfg.get("routes", [])
                      if r.get("name") == name), None)
        if not route:
            print("no such route: %s" % name)
            return 2
        changed = []
        if args.from_file:
            path = os.path.expanduser(args.from_file)
            names = SYNC.read_include_file(path)
            if not names:
                print("no repo names in %s" % path)
                return 2
            route["include_file"] = path
            route.pop("include", None)
            changed.append("include_file=%s (%d repos)" % (path, len(names)))
        if args.include is not None:
            route["include"] = [p.strip() for p in args.include.split(",")
                                if p.strip()]
            route.pop("include_file", None)
            changed.append("include")
        if args.exclude is not None:
            route["exclude"] = [p.strip() for p in args.exclude.split(",")
                                if p.strip()]
            changed.append("exclude")
        if args.clear_filters:
            route.pop("include", None)
            route.pop("include_file", None)
            route.pop("exclude", None)
            changed.append("filters cleared")
        if not changed:
            print("nothing to change")
            return 2
        C.save_config(cfg)
        print("%s: %s" % (name, ", ".join(changed)))
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


def _head_probe(plat, repo):
    """(sha, err) for one repo on one platform.

    err set means "could not tell" — a rate limit, a timeout, an unreadable
    folder. It must never be rendered as "missing": a remote ls-remote that
    fails for any transient reason returns nothing, and reporting that as an
    absent repo is exactly the false alarm that makes a drift view useless.
    """
    name = repo["name"]
    if plat.ptype == "local":
        path = os.path.join(plat.path, P._fs_safe(name) + ".git")
        if not os.path.isdir(path):
            return None, None                     # definitively not here
        rc, out, _ = P.run(["git", "rev-parse", "--verify", "HEAD"],
                           cwd=path, timeout=30)
        if rc == 0 and out.strip():
            return out.strip(), None
        rc, out, _ = P.run(["git", "for-each-ref", "--format=%(objectname)",
                            "refs/heads/"], cwd=path, timeout=30)
        if rc == 0 and out.strip():
            return out.strip().splitlines()[0], None
        if rc == 0:
            return None, None                     # empty repo: no refs at all
        return None, "cannot read %s" % path
    # the platform's own URL, never the source's clone_url (which made every
    # target probe the source and report a false "in sync") and never
    # ensure_repo (a read-only check must not create repositories)
    url = plat.repo_url(repo)
    rc, out, err = P.run(["git", "ls-remote", url, "HEAD"], timeout=120)
    if rc == 0:
        # empty output with a clean exit is the one reliable "not there"
        return (out.split("\t")[0] or None), None
    return None, SYNC._short_error(err) or "ls-remote failed"


def compare_matrix(cfg, route, log=None):
    """drift matrix for one route: HEAD sha of every repo on every site.

    Returns {"repos": [{name, src, targets: {name: sha|None},
                        errors: {name: reason}}],
             "extra": {target: [names]}, "problems": n, "unknown": n}.
    "extra" is the answer to "why does gitlab have 58 repos when github has
    48": full mode prunes *refs* inside a repo, never whole repositories —
    gitsync creates what is missing but never deletes a repo on a target.
    """
    import concurrent.futures as _cf
    say = log or (lambda m: None)
    source = P.make(route["source"], C.platform(cfg, route["source"]))
    targets = {t: P.make(t, C.platform(cfg, t))
               for t in route.get("targets", [])
               if C.platform(cfg, t) and C.platform(cfg, t).get("enabled")}
    try:
        repos = source.list_repos()
    except Exception as e:
        raise RuntimeError("cannot list %s: %s" % (route["source"], e))
    names = [r["name"] for r in repos if not r.get("fork")] or \
        [r["name"] for r in repos]
    say("route %s: %d repos × %d sites" % (route.get("name"), len(names),
                                           len(targets) + 1))

    def head(plat, repo):
        """probe twice: the first pass is a burst, and a rate-limited
        ls-remote returns nothing — which would otherwise read as a repo
        that is not on the site at all."""
        err = None
        for attempt in range(2):
            sha, err = _head_probe(plat, {"name": repo})
            if not err:
                return sha, None
            time.sleep(1.0 + attempt)
        return None, err

    srcs = [None] * len(names)
    tgts = [{} for _ in range(len(names))]
    errs = [{} for _ in range(len(names))]
    with _cf.ThreadPoolExecutor(max_workers=min(12, max(1, len(names)))) as ex:
        futs = {}
        for i, n in enumerate(names):
            futs[ex.submit(head, source, n)] = ("src", i)
            for tname, tplat in targets.items():
                futs[ex.submit(head, tplat, n)] = (tname, i)
        done = 0
        for f in _cf.as_completed(futs):
            kind, i = futs[f]
            try:
                sha, err = f.result()
            except Exception:
                sha, err = None, "probe failed"
            # a target future can finish before the source's, so the row is
            # assembled once every future is in
            if kind == "src":
                srcs[i] = sha
                if err:
                    errs[i]["__src__"] = err
            else:
                tgts[i][kind] = sha
                if err:
                    errs[i][kind] = err
            done += 1
            if done % 25 == 0:
                say("  resolved %d/%d heads" % (done, len(futs)))
    rows = [{"name": names[i], "src": srcs[i], "targets": tgts[i],
             "errors": errs[i]} for i in range(len(names))]
    for r in rows:
        for t in targets:
            r["targets"].setdefault(t, None)

    extra = {}
    for tname, tplat in targets.items():
        try:
            have = {r["name"] for r in tplat.list_repos()}
        except Exception as e:
            say("  cannot list %s: %s" % (tname, str(e)[:60]))
            have = set()
        gone = sorted(have - set(names))
        if gone:
            extra[tname] = gone
            say("  %s has %d repo(s) the source does not" % (tname, len(gone)))
    problems = unknown = 0
    for r in rows:
        if any(r["errors"].values()):
            unknown += 1
            continue
        if not r["src"] or any(v != r["src"] for v in r["targets"].values()):
            problems += 1
    return {"route": route.get("name"), "source": route["source"],
            "targets": sorted(targets), "repos": rows, "extra": extra,
            "problems": problems, "unknown": unknown,
            "prune_repos": C.prune_targets(cfg, route)}


def cmd_compare(args):
    """drift check: compare the HEAD of every repo across a route"""
    cfg = C.load_config()
    routes = C.routes(cfg)
    if args.route:
        routes = [r for r in routes if r.get("name") == args.route]
    if not routes:
        print("no routes configured")
        return 2
    if getattr(args, "json", False):
        # machine-readable matrix for the web UI's Repos page: one JSON
        # object per route, one per line (NDJSON). It used to print an
        # object for a single route and an ARRAY for several, so with two
        # routes configured the page parsed an array as a matrix and
        # rendered "no data" — the shape must not depend on the count.
        for route in routes:
            try:
                matrix = compare_matrix(cfg, route, log=print)
            except RuntimeError as e:
                print(str(e))
                return 2
            print(json.dumps(matrix))
        return 0
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
    # the platform's own URL, never the source's clone_url (which made every
    # target probe the source and report a false "in sync") and never
    # ensure_repo (a read-only check must not create repositories)
    url = plat.repo_url(repo)
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
                 for n in SYNC.route_peers(cfg, route)}
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
            status, detail, errors = SYNC.mesh_sync_repo(repo, peers,
                                                           conflict)
        else:
            status, detail, errors = SYNC.sync_repo(
                repo, source, targets, C.route_mode(cfg, route))
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
    # The hub is shared by every route, so "orphan" means: not in the
    # filtered repo list of ANY enabled route. Comparing against the first
    # route's source alone marked mirrors that belong to another route (or
    # that a route's include/exclude glob keeps) as orphaned, and
    # --prune-orphans then deleted live data.
    orphans = []
    live_names = set()
    listed = False
    for route in C.routes(cfg):
        if route.get("enabled") is False:
            continue
        sname = route.get("source")
        sp = C.platform(cfg, sname)
        if not sp or not sp.get("enabled"):
            continue
        try:
            repos = P.make(sname, sp).list_repos()
        except Exception as e:
            print("cannot list %s to check for orphans: %s" % (sname, e))
            continue
        listed = True
        for r in SYNC._filter_repos(repos, route):
            live_names.add(r["name"].lower())
    if listed:
        orphans = D.orphans(live_names)

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
        print("\norphaned mirrors (in no enabled route's repo list):")
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
        # a gc rewrites the same objects a sync is pushing: take the lock
        try:
            import fcntl
        except ImportError:
            fcntl = None
        _lock = os.path.join(C.DATA_DIR, "sync.lock")
        C.ensure_dirs()
        _fh = open(_lock, "w")
        if fcntl:
            try:
                fcntl.flock(_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                print("a sync is running - not gc-ing")
                return 2
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
        ok = S.uninstall_webhooks(system=getattr(args, "system", False))
        print("webhook service removed" if ok else
              "webhook service not installed")
        return 0

    if args.install:
        ok = S.install_webhooks(port=args.port, bind=args.bind,
                                system=getattr(args, "system", False))
        if not ok:
            print("install failed (needs systemd)")
            return 1
        print("webhook receiver installed and started")
        print("  port : %d" % args.port)
        print("  bind : %s" % args.bind)
        log_flag = "" if getattr(args, "system", False) else "--user "
        print("  log  : journalctl %s-u gitsync-webhooks -f" % log_flag)
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


def cmd_stars(args):
    """list or copy starred repos between accounts"""
    cfg = C.load_config()
    action = args.action or "list"
    plats = cfg.get("platforms") or {}
    if action == "list":
        name = args.source
        if not name:
            routes = C.routes(cfg)
            name = (routes[0].get("source") if routes
                    else (sorted(plats)[0] if plats else None))
        if not name:
            print("no platform configured - run: gitsync setup")
            return 2
        p = P.make(name, plats[name])
        try:
            rows = ST.starred(p)
        except Exception as e:
            print("cannot read stars on %s: %s" % (name, e))
            return 1
        if args.json:
            print(json.dumps([{"name": n, "url": u} for n, u in rows],
                             indent=2))
            return 0
        print("starred on %s: %d" % (name, len(rows)))
        for n, _u in rows:
            print("  %s" % n)
        return 0

    # copy
    if not args.source or not args.targets:
        print("usage: gitsync stars copy SOURCE t1,t2 [--dry-run]")
        return 2
    if args.source not in plats:
        print("no such platform: %s" % args.source)
        return 2
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    unknown = [t for t in targets if t not in plats]
    if unknown:
        print("no such platform(s): %s" % ", ".join(unknown))
        return 2
    only = [r.strip() for r in (args.repos or "").split(",") if r.strip()]
    t = ST.copy_stars(cfg, args.source, targets, dry_run=args.dry_run,
                      only=only or None, log=lambda m: print(m))
    print("\n%s: %d starred, %d already, %d not on that site, %d failed"
          % (args.source, t["starred"], t["already"], t["missing"],
             t["failed"]))
    return 0 if t["failed"] == 0 else 1


def cmd_restore(args):
    """push the local hub back out — for when the source host is gone"""
    cfg = C.load_config()
    if not args.to:
        print("usage: gitsync restore --to PLATFORM[,PLATFORM] [--repos a,b]")
        print("       gitsync restore --to backup --dry-run")
        return 2
    targets = [t.strip() for t in args.to.split(",") if t.strip()]
    unknown = [t for t in targets if not C.platform(cfg, t)]
    if unknown:
        print("no such platform(s): %s" % ", ".join(unknown))
        return 2
    only = [r.strip() for r in (args.repos or "").split(",") if r.strip()]
    t = SYNC.restore(cfg, targets, only=only or None, dry_run=args.dry_run,
                     prune=args.prune, private=not args.public,
                     log_fn=lambda m: print(m))
    if args.dry_run:
        print("\n%d repo(s) would be restored (nothing was modified)" % t["ok"])
        return 0
    print("\n%d restored, %d skipped, %d failed" % (t["ok"], t["skipped"],
                                                     t["failed"]))
    return 0 if t["failed"] == 0 else 1


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


def _secret_key_names():
    return C._PLATFORM_SECRETS


def cmd_config(args):
    """export / import the configuration (credentials stripped on export)."""
    action = getattr(args, "action", None) or "export"
    if action == "export":
        out = getattr(args, "out", "") or ""
        try:
            text, removed = C.export_config(out or None)
        except OSError as e:
            print("cannot write the export: %s" % e)
            return 1
        if out:
            print("wrote %s (mode 0600)" % out)
            print("every credential was blanked (%d field(s)); "
                  "re-enter tokens after importing" % len(removed))
        else:
            sys.stdout.write(text)
            if removed:
                sys.stderr.write(
                    "\n# %d credential field(s) left empty in this export\n"
                    % len(removed))
        return 0

    path = getattr(args, "file", "")
    if not path:
        print("usage: gitsync config import FILE")
        return 2
    try:
        incoming = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("cannot read %s: %s" % (path, e))
        return 1
    n_plat = len(incoming.get("platforms") or {})
    n_route = len(incoming.get("routes") or [])
    print("import will set %d platform(s) and %d route(s)." % (n_plat, n_route))
    print("existing credentials are kept; your current config is backed up "
          "next to it.")
    if not getattr(args, "yes", False):
        try:
            if not sys.stdin.isatty():
                print("not a terminal: pass --yes to import unattended")
                return 1
            if input("Import it? [y/N] ").strip().lower() not in ("y", "yes"):
                print("nothing changed.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("nothing changed.")
            return 1
    try:
        report = C.import_config(path, replace=getattr(args, "replace", False))
    except C.ConfigImportError as e:
        print("refusing to import: %s" % e)
        return 1
    print("imported %d platform(s), %d route(s)"
          % (report["platforms"], report["routes"]))
    if report["kept_secrets"]:
        print("kept %d existing credential(s) on this machine."
              % len(report["kept_secrets"]))
    if report["needs_token"]:
        print("needs a token before it can sync: %s"
              % ", ".join(report["needs_token"]))
        print("  gitsync platforms edit NAME --token ...")
    print("check it with: gitsync doctor")
    return 0


def cmd_verify(args):
    """compare the hub against every target: branch by branch, tag by tag.

    `gitsync sync` reports what it *attempted*; this reports what is
    actually there now, and creates/pushes nothing while doing it.
    """
    cfg = C.load_config()
    try:
        rows = SYNC.verify(cfg, route_name=getattr(args, "route", None))
    except Exception as e:
        print("verify failed: %s" % SYNC._redact(str(e)))
        return 2
    summary = SYNC.verify_summary(rows)
    drift = [r for r in rows if r["state"] not in ("in-sync",)]
    if getattr(args, "json", False):
        print(json.dumps({"ok": not drift, "summary": summary,
                          "checks": rows}, indent=2))
        return 0 if not drift else 1
    if not rows:
        print("nothing to verify (no enabled routes)")
        return 0
    for r in drift:
        where = "%s: %s" % (r.get("route"), r.get("repo"))
        if r.get("target") not in (None, "-"):
            where += " -> %s" % r["target"]
        if r["state"] == "in-sync":
            continue
        if r["state"] == "drift":
            bits = []
            if r.get("missing"):
                bits.append("%d ref(s) missing" % len(r["missing"]))
            if r.get("differ"):
                bits.append("%d ref(s) at a different commit" % len(r["differ"]))
            print("  DRIFT      %s: %s" % (where, ", ".join(bits)))
            for ref in (r.get("missing") or [])[:5]:
                print("               missing %s" % ref)
            for ref in (r.get("differ") or [])[:5]:
                print("               differs %s" % ref)
        elif r["state"] == "unreachable":
            print("  UNREACHABLE %s: %s" % (where, r.get("detail", "")))
        elif r["state"] == "no-mirror":
            print("  NO MIRROR  %s (run: gitsync sync)" % where)
        else:
            print("  %-11s %s" % (r["state"].upper(), where))
    print()
    print("%d check(s): %s" % (len(rows), ", ".join(
        "%d %s" % (n, k) for k, n in sorted(summary.items()))))
    if drift:
        print("\n%d target(s) are not in sync with the hub."
              % len([r for r in drift if r["state"] in
                     ("drift", "no-mirror", "unreachable")]))
        return 1
    print("every target matches the hub.")
    return 0


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
    rc, out, _ = sh(["df", "-h", C.DATA_DIR],
                    timeout=30)
    # no shell, so skip the header line ourselves
    lines = [l for l in (out or "").splitlines() if l.strip()]
    parts = (lines[-1].split() if lines else [])
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
    # F5: what to type next, in plain language, chosen from what was found.
    # At most three commands - a newbie should not be handed a manual.
    hints = _doctor_hints(cfg, problems)
    if hints:
        print("\nwhat to type next:")
        for h in hints[:3]:
            print("  %s" % h)
    return 0 if not problems else 1


def _doctor_hints(cfg, problems):
    """at most three plain-language next steps, mapped from the problems"""
    text = " ".join(problems).lower()
    hints = []
    if "no config" in text:
        hints.append("gitsync setup")
    if "platform" in text and "failing" in text:
        names = [p.split()[1] for p in problems
                 if p.startswith("platform ") and "failing" in p]
        if names:
            hints.append("gitsync platforms test %s   # see the exact error"
                         % names[0])
            hints.append("gitsync platforms edit %s   # fix url/token"
                         % names[0])
    if "token" in text:
        hints.append("gitsync tokens   # which credential, and when it dies")
    if "route" in text and "incomplete" in text:
        hints.append("gitsync routes   # then: gitsync platforms add ...")
    if "scheduler" in text:
        hints.append("gitsync install")
    if "last run" in text:
        hints.append("gitsync records -n 1   # what failed")
        hints.append("gitsync log -n 40      # the detail")
    if "no routes" in text:
        hints.append("gitsync routes add main github gitlab,backup")
    if "orphan" in text:
        hints.append("gitsync disk --orphans")
    # de-duplicate, keep order, and never hand a newbie more than three
    # things to type
    seen, out = set(), []
    for h in hints:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:3]


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
        # a bare mirror with no refs is a failed clone -> remove it. An
        # empty-but-valid repo (HEAD file, zero refs) is a real empty repo,
        # not a failure - keep it.
        rc, out, _ = sh(["git", "rev-parse", "--is-bare-repository"],
                        cwd=path, timeout=30)
        if rc != 0:
            try:
                shutil.rmtree(path)
                fixed.append("removed broken mirror: %s" % entry)
            except OSError as e:
                fixed.append("could not remove %s: %s" % (entry, e))
    # Never unlink sync.lock: a live sync holds an flock on that inode, so
    # deleting it lets the next sync lock a fresh file and two syncs push
    # the same repo. A dead holder is detected by trying the lock, not by
    # removing the file.
    lock = os.path.join(C.DATA_DIR, "sync.lock")
    if os.path.exists(lock):
        try:
            import fcntl
        except ImportError:
            fcntl = None
        if fcntl:
            try:
                fh = open(lock, "w")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh, fcntl.LOCK_UN)
                    fixed.append("sync lock was stale (no live sync)")
                except OSError:
                    fixed.append("a sync is running - lock left alone")
            except OSError:
                pass
    return fixed


UPDATE_REPO = "SeaXen/gitsync"
_RAW = "https://raw.githubusercontent.com/%s" % UPDATE_REPO


def _fetch_bytes(url, timeout=60):
    """one GET, returned as bytes (patched in tests; no network there)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "gitsync-update"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _parse_sums(text):
    """SHA256SUMS (``hash  name`` per line) -> {name: hash}."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1].lstrip("*").strip()] = parts[0].strip().lower()
    return out


def _verify_asset(blob, name, sums_text):
    """raise ValueError unless blob's SHA-256 matches the published one."""
    want = _parse_sums(sums_text).get(name)
    if not want:
        raise ValueError("SHA256SUMS has no entry for %r" % name)
    got = hashlib.sha256(blob).hexdigest()
    if not hmac.compare_digest(got, want):
        raise ValueError("checksum mismatch for %s: expected %s, got %s"
                         % (name, want, got))
    return got


def _latest_release_tag(timeout=30):
    """the newest published tag, from the releases API (no 'main' guessing)."""
    url = "https://api.github.com/repos/%s/releases/latest" % UPDATE_REPO
    data = json.loads(_fetch_bytes(url, timeout=timeout).decode("utf-8"))
    tag = (data.get("tag_name") or "").strip()
    if not re.match(r"^v?\d+\.\d+\.\d+$", tag):
        raise ValueError("unexpected release metadata: %r" % (tag or None))
    return tag if tag.startswith("v") else "v" + tag


def _run(cmd, **kw):
    """subprocess.run, patched in tests."""
    return subprocess.run(cmd, **kw)


def _confirm(question, assume_yes):
    if assume_yes:
        return True
    try:
        if not sys.stdin.isatty():
            print("(not a terminal: pass --yes to update unattended)")
            return False
    except Exception:
        return False
    try:
        ans = input("%s [y/N] " % question).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


def _git_checkout_update(root, assume_yes):
    """a source checkout updates with git, not by re-running an installer."""
    print("this is a git checkout: %s" % root)
    print("  git pull --ff-only   then   bash install.sh")
    if not _confirm("Update this checkout now?", assume_yes):
        print("nothing changed.")
        return 1
    r = _run(["git", "-C", root, "pull", "--ff-only"])
    if r.returncode != 0:
        print("git pull failed (local changes?) — fix it, or run: "
              "git pull && bash install.sh")
        return r.returncode
    r = _run(["bash", os.path.join(root, "install.sh")])
    print("updated" if r.returncode == 0 else "install failed (rc=%d)"
          % r.returncode)
    return r.returncode


def cmd_update(args):
    """update from a *pinned, checksum-verified release* — never from main.

    The old path wrote whatever ``main`` served at that instant to the fixed
    path /tmp/gitsync-quickinstall.sh and ran it, as root when installed
    system-wide. Two problems: a predictable /tmp name is a symlink/race
    target, and there was no version pin and no integrity check, so the
    script that runs as root could change under the user at any time.

    Now: the tag is resolved explicitly (latest release, or --version), the
    installer for *that tag* is fetched, its SHA-256 is checked against the
    tag's SHA256SUMS, it lands in a private mkstemp file (0600, then 0700)
    that is removed in a finally, and the user is told what will run.
    """
    import tempfile
    cfg = C.load_config()
    sched = C.setting(cfg, "schedule", "hourly")
    want = (getattr(args, "version", None) or "").strip()
    assume_yes = bool(getattr(args, "yes", False))

    root = repo_root_of(__file__)
    if not want and os.path.isdir(os.path.join(root, ".git")):
        return _git_checkout_update(root, assume_yes)

    try:
        tag = ("v" + want.lstrip("v")) if want else _latest_release_tag()
        print("target release: %s (currently %s)" % (tag, __version__))
        script_blob = _fetch_bytes(
            "%s/%s/quickinstall.sh" % (_RAW, tag))
        sums_text = _fetch_bytes("%s/%s/SHA256SUMS" % (_RAW, tag)).decode(
            "utf-8", "replace")
        digest = _verify_asset(script_blob, "quickinstall.sh", sums_text)
    except Exception as e:
        print("cannot verify the %s installer: %s"
              % (want or "latest", e))
        print("Nothing was executed. Update by hand instead:")
        print("  git pull && bash install.sh          (source checkout)")
        print("  curl -fsSL https://github.com/%s/releases/latest -o /dev/null"
              % UPDATE_REPO)
        return 1

    fd, script = tempfile.mkstemp(prefix="gitsync-qi-", suffix=".sh")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(script_blob)
        os.chmod(script, 0o700)
        user_mode = os.path.exists(os.path.join(
            os.path.expanduser("~"), ".local", "share", "gitsync")) and \
            not os.access("/opt/gitsync/gitsync", os.W_OK)
        cmd = ["bash", script, "--no-setup", "--no-sync",
               "--schedule", sched, "--tag", tag]
        if user_mode:
            cmd.append("--user")
        print("installer: %s (sha256 %s…)" % (script, digest[:12]))
        print("it will install gitsync %s, keeping your configuration "
              "and schedule (%s)." % (tag, sched))
        if not _confirm("Run it?", assume_yes):
            print("nothing changed.")
            return 1
        rc = _run(cmd).returncode
    finally:
        try:
            os.remove(script)
        except OSError:
            pass
    print("updated to %s" % tag if rc == 0 else "update failed (rc=%d)" % rc)
    return rc


HOOK_TEXT = """#!/usr/bin/env bash
# gitsync pre-push hook - runs the full check suite before every push.
# Installed by: gitsync ci --install-hook
# Skip once with: git push --no-verify
set -e
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$APP_DIR"
echo "pre-push: running gitsync ci checks..."
python3 -m gitsync ci || {
    echo ""
    echo "pre-push: checks FAILED - push aborted (fix, or bypass with --no-verify)"
    exit 1
}
echo "pre-push: all checks passed - pushing"
"""


def repo_root_of(path):
    """the repository root that contains gitsync/ (works installed or not)"""
    d = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    return d if os.path.isdir(os.path.join(d, "gitsync")) else os.getcwd()


def _install_hook(repo_root):
    """write .git/hooks/pre-push. Never overwrites an existing one."""
    hooks = os.path.join(repo_root, ".git", "hooks")
    if not os.path.isdir(hooks):
        return "not a git repository: %s" % repo_root
    path = os.path.join(hooks, "pre-push")
    if os.path.exists(path):
        return "a pre-push hook already exists: %s" % path
    with open(path, "w") as f:
        f.write(HOOK_TEXT)
    os.chmod(path, 0o755)
    return None


SUMS_FILE = "SHA256SUMS"
_SUM_EXTRA = ("install.sh", "quickinstall.sh", "Dockerfile",
              "docker-compose.yml", "bin/gitsync")


def shipped_files(root):
    """the files a release tarball actually installs — what SHA256SUMS covers."""
    out = [n for n in _SUM_EXTRA if os.path.isfile(os.path.join(root, n))]
    base = os.path.join(root, "gitsync")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(filenames):
            if fn.endswith((".py", ".js", ".css", ".html")):
                out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(out)


def compute_sums(root):
    """SHA256SUMS text (``hash  relative/path``) for the shipped tree."""
    lines = []
    for rel in shipped_files(root):
        with open(os.path.join(root, rel), "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        lines.append("%s  %s" % (digest, rel))
    return "\n".join(lines) + "\n"


def write_sums(root):
    path = os.path.join(root, SUMS_FILE)
    text = compute_sums(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def check_sums(root):
    """raise unless the committed SHA256SUMS matches the working tree.

    This is what makes `gitsync update` able to verify a download: a tag
    that ships a stale or missing SHA256SUMS cannot be installed by the
    updater at all, so the check has to fail before the tag is pushed.
    """
    path = os.path.join(root, SUMS_FILE)
    if not os.path.isfile(path):
        if not os.path.isdir(os.path.join(root, ".git")):
            return          # an installed copy has nothing to verify
        raise RuntimeError("%s is missing — run: gitsync ci --update-sums"
                           % SUMS_FILE)
    with open(path, "r", encoding="utf-8") as f:
        have = f.read()
    want = compute_sums(root)
    if have != want:
        raise RuntimeError("%s is stale — run: gitsync ci --update-sums"
                           % SUMS_FILE)


def cmd_ci(args):
    """run the full check suite locally (same checks as the CI workflows)"""
    import py_compile
    import tempfile
    root_for_sums = repo_root_of(__file__)
    if getattr(args, "update_sums", False):
        path = write_sums(root_for_sums)
        print("wrote %s (%d files)" % (path, len(shipped_files(root_for_sums))))
        return 0
    if getattr(args, "install_hook", False):
        err = _install_hook(repo_root_of(__file__))
        if err:
            print("hook not installed: %s" % err)
            return 2
        print("installed: %s" % os.path.join(repo_root_of(__file__),
                                             ".git", "hooks", "pre-push"))
        print("every `git push` now runs `gitsync ci` first "
              "(bypass once with --no-verify)")
        return 0

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
                     ["stars", "--help"], ["doctor", "--help"],
                     ["verify", "--help"], ["config", "export"]):
            p = subprocess.run(["python3", "-m", "gitsync"] + argv,
                               capture_output=True, text=True, env=env,
                               timeout=60)
            if p.returncode != 0:
                raise RuntimeError("gitsync %s failed: %s"
                                   % (" ".join(argv),
                                      (p.stderr or p.stdout).strip()[-120:]))
        # a real (offline) sync: two local folders and a route between them.
        # `gitsync sync` is the command the timer runs, and a broken name in
        # cmd_sync once shipped because only --help was exercised here.
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfgdir = os.path.join(td, "cfg")
            datadir = os.path.join(td, "data")
            src_dir = os.path.join(td, "src")
            dst_dir = os.path.join(td, "dst")
            for d in (cfgdir, datadir, src_dir, dst_dir):
                os.makedirs(d)
            cfg = {"version": 1,
                   "platforms": {
                       "src": {"type": "local", "enabled": True,
                               "path": src_dir},
                       "dst": {"type": "local", "enabled": True,
                               "path": dst_dir}},
                   "routes": [{"name": "r", "source": "src",
                               "targets": ["dst"]}],
                   "settings": {}}
            with open(os.path.join(cfgdir, "config.json"), "w") as f:
                _json.dump(cfg, f)
            e2 = dict(env, GITSYNC_CONFIG_DIR=cfgdir, GITSYNC_DATA_DIR=datadir)
            # a ref that exists only on the target must be reported as a
            # prune by a full-mode dry run (and never by a safe-mode one).
            # LocalPlatform lists the *.git dirs inside its path, so the
            # folders each hold one bare repo.
            def sh2(args, cwd=None):
                # every git call carries its own identity: a clean CI
                # container has no user.name/user.email, and `git commit`
                # then fails with "Committer identity unknown", which
                # silently starved the rest of this scenario
                ident = ["-c", "user.name=gitsync-ci",
                         "-c", "user.email=ci@example.invalid"]
                r = subprocess.run(["git"] + ident + args,
                                   capture_output=True, cwd=cwd, timeout=60)
                if r.returncode != 0:
                    raise RuntimeError(
                        "git %s failed: %s"
                        % (" ".join(args), (r.stderr or "").strip()[-160:]))

            sh2(["init", "--bare", "-q",
                 os.path.join(src_dir, "y.git")])
            sh2(["init", "--bare", "-q",
                 os.path.join(dst_dir, "y.git")])
            seed = os.path.join(td, "seed")
            os.makedirs(seed)
            sh2(["init", "-q", seed])
            sh2(["-C", seed, "commit", "--allow-empty", "-q",
                 "-m", "x", "--author", "t <t@t>",
                 "--date", "2020-01-01T00:00:00"])
            sh2(["-C", seed, "branch", "-M", "main"])
            sh2(["-C", seed, "push", "-q",
                 os.path.join(src_dir, "y.git"), "main"])
            sh2(["-C", seed, "push", "-q",
                 os.path.join(dst_dir, "y.git"), "main"])
            sh2(["-C", seed, "push", "-q",
                 os.path.join(dst_dir, "y.git"),
                 "main:refs/heads/only-on-target"])
            cfg["settings"]["mode"] = "full"
            with open(os.path.join(cfgdir, "config.json"), "w") as f:
                _json.dump(cfg, f)
            p = subprocess.run(["python3", "-m", "gitsync", "sync",
                                "--dry-run"], capture_output=True, text=True,
                               env=e2, timeout=120)
            if p.returncode != 0:
                raise RuntimeError("gitsync sync --dry-run failed: %s"
                                   % (p.stderr or p.stdout).strip()[-160:])
            if "WOULD DELETE" not in (p.stdout or ""):
                raise RuntimeError("dry run did not report the ref a "
                                   "full-mode push would delete")
            cfg["settings"]["mode"] = "safe"
            with open(os.path.join(cfgdir, "config.json"), "w") as f:
                _json.dump(cfg, f)
            p = subprocess.run(["python3", "-m", "gitsync", "sync",
                                "--dry-run"], capture_output=True, text=True,
                               env=e2, timeout=120)
            if "WOULD DELETE" in (p.stdout or ""):
                raise RuntimeError("safe mode must never report a prune")

            # and the real pushes behave the same way (P0-7): safe keeps a
            # branch only the destination has, full removes it
            def dest_refs():
                o = subprocess.run(
                    ["git", "-C", os.path.join(dst_dir, "y.git"),
                     "for-each-ref", "--format=%(refname:short)"],
                    capture_output=True, text=True).stdout
                return o.split()

            cfg["settings"]["mode"] = "safe"
            with open(os.path.join(cfgdir, "config.json"), "w") as f:
                _json.dump(cfg, f)
            subprocess.run(["python3", "-m", "gitsync", "sync"],
                           capture_output=True, text=True, env=e2,
                           timeout=120)
            if "only-on-target" not in dest_refs():
                raise RuntimeError("safe mode deleted a branch only the "
                                   "destination had")
            cfg["settings"]["mode"] = "full"
            with open(os.path.join(cfgdir, "config.json"), "w") as f:
                _json.dump(cfg, f)
            subprocess.run(["python3", "-m", "gitsync", "sync"],
                           capture_output=True, text=True, env=e2,
                           timeout=120)
            if "only-on-target" in dest_refs():
                raise RuntimeError("full mode did not prune the stale branch")

            for argv in (["status"], ["routes"],
                         ["stars", "list", "src"]):
                p = subprocess.run(["python3", "-m", "gitsync"] + argv,
                                   capture_output=True, text=True, env=e2,
                                   timeout=120)
                if p.returncode != 0:
                    raise RuntimeError(
                        "gitsync %s failed: %s" % (" ".join(argv),
                                                    (p.stderr or
                                                     p.stdout).strip()[-160:]))

    def c_unit():
        import unittest
        import warnings
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName("tests.test_logic")
        stream = open(os.devnull, "w")
        try:
            # a leaked socket or file must fail the run, not scroll past as
            # a warning nobody reads (P3-3)
            with warnings.catch_warnings():
                warnings.simplefilter("error", ResourceWarning)
                runner = unittest.TextTestRunner(stream=stream, verbosity=0)
                res = runner.run(suite)
        finally:
            stream.close()
        if not res.wasSuccessful():
            raise RuntimeError("%d failed, %d errors across %d tests"
                               % (len(res.failures), len(res.errors), res.testsRun))

    def c_sums():
        check_sums(root_for_sums)

    check("python syntax (gitsync/*.py)", c_syntax)
    check("shell syntax (installers)", c_shell)
    check("release checksums (SHA256SUMS)", c_sums)
    check("CLI smoke tests", c_cli)
    check("unit checks (tests.test_logic)", c_unit)

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


def cmd_notify(args):
    cfg = C.load_config()
    if args.test:
        ok, msg = NT.notify_test(cfg)
        print("notification test: %s" % msg)
        return 0 if ok else 1
    if getattr(args, "set_url", None):
        url = args.set_url.strip()
        if not url.startswith(NT.DISCORD_PREFIXES):
            print("that does not look like a Discord webhook "
                  "(expected %s...)" % NT.DISCORD_PREFIXES[0])
            return 2
        cfg.setdefault("settings", {}).setdefault("notify", {})
        cfg["settings"]["notify"]["discord_webhook"] = url
        C.save_config(cfg)
        print("discord webhook saved")
        return 0
    if getattr(args, "set_telegram", None):
        tok = args.set_telegram.strip()
        chat = (getattr(args, "chat_id", None) or "").strip()
        if not tok or not chat:
            print("--set-telegram needs --chat-id")
            return 2
        cfg.setdefault("settings", {}).setdefault("notify", {})
        cfg["settings"]["notify"]["telegram_token"] = tok
        cfg["settings"]["notify"]["telegram_chat_id"] = chat
        C.save_config(cfg)
        print("telegram credentials saved")
        return 0
    url, on = NT._settings(cfg)
    tok, chat = NT._telegram(cfg)
    print("discord webhook : %s" % ("configured" if url else "NOT configured"))
    print("telegram        : %s" % ("configured" if (tok and chat)
                                    else "NOT configured"))
    print("notify on       : %s" % on)
    if not url and not (tok and chat):
        print("\nset one with:")
        print("  gitsync notify --set-url https://discord.com/api/webhooks/...")
        print("  gitsync notify --set-telegram BOTTOKEN --chat-id 12345")
    return 0


def cmd_fix(args):
    """detect drift across a route and re-sync the mismatched repos"""
    cfg = C.load_config()
    dry = getattr(args, "dry_run", False)
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
        # the route's own mode and conflict policy, not just the globals
        mode = C.route_mode(cfg, route)
        mesh = route.get("direction") == "mesh"
        conflict = route.get("mesh_conflict") or \
            C.setting(cfg, "mesh_conflict", "newest")
        peers = SYNC.route_peers(cfg, route) if mesh else {}
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
                print("  %s %-28s -> %s"
                      % ("would fix" if dry else "fixing", repo["name"],
                         ", ".join(drifted)))
                if dry:
                    fixed += 1
                    continue
                if mesh:
                    status, detail, errors = SYNC.mesh_sync_repo(
                        repo, peers, conflict)
                else:
                    status, detail, errors = SYNC.sync_repo(repo, source,
                                                            targets, mode)
                if status in ("ok", "partial"):
                    fixed += 1
                else:
                    failed += 1
                    print("    FAILED: %s" % detail)
            except Exception as e:
                failed += 1
                print("    error: %s" % str(e)[:80])
    print("\nchecked %d repos: %d %s, %d failed"
          % (checked, fixed, "would fix" if dry else "fixed", failed))
    if not dry and NT.enabled(cfg) and (fixed or failed):
        NT.send(cfg, "gitsync: drift repair",
                [("Checked", str(checked), True),
                 ("Fixed", str(fixed), True),
                 ("Failed", str(failed), True)],
                color="ok" if not failed else "warn")
    return 0 if failed == 0 else 1


def cmd_watch(args):
    """foreground poller: sync every N seconds (faster than the hourly timer).

    The config was read once, before the loop, so a route or platform edited
    in the web UI or the TUI was never picked up until the watcher was
    restarted; and one exception from run_sync ended the process silently —
    in Docker that means the container exits and nothing syncs again. Both
    are fixed here: reload per iteration, log-and-continue on errors, honour
    the global pause, and show a clear line when a run is skipped.
    """
    interval = args.interval
    if interval < 30:
        # 0 busy-loops the run lock and a small number is almost always a
        # typo; 30s is the floor that still feels live
        print("interval %ss is too short - using 30s" % interval)
        interval = 30
    once = bool(getattr(args, "once", False))
    print("gitsync watch: syncing every %ds — Ctrl+C to stop" % interval)
    runs = 0
    failures = 0
    try:
        while True:
            try:
                cfg = C.load_config()          # pick up edits without a restart
            except Exception as e:
                print("[%s] cannot read the config (%s) — retrying in %ds"
                      % (datetime.now().strftime("%H:%M:%S"), e, interval))
                if once:
                    return 1
                time.sleep(interval)
                continue
            runs += 1
            try:
                if C.setting(cfg, "paused", False):
                    print("[%s] paused — skipping this run"
                          % datetime.now().strftime("%H:%M:%S"))
                else:
                    t = SYNC.run_sync(cfg)
                    print("[%s] run #%d: %d ok, %d failed — next in %ds"
                          % (datetime.now().strftime("%H:%M:%S"), runs,
                             t.get("ok", 0), t.get("failed", 0), interval))
            except Exception as e:
                failures += 1
                print("[%s] run #%d failed: %s"
                      % (datetime.now().strftime("%H:%M:%S"), runs,
                         SYNC._redact(str(e))[:200]))
                if once:
                    return 1
            if once or getattr(args, "max_runs", 0) and runs >= args.max_runs:
                return 0 if not failures else 1
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


def cmd_pause(args):
    cfg = C.load_config()
    cfg.setdefault("settings", {})["paused"] = True
    C.save_config(cfg)
    print("gitsync: background sync PAUSED globally")
    return 0


def cmd_resume(args):
    cfg = C.load_config()
    cfg.setdefault("settings", {})["paused"] = False
    C.save_config(cfg)
    print("gitsync: background sync RESUMED")
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

    sp = sub.add_parser("prune",
                        help="delete target repos the source does not have "
                             "(dry-run unless --yes)")
    sp.add_argument("--route", help="only this route")
    sp.add_argument("--yes", action="store_true",
                    help="actually delete (without it, only a list is shown)")
    sp.set_defaults(func=cmd_prune)

    sp = sub.add_parser("sync", help="run a sync now")
    sp.add_argument("--route", help="only this route")
    sp.add_argument("--to", help="only push to this platform")
    sp.add_argument("--repos", help="comma-separated repo names to sync")
    sp.add_argument("--retry-failed", action="store_true",
                    help="re-sync failed repos from last run (combine with --route to scope to a single route)")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what would change, change nothing")
    sp.add_argument("--scheduled", action="store_true",
                    help="skip routes whose own schedule is not due yet")
    sp.add_argument("--force", action="store_true",
                    help="run sync even if sync is paused globally")
    sp.add_argument("--prune-repos", action="store_true",
                    help="delete target repos the source does not have "
                         "(needs full mode + a target named in the route's "
                         "prune_repos; never runs from the timer)")
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

    sp = sub.add_parser("web", help="open the web dashboard in a browser")
    sp.add_argument("--port", type=int, default=None,
                    help="listen port (default %d; remembered once set)"
                         % WH_PORT_WEB)
    sp.add_argument("--bind", default=None,
                    help="bind address for `gitsync web` (default 127.0.0.1 — "
                         "this machine; use 0.0.0.0 for LAN. `--install` and "
                         "Docker default to 0.0.0.0). Remembered once set.")
    sp.add_argument("--allowed-host", dest="allowed_host", action="append",
                    help="extra Host/Origin to accept (repeatable; for a "
                         "reverse-proxy domain). DNS-rebinding guard stays on")
    sp.add_argument("--set-password", dest="set_password", metavar="USER",
                    help="set a username + password for web sign-in, then exit")
    sp.add_argument("--disable-password", dest="disable_password",
                    action="store_true",
                    help="remove the web password (token sign-in remains)")
    sp.add_argument("--install", action="store_true",
                    help="install + start a systemd service so the dashboard "
                         "is always on (survives reboot)")
    sp.add_argument("--uninstall", action="store_true",
                    help="remove the always-on web service")
    sp.add_argument("--status", action="store_true",
                    help="is the always-on web service running?")
    sp.add_argument("--system", action="store_true",
                    help="with --install: a root system service instead of a "
                         "user service")
    sp.set_defaults(func=cmd_web)

    sp = sub.add_parser("install", help="install the scheduler")
    sp.add_argument("--schedule", choices=sorted(C.SCHEDULES),
                    help="default: the most frequent route schedule")
    sp.add_argument("--cron", action="store_true",
                    help="force cron instead of systemd")
    sp.add_argument("--system", action="store_true",
                    help="install a root system unit instead of a user unit")
    sp.add_argument("--yes", action="store_true",
                    help="no prompts (idempotent)")
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
    sp.add_argument("--protocol", choices=["http", "ssh"],
                    help="clone protocol (ssh needs an ssh key on the host)")
    sp.add_argument("--ssh-port", type=int, dest="ssh_port",
                    help="ssh port when protocol=ssh")
    sp.add_argument("--url-template", dest="url_template",
                    help="generic: URL template with {owner}/{name}")
    sp.add_argument("--user", help="git username for token URLs")
    sp.add_argument("--enable", action="store_true")
    sp.add_argument("--disable", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_platforms)

    sp = sub.add_parser("scopes", help="show required token scopes")
    sp.set_defaults(func=cmd_scopes)

    sp = sub.add_parser("routes", help="list / add / remove sync routes")
    sp.add_argument("action", nargs="?",
                    choices=["list", "add", "remove", "edit", "pause",
                             "resume"],
                    default="list")
    sp.add_argument("rest", nargs="*",
                    help="add: NAME SOURCE t1,t2 | remove: NAME | "
                         "edit: NAME | pause/resume: NAME [TARGET...]")
    sp.add_argument("--include", help="comma-separated globs to include")
    sp.add_argument("--exclude", help="comma-separated globs to skip")
    sp.add_argument("--from-file", dest="from_file",
                    help="read the include list from a file (one repo per "
                         "line, # comments)")
    sp.add_argument("--clear-filters", action="store_true",
                    help="drop include/exclude/include_file")
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
    sp.add_argument("--json", action="store_true",
                    help="one JSON object (used by the web UI's Repos page)")
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
    sp.add_argument("--system", action="store_true",
                    help="install as a root system unit instead of a user unit")
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

    sp = sub.add_parser("stars", help="list / copy starred repos between accounts")
    sp.add_argument("action", nargs="?", choices=["list", "copy"],
                    default="list")
    sp.add_argument("source", nargs="?", help="platform to read stars from")
    sp.add_argument("targets", nargs="?",
                    help="copy: comma-separated platforms to star on")
    sp.add_argument("--repos", help="only these owner/repo names")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--json", action="store_true", help="list as JSON")
    sp.set_defaults(func=cmd_stars)

    sp = sub.add_parser("restore",
                        help="push the hub back out (source host is gone)")
    sp.add_argument("--to", required=True,
                    help="comma-separated platforms to restore to")
    sp.add_argument("--repos", help="only these repos")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--prune", action="store_true",
                    help="also delete target refs the hub does not have "
                         "(only with mode=full)")
    sp.add_argument("--public", action="store_true",
                    help="create missing repos as public (default private)")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("tokens", help="check credentials for expiry")
    sp.set_defaults(func=cmd_tokens)

    sp = sub.add_parser("config",
                        help="export / import the configuration")
    csp = sp.add_subparsers(dest="action")
    ex = csp.add_parser("export", help="print the config with secrets blanked")
    ex.add_argument("--out", default="", help="write to this file (0600)")
    ex.set_defaults(func=cmd_config)
    im = csp.add_parser("import", help="import a config, keeping local secrets")
    im.add_argument("file", help="config.json to import")
    im.add_argument("--yes", "-y", action="store_true",
                    help="do not ask for confirmation")
    im.add_argument("--replace", action="store_true",
                    help="do not carry over existing tokens")
    im.set_defaults(func=cmd_config)
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("verify",
                        help="check that every target matches the hub")
    sp.add_argument("--route", default=None, help="only this route")
    sp.add_argument("--json", action="store_true", help="machine-readable")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("doctor", help="health check: everything")
    sp.add_argument("--fix", action="store_true",
                    help="auto-repair: remove broken mirror shells")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("update", help="update gitsync (keeps your config)")
    sp.add_argument("--version", metavar="X.Y.Z", default="",
                    help="install this release instead of the latest one")
    sp.add_argument("--yes", "-y", action="store_true",
                    help="do not ask for confirmation")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("ci", help="run all checks locally (no provider needed)")
    sp.add_argument("--install-hook", action="store_true",
                    help="install a pre-push hook in this repo")
    sp.add_argument("--update-sums", action="store_true",
                    help="rewrite SHA256SUMS after a release-worthy change")
    sp.set_defaults(func=cmd_ci)

    sp = sub.add_parser("notify", help="Discord / Telegram notification settings")
    sp.add_argument("--test", action="store_true", help="send a test message")
    sp.add_argument("--set-url", dest="set_url",
                    help="save a Discord webhook URL")
    sp.add_argument("--set-telegram", dest="set_telegram",
                    help="save a Telegram bot token (needs --chat-id)")
    sp.add_argument("--chat-id", dest="chat_id",
                    help="Telegram chat id for --set-telegram")
    sp.set_defaults(func=cmd_notify)

    sp = sub.add_parser("fix", help="detect drift and re-sync mismatched repos")
    sp.add_argument("--route", help="only this route")
    sp.add_argument("--dry-run", action="store_true",
                    help="report drift without pushing anything")
    sp.set_defaults(func=cmd_fix)

    sp = sub.add_parser("watch", help="sync every N seconds in the foreground")
    sp.add_argument("--interval", type=int, default=60)
    sp.add_argument("--once", action="store_true",
                    help="run one cycle and exit (container / cron friendly)")
    sp.add_argument("--max-runs", type=int, default=0,
                    help="stop after N cycles (0 = forever)")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("uninstall", help="remove scheduler + symlink")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("pause", help="pause background sync globally")
    sp.set_defaults(func=cmd_pause)

    sp = sub.add_parser("resume", help="resume background sync globally")
    sp.set_defaults(func=cmd_resume)

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
