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
        err = _push_target(tname, tplat, repo, hub, mode)
        if err:
            errors.append(err)
        else:
            pushed.append(tname)

    if errors and not pushed:
        return "push-failed", "; ".join(errors), errors
    if errors:
        return "partial", "; ".join(errors), errors
    return "ok", ",".join(pushed), []


PUSH_REFS = ["+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"]


def _push_target(tname, tplat, repo, hub, mode, refspecs=None):
    """push the hub to one target. Returns None on success, else an error.

    refspecs pins exactly which refs travel (used by a mesh that is skipping
    a conflicted branch: the wildcard push would otherwise carry the hub's
    stale copy of it and overwrite the newer commit on the peer).
    """
    try:
        push_url = tplat.ensure_repo(repo)
        if refspecs is not None:
            if not refspecs:
                return None               # nothing is safe to push
            cmd = ["git", "push", "--force", push_url] + refspecs
        elif tplat.ptype in ("forgejo", "gitea"):
            # GitHub's refs/pull/* namespace collides with Forgejo's own
            # PR storage and its pre-receive hook rejects the whole push
            cmd = ["git", "push", "--force", push_url] + PUSH_REFS
        elif mode == "full":
            cmd = ["git", "push", "--mirror", push_url]
        else:
            cmd = ["git", "push", "--force", push_url] + PUSH_REFS
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
            return "%s: %s" % (tname, _short_error(err))
        if tplat.ptype == "local":
            # git init --bare defaults HEAD to master; point it at the
            # branch we actually pushed so clones are not empty
            rc2, head_ref, _ = P.run(["git", "symbolic-ref", "HEAD"],
                                     cwd=hub, timeout=30)
            if rc2 == 0 and head_ref.strip():
                rc3, exists, _ = P.run(["git", "-C", push_url, "rev-parse",
                                        "--verify", "--quiet",
                                        head_ref.strip()], timeout=30)
                if rc3 == 0 and exists.strip():
                    P.run(["git", "-C", push_url, "symbolic-ref",
                           "HEAD", head_ref.strip()], timeout=30)
        return None
    except Exception as e:
        return "%s: %s" % (tname, str(e)[:120])


# ---------------------------------------------------------------- mesh sync
#
# A one-way route trusts one source. A mesh route trusts all of its peers:
# every repo is fetched from each of them, the newest commit wins per branch
# and per tag, and the result is pushed back to everyone. That is what makes
# "change it on GitLab, see it on GitHub" work without a fixed source.

MESH_NS = "refs/gitsync-mesh"


def _ls_remote_map(url, timeout=90):
    """{refname: sha} for one remote, or None when it cannot be reached"""
    rc, out, _ = P.run(["git", "ls-remote", url], timeout=timeout)
    if rc != 0:
        return None
    refs = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            refs[parts[1]] = parts[0]
    return refs


def _newest(cands):
    """the entry with the newest date; ties keep the first (source order)"""
    best = cands[0]
    for c in cands[1:]:
        if c[0] > best[0]:
            best = c
    return best


def _peer_refs(hub, peer):
    """({branch: (date, sha)}, {tag: (date, sha)}) fetched from one peer"""
    heads, tags = {}, {}
    prefix = "%s/%s/" % (MESH_NS, peer)
    rc, out, _ = P.run(
        ["git", "for-each-ref",
         "--format=%(refname)%09%(objectname)%09%(committerdate:unix)"
         "%09%(creatordate:unix)", prefix], cwd=hub, timeout=120)
    if rc != 0:
        return heads, tags
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        ref, sha, cdate, adate = parts[0], parts[1], parts[2], parts[3]
        try:
            date = int(cdate or adate or 0)
        except ValueError:
            date = 0
        if ref.startswith(prefix + "heads/"):
            heads[ref[len(prefix) + 6:]] = (date, sha)
        elif ref.startswith(prefix + "tags/"):
            tags[ref[len(prefix) + 5:]] = (date, sha)
    return heads, tags


def _set_ref(hub, ref, sha):
    """point ref at sha; returns True when that changed something"""
    rc, cur, _ = P.run(["git", "rev-parse", "--verify", "--quiet", ref],
                       cwd=hub, timeout=60)
    if rc == 0 and cur.strip() == sha:
        return False
    P.run(["git", "update-ref", ref, sha], cwd=hub, timeout=60)
    return True


def _drop_absent(hub, space, keep):
    """delete refs/<space>/* that no peer has any more"""
    dropped = 0
    rc, out, _ = P.run(["git", "for-each-ref", "--format=%(refname)",
                        "refs/%s" % space], cwd=hub, timeout=60)
    if rc != 0:
        return 0
    for ref in out.split():
        short = ref.split("/", 2)[2]
        if short in keep:
            continue
        P.run(["git", "update-ref", "-d", ref], cwd=hub, timeout=60)
        dropped += 1
    return dropped


