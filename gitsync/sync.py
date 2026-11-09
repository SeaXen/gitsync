"""Sync engine: source platform -> local mirror hub -> target platforms.

Design (proven): every repo is first mirrored into the local hub as a bare
repo, then pushed from the hub to each target. The hub makes the sync
resumable and gives an offline copy for free.
"""

import fnmatch
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from . import config as C
from . import notify as NT
from . import platforms as P

_log_lock = threading.Lock()
_start = None

# live per-repo progress, read by the TUI while a sync runs
progress = {"active": {}, "done": 0, "total": 0, "route": ""}


def reset_progress():
    progress["active"] = {}
    progress["done"] = 0
    progress["total"] = 0


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _log_lock:
        print(line, flush=True)
        try:
            C.ensure_dirs()
            with open(C.LOG_FILE, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass


def mirror_path(name):
    return os.path.join(C.MIRROR_DIR, P._fs_safe(name) + ".git")


def mirror_is_valid(path):
    if not os.path.isdir(path):
        return False
    rc, out, _ = P.run(["git", "for-each-ref"], cwd=path, timeout=60)
    return rc == 0 and bool(out.strip())


def sync_repo(repo, source, targets, mode):
    """one repo: source -> hub -> targets. Returns (status, detail, errors)."""
    name = repo["name"]
    src_url = repo["clone_url"]
    hub = mirror_path(name)
    errors = []

    # 1. hub mirror
    if os.path.isdir(hub) and not mirror_is_valid(hub):
        log("  removing incomplete mirror for %s" % name)
        P.run(["rm", "-rf", hub])
    if os.path.isdir(hub):
        P.run(["git", "remote", "set-url", "origin", src_url], cwd=hub)
        rc, _, err = P.run(["git", "remote", "update", "--prune"], cwd=hub,
                            timeout=P.GIT_TIMEOUT)
        if rc != 0:
            return "mirror-update-failed", err.strip()[:200], []
    else:
        rc, _, err = P.run(["git", "clone", "--mirror", src_url, hub],
                            timeout=P.GIT_TIMEOUT)
        if rc != 0:
            P.run(["rm", "-rf", hub])
            return "clone-failed", err.strip()[:200], []

    # 2. push to each target
    pushed = []
    for tname, tplat in targets.items():
        try:
            push_url = tplat.ensure_repo(repo)
            if tplat.ptype == "forgejo" or tplat.ptype == "gitea":
                # GitHub's refs/pull/* namespace collides with Forgejo's own
                # PR storage and its pre-receive hook rejects the whole push
                cmd = ["git", "push", "--force", push_url,
                       "+refs/heads/*:refs/heads/*",
                       "+refs/tags/*:refs/tags/*"]
            elif mode == "full":
                cmd = ["git", "push", "--mirror", push_url]
            else:
                cmd = ["git", "push", "--force", push_url,
                       "+refs/heads/*:refs/heads/*",
                       "+refs/tags/*:refs/tags/*"]
            rc, _, err = P.run(cmd, cwd=hub, timeout=P.GIT_TIMEOUT)
            if rc != 0 and _protection_error(err):
                # a protected branch refuses force-push. Ask the adapter to
                # relax it, then retry the same push exactly once. Doing this
                # lazily keeps the happy path free of extra API calls, which
                # matters: GitLab rate-limits aggressively.
                try:
                    for note in tplat.prepare_push(repo, push_url) or []:
                        log("    %s" % note)
                    rc, _, err = P.run(cmd, cwd=hub, timeout=P.GIT_TIMEOUT)
                except Exception as e:
                    log("    %s: prepare_push failed: %s"
                        % (tname, str(e)[:100]))
            if rc != 0:
                errors.append("%s: %s" % (tname, _short_error(err)))
            else:
                pushed.append(tname)
                if tplat.ptype == "local":
                    # git init --bare defaults HEAD to master; point it at the
                    # branch we actually pushed so clones are not empty
                    rc2, head_ref, _ = P.run(["git", "symbolic-ref", "HEAD"],
                                             cwd=hub, timeout=30)
                    if rc2 == 0 and head_ref.strip():
                        P.run(["git", "-C", push_url, "symbolic-ref",
                               "HEAD", head_ref.strip()], timeout=30)
        except Exception as e:
            errors.append("%s: %s" % (tname, str(e)[:120]))

    if errors and not pushed:
        return "push-failed", "; ".join(errors), errors
    if errors:
        return "partial", "; ".join(errors), errors
    return "ok", ",".join(pushed), []


def _filter_repos(repos, route, only=None):
    """route-level include/exclude globs on repo names, plus an exact
    'only' list (comma-separated names) for one-off selective syncs"""
    inc = route.get("include") or []
    exc = route.get("exclude") or []
    if inc:
        repos = [r for r in repos
                 if any(fnmatch.fnmatch(r["name"], p) for p in inc)]
    if exc:
        repos = [r for r in repos
                 if not any(fnmatch.fnmatch(r["name"], p) for p in exc)]
    if only:
        wanted = {n.strip().lower() for n in only if n.strip()}
        repos = [r for r in repos if r["name"].lower() in wanted]
    return repos


def _remote_head(url, timeout=60):
    """HEAD sha of a remote, or None if unreachable / no refs"""
    rc, out, _ = P.run(["git", "ls-remote", url, "HEAD"], timeout=timeout)
    if rc != 0 or not out.strip():
        return None
    return out.split()[0]


def _plan_repo(repo, source, targets, mode):
    """what a sync of this repo WOULD do — read-only, no mutations.

    Returns a list of human-readable action strings. Uses git ls-remote
    against the source and each target's expected URL, so it never creates
    a repo and never writes to the hub.
    """
    name = repo["name"]
    src_url = repo.get("clone_url")
    src_head = _remote_head(src_url) if src_url else None
    if src_head is None:
        return ["cannot reach source %s" % name], "error"

    hub = mirror_path(name)
    actions = []
    if os.path.isdir(hub) and mirror_is_valid(hub):
        hub_head = _remote_head(hub)
        if hub_head == src_head:
            actions.append("hub up to date")
        else:
            actions.append("hub: %s -> %s" % ((hub_head or "none")[:8],
                                              src_head[:8]))
    else:
        actions.append("hub: would clone (%s)" % src_head[:8])
        behind += 1

    behind = 0
    for tname, tplat in targets.items():
        try:
            url = tplat.repo_url(repo)
        except Exception as e:
            actions.append("%s: cannot probe (%s)" % (tname, str(e)[:60]))
            behind += 1
            continue
        thead = _remote_head(url)
        if thead is None:
            actions.append("%s: would CREATE" % tname)
            behind += 1
            continue
        if thead == src_head:
            actions.append("%s: up to date" % tname)
            continue
        # count how many commits the target is missing
        rc, out, _ = P.run(["git", "ls-remote", url, "refs/heads/*"],
                           timeout=60)
        refs = [l.split() for l in out.strip().splitlines() if l.strip()]
        # count objects the target lacks by asking the source
        rc2, cnt, _ = P.run(
            ["git", "ls-remote", src_url, "refs/heads/*"], timeout=60)
        src_refs = {l.split()[1]: l.split()[0]
                    for l in cnt.strip().splitlines() if l.strip()}
        missing = [r for r, sha in refs if src_refs.get(r) != sha]
        if missing:
            actions.append("%s: %d ref(s) behind (%s)"
                           % (tname, len(missing), ", ".join(
                               m.split("/")[-1] for m in missing[:3])))
            behind += len(missing)
        else:
            actions.append("%s: would update" % tname)
    return actions, ("update" if behind else "ok")


ROUTE_STATE_FILE = "route_state.json"


def _route_state_path():
    C.ensure_dirs()
    return os.path.join(C.DATA_DIR, ROUTE_STATE_FILE)


def _load_route_state():
    try:
        with open(_route_state_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_route_state(state):
    try:
        with open(_route_state_path(), "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def route_due(route, cfg, now=None):
    """is this route due to run? (False = it ran too recently)"""
    import time as _t
    now = now or _t.time()
    key = C.route_schedule(cfg, route)
    hours = C.SCHEDULE_HOURS.get(key)
    if hours is None:            # "off" — timer never runs it
        return False, "schedule is off"
    last = _load_route_state().get(route.get("name"))
    if not last:
        return True, "never run"
    elapsed_h = (now - last) / 3600.0
    if elapsed_h >= hours:
        return True, "due (last run %.1fh ago)" % elapsed_h
    return False, "not due for %.1fh" % (hours - elapsed_h)


def mark_route_run(route, now=None):
    import time as _t
    st = _load_route_state()
    st[route.get("name")] = now or _t.time()
    _save_route_state(st)


def next_due_text(route, cfg, now=None):
    """human-readable 'next run' for a route"""
    import time as _t
    now = now or _t.time()
    key = C.route_schedule(cfg, route)
    hours = C.SCHEDULE_HOURS.get(key)
    if hours is None:
        return "off"
    last = _load_route_state().get(route.get("name"))
    if not last:
        return "on next timer run"
    due_at = last + hours * 3600
    delta = due_at - now
    if delta <= 0:
        return "now"
    if delta < 3600:
        return "in %d min" % int(delta / 60)
    if delta < 86400:
        return "in %.1f h" % (delta / 3600)
    return "in %.1f d" % (delta / 86400)


def _protection_error(err):
    """did the push fail because a branch is protected?"""
    e = (err or "").lower()
    return ("protected branch" in e or "pre-receive hook declined" in e
            or "protected branches" in e or "force push" in e
            or "not allowed to force push" in e)


def _short_error(err, limit=160):
    """the most useful line of a git error, not the wall of remote noise"""
    lines = [l.strip() for l in (err or "").strip().splitlines() if l.strip()]
    for l in lines:
        if l.lower().startswith("remote:"):
            l = l[7:].strip()
        if l and not l.startswith("To ") and not l.startswith("hint:"):
            return l[:limit]
    return (lines[0] if lines else "")[:limit]


def run_sync(cfg=None, route_name=None, quiet=False, only=None,
             dry_run=False, scheduled=False):
    """Run all routes (or one). Returns a summary dict.

    dry_run=True    computes what would change without touching anything.
    scheduled=True  skip routes whose own schedule says they are not due
                    yet (this is what the systemd timer uses).
    """
    global _start
    # one run at a time — the timer and manual runs must never overlap
    import fcntl
    C.ensure_dirs()
    lockf = open(os.path.join(C.DATA_DIR, "sync.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another gitsync run is already active — exiting")
        return {"repos": 0, "ok": 0, "partial": 0, "failed": 0,
                "failures": [], "elapsed_sec": 0,
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "routes": 0, "skipped": "locked"}

    _start = time.time()
    reset_progress()
    cfg = cfg or C.load_config()
    workers = int(C.setting(cfg, "workers", 4) or 4)
    mode = C.setting(cfg, "mode", "full")
    include_forks = bool(C.setting(cfg, "include_forks", False))

    selected = C.routes(cfg)
    if route_name:
        selected = [r for r in selected if r.get("name") == route_name]

    totals = {"routes": len(selected), "repos": 0, "ok": 0,
              "failed": 0, "partial": 0, "failures": [], "planned": [],
              "dry_run": dry_run, "skipped": 0}

    for route in selected:
        if scheduled:
            due, why = route_due(route, cfg)
            if not due:
                log("route '%s': skipped — %s" % (route.get("name"), why))
                totals["skipped"] = totals.get("skipped", 0) + 1
                continue
        mark_route_run(route)
        sname = route.get("source")
        sp = C.platform(cfg, sname)
        if not sp or not sp.get("enabled"):
            log("route '%s': source '%s' not enabled — skipped"
                % (route.get("name"), sname))
            continue
        source = P.make(sname, sp)
        targets = {}
        for t in route.get("targets", []):
            tp = C.platform(cfg, t)
            if tp and tp.get("enabled"):
                targets[t] = P.make(t, tp)
        if not targets:
            log("route '%s': no enabled targets — skipped" % route.get("name"))
            continue

        log("=== route '%s': %s -> %s (workers=%d, mode=%s%s) ==="
            % (route.get("name"), sname, ", ".join(targets), workers, mode,
               ", DRY RUN" if dry_run else ""))
        progress["route"] = route.get("name")
        try:
            repos = source.list_repos()
        except Exception as e:
            log("  cannot list repos on %s: %s" % (sname, e))
            totals["failures"].append({"route": route.get("name"),
                                       "repo": "-", "error": str(e)[:200]})
            continue
        repos = _filter_repos(repos, route, only)
        if not include_forks:
            # best-effort fork filter (platforms that expose it)
            repos = [r for r in repos if not r.get("fork", False)]
        totals["repos"] += len(repos)
        log("  %s: %d repos" % (sname, len(repos)))

        done = {"n": 0}
        dlock = threading.Lock()

        progress["total"] = len(repos)

        def work(repo):
            progress["active"][repo["name"]] = "syncing"
            try:
                if dry_run:
                    actions, verdict = _plan_repo(repo, source, targets, mode)
                    status, detail, errors = verdict, "", actions
                else:
                    status, detail, errors = sync_repo(repo, source, targets,
                                                       mode)
            finally:
                progress["active"].pop(repo["name"], None)
                progress["done"] += 1
            with dlock:
                done["n"] += 1
                idx = done["n"]
            if dry_run:
                log("  [%d/%d] PLAN %s" % (idx, len(repos), repo["name"]))
                for a in errors:
                    log("           - %s" % a)
                return repo, status, errors
            if status == "ok":
                log("  [%d/%d] OK   %s -> %s"
                    % (idx, len(repos), repo["name"], detail))
            elif status == "partial":
                log("  [%d/%d] PART %s (%s)"
                    % (idx, len(repos), repo["name"], detail))
            else:
                log("  [%d/%d] FAIL %s (%s)"
                    % (idx, len(repos), repo["name"], detail))
            return repo, status, errors

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, r) for r in repos]
            for f in as_completed(futs):
                try:
                    repo, status, errors = f.result()
                except Exception as e:
                    totals["failed"] += 1
                    totals["failures"].append({"route": route.get("name"),
                                               "repo": "?", "error": str(e)[:200]})
                    continue
                if dry_run:
                    totals["planned"].append(
                        {"route": route.get("name"), "repo": repo["name"],
                         "verdict": status, "actions": errors})
                    if status == "update":
                        totals["ok"] += 1
                    continue
                if status == "ok":
                    totals["ok"] += 1
                elif status == "partial":
                    totals["partial"] += 1
                    totals["failures"].append(
                        {"route": route.get("name"), "repo": repo["name"],
                         "error": "; ".join(errors)[:200]})
                else:
                    totals["failed"] += 1
                    totals["failures"].append(
                        {"route": route.get("name"), "repo": repo["name"],
                         "error": "; ".join(errors)[:200]})

    elapsed = round(time.time() - _start, 1)
    totals["elapsed_sec"] = elapsed
    totals["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if dry_run:
        n_upd = sum(1 for p in totals["planned"]
                    if p["verdict"] == "update")
        log("=== dry run done: %d of %d repos would change (%ss) ==="
            % (n_upd, totals["repos"], elapsed))
    else:
        log("=== sync done: %d ok, %d partial, %d failed of %d repos in %ss ==="
            % (totals["ok"], totals["partial"], totals["failed"],
               totals["repos"], elapsed))
    _record(totals)
    try:
        if NT.enabled(cfg):
            NT.notify_run(cfg, totals)
    except Exception as e:
        log("  notification failed: %s" % e)
    return totals


def _record(summary):
    try:
        C.ensure_dirs()
        records = []
        if os.path.exists(C.RECORDS_FILE):
            try:
                records = json.load(open(C.RECORDS_FILE))
            except (OSError, ValueError):
                records = []
        records.append(summary)
        records = records[-200:]          # keep the last 200 runs
        tmp = C.RECORDS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(records, f, indent=2)
        os.replace(tmp, C.RECORDS_FILE)
    except OSError:
        pass


import json  # noqa: E402  (used by _record)


def last_records(n=10):
    try:
        if os.path.exists(C.RECORDS_FILE):
            return json.load(open(C.RECORDS_FILE))[-n:]
    except (OSError, ValueError):
        pass
    return []
