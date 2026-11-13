"""Mirror-hub disk analytics and safe space reclaim.

The hub holds one bare mirror per repo. Over time these accumulate loose
objects, stale reflogs and unreachable objects — `git gc` reclaims that
without touching any reachable commit, so it is safe to run on a mirror.
"""

import os
import subprocess
import time

from . import config as C


def dir_size(path):
    """bytes of disk actually used by a directory tree.

    Counts st_blocks (what du reports) rather than st_size, so sparse
    files and per-file block rounding are reflected honestly — this is
    the number that matters when asking "am I running out of space".
    """
    total = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += (getattr(st, "st_blocks", 0) * 512) if hasattr(st, "st_blocks") else getattr(st, "st_size", 0)
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def human(n):
    for unit in ("B", "K", "M", "G", "T", "P"):
        if n < 1024 or unit == "P":
            if unit == "B":
                return "%d%s" % (n, unit)
            return "%.1f%s" % (n, unit)
        n /= 1024.0
    return "%.1fP" % n


def mirrors():
    """[(id, path, bytes, mtime)] for every hub mirror, biggest first.

    Walks both layouts: the new mirrors/{type}/{id}.git and the legacy flat
    mirrors/{name}.git, so an upgraded hub keeps reporting its mirrors.
    """
    out = []
    root = C.MIRROR_DIR
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, _files in os.walk(root):
        for entry in sorted(dirnames):
            if not entry.endswith(".git"):
                continue
            full = os.path.join(dirpath, entry)
            rel = os.path.relpath(full, root)[:-4].replace("\\", "/")      # type/id or name
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                mtime = 0
            out.append((rel, full, dir_size(full), mtime))
        dirnames[:] = [d for d in dirnames
                       if not d.endswith(".git")]        # do not descend
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def fs_free(path):
    """(free, total) bytes for the filesystem holding path"""
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize
    except OSError:
        return None, None


def guard(path, min_free_gb=0.5, need_bytes=0):
    """is there room to keep mirroring? (ok, message).

    A full disk turns a fetch into a half-written mirror, which then fails
    every later run with a confusing git error. Checking once at the start
    is cheap; a stopped sync with a plain sentence is better than that.

    Never blocks when the free space cannot be read (an exotic filesystem),
    and min_free_gb=0 disables it.
    """
    need = int(float(min_free_gb or 0) * (1024 ** 3)) + int(need_bytes or 0)
    if need <= 0:
        return True, ""
    free, _total = fs_free(path)
    if free is None:
        return True, ""
    if free < need:
        return False, ("only %s free on the mirror disk, %s needed — free "
                       "space, run 'gitsync disk --gc', or lower "
                       "settings.min_free_gb" % (human(free), human(need)))
    return True, ""


def gc_mirror(path, aggressive=False, timeout=600):
    """reflog-expire + gc one mirror. Returns bytes reclaimed."""
    before = dir_size(path)
    steps = [["reflog", "expire", "--expire=now", "--all"],
             ["gc", "--prune=now", "--quiet"] +
             (["--aggressive"] if aggressive else [])]
    for step in steps:
        rc = subprocess.run(["git", "-C", path] + step,
                            capture_output=True, timeout=timeout).returncode
        if rc != 0:
            break
    return max(0, before - dir_size(path))


def local_platform_paths(cfg):
    """[(name, path)] for every local-type platform"""
    out = []
    for name, p in (cfg.get("platforms") or {}).items():
        if p.get("type") == "local" and p.get("enabled") and p.get("path"):
            out.append((name, p["path"]))
    return out


def orphans(source_names):
    """hub mirrors whose repo no longer exists on the source.

    source_names: iterable of current repo names (any case). Returns
    [(name, path, bytes)] sorted biggest-first — safe to delete, since a
    mirror is only a cache of something that must exist upstream.
    """
    live = {str(n).lower() for n in source_names}
    out = []
    for name, path, size, _ in mirrors():
        # name is "type/id" (new) or "name" (legacy): compare the id part
        rid = name.split("/", 1)[1] if "/" in name else name
        if rid.lower() not in live and name.lower() not in live:
            out.append((name, path, size))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def delete_mirror(path):
    """remove one hub mirror. Returns True on success."""
    import shutil
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        return False
