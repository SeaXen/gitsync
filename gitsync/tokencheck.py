"""Token expiry checks — warn before a credential silently kills the sync.

GitLab exposes expiry via /personal_access_tokens/self; GitHub reports it in
the GitHub-Authentication-Token-Expiration response header. Forgejo/Gitea
tokens do not expire, and Bitbucket does not expose it — those report
"unknown" rather than guessing.
"""

import os
from datetime import datetime, timedelta

from . import platforms as P

DEFAULT_WARN_DAYS = 30


def _parse(ts):
    """accept '2026-12-01', '2026-12-01T10:00:00Z', '... 10:00:00 UTC'"""
    if not ts:
        return None
    s = str(ts).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    # last resort: ISO-ish prefix
    try:
        return datetime.fromisoformat(s[:19])
    except ValueError:
        return None


def days_left(expires_at):
    """whole days until expiry; negative if already expired; None if unknown"""
    dt = _parse(expires_at)
    if dt is None:
        return None
    return (dt - datetime.now()).days


def level_for(days, warn_days=DEFAULT_WARN_DAYS):
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= 7:
        return "critical"
    if days <= warn_days:
        return "warning"
    return "ok"


def check(cfg, warn_days=None):
    """inspect every enabled platform. Returns a list of result dicts."""
    if warn_days is None:
        try:
            from . import config as C
            warn_days = int(C.setting(cfg, "token_warn_days",
                                      DEFAULT_WARN_DAYS) or DEFAULT_WARN_DAYS)
        except Exception:
            warn_days = DEFAULT_WARN_DAYS

    out = []
    for name, pcfg in (cfg.get("platforms") or {}).items():
        if not pcfg.get("enabled"):
            continue
        entry = {"platform": name, "type": pcfg.get("type", "local")}
        try:
            plat = P.make(name, pcfg)
            info = plat.token_info()
        except Exception as e:
            entry.update(level="unknown", note="check failed: %s" % e)
            out.append(entry)
            continue

        exp = info.get("expires_at")
        days = days_left(exp)
        lvl = level_for(days, warn_days)
        entry["level"] = lvl
        entry["expires_at"] = exp
        entry["days_left"] = days
        entry["scopes"] = info.get("scopes")
        if info.get("error"):
            entry["note"] = info["error"]
            entry["level"] = "critical"
        elif info.get("note"):
            entry["note"] = info["note"]
        elif info.get("revoked"):
            entry["note"] = "token revoked"
            entry["level"] = "expired"
        elif info.get("active") is False:
            entry["note"] = "token inactive"
            entry["level"] = "expired"
        elif days is None:
            entry["note"] = "expiry not reported"
        elif days < 0:
            entry["note"] = "EXPIRED %d day(s) ago" % (-days)
        else:
            entry["note"] = "expires in %d day(s)" % days
        out.append(entry)
    return out


def bad(results):
    """the results that should actually worry the user"""
    return [r for r in results if r["level"] in ("expired", "critical",
                                                 "warning")]


def summary(results):
    """one-line digest for the TUI / doctor"""
    b = bad(results)
    if not b:
        unverified = [r for r in results if r.get("level") == "unknown"]
        if unverified:
            return "%d token(s) unverified" % len(unverified)
        return "all tokens healthy"
    worst = sorted(b, key=lambda r: (r["days_left"] is None,
                                     r["days_left"] if r["days_left"] is not None
                                     else 0))[0]
    return "%d token(s) need attention (worst: %s)" % (len(b),
                                                       worst["platform"])
