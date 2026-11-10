"""Copy a starred-repo list between platforms.

GitHub, GitLab and Forgejo/Gitea all let you star projects, but a star lives
inside one account: there is no way to say "star everything I starred on
GitHub here too". This module reads one account's stars and applies them to
another, so the list survives an account move or a new server.

Matching is deliberately conservative:

- GitHub -> GitHub matches on the exact ``owner/repo`` name, which is unique
  and cannot hit the wrong project.
- Anything else matches only when the target *already has* a project whose
  remote URL (or, for GitLab, its path) is the same repo. A name match is
  never enough on its own — ``SeaXen/hermes-telemetry`` and
  ``nujovich/hermes-telemetry`` are different projects with the same name,
  and starring the wrong one is worse than skipping it.
"""

from . import platforms as P


def _gh_starred(plat):
    """[full_name] via the gh CLI (config uses auth: "gh")"""
    out, page = [], 1
    while True:
        rc, o, _ = plat._gh(["api", "user/starred?per_page=100&page=%d"
                             % page, "--jq", ".[].full_name"])
        if rc != 0:
            break
        names = [l.strip() for l in o.splitlines() if l.strip()]
        out += names
        if len(names) < 100:
            break
        page += 1
        if page > 20:                    # 2000 stars is already absurd
            break
    return out


def starred(plat):
    """[(name, url)] the authenticated user has starred, or [] if unsupported"""
    ptype = getattr(plat, "ptype", None)
    try:
        if ptype == "github":
            if not plat.token:
                return [(n, "") for n in _gh_starred(plat)]
            out, page = [], 1
            while True:
                rc, o = plat._api("/user/starred?per_page=100&page=%d" % page)
                # rc != 0 treated a perfectly good 200 as failure
                if rc != 200 or not isinstance(o, list):
                    break
                out += [r.get("full_name") or "" for r in o if r]
                if len(o) < 100:
                    break
                page += 1
                if page > 20:
                    break
            return [(n, "") for n in out if n]
        if ptype == "gitlab":
            out, page = [], 1
            while True:
                rc, o = plat._api("/projects?starred=true&per_page=100"
                                  "&page=%d" % page)
                if rc != 200 or not isinstance(o, list):
                    break
                out += [(r.get("path_with_namespace") or "",
                         r.get("http_url_to_repo") or "") for r in o if r]
                if len(o) < 100:
                    break
                page += 1
                if page > 20:
                    break
            return [t for t in out if t[0]]
        if ptype in ("forgejo", "gitea"):
            out, page = [], 1
            while True:
                rc, o = plat._api("/user/starred?limit=50&page=%d" % page)
                if rc != 200 or not isinstance(o, list):
                    break
                out += [(r.get("full_name") or "", r.get("clone_url") or "")
                        for r in o if r]
                if len(o) < 50:
                    break
                page += 1
                if page > 20:
                    break
            return [t for t in out if t[0]]
    except Exception:
        return []
    return []


def star(plat, name):
    """star one repo. True on success (or already starred)."""
    ptype = getattr(plat, "ptype", None)
    try:
        if ptype == "github":
            owner, _, repo = name.partition("/")
            if not repo:
                return False
            path = "/user/starred/%s/%s" % (owner, repo)
            if plat.token:
                rc, _o = plat._api(path, "PUT")
                return rc in (204, 200)
            rc, _o, _e = plat._gh(["api", "--method", "PUT", path])
            return rc == 0
        if ptype == "gitlab":
            # GitLab stars by project id, not path. The path is quoted once:
            # quoting a string that already held %2F produced %252F. The
            # star call itself must be a POST - a GET only reports the
            # current status, so an unstarred project looked "already ok".
            quoted = P._url_quote(name)
            rc, _o = plat._api("/projects/%s/star" % quoted, "POST")
            if rc in (201, 200, 304):
                return True
            # not found under that path: resolve the project by path, then
            # star it by id
            rc2, pr = plat._api("/projects/%s" % quoted)
            if rc2 != 200 or not isinstance(pr, dict) or not pr.get("id"):
                return False
            rc, _o = plat._api("/projects/%s/star" % pr["id"], "POST")
            return rc in (201, 200, 304)
        if ptype in ("forgejo", "gitea"):
            rc, _o = plat._api("/repos/%s/star" % name, "PUT")
            return rc in (204, 200, 304)
    except Exception:
        return False
    return False