def mesh_sync_repo(repo, peers, conflict="newest"):
    """all-direction sync: newest commit wins per branch and per tag.

    Peers' refs are fetched into a private namespace in the hub, the hub
    takes the newest version of every branch/tag, and the result is pushed
    to every peer. Nothing is deleted on a peer unless no peer has the ref
    any more (union semantics), so a mesh never resurrects a branch that was
    intentionally deleted everywhere.

    conflict="newest" (default) resolves a disagreement by date, which is
    what makes "edit it on GitLab, see it on GitHub" work. conflict="skip"
    instead leaves any ref the peers disagree about exactly as it is and
    reports it, so a mesh can never quietly overwrite one platform's commit
    with another's — reconcile by hand, then let the next run converge.
    """
    name = repo["name"]
    hub = mirror_path(name)
    errors = []
    reachable = {}
    for pname, plat in peers.items():
        try:
            url = plat.repo_url(repo)
        except Exception as e:
            errors.append("%s: %s" % (pname, str(e)[:100]))
            continue
        refs = _ls_remote_map(url)
        if refs is None:
            continue                      # repo not created there yet
        reachable[pname] = (url, refs)

    if not reachable:
        return "push-failed", "; ".join(errors) or "no peer reachable", errors

    # fast path: every peer already holds exactly the same refs
    if len(reachable) == len(peers):
        first = next(iter(reachable.values()))[1]
        if all(refs == first for _, refs in reachable.values()):
            return "ok", "in sync on %d peers" % len(reachable), []

    if os.path.isdir(hub) and not mirror_is_valid(hub):
        P.run(["rm", "-rf", hub])
    if not os.path.isdir(hub):
        rc, _, err = P.run(["git", "init", "--bare", hub])
        if rc != 0:
            return "clone-failed", err.strip()[:200], []

    # 1. fetch every reachable peer into the private namespace
    for pname, (url, _refs) in reachable.items():
        rc, _, err = P.run(
            ["git", "fetch", "--prune", "--force", "--no-tags", url,
             "+refs/heads/*:%s/%s/heads/*" % (MESH_NS, pname),
             "+refs/tags/*:%s/%s/tags/*" % (MESH_NS, pname)],
            cwd=hub, timeout=P.GIT_TIMEOUT)
        if rc != 0:
            errors.append("%s: fetch failed: %s" % (pname,
                                                    _short_error(err)))

    # 2. newest commit wins, per branch and per tag
    cand_heads, cand_tags = {}, {}
    for pname in reachable:
        ph, pt = _peer_refs(hub, pname)
        for b, cand in ph.items():
            cand_heads.setdefault(b, []).append(cand)
        for t, cand in pt.items():
            cand_tags.setdefault(t, []).append(cand)

    chosen_heads, skip_heads = _choose(cand_heads, conflict)
    chosen_tags, skip_tags = _choose(cand_tags, conflict)
    skipped = skip_heads + skip_tags

    updated = 0
    for b, (_d, sha) in chosen_heads.items():
        if _set_ref(hub, "refs/heads/" + b, sha):
            updated += 1
    for t, (_d, sha) in chosen_tags.items():
        if _set_ref(hub, "refs/tags/" + t, sha):
            updated += 1
    # a skipped ref must not be pruned either - leaving it alone is the point
    _drop_absent(hub, "heads", list(chosen_heads) + skip_heads)
    _drop_absent(hub, "tags", list(chosen_tags) + skip_tags)

    # 3. keep the hub's HEAD on a branch that actually exists
    rc, cur, _ = P.run(["git", "symbolic-ref", "HEAD"], cwd=hub, timeout=30)
    target = cur.strip()[len("refs/heads/"):] if rc == 0 else ""
    if target not in chosen_heads and chosen_heads:
        for cand in ("main", "master"):
            if cand in chosen_heads:
                P.run(["git", "symbolic-ref", "HEAD", "refs/heads/" + cand],
                      cwd=hub, timeout=30)
                break

    # 4. push the merged hub back to every peer
    refspecs = None
    if skipped:
        # a skipped ref is left exactly as it is on every peer, so it must
        # not travel in the push either
        refspecs = (["+refs/heads/%s:refs/heads/%s" % (b, b)
                     for b in chosen_heads]
                    + ["+refs/tags/%s:refs/tags/%s" % (t, t)
                       for t in chosen_tags])
    pushed = []
    for pname, plat in peers.items():
        err = _push_target(pname, plat, repo, hub, "safe", refspecs)
        if err:
            errors.append(err)
        else:
            pushed.append(pname)

    if errors and not pushed:
        return "push-failed", "; ".join(errors), errors
    if errors:
        return "partial", "; ".join(errors), errors
    detail = "%s (%d refs merged)" % (",".join(pushed), updated)
    if skipped:
        detail += ", %d conflict(s) skipped: %s" % (
            len(skipped), ", ".join(sorted(skipped)[:4]))
    return "ok", detail, []


