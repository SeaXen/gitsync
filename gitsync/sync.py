"""Sync engine: source platform -> local mirror hub -> target platforms.

Design (proven): every repo is first mirrored into the local hub as a bare
repo, then pushed from the hub to each target. The hub makes the sync
resumable and gives an offline copy for free.
"""

import atexit
import fnmatch
import json
import os
import re as _re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from . import config as C
from . import diskutil as DK
from . import notify as NT
from . import platforms as P


def split_url_auth(url):
    """Split (clean_url, user, token) from a git clone/push URL."""
    if not url or not isinstance(url, str):
        return url, None, None
    if not (url.startswith("http://") or url.startswith("https://")):
        return url, None, None
    parsed = urllib.parse.urlsplit(url)
    if "@" not in parsed.netloc:
        return url, None, None
    userinfo, host = parsed.netloc.rsplit("@", 1)
    user = None
    token = None
    if ":" in userinfo:
        user, token = userinfo.split(":", 1)
        user = urllib.parse.unquote(user)
        token = urllib.parse.unquote(token)
    else:
        token = urllib.parse.unquote(userinfo)
    clean_url = urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return clean_url, user, token


_HELPER_WRAPPERS = {}


def ensure_executable_helper(helper):
    """Ensure a helper script (GIT_ASKPASS) is executable.

    If on POSIX and not executable, tries os.chmod. If chmod fails or the file
    is still not executable (e.g. read-only mount), falls back to a generated
    temporary wrapper script (chmod 0700) invoking sys.executable.
    """
    if os.name == "nt":
        return helper
    try:
        st = os.stat(helper)
        if not (st.st_mode & 0o111):
            os.chmod(helper, st.st_mode | 0o111)
    except Exception:
        pass
    if os.access(helper, os.X_OK):
        return helper
    if helper in _HELPER_WRAPPERS and os.path.exists(_HELPER_WRAPPERS[helper]):
        return _HELPER_WRAPPERS[helper]
    td = tempfile.mkdtemp(prefix="gitsync_helper_")
    wrapper = os.path.join(td, os.path.basename(helper) + ".sh")
    with open(wrapper, "w", encoding="utf-8") as f:
        f.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, helper))
    try:
        os.chmod(wrapper, 0o700)
    except Exception:
        pass
    _HELPER_WRAPPERS[helper] = wrapper
    atexit.register(lambda d=td: shutil.rmtree(d, ignore_errors=True))
    return wrapper


def askpass_env(user=None, token=None, base_env=None):
    """Build an env dict configuring GIT_ASKPASS with safe credentials."""
    env = dict(base_env if base_env is not None else os.environ)
    here = os.path.dirname(os.path.abspath(__file__))
    helper = os.path.join(here, "askpass.bat" if os.name == "nt" else "askpass.py")
    helper = ensure_executable_helper(helper)
    env["GIT_ASKPASS"] = helper
    env["GIT_TERMINAL_PROMPT"] = "0"
    if "GIT_CONFIG_PARAMETERS" not in env:
        env["GIT_CONFIG_PARAMETERS"] = "'credential.helper='"
    if os.name == "nt":
        env["GITSYNC_PYTHON"] = sys.executable
    if user:
        env["GITSYNC_ASKPASS_USER"] = user
    if token:
        env["GITSYNC_ASKPASS_TOKEN"] = token
    return env

# the run lock lives here; web.py probes it so the dashboard can report a
# timer/webhook sync that is already running (one owner for the lock's path)
SYNC_LOCK = "sync.lock"

# the live per-repo progress line the engine prints — "[12/54] OK repo -> t".
# One parser, shared by the TUI and the web UI, so a tweak to the format
# cannot silently zero one consumer's progress bar.
_PROGRESS_RE = _re.compile(r"\[(\d+)\s*/\s*(\d+)\]\s+(\S+)\s+(\S+)")


def parse_progress_line(line):
    """(done, total, status, repo) from a sync stdout line, or None."""
    m = _PROGRESS_RE.search(line or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)


def engine_busy():
    """is a sync (this process, the timer, or the webhook receiver) holding
    the engine lock? Read-only probe; never blocks."""
    try:
        import fcntl
    except ImportError:
        return False
    path = os.path.join(C.DATA_DIR, SYNC_LOCK)
    try:
        fh = open(path, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        try:
            fh.close()
        except OSError:
            pass


# public alias: the web layer redacts on top of this, and should not reach
# into a private symbol (see gitsync/web.py Redactor)
def redact(text):
    """public wrapper for the engine's token-URL redaction."""
    return _redact(text)

_log_lock = threading.Lock()
_start = None

# live per-repo progress, read by the TUI while a sync runs
progress = {"active": {}, "done": 0, "total": 0, "route": ""}


def reset_progress():
    progress["active"] = {}
    progress["done"] = 0
    progress["total"] = 0


def log(msg):
    # redacted: git error text echoes the remote URL, and the URL carries
    # the token. The file is 0600 for the same reason.
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        _redact(str(msg)))
    with _log_lock:
        print(line, flush=True)
        try:
            C.ensure_dirs()
            _rotate_log()
            with open(C.log_file(), "a") as f:
                f.write(line + "\n")
            os.chmod(C.log_file(), 0o600)
        except OSError:
            pass


LOG_MAX_BYTES = 2 * 1024 * 1024      # 2 MB, one backup


def _rotate_log():
    """cap gitsync.log at 2 MB by renaming it to gitsync.log.1.

    A sync runs every few hours forever; without this the log grows without
    bound and `read_log_tail` pays for it on every TUI frame.
    """
    try:
        if os.path.getsize(C.log_file()) < LOG_MAX_BYTES:
            return
        os.replace(C.log_file(), C.log_file() + ".1")
    except OSError:
        pass


def _rmtree(path):
    """delete a mirror directory without shelling out to `rm -rf`.

    A path that starts with "-" cannot be mistaken for an option this way,
    and it works the same on every platform git itself supports.
    """
    import stat
    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    shutil.rmtree(path, onerror=_onerror)


def hub_rel(repo_id):
    """'group/docs' -> 'gitlab/group__docs': the hub path inside MIRROR_DIR.

    Keyed by the repo's stable id, not its short name: two projects called
    `docs` in different groups used to share one mirror and overwrite each
    other.
    """
    return P._fs_unique(str(repo_id).replace("/", "__"))