def _norm_url(u):
    """comparable form of a repo URL: scheme and .git gone, case folded.

    Two spellings of the same repo must land on the same string:
    ``https://github.com/SeaXen/gitsync.git``,
    ``git@github.com:SeaXen/gitsync.git`` and a token-embedded URL all do.
    A ``host:port`` is kept (it distinguishes two servers on one machine);
    only the scp-style ``host:owner/repo`` colon is turned into a slash.
    """
    if not u:
        return ""
    u = str(u).strip()
    for pre in ("https://", "http://", "git://", "ssh://"):
        if u.startswith(pre):
            u = u[len(pre):]
            break
    if "@" in u:                       # user@host or user:token@host
        u = u.split("@", 1)[1]
    head, sep, tail = u.partition(":")
    if sep and not tail.split("/", 1)[0].isdigit():
        u = head + "/" + tail          # scp style: host:owner/repo
    if u.endswith(".git"):
        u = u[:-4]
    return u.strip("/").lower()


def copy_stars(cfg, source_name, target_names, dry_run=False, only=None,
               log=None):
    """apply the source account's stars to the target accounts.

    Returns a summary dict. Only repos the target already has are starred —
    this never creates projects, and never guesses from a name alone.
    """
    def say(msg):
        if log:
            log(msg)

    sp = P.make(source_name, cfg["platforms"][source_name])
    src = starred(sp)
    if not src:
        return {"source": source_name, "total": 0, "starred": 0,
                "already": 0, "missing": 0, "failed": 0,
                "note": "no stars read (unsupported or empty)"}
    if only:
        want = {o.lower() for o in only}
        src = [t for t in src if t[0].lower() in want]

    totals = {"source": source_name, "total": len(src), "starred": 0,
              "already": 0, "missing": 0, "failed": 0, "note": ""}
    say("%s: %d starred repo(s)" % (source_name, len(src)))

    for tname in target_names:
        tp_cfg = cfg["platforms"].get(tname)
        if not tp_cfg or not tp_cfg.get("enabled"):
            say("  %s: not configured/enabled - skipped" % tname)
            continue
        tp = P.make(tname, tp_cfg)
        theirs = starred(tp)
        if not theirs:
            # a platform that cannot list stars may still accept one, so only
            # give up when it also refuses the star call itself
            say("  %s: cannot list its stars - trying direct matches" % tname)
        have_names = {n.lower() for n, _u in theirs}
        have_urls = {_norm_url(u) for _n, u in theirs if u}

        hit = miss = fail = already = 0
        for name, url in src:
            key = name.lower()
            known = key in have_names
            if not known and url and _norm_url(url) in have_urls:
                known = True
            if not known:
                # GitHub -> GitHub is the only exact-name case that is safe
                if not (getattr(sp, "ptype", None) == "github"
                        and getattr(tp, "ptype", None) == "github"):
                    miss += 1
                    continue
            if dry_run:
                say("    would star %s on %s" % (name, tname))
                hit += 1
                continue
            if star(tp, name):
                if key in have_names:
                    already += 1
                else:
                    hit += 1
            else:
                fail += 1
                say("    FAILED %s on %s" % (name, tname))
        totals["starred"] += hit
        totals["already"] += already
        totals["missing"] += miss
        totals["failed"] += fail
        say("  %s: %d starred, %d already, %d not on that site, %d failed"
            % (tname, hit, already, miss, fail))
    return totals