def _choose(cands, conflict):
    """({ref: (date, sha)}, [skipped refs]) for one namespace

    "newest" takes the newest candidate. "skip" keeps only refs every peer
    agrees on, and reports the rest instead of overwriting anyone.
    """
    out, skipped = {}, []
    for ref, options in cands.items():
        if conflict == "skip" and len({s for _d, s in options}) > 1:
            skipped.append(ref)
            continue
        out[ref] = _newest(options)
    return out, skipped


def mesh_plan_repo(repo, peers, conflict="newest"):
    """read-only plan for a mesh route: do the peers agree?"""
    heads = {}
    for pname, plat in peers.items():
        try:
            url = plat.repo_url(repo)
        except Exception as e:
            return ["%s: %s" % (pname, str(e)[:80])], "error"
        refs = _ls_remote_map(url)
        if refs is None:
            continue
        heads[pname] = refs.get("HEAD") or next(iter(sorted(refs.values())),
                                                "none")
    if not heads:
        return ["no peer has this repo"], "error"
    distinct = set(heads.values())
    if len(distinct) == 1 and len(heads) == len(peers):
        return ["all %d peers agree (%s)" % (len(heads),
                                             next(iter(distinct))[:8])], "ok"
    detail = ", ".join("%s %s" % (p, h[:8]) for p, h in heads.items())
    if conflict == "skip":
        return ["peers disagree - would SKIP (newest wins is off): %s"
                % detail], "update"
    return ["would merge: %s" % detail], "update"


def active_targets(cfg, route):
    """{target_name: platform_cfg} for this route, paused ones excluded"""
    paused = set(route.get("paused_targets") or [])
    out = {}
    for t in route.get("targets", []):
        if t in paused:
            continue
        tp = C.platform(cfg, t)
        if tp and tp.get("enabled"):
            out[t] = tp
    return out