def mirror_path(repo, ptype, claimants=None):
    """hub path for one repo, migrating the legacy flat layout read-only.

    New layout:  mirrors/{type}/{id}.git
    Legacy:      mirrors/{name}.git

    If the new path does not exist and exactly one legacy mirror with this
    repo's short name does, the legacy one is used as-is - never deleted, so
    an upgrade keeps the history. If two live repos would claim the same
    legacy directory the caller must skip both (pass claimants), rather than
    mix two histories into one mirror.
    """
    if isinstance(repo, str):                     # old call shape: a name
        repo = {"name": repo}
    rid = repo.get("id") or repo.get("name") or ""
    name = repo.get("name") or rid
    new_path = os.path.join(C.MIRROR_DIR, ptype, hub_rel(rid) + ".git")
    if os.path.isdir(new_path):
        return new_path
    legacy = os.path.join(C.MIRROR_DIR, P._fs_safe(name) + ".git")
    if os.path.isdir(legacy):
        if claimants and len(claimants) > 1:
            log("  %s: %d repos share the legacy mirror %s - skipping"
                % (name, len(claimants), os.path.basename(legacy)))
            return None
        return legacy
    return new_path


def mirror_is_valid(path):
    """is this a usable bare mirror?

    Zero refs is not a failure: an empty source repo is legitimately empty.
    Requiring at least one ref made an empty repo get rm -rf'd and re-cloned
    on every single run. A bare repo git can talk to is valid.
    """
    if not os.path.isdir(path):
        return False
    rc, out, _ = P.run(["git", "rev-parse", "--is-bare-repository"],
                       cwd=path, timeout=60)
    return rc == 0 and out.strip() == "true"


def sync_repo(repo, source, targets, mode, claimants=None, relax=False):
    """one repo: source -> hub -> targets. Returns (status, detail, errors)."""
    name = repo["name"]
    src_url = repo["clone_url"]
    hub = mirror_path(repo, source.ptype, claimants)
    if hub is None:
        return "skipped", "legacy mirror name collision", []
    errors = []

    # 1. hub mirror
    if os.path.isdir(hub) and not mirror_is_valid(hub):
        log("  removing incomplete mirror for %s" % name)
        _rmtree(hub)
    clean_src_url, src_user, src_token = split_url_auth(src_url)
    genv = source.git_env() if hasattr(source, "git_env") else None
    if src_token or (genv and "GIT_ASKPASS" in genv):
        genv = askpass_env(src_user, src_token, base_env=genv)

    if os.path.isdir(hub):
        P.run(["git", "remote", "set-url", "origin", clean_src_url], cwd=hub)
        _secure_hub_config(hub)
        rc, _, err = P.run(["git", "remote", "update", "--prune"], cwd=hub,
                            timeout=P.GIT_TIMEOUT, env=genv)
        if rc != 0:
            return "mirror-update-failed", _redact(err.strip())[:200], []
    else:
        rc, _, err = P.run(["git", "clone", "--mirror", clean_src_url, hub],
                            timeout=P.GIT_TIMEOUT, env=genv)
        if rc != 0:
            _rmtree(hub)
            return "clone-failed", _redact(err.strip())[:200], []
        P.run(["git", "remote", "set-url", "origin", clean_src_url], cwd=hub)
        _secure_hub_config(hub)

    # LFS/submodule awareness: refs are synced either way, so this is a log
    # line, not a failure — red runs people cannot fix train them to ignore
    # red runs. A target clone missing its LFS blobs is still worth saying.
    _note_extras(hub, name)

    # 2. push to each target
    pushed = []
    for tname, tplat in targets.items():
        err = _push_target(tname, tplat, repo, hub, mode, relax=relax)
        if err:
            errors.append(err)
        else:
            pushed.append(tname)

    if errors and not pushed:
        return "push-failed", "; ".join(errors), errors
    if errors:
        return "partial", "; ".join(errors), errors
    return "ok", ",".join(pushed), []


# only heads and tags ever travel to a non-local host. --mirror also copies
# refs/pull/* and other host-specific namespaces, which those hosts reject or
# store as junk (and Forgejo's collide with its own PR storage).
HEAD_TAGS = ["refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*"]


def push_args(mode, ptype, url, refspecs=None):
    """the git push argv for one target. Pure, so tests can assert it.

    safe  -> no --force, no `+`, no --mirror. A non-fast-forward is refused
             by the remote and reported, never retried with force.
    full  -> force-update heads and tags, and prune target heads/tags the
             hub does not have. Still never --mirror off a local target.
    local -> --mirror is allowed: it is our own bare copy.

    Forgejo/Gitea get the same treatment as every other host: the special
    case that always force-pushed is gone.
    """
    if refspecs is not None:
        if not refspecs:
            return []                       # nothing is safe to push
        return ["git", "push", "--force", url] + refspecs
    if ptype == "local" and mode == "full":
        return ["git", "push", "--mirror", url]
    if mode == "full":
        return ["git", "push", "--force", "--prune", url] + HEAD_TAGS
    return ["git", "push", url] + HEAD_TAGS


def _secure_hub_config(hub):
    """the hub's git config holds the remote URL, which holds the token.

    git creates it with the process umask (world-readable by default), so
    tighten it after any write that could have touched it.
    """
    try:
        os.chmod(os.path.join(hub, "config"), 0o600)
    except OSError:
        pass


