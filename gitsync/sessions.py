"""Server-side web sessions.

The session cookie used to hold the access token itself: it never expired,
could not be revoked, and a copied cookie was a permanent API credential —
signing out, changing the password or rotating the token changed nothing for
whoever already had it.

A cookie now carries an opaque random id. Only its SHA-256 is stored
(``CONFIG_DIR/sessions.json``, mode 0600), so a leaked store cannot be
replayed, and the record can be revoked or allowed to expire:

* 30 days absolute, 12 hours idle (both constants below, tests patch ``_now``)
* signing out revokes that session
* changing/clearing the web password revokes every session
* ``Authorization: Bearer <token>`` keeps working for scripts and never
  touches the store

The store is a JSON file rather than memory so a restart does not sign
everyone out; it is written atomically and pruned on every write.
"""

import hashlib
import json
import os
import secrets
import threading
import time

from . import config as C

COOKIE_NAME = "gitsync_session"
IDLE_SECONDS = 12 * 3600
ABSOLUTE_SECONDS = 30 * 24 * 3600

# last_seen is rewritten at most this often: a page load fires a dozen API
# calls and each one would otherwise rewrite the file
_TOUCH_EVERY = 60

_lock = threading.RLock()


def _now():
    """indirection so tests can move the clock without sleeping."""
    return time.time()


def _path():
    return os.path.join(C.CONFIG_DIR, "sessions.json")


def _key(sid):
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "sessions": {}}


def _save(data):
    C.ensure_dirs()
    path = _path()
    tmp = path + ".tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)          # 0600 at creation, not after
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _expired(rec, now=None):
    now = _now() if now is None else now
    try:
        created = float(rec.get("created", 0))
        seen = float(rec.get("last_seen", created))
    except (TypeError, ValueError):
        return True
    return (now - created > ABSOLUTE_SECONDS
            or now - seen > IDLE_SECONDS)


def _prune(data):
    """drop expired records; returns how many went."""
    now = _now()
    gone = [k for k, rec in data["sessions"].items()
            if not isinstance(rec, dict) or _expired(rec, now)]
    for k in gone:
        data["sessions"].pop(k, None)
    return len(gone)


def create(ip="", ua=""):
    """make a session, return the id to put in the cookie (never stored)."""
    sid = secrets.token_urlsafe(32)
    now = _now()
    with _lock:
        data = _load()
        _prune(data)
        data["sessions"][_key(sid)] = {
            "created": now, "last_seen": now,
            "ip": (ip or "")[:64], "ua": (ua or "")[:200]}
        _save(data)
    return sid


def validate(sid):
    """the session record for a live id, or None. Touches last_seen."""
    if not sid:
        return None
    k = _key(sid)
    now = _now()
    with _lock:
        data = _load()
        rec = data["sessions"].get(k)
        if not isinstance(rec, dict):
            return None
        if _expired(rec, now):
            data["sessions"].pop(k, None)
            _save(data)
            return None
        if now - float(rec.get("last_seen", 0)) > _TOUCH_EVERY:
            rec["last_seen"] = now
            data["sessions"][k] = rec
            _save(data)
        return dict(rec)


def revoke(sid):
    """sign one session out; True when it existed."""
    if not sid:
        return False
    k = _key(sid)
    with _lock:
        data = _load()
        had = data["sessions"].pop(k, None) is not None
        if had:
            _save(data)
        return had


def revoke_all():
    """sign every session out (password/token change). Returns the count."""
    with _lock:
        data = _load()
        n = len(data["sessions"])
        if n:
            data["sessions"] = {}
            _save(data)
        return n


def active():
    """live sessions, newest activity first, without the stored hashes."""
    with _lock:
        data = _load()
        if _prune(data):
            _save(data)
        out = []
        for rec in data["sessions"].values():
            out.append({"created": rec.get("created"),
                        "last_seen": rec.get("last_seen"),
                        "ip": rec.get("ip", ""), "ua": rec.get("ua", "")})
    out.sort(key=lambda r: r.get("last_seen") or 0, reverse=True)
    return out


def count():
    return len(active())