def route_peers(cfg, route):
    """mesh peers: the source plus every target that is not paused"""
    names = [route.get("source")]
    names += [t for t in active_targets(cfg, route)]
    out = {}
    for n in names:
        p = C.platform(cfg, n)
        if p and p.get("enabled"):
            out[n] = p
    return out


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

    In "full" mode the push is `--mirror`, which *deletes* refs the target
    has and the source does not. That is the one part of a sync that can
    lose work, so a dry run names every ref that would be pruned.
    """
    name = repo["name"]
    src_url = repo.get("clone_url")
    src_refs = _ls_remote_map(src_url) if src_url else {}
    if not src_refs:
        return ["cannot reach source %s" % name], "error"
    src_head = src_refs.get("HEAD")

    hub = mirror_path(name)
    actions = []
    if os.path.isdir(hub) and mirror_is_valid(hub):
        hub_head = _remote_head(hub)
        if hub_head == src_head:
            actions.append("hub up to date")
        else:
            actions.append("hub: %s -> %s" % ((hub_head or "none")[:8],
                                              (src_head or "?")[:8]))
    else:
        actions.append("hub: would clone (%s)" % (src_head or "?")[:8])

    behind = 0
    for tname, tplat in targets.items():
        try:
            url = tplat.repo_url(repo)
        except Exception as e:
            actions.append("%s: cannot probe (%s)" % (tname, str(e)[:60]))
            behind += 1
            continue
        trefs = _ls_remote_map(url)
        thead = trefs.get("HEAD")
        if not trefs:
            actions.append("%s: would CREATE" % tname)
            behind += 1
            continue
        if thead == src_head:
            actions.append("%s: up to date" % tname)
        else:
            missing = [r for r, sha in trefs.items()
                       if src_refs.get(r) != sha
                       and r.startswith(("refs/heads/", "refs/tags/"))]
            if missing:
                actions.append("%s: %d ref(s) behind (%s)"
                               % (tname, len(missing), ", ".join(
                                   m.split("/")[-1] for m in missing[:3])))
                behind += len(missing)
            else:
                actions.append("%s: would update" % tname)
        # refs the target has and the source does not: pruned by --mirror
        if mode == "full":
            doomed = [r for r in trefs
                      if r.startswith(("refs/heads/", "refs/tags/"))
                      and r not in src_refs]
            if doomed:
                behind += len(doomed)
                shown = ", ".join(sorted(
                    r.split("/", 2)[-1] for r in doomed)[:4])
                more = "" if len(doomed) <= 4 else " +%d more" % (
                    len(doomed) - 4)
                actions.append(
                    "%s: WOULD DELETE %d ref(s): %s%s"
                    % (tname, len(doomed), shown, more))
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


def last_route_run(name):
    """unix time this route last ran, or None"""
    return _load_route_state().get(name)


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
    include_forks = bool(C.setting(cfg, "include_forks", False))
    pace = float(C.setting(cfg, "pace", 0) or 0)

    selected = C.routes(cfg)
    if route_name:
        selected = [r for r in selected if r.get("name") == route_name]

    totals = {"routes": len(selected), "repos": 0, "ok": 0,
              "failed": 0, "partial": 0, "failures": [], "planned": [],
              "dry_run": dry_run, "skipped": 0}

    for route in selected:
        rname = route.get("name")
        if route.get("enabled") is False:
            log("route '%s': paused — skipped" % rname)
            totals["skipped"] = totals.get("skipped", 0) + 1
            continue
        if scheduled:
            due, why = route_due(route, cfg)
            if not due:
                log("route '%s': skipped — %s" % (rname, why))
                totals["skipped"] = totals.get("skipped", 0) + 1
                continue
        sname = route.get("source")
        sp = C.platform(cfg, sname)
        if not sp or not sp.get("enabled"):
            log("route '%s': source '%s' not enabled — skipped"
                % (rname, sname))
            continue
        source = P.make(sname, sp)

        mesh = route.get("direction") == "mesh"
        peers = {}
        if mesh:
            peers = {n: P.make(n, p)
                     for n, p in route_peers(cfg, route).items()}
        targets = {}
        if mesh:
            if len(peers) < 2:
                log("route '%s': mesh needs at least two live peers — skipped"
                    % rname)
                continue
        else:
            for t, tp in active_targets(cfg, route).items():
                targets[t] = P.make(t, tp)
            paused = [t for t in (route.get("paused_targets") or [])
                      if t in (route.get("targets") or [])]
            if paused:
                log("  paused target(s): %s" % ", ".join(paused))
            if not targets:
                log("route '%s': no enabled targets — skipped" % rname)
                continue

        # a route may override the global worker count, mirror mode and
        # (for mesh) the conflict policy; the global values stay defaults
        workers = int(route.get("workers")
                      or C.setting(cfg, "workers", 4) or 4)
        mode = route.get("mode") or C.setting(cfg, "mode", "full")
        conflict = route.get("mesh_conflict") or \
            C.setting(cfg, "mesh_conflict", "newest")

        mark_route_run(route)
        if mesh:
            log("=== route '%s': mesh %s (workers=%d%s) ==="
                % (rname, ", ".join(peers), workers,
                   ", DRY RUN" if dry_run else ""))
        else:
            log("=== route '%s': %s -> %s (workers=%d, mode=%s%s) ==="
                % (rname, sname, ", ".join(targets), workers, mode,
                   ", DRY RUN" if dry_run else ""))
        progress["route"] = rname
        try:
            repos = source.list_repos()
        except Exception as e:
            log("  cannot list repos on %s: %s" % (sname, e))
            totals["failures"].append({"route": rname,
                                       "repo": "-", "error": str(e)[:200]})
            continue
        repos = _filter_repos(repos, route, only)
        if not include_forks:
            # best-effort fork filter (platforms that expose it)
            repos = [r for r in repos if not r.get("fork", False)]
        totals["repos"] += len(repos)
        log("  %s: %d repos" % ("mesh" if mesh else sname, len(repos)))

        done = {"n": 0}
        dlock = threading.Lock()

        progress["total"] = len(repos)

        def work(repo):
            progress["active"][repo["name"]] = "syncing"
            try:
                if dry_run:
                    if mesh:
                        actions, verdict = mesh_plan_repo(repo, peers,
                                                          conflict)
                    else:
                        actions, verdict = _plan_repo(repo, source, targets,
                                                     mode)
                    status, detail, errors = verdict, "", actions
                elif mesh:
                    status, detail, errors = mesh_sync_repo(repo, peers,
                                                           conflict)
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

        if pace:
            log("  pacing: %.1fs between repos" % pace)

        def _paced_work(r):
            out = work(r)
            if pace:
                time.sleep(pace)
            return out

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_paced_work, r) for r in repos]
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