def _push_target(tname, tplat, repo, hub, mode, refspecs=None,
                 relax=False):
    """push the hub to one target. Returns None on success, else an error.

    refspecs pins exactly which refs travel (used by a mesh that is skipping
    a conflicted branch: the wildcard push would otherwise carry the hub's
    stale copy of it and overwrite the newer commit on the peer).
    """
    try:
        push_url = tplat.ensure_repo(repo)
        # An empty repo has no refs, and `git push` with none to push says
        # "No refs in common and none specified; doing nothing" and exits
        # non-zero — which turned every empty source repo into a red run
        # (P2-3 made them visible; this makes them sync). The repo is still
        # created on the target, because the source does have it.
        rc0, refs, _ = P.run(["git", "for-each-ref", "--format=%(refname)"],
                             cwd=hub, timeout=30)
        if rc0 == 0 and not refs.strip():
            return None
        clean_push_url, dst_user, dst_token = split_url_auth(push_url)
        cmd = push_args(mode, tplat.ptype, clean_push_url, refspecs)
        if not cmd:
            return None
        tenv = tplat.git_env() if hasattr(tplat, "git_env") else None
        if dst_token or (tenv and "GIT_ASKPASS" in tenv):
            tenv = askpass_env(dst_user, dst_token, base_env=tenv)
        rc, _, err = P.run(cmd, cwd=hub, timeout=P.GIT_TIMEOUT, env=tenv)
        if rc != 0 and _protection_error(err):
            # A protected branch refused the push. Turning on force-push
            # there is a lasting security change on the destination, so it
            # only happens when the user asked for it
            # (settings.relax_protection). Otherwise say so and move on.
            if not relax:
                return ("%s: push rejected by branch protection. gitsync "
                        "will not change it. Set settings.relax_protection="
                        "true only if you mean to." % tname)
            try:
                for note in tplat.prepare_push(repo, push_url) or []:
                    log("    %s" % note)
                rc, _, err = P.run(cmd, cwd=hub, timeout=P.GIT_TIMEOUT,
                                   env=tenv)
            except Exception as e:
                log("    %s: prepare_push failed: %s"
                    % (tname, str(e)[:100]))
        if rc != 0:
            if "non-fast-forward" in (err or "") or \
                    "fetch first" in (err or ""):
                # safe mode must never fall back to force
                return ("%s: refused non-fast-forward (run mode=full to "
                        "overwrite)" % tname)
            return "%s: %s" % (tname, _short_error(err))
        if tplat.ptype == "local":
            # git init --bare defaults HEAD to master; point it at the
            # branch we actually pushed so clones are not empty
            rc2, head_ref, _ = P.run(["git", "symbolic-ref", "HEAD"],
                                     cwd=hub, timeout=30)
            if rc2 == 0 and head_ref.strip():
                rc3, exists, _ = P.run(["git", "-C", clean_push_url, "rev-parse",
                                        "--verify", "--quiet",
                                        head_ref.strip()], timeout=30)
                if rc3 == 0 and exists.strip():
                    P.run(["git", "-C", clean_push_url, "symbolic-ref",
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


def _ls_remote_map(url, timeout=90, env=None):
    """{refname: sha} for one remote, or None when it cannot be reached"""
    clean_url, user, token = split_url_auth(url)
    if token or (env and "GIT_ASKPASS" in env):
        env = askpass_env(user, token, base_env=env)
    rc, out, _ = P.run(["git", "ls-remote", clean_url], timeout=timeout, env=env)
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


def mesh_sync_repo(repo, peers, conflict="newest", claimants=None,
                   relax=False):
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
    hub = mirror_path(repo, next(iter(peers.values())).ptype, claimants)
    if hub is None:
        return "skipped", "legacy mirror name collision", []
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
        _rmtree(hub)
    if not os.path.isdir(hub):
        rc, _, err = P.run(["git", "init", "--bare", hub])
        if rc != 0:
            return "clone-failed", _redact(err.strip())[:200], []
        _secure_hub_config(hub)

    # 1. fetch every reachable peer into the private namespace
    for pname, (url, _refs) in reachable.items():
        plat = peers[pname]
        clean_url, user, token = split_url_auth(url)
        penv = plat.git_env() if hasattr(plat, "git_env") else None
        if token or (penv and "GIT_ASKPASS" in penv):
            penv = askpass_env(user, token, base_env=penv)
        rc, _, err = P.run(
            ["git", "fetch", "--prune", "--force", "--no-tags", clean_url,
             "+refs/heads/*:%s/%s/heads/*" % (MESH_NS, pname),
             "+refs/tags/*:%s/%s/tags/*" % (MESH_NS, pname)],
            cwd=hub, timeout=P.GIT_TIMEOUT, env=penv)
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

    chosen_heads, skip_heads = _choose(cand_heads, conflict, hub=hub, is_tag=False)
    chosen_tags, skip_tags = _choose(cand_tags, conflict, hub=hub, is_tag=True)
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
        err = _push_target(pname, plat, repo, hub, "safe", refspecs,
                           relax=relax)
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
        # a mesh that refused to overwrite is NOT a clean run: say so, or a
        # newbie reads "ok" and assumes everything converged
        desc_list = []
        has_diverged = False
        for b in skip_heads:
            shas = sorted({s[:7] for _d, s in cand_heads.get(b, [])})
            shas_str = " vs ".join(shas) if len(shas) > 1 else (shas[0] if shas else "")
            desc_list.append("%s (%s)" % (b, shas_str) if shas_str else b)
            has_diverged = True
        for t in skip_tags:
            shas = sorted({s[:7] for _d, s in cand_tags.get(t, [])})
            shas_str = " vs ".join(shas) if len(shas) > 1 else (shas[0] if shas else "")
            desc_list.append("%s [tag] (%s)" % (t, shas_str) if shas_str else t)

        names = ", ".join(desc_list[:20])
        if len(desc_list) > 20:
            names += " +%d more" % (len(desc_list) - 20)

        kind_label = "diverged" if has_diverged and not skip_tags else ("conflict(s)" if not has_diverged else "diverged/conflict(s)")
        detail += ", %d %s SKIPPED: %s" % (len(skipped), kind_label, names)
        reason_word = "branches diverged" if has_diverged and not skip_tags else "peers disagree"
        return "partial", detail, [
            "%d ref(s) left alone because the %s: %s"
            % (len(skipped), reason_word, names)]
    return "ok", detail, []


def _choose(cands, conflict, hub=None, is_tag=False):
    """({ref: (date, sha)}, [skipped refs]) for one namespace

    "newest" takes the newest candidate. "skip" keeps only refs every peer
    agrees on, or branches where one candidate is a strict descendant of all
    others (fast-forward). Truly diverged branches and mismatched tags are
    skipped and reported instead of overwriting anyone.
    """
    out, skipped = {}, []
    for ref, options in cands.items():
        distinct_shas = {s for _d, s in options}
        if len(distinct_shas) <= 1:
            out[ref] = _newest(options)
            continue
        if conflict == "newest":
            out[ref] = _newest(options)
            continue
        # conflict == "skip":
        if is_tag or not hub or not os.path.isdir(hub):
            skipped.append(ref)
            continue
        winner = None
        for cand_sha in distinct_shas:
            is_ahead = True
            for other_sha in distinct_shas:
                if other_sha == cand_sha:
                    continue
                rc, _, _ = P.run(
                    ["git", "merge-base", "--is-ancestor", other_sha, cand_sha],
                    cwd=hub, timeout=30)
                if rc != 0:
                    is_ahead = False
                    break
            if is_ahead:
                winner = cand_sha
                break
        if winner:
            matching = [c for c in options if c[1] == winner]
            out[ref] = _newest(matching) if matching else options[0]
        else:
            skipped.append(ref)
    return out, skipped


def mesh_plan_repo(repo, peers, conflict=None, hub=None):
    """read-only plan for a mesh route: do the peers agree?"""
    if conflict is None:
        conflict = C.DEFAULT_MESH_CONFLICT
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
        winner = None
        if hub and os.path.isdir(hub):
            for cand_sha in distinct:
                is_ahead = True
                for other_sha in distinct:
                    if other_sha == cand_sha:
                        continue
                    rc, _, _ = P.run(
                        ["git", "merge-base", "--is-ancestor", other_sha, cand_sha],
                        cwd=hub, timeout=30)
                    if rc != 0:
                        is_ahead = False
                        break
                if is_ahead:
                    winner = cand_sha
                    break
        if winner:
            return ["peers differ but fast-forward possible (%s ahead) — would merge: %s"
                    % (winner[:8], detail)], "update"
        return ["peers diverged — would SKIP (newest wins is off): %s"
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


def read_include_file(path):
    """repo names from a file: one per line, '#' comments, blanks ignored.

    Returns [] when the file is missing or empty — a route whose include
    file was deleted must not silently sync nothing.
    """
    try:
        with open(os.path.expanduser(path)) as f:
            return [l.strip() for l in f
                    if l.strip() and not l.strip().startswith("#")]
    except OSError:
        return []


def _filter_repos(repos, route, only=None):
    """route-level include/exclude globs on repo names, plus an exact
    'only' list (comma-separated names) for one-off selective syncs.

    `include_file` is an alternative to `include`: a path whose lines are
    exact repo names. Kept out of the config so a 500-repo selection stays
    diffable in git.
    """
    inc = route.get("include") or []
    inc_file = route.get("include_file")
    if inc_file:
        names = read_include_file(inc_file)
        if names:
            wanted = {n.lower() for n in names}
            repos = [r for r in repos if r["name"].lower() in wanted]
        inc = []                     # the file replaces the inline globs
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


def _wanted_names(only, only_map, rname):
    """which repo names a route should keep in a selective run.

    None means "no selection — sync everything on this route"; a list means
    "only these", and an empty list means "this route has nothing to do".

    only_map exists because `only` is a flat list of names: retrying route
    A's failure used to re-run that name on every other route too (a repo
    that is healthy on B got pushed again, and a missing one failed again).
    The "*" key carries an explicit --repos choice, which does apply
    everywhere.
    """
    if only_map is None:
        return list(only) if only else None
    return list(only_map.get(rname) or []) + list(only_map.get("*") or [])


def _apply_route_skips(cfg, route, repos):
    """drop repos the route asked to skip: forks, archived, empty.

    Forks default to the global `include_forks` and a route may override it.
    Archived repos are read-only on every supported host, so syncing one only
    produces push failures (and a mesh cannot converge on it either). An empty
    repo is only known for the local platform — no API reports "no commits" —
    and is almost always a placeholder, so skipping is opt-in.

    Returns (kept, {"fork": n, "archived": n, "empty": n}).
    """
    inc_forks = route.get("include_forks")
    if inc_forks is None:
        inc_forks = bool(C.setting(cfg, "include_forks", False))
    skip_archived = route.get("skip_archived")
    skip_archived = True if skip_archived is None else bool(skip_archived)
    skip_empty = bool(route.get("skip_empty", False))
    kept, skipped = [], {"fork": 0, "archived": 0, "empty": 0}
    for r in repos:
        if not inc_forks and r.get("fork"):
            skipped["fork"] += 1
            continue
        if skip_archived and r.get("archived"):
            skipped["archived"] += 1
            continue
        if skip_empty and r.get("empty"):
            skipped["empty"] += 1
            continue
        kept.append(r)
    return kept, skipped


def _mirror_extras(hub):
    """(needs_lfs, has_submodules) for one bare mirror.

    gitsync mirrors refs, and that is all: LFS blobs and submodule
    checkouts are separate object stores. A target clone then silently misses
    the large files, which is worth one local `git ls-tree` per repo to say
    out loud. The files are read out of HEAD, so nothing is checked out.
    """
    ls = P.run(["git", "ls-tree", "-r", "--name-only", "HEAD", "--",
                ".gitattributes", ".gitmodules"], cwd=hub, timeout=30)
    if ls[0] != 0:
        return False, False
    names = [l.strip() for l in (ls[1] or "").splitlines() if l.strip()]
    lfs = False
    if ".gitattributes" in names:
        rc, text, _ = P.run(["git", "show", "HEAD:.gitattributes"],
                            cwd=hub, timeout=30)
        lfs = rc == 0 and "filter=lfs" in (text or "")
    return lfs, ".gitmodules" in names


def _note_extras(hub, name, say=None):
    """warn about LFS/submodules, and fetch LFS objects when git-lfs exists"""
    lfs, subs = _mirror_extras(hub)
    if not (lfs or subs):
        return []
    notes = []
    if lfs:
        rc, _, _ = P.run(["git", "lfs", "version"], timeout=15)
        if rc == 0:
            rc2, _, err2 = P.run(["git", "lfs", "fetch", "--all"], cwd=hub,
                                 timeout=P.GIT_TIMEOUT)
            notes.append("LFS objects fetched" if rc2 == 0
                         else "LFS fetch failed: %s"
                              % _redact((err2 or "").strip())[:80])
        else:
            notes.append("uses Git LFS — install git-lfs to mirror the "
                         "objects (refs are synced, blobs are not)")
    if subs:
        notes.append("has submodules — gitsync mirrors the refs, not the "
                     "submodule working trees")
    for n in notes:
        (say or log)("  %-24s %s" % (name, n))
    return notes


def _run_hook(what, cmd, extra_env=None, timeout=300):
    """run settings.hooks.pre/post. Returns (ok, message).

    shlex.split, never shell=True: the config is a file a web session can
    write, so a shell would turn "save settings" into remote code execution.
    A hook that needs a pipeline can call a script of its own.
    """
    try:
        argv = shlex.split(cmd or "")
    except ValueError as e:
        return False, "%s hook is not a valid command: %s" % (what, e)
    if not argv:
        return True, ""
    env = os.environ.copy()
    env.update({k: str(v) for k, v in (extra_env or {}).items()})
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "%s hook could not run: %s" % (what, e)
    if r.returncode != 0:
        lines = [l for l in ((r.stderr or "") + "\n" + (r.stdout or ""))
                 .splitlines() if l.strip()]
        return False, "%s hook exited %d: %s" % (
            what, r.returncode, _redact(lines[-1])[:160] if lines else "")
    return True, ""


def _remote_head(url, timeout=60, env=None):
    """HEAD sha of a remote, or None if unreachable / no refs"""
    clean_url, user, token = split_url_auth(url)
    if token or (env and "GIT_ASKPASS" in env):
        env = askpass_env(user, token, base_env=env)
    rc, out, _ = P.run(["git", "ls-remote", clean_url, "HEAD"], timeout=timeout, env=env)
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

    hub = mirror_path(repo, source.ptype)
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
        if not trefs:
            # unreachable (or not created yet). Must be checked before any
            # use of trefs: an unreachable target used to crash the plan and
            # make a dry run report every repo as failed.
            actions.append("%s: unreachable - would CREATE" % tname)
            behind += 1
            continue
        # every branch and tag is compared, even when HEAD agrees: a target
        # can be level with HEAD and still be missing (or ahead on) a branch
        missing = [r for r, sha in trefs.items()
                   if r.startswith(("refs/heads/", "refs/tags/"))
                   and src_refs.get(r) != sha]
        if not missing:
            actions.append("%s: up to date" % tname)
        else:
            behind_names = [m.split("/")[-1] for m in missing
                            if m.startswith("refs/heads/")]
            extra = [m.split("/")[-1] for m in missing
                     if m.startswith("refs/tags/")]
            bits = []
            if behind_names:
                bits.append("%d branch(es)" % len(behind_names))
            if extra:
                bits.append("%d tag(s)" % len(extra))
            actions.append("%s: %s differ (%s)"
                           % (tname, " + ".join(bits),
                              ", ".join((behind_names + extra)[:3])))
            behind += len(missing)
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


import re as _re

_TOKEN_URL = _re.compile(
    r"(https?://)[^/\s@]+@|x-access-token:[^@\s]+@")


def _redact(text):
    """strip credentials out of git error text before it is logged/stored.

    git echoes the remote URL, and the URL carries the token (every HTTPS
    adapter embeds it). A log line or a records.json entry must never
    contain it.
    """
    if not text:
        return text
    return _TOKEN_URL.sub(lambda m: m.group(0).split(":")[0] + ":***@"
                          if m.group(0).startswith("x-access-token")
                          else m.group(1) + "***@", text)


def _short_error(err, limit=160):
    """the most useful line of a git error, not the wall of remote noise.

    Redacted first: git echoes the remote URL, and the URL carries the token.
    """
    lines = [l.strip() for l in (err or "").strip().splitlines() if l.strip()]
    for l in lines:
        if l.lower().startswith("remote:"):
            l = l[7:].strip()
        if l and not l.startswith("To ") and not l.startswith("hint:"):
            return _redact(l)[:limit]
    return _redact(lines[0])[:limit] if lines else ""


def run_sync(cfg=None, route_name=None, quiet=False, only=None,
             dry_run=False, scheduled=False, force=False, prune_repos=False,
             only_map=None):
    """Run all routes (or one). Returns a summary dict.

    dry_run=True    computes what would change without touching anything.
    scheduled=True  skip routes whose own schedule says they are not due
                    yet (this is what the systemd timer uses).
    force=True      run even if sync is paused globally.
    prune_repos=True delete, on the targets a route names in
                    "prune_repos", every repository the source does not
                    have. Refused when scheduled: repo deletion is never
                    something an unattended timer decides.
    only_map        {route name: [repo names]} — a selective run that keeps
                    the scoping of a failure. `only` is by repo name alone, so
                    retrying a failure on route A used to re-run the same
                    name on every other route too.
    """
    global _start
    # one run at a time — the timer and manual runs must never overlap
    try:
        import fcntl
    except ImportError:
        fcntl = None
    C.ensure_dirs()
    lockf = open(os.path.join(C.DATA_DIR, "sync.lock"), "w")
    if fcntl:
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                lockf.close()
            except OSError:
                pass
            log("another gitsync run is already active — exiting")
            return {"repos": 0, "ok": 0, "partial": 0, "failed": 0,
                    "failures": [], "elapsed_sec": 0,
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "routes": 0, "skipped": "locked"}

    cfg = cfg or C.load_config()
    if not force and C.setting(cfg, "paused", False):
        if not quiet:
            log("sync is paused globally (run 'gitsync resume' or toggle in TUI)")
        if fcntl:
            try:
                fcntl.flock(lockf, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            lockf.close()
        except OSError:
            pass
        return {"repos": 0, "ok": 0, "partial": 0, "failed": 0,
                "failures": [], "elapsed_sec": 0,
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "routes": 0, "skipped": "paused", "paused": True}

    def _finish_lock():
        if fcntl:
            try:
                fcntl.flock(lockf, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            lockf.close()
        except OSError:
            pass

    def _bail(reason, message):
        """stop before anything was touched, and record why.

        A blocked run is not a silent no-op: it goes into records.json (so
        the dashboard and history show it), pages the notification channel,
        and makes the CLI exit non-zero — the timer's mail is the only alarm
        a headless box has.
        """
        log(message)
        totals = {"repos": 0, "ok": 0, "partial": 0, "failed": 0,
                  "failures": [], "elapsed_sec": 0,
                  "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "routes": 0, "skipped": reason, "blocked": reason,
                  "error": message}
        _record(totals)
        try:
            if NT.channels(cfg):
                NT.notify_run(cfg, totals, routes=None)
        except Exception:
            pass
        _finish_lock()
        return totals

    # A full disk corrupts a mirror half-way through a fetch; stopping with a
    # sentence beats a confusing git error on every run afterwards.
    min_free = float(C.setting(cfg, "min_free_gb", 0.5) or 0)
    if not dry_run and min_free > 0:
        ok_disk, disk_msg = DK.guard(C.MIRROR_DIR, min_free_gb=min_free)
        if not ok_disk:
            return _bail("disk", "sync blocked: " + disk_msg)

    hooks = C.setting(cfg, "hooks", {}) or {}
    pre_hook = (hooks.get("pre") or "").strip()
    post_hook = (hooks.get("post") or "").strip()
    if pre_hook and not dry_run:
        ok_hook, hook_msg = _run_hook("pre", pre_hook)
        if not ok_hook:
            return _bail("hook", "sync blocked — " + hook_msg)

    _start = time.time()
    reset_progress()
    pace = float(C.setting(cfg, "pace", 0) or 0)
    # changing branch protection is a lasting security change on a
    # destination; it is opt-in (P1-2)
    relax = bool(C.setting(cfg, "relax_protection", False))

    selected = C.routes(cfg)
    if route_name:
        selected = [r for r in selected if r.get("name") == route_name]

    totals = {"routes": len(selected), "repos": 0, "ok": 0,
              "failed": 0, "partial": 0, "failures": [], "planned": [],
              "dry_run": dry_run, "skipped": 0}

    for route in selected:
        rname = route.get("name")
        # decided before any network call: a retry run must not list (or
        # push) a route that had no failure in the first place
        want = _wanted_names(only, only_map, rname)
        if want == []:
            log("route '%s': nothing to retry — skipped" % rname)
            totals["skipped"] = totals.get("skipped", 0) + 1
            continue
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
        mode = C.route_mode(cfg, route)
        conflict = route.get("mesh_conflict") or \
            C.setting(cfg, "mesh_conflict", C.DEFAULT_MESH_CONFLICT)

        mode_note = ("mode=%s (%s)"
                     % (mode, "will not delete or force-push" if mode != "full"
                        else "force-push, prunes heads and tags on non-local "
                             "targets"))
        if mesh:
            log("=== route '%s': mesh %s (workers=%d, conflicts=%s, %s%s) ==="
                % (rname, ", ".join(peers), workers, conflict, mode_note,
                   ", DRY RUN" if dry_run else ""))
        else:
            log("=== route '%s': %s -> %s (workers=%d, %s%s) ==="
                % (rname, sname, ", ".join(targets), workers, mode_note,
                   ", DRY RUN" if dry_run else ""))
        progress["route"] = rname
        _rt_before = (totals["ok"], totals["partial"], totals["failed"])
        try:
            repos = source.list_repos()
        except Exception as e:
            # listing failed: this is not a run, so the schedule slot must
            # not be consumed (a newbie previewing with --dry-run used to
            # silently delay the real backup)
            log("  cannot list repos on %s: %s" % (sname, e))
            totals["failures"].append({"route": rname,
                                       "repo": "-", "error": str(e)[:200]})
            continue
        if not dry_run:
            mark_route_run(route)
        repos = _filter_repos(repos, route, want)
        # legacy-collision guard: two repos whose short names are equal would
        # both claim mirrors/{name}.git and mix two histories into one mirror
        claims = {}
        for r in repos:
            key = (P._fs_safe(r.get("name") or ""), source.ptype)
            claims.setdefault(key, []).append(r)
        # new-layout guard: distinct repos must never share one hub folder.
        # _fs_unique() makes a clash practically impossible, but a silent
        # merge of two histories is bad enough that we refuse the pair and
        # name them instead of pushing one over the other.
        by_path = {}
        for r in repos:
            rid = r.get("id") or r.get("name") or ""
            by_path.setdefault(hub_rel(rid), []).append(r)
        clash = {p: rs for p, rs in by_path.items() if len(rs) > 1}
        if clash:
            for path, rs in clash.items():
                names = ", ".join(sorted(str(r.get("name")) for r in rs))
                log("  %s: %d repos map to the same folder %s (%s) — "
                    "skipping them. Rename one, or exclude it: "
                    "gitsync routes edit %s --exclude '%s'"
                    % (sname, len(rs), path, names, rname,
                       str(rs[0].get("name"))))
                totals["failures"].append({"route": rname, "repo": names,
                                           "error": "hub path collision"})
                totals["failed"] += len(rs)
            bad = set()
            for path, rs in clash.items():
                for r in rs:
                    bad.add(id(r))
            repos = [r for r in repos if id(r) not in bad]
        repos, skipped = _apply_route_skips(cfg, route, repos)
        if any(skipped.values()):
            log("  skipped: %s"
                % ", ".join("%d %s" % (n, k) for k, n in sorted(skipped.items())
                            if n))
        totals["repos"] += len(repos)
        log("  %s: %d repos" % ("mesh" if mesh else sname, len(repos)))

        done = {"n": 0}
        dlock = threading.Lock()

        progress["total"] = len(repos)

        def work(repo):
            progress["active"][repo["name"]] = "syncing"
            key = (P._fs_safe(repo.get("name") or ""), source.ptype)
            shared = claims.get(key) or []
            try:
                if dry_run:
                    if mesh:
                        actions, verdict = mesh_plan_repo(
                            repo, peers, conflict,
                            hub=mirror_path(repo, next(iter(peers.values())).ptype, shared))
                    else:
                        actions, verdict = _plan_repo(repo, source, targets,
                                                      mode)
                    status, detail, errors = verdict, "", actions
                elif mesh:
                    status, detail, errors = mesh_sync_repo(repo, peers,
                                                           conflict,
                                                           shared)
                else:
                    status, detail, errors = sync_repo(repo, source, targets,
                                                       mode, shared)
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

        if prune_repos:
            _prune_route_repos(cfg, route, repos, dry_run, totals)

        # per-route counters: notifications and the UI both need to know
        # WHICH route failed, not just how many repos did
        totals.setdefault("per_route", {})[rname] = {
            "ok": totals["ok"] - _rt_before[0],
            "partial": totals["partial"] - _rt_before[1],
            "failed": totals["failed"] - _rt_before[2],
        }

    elapsed = round(time.time() - _start, 1)
    totals["elapsed_sec"] = elapsed
    totals["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if dry_run:
        n_upd = sum(1 for p in totals["planned"]
                    if p["verdict"] == "update")
        totals["would_change"] = n_upd
        log("=== dry run done: %d of %d repos would change (%ss) ==="
            % (n_upd, totals["repos"], elapsed))
    else:
        log("=== sync done: %d ok, %d partial, %d failed of %d repos in %ss ==="
            % (totals["ok"], totals["partial"], totals["failed"],
               totals["repos"], elapsed))
    _record(totals)
    if post_hook and not dry_run:
        ok_hook, hook_msg = _run_hook("post", post_hook, {
            "GITSYNC_OK": totals.get("ok", 0),
            "GITSYNC_PARTIAL": totals.get("partial", 0),
            "GITSYNC_FAILED": totals.get("failed", 0),
            "GITSYNC_REPOS": totals.get("repos", 0),
            "GITSYNC_ELAPSED": totals.get("elapsed_sec", 0),
            "GITSYNC_FINISHED": totals.get("finished_at", ""),
        })
        if not ok_hook:
            # the sync itself succeeded; a broken post hook must not change
            # the exit code of a backup that worked
            log("  " + hook_msg)
    try:
        if NT.channels(cfg):
            # per-route rules: a route set to notify_on:never stays silent, a
            # route set to always pages even when the global mode is failure
            NT.notify_run(cfg, totals, routes=selected)
    except Exception as e:
        log("  notification failed: %s" % e)
    if fcntl:
        try:
            fcntl.flock(lockf, fcntl.LOCK_UN)
        except OSError:
            pass
    try:
        lockf.close()
    except OSError:
        pass
    return totals


def prune_repos(cfg, route, dry_run=False):
    """repositories to delete per target: present on a target, absent on the
    source. Read-only; nothing is deleted here.

    Returns {target: [{"name": …, "why": …}]} and skips any target the route
    has not explicitly opted in via "prune_repos".
    """
    out = {}
    source = P.make(route["source"], C.platform(cfg, route["source"]))
    try:
        src_names = {r["name"] for r in source.list_repos()}
    except Exception as e:
        log("  prune: cannot list the source — nothing will be deleted (%s)"
            % str(e)[:120])
        return out
    if not src_names:
        # an empty source list means the listing failed or the account is
        # empty; either way deleting everything would be catastrophic
        log("  prune: source lists no repositories — refusing to delete")
        return out
    for tname in C.prune_targets(cfg, route):
        tp = C.platform(cfg, tname)
        if not tp or not tp.get("enabled"):
            continue
        try:
            have = P.make(tname, tp).list_repos()
        except Exception as e:
            log("  prune: cannot list %s — skipped (%s)"
                % (tname, str(e)[:100]))
            continue
        gone = [{"name": r["name"], "why": "not on the source"}
                for r in have if r["name"] not in src_names]
        if gone:
            out[tname] = gone
    return out


def _prune_route_repos(cfg, route, repos, dry_run, totals):
    """delete the target repos the source does not have (opt-in, human-run).

    Four locks, all of which must be open: the route names the target in
    "prune_repos", the caller passed prune_repos=True (the CLI flag or the
    WebUI action), the route is in full mode, and the run is not scheduled.
    """
    rname = route.get("name")
    if C.route_mode(cfg, route) != "full":
        log("route '%s': repo deletion needs full mode — skipped" % rname)
        return
    gone = prune_repos(cfg, route, dry_run=dry_run)
    if not gone:
        return
    n = sum(len(v) for v in gone.values())
    for tname, items in gone.items():
        for it in items:
            log("  %s: %s %s (%s)"
                % (tname, "would delete" if dry_run else "deleting",
                   it["name"], it["why"]))
    log("=== route '%s': %d repo(s) %s the source does not have%s ==="
        % (rname, n, "would be deleted from" if dry_run else "deleted from",
           ", ".join(sorted(gone))))
    if dry_run:
        totals.setdefault("prune_planned", []).extend(
            [{"target": t, "name": it["name"]}
             for t, items in gone.items() for it in items])
        return
    deleted = failed = 0
    for tname, items in gone.items():
        plat = P.make(tname, C.platform(cfg, tname))
        for it in items:
            try:
                ok, err = plat.delete_repo({"name": it["name"]})
            except Exception as e:
                ok, err = False, str(e)[:120]
            if ok:
                deleted += 1
                log("  deleted %s/%s" % (tname, it["name"]))
            else:
                failed += 1
                log("  FAILED to delete %s/%s: %s"
                    % (tname, it["name"], err))
    totals["pruned"] = deleted
    totals["prune_failed"] = failed
    if failed:
        totals["failures"].append({"route": rname, "repo": "prune",
                                   "error": "%d repo(s) could not be deleted"
                                   % failed})


def _record(summary):
    try:
        C.ensure_dirs()
        records = []
        if os.path.exists(C.records_file()):
            try:
                with open(C.records_file(), "r", encoding="utf-8") as rf:
                    records = json.load(rf)
            except (OSError, ValueError):
                records = []
        records.append(summary)
        records = records[-200:]          # keep the last 200 runs
        tmp = C.records_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
        os.replace(tmp, C.records_file())
        os.chmod(C.records_file(), 0o600)   # failure text can name a remote
    except OSError:
        pass




def last_records(n=10):
    try:
        if os.path.exists(C.records_file()):
            with open(C.records_file(), "r", encoding="utf-8") as rf:
                return json.load(rf)[-n:]
    except (OSError, ValueError):
        pass
    return []


# ---------------------------------------------------------------- verify

def _refs_local(path):
    """{ref: sha} for a local bare repo, or None if it is not one."""
    rc, out, _ = P.run(["git", "for-each-ref",
                        "--format=%(refname) %(objectname)"],
                       cwd=path, timeout=120)
    if rc != 0:
        return None
    refs = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            refs[parts[0]] = parts[1]
    return refs


def _refs_remote(url):
    """({ref: sha}, error) from `git ls-remote` (works for paths and URLs)."""
    # stdin is /dev/null inside P.run, so this fails fast instead of hanging
    # on a credential prompt for a private repo with a bad token
    rc, out, err = P.run(["git", "ls-remote", url], timeout=P.GIT_TIMEOUT)
    if rc != 0:
        return None, _redact(err.strip())[:200]
    refs = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:                 # ls-remote prints "sha<TAB>ref"
            refs[parts[1]] = parts[0]
    return refs, ""


def _tracked(ref):
    return ref.startswith("refs/heads/") or ref.startswith("refs/tags/")


def verify(cfg, route_name=None, log_fn=None):
    """read-only drift report: hub vs every target, per branch and tag.

    Nothing here pushes, creates or deletes. It answers "is the backup
    actually a backup?" long before a restore is needed: a target that
    stopped accepting pushes still looks healthy in the dashboard, because
    the run records only say what was attempted.
    """
    log = log_fn or globals()["log"]
    rows = []
    for route in C.routes(cfg):
        rname = route.get("name")
        if route_name and rname != route_name:
            continue
        if route.get("enabled") is False:
            rows.append({"route": rname, "state": "paused", "repo": "-",
                         "target": "-"})
            continue
        sname = route.get("source")
        sp = C.platform(cfg, sname)
        if not sp or not sp.get("enabled"):
            rows.append({"route": rname, "state": "source-disabled",
                         "repo": "-", "target": "-"})
            continue
        source = P.make(sname, sp)
        # these helpers return platform *config dicts*; verify needs the
        # live platform objects to build URLs
        if route.get("direction") == "mesh":
            peers = {n: P.make(n, p)
                     for n, p in route_peers(cfg, route).items()}
        else:
            peers = {n: P.make(n, p)
                     for n, p in active_targets(cfg, route).items()}
        try:
            repos = source.list_repos()
        except Exception as e:
            rows.append({"route": rname, "state": "list-failed", "repo": "-",
                         "target": "-", "detail": _redact(str(e))[:200]})
            continue
        log("verifying %d repo(s) on route '%s'" % (len(repos), rname))
        for repo in repos:
            name = repo.get("name")
            hub = mirror_path(repo, source.ptype)
            hub_refs = _refs_local(hub) if os.path.isdir(hub) else None
            if hub_refs is None:
                rows.append({"route": rname, "repo": name,
                             "target": "-", "state": "no-mirror"})
                continue
            hub_refs = {r: v for r, v in hub_refs.items() if _tracked(r)}
            for tname, tgt in peers.items():
                url = tgt.repo_url(repo)
                refs, err = _refs_remote(url)
                if refs is None:
                    rows.append({"route": rname, "repo": name,
                                 "target": tname, "state": "unreachable",
                                 "detail": err})
                    continue
                refs = {r: v for r, v in refs.items() if _tracked(r)}
                missing = sorted(r for r in hub_refs if r not in refs)
                differ = sorted(r for r in hub_refs
                                if r in refs and refs[r] != hub_refs[r])
                extra = sorted(r for r in refs if r not in hub_refs)
                rows.append({
                    "route": rname, "repo": name, "target": tname,
                    "state": "drift" if (missing or differ) else "in-sync",
                    "missing": missing, "differ": differ, "extra": extra,
                    "refs": len(hub_refs)})
    return rows


def verify_summary(rows):
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return counts


# ---------------------------------------------------------------- restore

def _hub_repo_name(rel_id):
    """the repo name a hub mirror directory stands for.

    New layout is mirrors/{type}/{fs_safe(id)}.git with '/' turned into
    '__', so 'SeaXen__gitsync' is the repo 'gitsync' in owner 'SeaXen'. The
    legacy flat layout stored the name directly.
    """
    last = rel_id.split("/")[-1]
    if "__" in last:
        return last.split("__")[-1]
    return last


def restore(cfg, target_names, only=None, dry_run=False, prune=False,
            private=True, log_fn=None):
    """push every hub mirror to the given targets — the hub is the truth.

    This is the answer to "what if the source host disappears": no route can
    list repos from a dead platform, but the hub still holds every mirror.

    - skips mirrors with no refs (an empty repo), with a log line
    - creates missing repos on the target via ensure_repo
    - never deletes anything on a target unless the global mode is full AND
      the caller passed prune=True
    """
    def say(msg):
        (log_fn or log)(msg)

    mode = C.route_mode(cfg)
    do_prune = prune and mode == "full"
    targets = {}
    for t in target_names:
        tp = C.platform(cfg, t)
        if not tp or not tp.get("enabled"):
            say("%s: not configured/enabled — skipped" % t)
            continue
        targets[t] = P.make(t, tp)
    if not targets:
        return {"repos": 0, "ok": 0, "partial": 0, "failed": 0,
                "failures": [], "restored": 0, "skipped": 0}

    want = {o.lower() for o in only} if only else None
    ok = partial = failed = skipped = 0
    failures = []
    for rel_id, path, _size, _mt in DK.mirrors():
        name = _hub_repo_name(rel_id)
        if want and name.lower() not in want:
            continue
        rc, out, _ = P.run(["git", "for-each-ref"], cwd=path, timeout=60)
        if rc != 0 or not out.strip():
            skipped += 1
            say("  %-28s empty mirror — skipped" % name)
            continue
        repo = {"name": name, "id": rel_id, "private": private,
                "clone_url": path}
        bad = []
        for tname, tplat in targets.items():
            if dry_run:
                say("  would restore %-22s -> %s" % (name, tname))
                continue
            err = _push_target(tname, tplat, repo, path,
                               "full" if do_prune else "safe")
            if err:
                bad.append(err)
        if dry_run:
            ok += 1
            continue
        if bad:
            failed += 1
            failures.append({"repo": name, "error": "; ".join(bad)[:200]})
            say("  FAILED %-24s %s" % (name, "; ".join(bad)[:120]))
        else:
            ok += 1
    say("restore: %d ok, %d skipped, %d failed%s"
        % (ok, skipped, failed, " (dry run)" if dry_run else ""))
    return {"repos": ok + skipped + failed, "ok": ok, "partial": 0,
            "failed": failed, "failures": failures, "restored": ok,
            "skipped": skipped}
