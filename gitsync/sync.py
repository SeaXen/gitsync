"""Sync engine: source platform -> local mirror hub -> target platforms.

Design (proven): every repo is first mirrored into the local hub as a bare
repo, then pushed from the hub to each target. The hub makes the sync
resumable and gives an offline copy for free.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from . import config as C
from . import platforms as P

_log_lock = threading.Lock()
_start = None


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
        rc, _, err = P.run(["git", "remote", "update", "--prune"], cwd=hub)
        if rc != 0:
            return "mirror-update-failed", err.strip()[:200], []
    else:
        rc, _, err = P.run(["git", "clone", "--mirror", src_url, hub])
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
            rc, _, err = P.run(cmd, cwd=hub)
            if rc != 0:
                errors.append("%s: %s" % (tname, err.strip()[:120]))
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


def run_sync(cfg=None, route_name=None, quiet=False):
    """Run all routes (or one). Returns a summary dict."""
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
    cfg = cfg or C.load_config()
    workers = int(C.setting(cfg, "workers", 4) or 4)
    mode = C.setting(cfg, "mode", "full")
    include_forks = bool(C.setting(cfg, "include_forks", False))

    selected = C.routes(cfg)
    if route_name:
        selected = [r for r in selected if r.get("name") == route_name]

    totals = {"routes": len(selected), "repos": 0, "ok": 0,
              "failed": 0, "partial": 0, "failures": []}

    for route in selected:
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

        log("=== route '%s': %s -> %s (workers=%d, mode=%s) ==="
            % (route.get("name"), sname, ", ".join(targets), workers, mode))
        try:
            repos = source.list_repos()
        except Exception as e:
            log("  cannot list repos on %s: %s" % (sname, e))
            totals["failures"].append({"route": route.get("name"),
                                       "repo": "-", "error": str(e)[:200]})
            continue
        if not include_forks:
            # best-effort fork filter (platforms that expose it)
            repos = [r for r in repos if not r.get("fork", False)]
        totals["repos"] += len(repos)
        log("  %s: %d repos" % (sname, len(repos)))

        done = {"n": 0}
        dlock = threading.Lock()

        def work(repo):
            status, detail, errors = sync_repo(repo, source, targets, mode)
            with dlock:
                done["n"] += 1
                idx = done["n"]
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
    log("=== sync done: %d ok, %d partial, %d failed of %d repos in %ss ==="
        % (totals["ok"], totals["partial"], totals["failed"],
           totals["repos"], elapsed))
    _record(totals)
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
