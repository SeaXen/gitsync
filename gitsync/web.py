"""gitsync web UI — a browser dashboard for everything the TUI does.

    gitsync web                       # http://127.0.0.1:7765 (this machine)
    gitsync web --port 7765 --bind 127.0.0.1
    gitsync web --bind 0.0.0.0        # LAN/proxy — auth is always required
    gitsync web --install             # always-on systemd service (binds 0.0.0.0)

Zero dependencies: Python standard library only (http.server, like
webhooks.py). The engine (config/sync/platforms/stars/tokencheck/diskutil/
scheduler) is called directly for fast, read-only and config-mutating
actions; long-running actions (sync, compare, doctor, fix, restore, mirror,
disk) are spawned as `python3 -m gitsync <subcommand>` subprocesses — the
same command the timer runs — and streamed to the browser over Server-Sent
Events. No sync logic is reimplemented here.

Security model (the config holds platform tokens, so this is the priority):
  * `gitsync web` (foreground) binds 127.0.0.1 by default — this machine only;
    the always-on service (`gitsync web --install`) and Docker bind 0.0.0.0
    (LAN). Authentication is ALWAYS required on every request, whatever the
    bind; the chosen bind/port is remembered in settings.web
  * a random access token (CONFIG_DIR/web.token, 0600) plus an optional
    username + password (PBKDF2 hash, 0600) guard every /api call — via
    `Authorization: Bearer` or an HttpOnly, SameSite=Strict cookie
    (hmac.compare_digest on bytes)
  * a token is NEVER returned in any API response or HTML page — only state
    (set/missing/gh/glab). The first-run token is not printed when stdout is
    the systemd journal or a pipe (read CONFIG_DIR/web.token instead)
  * state-changing requests are CSRF-guarded (custom header for cookie
    sessions) and DNS-rebinding-guarded (Host/Origin allowlist on every
    request, including the public login/auth routes)
  * every outgoing body/SSE line is passed through a redactor that strips
    any known secret, on top of the engine's own sync.redact()
  * all subprocesses are argv lists (never a shell); all names/paths/URLs are
    validated; static files and local-folder targets are confined (no
    traversal); config writes are serialized and atomic
"""

import hashlib
import hmac
import html
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from . import config as C
from . import platforms as P
from . import sync as SYNC
from . import stars as ST
from . import tokencheck as TK
from . import diskutil as DK
from . import scheduler as S
from . import notify as NT
from . import permissions as PERM
from . import webhooks as WH

DEFAULT_PORT = 7765
# `gitsync web` in the foreground is for the person on this machine, so it
# binds localhost by default. The always-on service (`gitsync web --install`)
# and Docker are the "share it on the LAN" paths and bind 0.0.0.0. Any bind
# still requires authentication on every request — never an open port.
DEFAULT_BIND = "127.0.0.1"
LAN_BIND = "0.0.0.0"
WEBUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")

# A browser that mistypes a URL should get a page, not {"error": "not found"}.
# Self-contained on purpose: it must render even if the asset path is what was
# wrong. Uses the same palette as the app.
_NOT_FOUND_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>404 — gitsync</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;
 background:#0d1117;color:#e6edf3;
 font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.card{max-width:420px;width:100%;background:#161b22;border:1px solid #263040;
 border-radius:12px;padding:26px;box-shadow:0 8px 24px rgba(0,0,0,.35)}
.code{font:600 12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 letter-spacing:.12em;text-transform:uppercase;color:#8b949e}
h1{font-size:19px;margin:8px 0 6px}
p{color:#c9d1d9;margin:0 0 18px}
a{display:inline-flex;align-items:center;min-height:36px;padding:7px 14px;
 border-radius:9px;background:#388bfd;color:#040c18;font-weight:600;
 text-decoration:none}
a:hover{text-decoration:underline}
code{color:#8b949e}
</style></head><body><div class="card">
<div class="code">404 · no such page</div>
<h1>That page does not exist</h1>
<p><code>__PATH__</code> is not part of the gitsync web UI. Check the address, or
start from the dashboard.</p>
<a href="/">Back to the dashboard</a>
</div></body></html>
"""
MAX_BODY = 1 << 20                      # 1 MB request cap
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._/*?-]{1,128}$")

_TTY = sys.stdout.isatty()
_UNDER_SYSTEMD = bool(os.environ.get("JOURNAL_STREAM")
                      or os.environ.get("INVOCATION_ID"))


def _c(code, text):
    if not _TTY:
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def _say(msg):
    print(msg, flush=True)


def _audit(msg):
    """one redacted, timestamped line to gitsync.log for security-relevant
    web events (auth failures, guard rejections, 5xx, mutations, job end)."""
    line = "[%s] web: %s" % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _redactor().redact(msg))
    try:
        C.ensure_dirs()
        if os.path.exists(C.log_file()) and os.path.getsize(C.log_file()) > (2 << 20):
            os.replace(C.log_file(), C.log_file() + ".1")
        with open(C.log_file(), "a") as f:
            f.write(line + "\n")
        os.chmod(C.log_file(), 0o600)
    except OSError:
        pass


class _Http(Exception):
    """raised inside a config transaction to abort with (code, message)."""

    def __init__(self, code, msg):
        self.code = code
        self.msg = msg


# ---------------------------------------------------------------- token

def _token_path():
    return os.path.join(C.CONFIG_DIR, "web.token")


def get_web_token():
    """(token, freshly_generated). Order: $GITSYNC_WEB_TOKEN, then the
    stored file, then generate+persist a new one."""
    env = (os.environ.get("GITSYNC_WEB_TOKEN") or "").strip()
    if env:
        return env, False
    try:
        with open(_token_path()) as f:
            tok = f.read().strip()
            if tok:
                return tok, False
    except OSError:
        pass
    tok = secrets.token_urlsafe(32)
    try:
        C.ensure_dirs()
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(_token_path(), flags, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(tok)
    except OSError:
        pass
    return tok, True


# ---------------------------------------------------------------- password auth

def _auth_path():
    return os.path.join(C.CONFIG_DIR, "web.auth.json")


_PBKDF2_ITER = 200_000


def get_auth():
    """{'username','salt','hash','iterations'} or None when unset."""
    try:
        with open(_auth_path()) as f:
            d = json.load(f)
        if d.get("salt") and d.get("hash") and d.get("username"):
            return d
    except (OSError, ValueError):
        pass
    return None


def set_password(username, password):
    if not (username or "").strip():
        raise ValueError("a username is required")
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                             _PBKDF2_ITER)
    data = {"username": username.strip(), "salt": salt.hex(),
            "hash": dk.hex(), "iterations": _PBKDF2_ITER,
            "set_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    C.ensure_dirs()
    tmp = _auth_path() + ".tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, _auth_path())
    try:
        os.chmod(_auth_path(), 0o600)
    except OSError:
        pass


def clear_password():
    try:
        os.remove(_auth_path())
        return True
    except OSError:
        return False


def check_password(username, password):
    a = get_auth()
    if not a:
        return False
    if not hmac.compare_digest(str(username or "").encode("utf-8"),
                               str(a.get("username", "")).encode("utf-8")):
        return False
    try:
        salt = bytes.fromhex(a["salt"])
        iterations = int(a.get("iterations", _PBKDF2_ITER))
    except (ValueError, KeyError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                             salt, iterations)
    return hmac.compare_digest(dk.hex(), str(a.get("hash", "")))


class _Throttle:
    """in-memory brake on guessing, keyed per client+account, lost on
    restart (documented trade-off)."""

    def __init__(self, max_fails=8, window=120):
        self.max_fails = max_fails
        self.window = window
        self._fails = {}
        self._lock = threading.Lock()

    def locked(self, key):
        with self._lock:
            now = time.time()
            recent = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = recent
            return len(recent) >= self.max_fails

    def fail(self, key):
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key):
        with self._lock:
            self._fails.pop(key, None)


_pw_throttle = _Throttle(max_fails=8, window=120)
_tok_throttle = _Throttle(max_fails=40, window=120)


# ---------------------------------------------------------------- redaction

class Redactor:
    """Strip any known secret from text before it leaves the process.

    The engine already redacts git error text (sync.redact), and it never
    serialises a token — but the web layer is a new exit path, so it gets its
    own pass as defence in depth. The unit test plants real token strings and
    greps every response for them.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._secrets = set()
        self.reload()

    def reload(self):
        secs = set()
        try:
            cfg = C.load_config()
            for p in (cfg.get("platforms") or {}).values():
                t = p.get("token")
                if t:
                    secs.add(t)
            n = (cfg.get("settings") or {}).get("notify") or {}
            for k in ("telegram_token", "discord_webhook"):
                v = n.get(k)
                if v:
                    secs.add(v)
        except Exception:
            pass
        try:
            if WH.get_secret():
                secs.add(WH.get_secret())
        except Exception:
            pass
        for k in ("GITHUB_TOKEN", "GITLAB_TOKEN", "FORGEJO_TOKEN",
                  "GITEA_TOKEN", "BITBUCKET_TOKEN", "GITSYNC_WEB_TOKEN"):
            v = os.environ.get(k)
            if v:
                secs.add(v)
        with self._lock:
            # a 1-2 char "secret" would shred unrelated text; ignore tiny ones
            self._secrets = {s for s in secs if len(s) >= 6}

    def redact(self, text):
        if not text or not isinstance(text, str):
            return text
        with self._lock:
            secs = list(self._secrets)
        for s in secs:
            if s in text:
                text = text.replace(s, "***")
        try:
            return SYNC.redact(text)
        except Exception:
            return text


_redactor_singleton = None
_redactor_lock = threading.Lock()


def _redactor():
    """lazy singleton — constructing at import time would read the live
    config (and load every real token into memory) merely on `import web`."""
    global _redactor_singleton
    with _redactor_lock:
        if _redactor_singleton is None:
            _redactor_singleton = Redactor()
        return _redactor_singleton


# ---------------------------------------------------------------- caches

_cache = {"sched": ("unknown", 0.0), "next": (None, 0.0), "health": (None, 0.0),
          "platforms": (None, 0.0)}
_cache_lock = threading.Lock()


def _cached(key, ttl, fn, default=None):
    now = time.time()
    with _cache_lock:
        val, at = _cache.get(key, (default, 0.0))
        # cache on the timestamp, not the value: a legitimate None (no
        # timer installed, no next run) must be cached too, or /api/status
        # shells out to systemctl on every poll
        if at and now - at < ttl:
            return val
    try:
        val = fn()
    except Exception:
        val = default
    with _cache_lock:
        _cache[key] = (val, time.time())
    return val


def _invalidate(*keys):
    """drop cached probes after a mutation so the next GET is fresh."""
    with _cache_lock:
        for key in keys:
            _cache.pop(key, None)


def _cached_scheduler():
    return _cached("sched", 15, lambda: _safe(S.status, "unknown"), "unknown")


def _cached_next_run():
    return _cached("next", 15, lambda: _safe(S.next_run, None), None)


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------- jobs

class Job:
    """one subprocess run (sync or a tool), streamed to any number of
    SSE subscribers via a shared condition variable."""

    MAX_LINES = 20000

    def __init__(self, kind, argv, mutating=True):
        self.id = secrets.token_hex(8)
        self.kind = kind
        self.argv = argv
        self.mutating = mutating
        self.lines = []
        self.progress = {"done": 0, "total": 0, "repo": "", "status": ""}
        self.done = False
        self.exit = None
        self.error = None
        self.started = time.time()
        self.finished = None
        self.cond = threading.Condition()
        self.proc = None

    def snapshot(self):
        with self.cond:
            return {"job_id": self.id, "kind": self.kind,
                    "progress": dict(self.progress), "done": self.done,
                    "exit": self.exit,
                    "error": _redactor().redact(self.error)
                    if self.error else None,
                    "elapsed": round((self.finished or time.time())
                                     - self.started, 1)}


_jobs = {}
_jobs_lock = threading.Lock()
_mutating_busy = threading.Lock()      # web-level: one mutating job at a time
_current_sync = None                   # most recent sync job (for /sync/state)


def _run_job(job):
    try:
        env = dict(os.environ, PYTHONPATH=_app_dir())
        # detach from the server's session so Ctrl-C on `gitsync web` does not
        # SIGINT an in-flight push; stdout is still piped for SSE streaming
        job.proc = subprocess.Popen(job.argv, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    env=env, cwd=_app_dir(),
                                    start_new_session=True)
        for line in job.proc.stdout:
            line = _redactor().redact(line.rstrip("\n"))
            with job.cond:
                job.lines.append(line)
                if len(job.lines) > Job.MAX_LINES:
                    del job.lines[:len(job.lines) - Job.MAX_LINES]
                parsed = SYNC.parse_progress_line(line)
                if parsed:
                    d, t, st, repo = parsed
                    if t:
                        job.progress["total"] = t
                    job.progress["done"] = max(job.progress["done"], d)
                    job.progress["status"] = st
                    job.progress["repo"] = repo
                job.cond.notify_all()
        job.proc.wait()
        job.exit = job.proc.returncode
        _remember_job_result(job)
    except Exception as e:
        job.error = _redactor().redact(str(e)[:200])
        with job.cond:
            job.lines.append("error: %s" % job.error)
    finally:
        with job.cond:
            job.done = True
            job.finished = time.time()
            job.cond.notify_all()
        if job.mutating:
            try:
                _mutating_busy.release()
            except Exception:
                pass
        _audit("job %s (%s) finished: exit=%s elapsed=%ss"
               % (job.kind, job.id, job.exit,
                  round((job.finished or time.time()) - job.started, 1)))
        _redactor().reload()


def start_job(kind, argv, mutating=True):
    """returns a Job, or None if a mutating job is already running."""
    if mutating and not _mutating_busy.acquire(blocking=False):
        return None
    job = Job(kind, argv, mutating)
    with _jobs_lock:
        _jobs[job.id] = job
        if len(_jobs) > 50:                     # bound memory: drop oldest
            for old in sorted(_jobs.values(), key=lambda j: j.started)[:25]:
                if old.done:
                    _jobs.pop(old.id, None)
    try:
        threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    except Exception:
        # never leak the mutex if the thread could not start
        with _jobs_lock:
            _jobs.pop(job.id, None)
        if mutating:
            try:
                _mutating_busy.release()
            except Exception:
                pass
        raise
    return job


def _engine_sync_busy():
    return SYNC.engine_busy()


# ---- compare cache, so the Repos page shows the matrix with no click ----
_CMP_CACHE = {"at": 0.0, "data": None}
_CMP_TTL = 300.0


def _remember_job_result(job):
    """keep the last compare matrix per route (and drop it when a sync ran).

    A compare is 40 repos x 4 sites of git ls-remote (~15s); caching it
    makes the Repos page instant on every revisit, and a finished sync
    invalidates it because the answer just changed.

    Keyed by route name: compare --json prints one matrix per route, and a
    single-route compare must not throw the other routes away.
    """
    if job.kind == "compare" and job.exit == 0:
        with job.cond:
            lines = list(job.lines)
        found = {}
        for line in lines:
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if (isinstance(data, dict) and "repos" in data
                    and data.get("route")):
                found[data["route"]] = data
        if not found:
            return
        fresh = (time.time() - _CMP_CACHE["at"]) <= _CMP_TTL
        prev = _CMP_CACHE["data"] if fresh and isinstance(
            _CMP_CACHE["data"], dict) else {}
        prev = dict(prev)
        prev.update(found)
        _CMP_CACHE["at"] = time.time()
        _CMP_CACHE["data"] = prev
    elif job.kind in ("sync", "fix", "restore", "mirror"):
        _CMP_CACHE["at"] = 0.0
        _CMP_CACHE["data"] = None


def _drop_compare_cache():
    """a route edit changes the answer (prune_repos, filters), so the cached
    matrix is no longer the current configuration."""
    _CMP_CACHE["at"] = 0.0
    _CMP_CACHE["data"] = None

def _app_dir():
    return C.APP_DIR


def _is_loopback(bind):
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return bind in ("localhost",)


# ---------------------------------------------------------------- validation

def _valid_name(s):
    return bool(s) and bool(_NAME_RE.match(s))


def _clean_repo_list(s):
    """comma list -> validated names; rejects anything with shell/space junk.
    Argv lists mean injection is impossible, but nonsense is still rejected."""
    out = []
    for part in (s or "").split(","):
        name = part.strip()
        if not name:
            continue
        if not _REPO_RE.match(name):
            raise ValueError("bad repo name: %r" % name[:40])
        out.append(name)
    return out


def _confined_path(path, root):
    """realpath of path if it sits inside root, else None (no traversal)."""
    try:
        full = os.path.realpath(os.path.expanduser(path))
        root_real = os.path.realpath(root)
        return full if (full == root_real
                        or full.startswith(root_real + os.sep)) else None
    except (OSError, ValueError):
        return None


def _valid_platform_url(url):
    """http(s) only, with a real host — stops the stored token being relayed
    to an arbitrary scheme/host (SSRF / confused deputy)."""
    if not url:
        return True
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    return True


def _valid_local_path(path):
    """a local-folder target must be absolute-ish and not a sensitive system
    dir; confined enough to avoid clobbering / , /etc, home config, etc."""
    if not path:
        return False
    full = os.path.realpath(os.path.expanduser(path))
    if full in ("/",) or full.startswith("/etc") or \
            full.startswith("/sys") or full.startswith("/proc") or \
            full.startswith("/boot") or full == os.path.expanduser("~"):
        return False
    if _confined_path(full, C.CONFIG_DIR) == full:
        return False
    return True


# ---------------------------------------------------------------- public views

def _token_state(p):
    if p.get("type") == "local":
        return "local"
    if p.get("auth") == "oauth":
        return "oauth"
    if p.get("token"):
        return "set"
    if p.get("auth") == "gh":
        return "gh"
    if p.get("auth") == "glab":
        return "glab"
    return "missing"


def _public_platform(name, p):
    res = {"name": name, "type": p.get("type", "?"),
            "enabled": bool(p.get("enabled")),
            "token_state": _token_state(p),
            "url": p.get("url", ""), "owner": p.get("owner", ""),
            "path": p.get("path", ""), "protocol": p.get("protocol", "http"),
            "ssh_port": p.get("ssh_port"),
            "url_template": p.get("url_template", ""),
            "user": p.get("user", ""), "ns_id": p.get("ns_id", ""),
            "affiliation": p.get("affiliation", "")}
    if p.get("type") == "github":
        try:
            plat = P.make(name, p)
            if hasattr(plat, "rate_limit_info"):
                res["rate_limit"] = plat.rate_limit_info()
        except Exception:
            pass
    return res


def _public_settings(cfg):
    """settings with notify secrets reduced to booleans — the other place raw
    webhook/telegram credentials live, so it gets the same care as platforms."""
    st = dict(cfg.get("settings") or {})
    notify = dict(st.get("notify") or {})
    st["notify"] = {
        "on": notify.get("on", "failure"),
        "discord_set": bool(notify.get("discord_webhook")),
        "telegram_set": bool(notify.get("telegram_token")
                             and notify.get("telegram_chat_id")),
    }
    return st


def _route_view(cfg, r):
    paused = set(r.get("paused_targets") or [])
    return {"name": r.get("name"), "source": r.get("source"),
            "targets": list(r.get("targets") or []),
            "direction": r.get("direction") or "one-way",
            "enabled": r.get("enabled") is not False,
            "paused_targets": sorted(paused),
            "schedule": r.get("schedule"),
            "effective_schedule": C.route_schedule(cfg, r),
            "next": SYNC.next_due_text(r, cfg),
            "include": list(r.get("include") or []),
            "exclude": list(r.get("exclude") or []),
            "include_file": r.get("include_file", ""),
            "workers": r.get("workers"), "mode": r.get("mode"),
            "mesh_conflict": r.get("mesh_conflict"),
            "effective_mode": C.route_mode(cfg, r),
            "prune_repos": C.prune_targets(cfg, r)}


def _public_record(r):
    if not r:
        return None
    fails = [{"route": f.get("route"), "repo": f.get("repo"),
              "error": _redactor().redact(str(f.get("error", ""))[:200])}
             for f in (r.get("failures") or [])]
    return {"finished_at": r.get("finished_at"), "ok": r.get("ok", 0),
            "partial": r.get("partial", 0), "failed": r.get("failed", 0),
            "repos": r.get("repos", 0), "elapsed_sec": r.get("elapsed_sec"),
            "routes": r.get("routes", 0), "failures": fails,
            "dry_run": bool(r.get("dry_run")),
            "would_change": r.get("would_change")}


def _probe(name, p):
    try:
        plat = P.make(name, p)
        ok, info = plat.test()
        count = None
        if ok and p.get("enabled"):
            try:
                count = len(plat.list_repos())
            except Exception:
                count = None
    except Exception as e:
        ok, info, count = False, _redactor().redact(str(e)[:120]), None
    row = _public_platform(name, p)
    row.update(ok=bool(ok), info=_redactor().redact(info or ""),
               repo_count=count)
    return row


def _probe_all(cfg):
    plats = list((cfg.get("platforms") or {}).items())
    if not plats:
        return []
    rows = [None] * len(plats)
    with ThreadPoolExecutor(max_workers=max(1, len(plats))) as ex:
        futs = {ex.submit(_probe, n, p): i for i, (n, p) in enumerate(plats)}
        for f in as_completed(futs):
            rows[futs[f]] = f.result()
    return rows


def _tool_argv(kind, b):
    """(argv_after-'gitsync', mutating) for a Tools action — reuses the CLI.
    Accepts the 'doctorfix' card id as an alias for doctor --fix so the
    frontend contract can never silently drift again."""
    route = (b.get("route") or "").strip()
    if kind in ("compare",):
        # --json: the last line the job prints is the matrix the Repos page
        # renders (progress lines come first)
        return ["compare", "--json"] + (["--route", route] if route else []), False
    if kind == "doctor":
        return ["doctor"] + (["--fix"] if b.get("fix") else []), bool(b.get("fix"))
    if kind == "doctorfix":                     # alias for doctor --fix
        return ["doctor", "--fix"], True
    if kind == "fix":
        argv = ["fix"] + (["--route", route] if route else [])
        if b.get("dry_run"):
            argv.append("--dry-run")
        return argv, not b.get("dry_run")
    if kind == "prune":
        # deletion is opt-in per target and per run: the route must name the
        # target in prune_repos, and this job only ever runs from an
        # explicit click (never from the timer)
        return ["prune"] + (["--route", route] if route else []) + ["--yes"], True
    if kind == "tokens":
        return ["tokens"], False
    if kind == "disk":
        argv = ["disk"]
        if b.get("orphans"):
            argv.append("--orphans")
        if b.get("top"):
            argv += ["--top", str(int(b["top"]))]
        return argv, False
    if kind == "gc":
        argv = ["disk", "--gc"]
        if b.get("older_than"):
            argv += ["--older-than", str(int(b["older_than"]))]
        if b.get("aggressive"):
            argv.append("--aggressive")
        return argv, True
    if kind == "restore":
        to = (b.get("to") or "").strip()
        if not _valid_name(to.split(",")[0]):
            raise ValueError("restore needs --to PLATFORM")
        argv = ["restore", "--to", to]
        repos = _clean_repo_list(b.get("repos"))
        if repos:
            argv += ["--repos", ",".join(repos)]
        if b.get("dry_run"):
            argv.append("--dry-run")
        if b.get("prune"):
            argv.append("--prune")
        if b.get("public"):
            argv.append("--public")
        return argv, True
    if kind == "mirror":
        repos = _clean_repo_list(b.get("repos"))
        if not repos:
            raise ValueError("mirror needs at least one repo")
        argv = ["mirror"] + repos
        if b.get("src") and _valid_name(b["src"]):
            argv += ["--from", b["src"]]
        if b.get("to") and _valid_name(b["to"]):
            argv += ["--to", b["to"]]
        return argv, True
    raise ValueError("unknown tool: %s" % kind)


# ---------------------------------------------------------------- bind/port

def resolve_bind_port(cli_bind, cli_port, cfg, default_bind=None):
    """Effective (bind, port): explicit CLI flag > saved preference > default.
    Pure, so it can be unit-tested without starting a server."""
    web = (cfg.get("settings") or {}).get("web") or {}
    bind = cli_bind or web.get("bind") or default_bind or DEFAULT_BIND
    port = cli_port or web.get("port") or DEFAULT_PORT
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return bind, port


def remember_bind_port(cfg, cli_bind, cli_port):
    """persist an explicitly-chosen bind/port so `gitsync web` (bare) reuses
    it. No-op when neither flag was given (the default/saved value stands)."""
    if not cli_bind and not cli_port:
        return
    web = cfg.setdefault("settings", {}).setdefault("web", {})
    if cli_bind:
        web["bind"] = cli_bind
    if cli_port:
        try:
            web["port"] = int(cli_port)
        except (TypeError, ValueError):
            pass
    C.save_config(cfg)


def _usable_ip(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_loopback or a.is_unspecified or a.is_link_local
                or a.is_multicast)


def _local_ips():
    """this machine's non-loopback IPv4 addresses, best guess first."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))            # no traffic; picks the route
        ip = s.getsockname()[0]
        s.close()
        if ip and _usable_ip(ip):
            ips.append(ip)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and _usable_ip(ip):
                ips.append(ip)
    except Exception:
        pass
    return ips


# ================================================================ HTTP layer

def make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = "gitsync-web"
        protocol_version = "HTTP/1.1"
        timeout = 30                          # reap half-open connections

        def log_message(self, *a):            # silence per-request stderr noise
            pass

        # ---- plumbing ----
        @property
        def _token(self):
            return self.server.token

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            if length > MAX_BODY:
                # drain the declared body so the client's send completes and
                # it reads a clean 413 (rejecting without reading desyncs the
                # connection and the client sees a reset)
                self.close_connection = True
                remaining = min(length, 64 << 20)
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                raise _BodyTooLarge()
            try:
                return json.loads(self.rfile.read(length).decode() or "{}")
            except ValueError:
                raise _BadJson()

        def _qs(self):
            if "?" not in self.path:
                return {}
            return urllib.parse.parse_qs(self.path.split("?", 1)[1])

        def _send(self, code, body_bytes, ctype, extra=None):
            try:
                extra = extra or {}
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body_bytes)))
                # one Cache-Control only: a caller-supplied value wins
                # (static assets say no-cache, JSON says no-store)
                self.send_header("Cache-Control",
                                 extra.get("Cache-Control", "no-store"))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                for k, v in extra.items():
                    if k.lower() == "cache-control":
                        continue
                    self.send_header(k, v)
                self.end_headers()
                # HEAD must advertise the same Content-Length but send no
                # body: proxies and uptime checks probe with HEAD, and a
                # 501 there reads as "the service is broken"
                if not getattr(self, "_head_only", False):
                    self.wfile.write(body_bytes)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _err(self, code, msg):
            self._json({"error": msg}, code)

        # ---- auth + guards ----
        def _supplied_token(self):
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return ("bearer", auth[7:].strip())
            for part in self.headers.get("Cookie", "").split(";"):
                part = part.strip()
                if part.startswith("gitsync_session="):
                    return ("cookie", part.split("=", 1)[1])
            return (None, "")

        def _authed(self):
            _kind, tok = self._supplied_token()
            if not tok:
                return False
            try:
                return hmac.compare_digest(tok.encode("utf-8"),
                                           self._token.encode("utf-8"))
            except (AttributeError, UnicodeError):
                return False

        def _client_ip(self):
            try:
                return self.client_address[0]
            except Exception:
                return "?"

        def _host_ok(self):
            host = self.headers.get("Host", "")
            h = host.rsplit(":", 1)[0].strip().strip("[]").lower() \
                if ":" in host and not host.strip().startswith("[") \
                else host.split(":")[0].strip().strip("[]").lower()
            allowed = ("localhost", "127.0.0.1", "::1", self.server.bind_host)
            if h in allowed or h in self.server.extra_hosts:
                return True
            try:
                ip = ipaddress.ip_address(h)
                return ip.is_loopback or ip.is_private
            except ValueError:
                if self.server.bind_host in ("0.0.0.0", "::"):
                    if h.endswith((".local", ".lan", ".home.arpa", ".internal")):
                        return True
                    if "." not in h and _NAME_RE.match(h):
                        return True
                return False

        def _guard(self, method):
            """(code, msg) on failure, else None. Host + CSRF + Origin."""
            if not self._host_ok():
                return 403, "bad Host header (possible DNS rebinding)"
            if method in ("POST", "PUT", "DELETE", "PATCH"):
                kind, _tok = self._supplied_token()
                # CSRF only threatens ambient (cookie) credentials; a Bearer
                # token is never attached automatically by a browser.
                if kind == "cookie" and \
                        self.headers.get("X-Gitsync-Request") != "1":
                    return 403, "missing CSRF header"
                origin = self.headers.get("Origin", "")
                if origin:
                    oh = origin.split("//", 1)[-1]
                    oh = oh.rsplit(":", 1)[0].strip().strip("[]").lower() \
                        if ":" in oh and not oh.startswith("[") \
                        else oh.split(":")[0].lower()
                    try:
                        ip = ipaddress.ip_address(oh)
                        if not (ip.is_loopback or ip.is_private):
                            return 403, "bad Origin"
                    except ValueError:
                        if self.server.bind_host in ("0.0.0.0", "::") and (
                                oh.endswith((".local", ".lan", ".home.arpa", ".internal"))
                                or ("." not in oh and _NAME_RE.match(oh))):
                            pass
                        elif oh not in ("localhost", self.server.bind_host) \
                                and oh not in self.server.extra_hosts:
                            return 403, "bad Origin"
            return None

        # ---- dispatch ----
        def _route(self, method):
            path = self.path.split("?", 1)[0]
            path = re.sub(r"/+$", "", path) or "/"
            try:
                if path == "/healthz":
                    if method == "GET":
                        return self._send(200, b"ok\n", "text/plain")
                    return self._err(405, "method not allowed")
                if path.startswith("/api"):
                    is_public = ((method == "POST" and path == "/api/session")
                                 or (method == "GET" and path == "/api/auth"))
                    # the DNS-rebinding Host guard applies to EVERY request,
                    # including the public login/auth routes
                    if not self._host_ok():
                        return self._err(403, "bad Host header")
                    if not is_public:
                        if not self._authed():
                            if not (method == "GET" and path == "/api/session"):
                                _audit("401 %s %s from %s"
                                       % (method, path, self._client_ip()))
                            return self._err(401, "unauthorized")
                        g = self._guard(method)
                        if g:
                            _audit("guard %s %s %s from %s"
                                   % (g[0], method, path, self._client_ip()))
                            return self._err(g[0], g[1])
                    for m, rgx, fn in ROUTES:
                        if m != method:
                            continue
                        mt = rgx.match(path)
                        if mt:
                            return fn(self, **mt.groupdict())
                    return self._err(404, "no such endpoint")
                if method != "GET":
                    return self._err(405, "method not allowed")
                return self._serve_static(path)
            except _BodyTooLarge:
                return self._err(413, "request body too large")
            except _BadJson:
                return self._err(400, "invalid JSON body")
            except _Http as h:
                return self._err(h.code, h.msg)
            except Exception as e:
                _audit("500 %s %s: %s" % (method, path,
                                          _redactor().redact(str(e)[:200])))
                return self._err(500, "internal error")

        def do_GET(self):
            self._route("GET")

        def do_HEAD(self):
            # same headers as GET, no body — see _send()
            self._head_only = True
            try:
                self._route("GET")
            finally:
                self._head_only = False

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

        # ---- static ----
        def _serve_static(self, path):
            if path in ("/", ""):
                path = "/index.html"
            root = os.path.realpath(WEBUI_DIR)
            clean = os.path.normpath(path).lstrip("/\\")
            full = os.path.realpath(os.path.join(root, clean))
            if full != root and not full.startswith(root + os.sep):
                return self._err(403, "forbidden")
            if not os.path.isfile(full):
                # a browser navigation gets a page; fetch()/curl still gets
                # the JSON error (they do not ask for text/html)
                accept = self.headers.get("Accept") or ""
                if "text/html" in accept:
                    return self._send(
                        404,
                        _NOT_FOUND_HTML.replace("__PATH__",
                                                html.escape(path)).encode(),
                        "text/html; charset=utf-8")
                return self._err(404, "not found")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                body = f.read()
            self._send(200, body, ctype, {"Cache-Control": "no-cache"})

        # ---- SSE ----
        def _sse(self, job):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")   # defeat proxy buffer
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.connection.settimeout(90)
            except OSError:
                pass

            def send(obj):
                try:
                    self.wfile.write(
                        ("data: " + json.dumps(obj) + "\n\n").encode())
                    self.wfile.flush()
                    return True
                except (socket.timeout, OSError):
                    return False     # stalled/closed client: drop it

            # snapshot under the lock, write OUTSIDE it — otherwise a slow
            # reader blocks the producer, the subprocess stdout pipe fills,
            # and every mutating job wedges on the mutex until restart
            sent = 0
            while True:
                with job.cond:
                    lines = job.lines[sent:]
                    sent = len(job.lines)
                    snap = dict(job.progress)
                    done, ex, er = job.done, job.exit, job.error
                    el = round((job.finished or time.time()) - job.started, 1)
                for line in lines:
                    if not send({"line": line}):
                        return
                if not send({"progress": snap, "done": done, "exit": ex,
                             "error": _redactor().redact(er) if er else None,
                             "elapsed": el}):
                    return
                if done:
                    break
                with job.cond:
                    if not job.cond.wait(timeout=15):
                        if not send({"ping": True}):
                            return

    return Handler


class _BodyTooLarge(Exception):
    pass


class _BadJson(Exception):
    pass


# ---------------------------------------------------------------- endpoint handlers
# module level (not closures in make_handler) so each is reachable/testable;
# they talk to the request only through `handler` and read server state via
# handler.server.

def h_healthz(handler):
    handler._send(200, b"ok\n", "text/plain")


def h_auth_info(handler):
    a = get_auth()
    authed = handler._authed()
    handler._json({"password_set": bool(a),
                   # the username is only revealed to an authenticated caller
                   "username": a.get("username") if (a and authed) else "",
                   "token_login": True})


def h_login(handler):
    b = handler._body()
    ip = handler._client_ip()
    secure = "; Secure" \
        if handler.headers.get("X-Forwarded-Proto") == "https" else ""
    cookie = "gitsync_session=%s; Path=/; HttpOnly; SameSite=Strict%s" \
        % (handler._token, secure)
    if "username" in b or "password" in b:
        if not get_auth():
            return handler._err(400, "password sign-in is not set up; "
                                     "use the access token")
        key = "%s|%s" % (ip, b.get("username", ""))
        if _pw_throttle.locked(key):
            _audit("429 password login locked from %s" % ip)
            return handler._err(429, "too many attempts — wait a minute")
        if check_password(b.get("username", ""), b.get("password", "")):
            _pw_throttle.reset(key)
            return handler._send(200, b'{"ok":true}', "application/json",
                                 {"Set-Cookie": cookie})
        _pw_throttle.fail(key)
        time.sleep(0.25)                     # slow online guessing
        _audit("401 bad password for '%s' from %s" % (b.get("username"), ip))
        return handler._err(401, "invalid username or password")
    tok = b.get("token")
    if tok is not None and not isinstance(tok, str):
        return handler._err(400, "invalid token")
    tok = (tok or "").strip()
    if _tok_throttle.locked(ip):
        return handler._err(429, "too many attempts — wait a minute")
    if tok and hmac.compare_digest(tok.encode("utf-8"),
                                   handler._token.encode("utf-8")):
        _tok_throttle.reset(ip)
        return handler._send(200, b'{"ok":true}', "application/json",
                             {"Set-Cookie": cookie})
    _tok_throttle.fail(ip)
    return handler._err(401, "invalid token")


def h_logout(handler):
    handler._send(200, b'{"ok":true}', "application/json",
                  {"Set-Cookie": "gitsync_session=; Path=/; HttpOnly; "
                                 "SameSite=Strict; Max-Age=0"})


def h_session(handler):
    handler._json({"authed": True, "version": __version__})


def h_set_password(handler):
    b = handler._body()
    if b.get("disable"):
        clear_password()
        _audit("web password disabled")
        return handler._json({"ok": True, "password_set": False,
                              "message": "password sign-in disabled"})
    try:
        set_password(b.get("username", ""), b.get("password", ""))
    except ValueError as e:
        return handler._err(400, str(e))
    _audit("web password set for '%s'" % (b.get("username") or ""))
    handler._json({"ok": True, "password_set": True,
                   "message": "password set — sign in with it next time"})


def h_status(handler):
    cfg = C.load_config()
    recs = SYNC.last_records(1)
    last = recs[-1] if recs else None
    syncing = bool(_current_sync and not _current_sync.done) or _engine_sync_busy()
    handler._json({
        "version": __version__,
        "syncing": syncing,
        "paused": bool(C.setting(cfg, "paused", False)),
        "schedule": C.setting(cfg, "schedule", "hourly"),
        "scheduler": _cached_scheduler(),
        "next_run": _cached_next_run(),
        "config_file": C.CONFIG_FILE, "mirror_dir": C.MIRROR_DIR,
        "log_file": C.log_file(), "bind": handler.server.bind_host,
        "port": handler.server.port,
        "last_run": _public_record(last) if last else None,
        "counts": {"platforms": len(cfg.get("platforms") or {}),
                   "routes": len(C.routes(cfg))},
    })


_device_flow_lock = threading.Lock()
_active_device_flows = {}
MAX_ACTIVE_DEVICE_FLOWS = 25


def h_pause(handler):
    b = handler._body()
    requested_paused = b.get("paused")

    def do(cfg):
        paused = requested_paused
        if paused is None:
            paused = not C.setting(cfg, "paused", False)
        cfg.setdefault("settings", {})["paused"] = bool(paused)
        return {"ok": True, "paused": bool(paused)}

    payload, code = _locked(do)
    if code == 200:
        _audit("web: sync %s globally" % ("paused" if payload["paused"] else "resumed"))
    handler._json(payload, code)


def h_github_device_start(handler):
    try:
        init_data = P.github_device_flow_start()
    except Exception as e:
        return handler._err(502, "failed to initiate GitHub device flow: %s" % str(e)[:160])

    if not isinstance(init_data, dict) or "device_code" not in init_data or "user_code" not in init_data:
        return handler._err(502, "invalid response from GitHub device authorization")

    flow_id = secrets.token_hex(16)
    now = time.time()
    with _device_flow_lock:
        expired = [fid for fid, f in _active_device_flows.items() if now - f.get("created_at", 0) > 900]
        for fid in expired:
            _active_device_flows.pop(fid, None)

        if len(_active_device_flows) >= MAX_ACTIVE_DEVICE_FLOWS:
            oldest_fid = min(_active_device_flows.keys(), key=lambda k: _active_device_flows[k].get("created_at", 0))
            _active_device_flows.pop(oldest_fid, None)

        _active_device_flows[flow_id] = {
            "device_code": init_data["device_code"],
            "user_code": init_data["user_code"],
            "verification_uri": init_data.get("verification_uri", "https://github.com/login/device"),
            "expires_in": init_data.get("expires_in", 900),
            "interval": int(init_data.get("interval", 5)),
            "created_at": now,
            "last_poll_at": 0.0,
        }

    handler._json({
        "flow_id": flow_id,
        "user_code": init_data["user_code"],
        "verification_uri": init_data.get("verification_uri", "https://github.com/login/device"),
        "expires_in": init_data.get("expires_in", 900),
        "interval": int(init_data.get("interval", 5)),
    })


def h_github_device_poll(handler):
    b = handler._body()
    flow_id = b.get("flow_id")
    with _device_flow_lock:
        flow = _active_device_flows.get(flow_id)
        if not flow:
            return handler._err(404, "flow not found or expired")

        now = time.time()
        min_interval = flow.get("interval", 5)
        last_poll = flow.get("last_poll_at", 0.0)
        if now - last_poll < (min_interval - 0.5):
            return handler._json({"status": "slow_down", "interval": min_interval})

        flow["last_poll_at"] = now
        device_code = flow["device_code"]

    res = P.github_device_flow_poll(device_code)
    status = res.get("status")

    if status == "ok":
        token = res["token"]
        raw_name = (b.get("platform_name") or "").strip()

        temp_plat = P.GitHubPlatform("temp", {"token": token, "auth": "oauth"})
        username = temp_plat.user() or ""

        def do(cfg):
            plats = cfg.setdefault("platforms", {})
            if raw_name:
                plat_name = raw_name
                if not _valid_name(plat_name):
                    raise _Http(400, "invalid platform name")
                if plat_name in plats and plats[plat_name].get("type") != "github":
                    raise _Http(409, "platform '%s' exists and is not github" % plat_name)
            else:
                if "github" not in plats:
                    plat_name = "github"
                elif username and ("github-" + username) not in plats:
                    plat_name = "github-" + username
                else:
                    idx = 2
                    while ("github-%d" % idx) in plats:
                        idx += 1
                    plat_name = "github-%d" % idx

            pcfg = plats.get(plat_name) or {"type": "github"}
            pcfg["type"] = "github"
            pcfg["enabled"] = True
            pcfg["token"] = token
            pcfg["auth"] = "oauth"
            if username:
                pcfg["owner"] = username
            plats[plat_name] = pcfg
            return {
                "status": "ok",
                "user": username,
                "platform": plat_name,
                "message": "Connected to GitHub as @%s" % (username or plat_name),
            }

        payload, code = _locked(do)
        if code != 200:
            return handler._json(payload, code)

        with _device_flow_lock:
            _active_device_flows.pop(flow_id, None)

        _redactor().reload()
        _audit("web: connected GitHub platform '%s' as @%s via device flow" % (payload["platform"], username))
        return handler._json(payload)

    elif status == "slow_down":
        with _device_flow_lock:
            if flow_id in _active_device_flows:
                curr_int = _active_device_flows[flow_id].get("interval", 5)
                new_int = res.get("interval") or (curr_int + 5)
                _active_device_flows[flow_id]["interval"] = new_int
            else:
                new_int = res.get("interval", 10)
        return handler._json({"status": "slow_down", "interval": new_int})

    elif status == "pending":
        return handler._json({"status": "pending"})

    elif status == "expired":
        with _device_flow_lock:
            _active_device_flows.pop(flow_id, None)
        return handler._json({"status": "expired", "error": res.get("error", "authorization expired")})

    else:
        return handler._json({"status": "error", "error": res.get("error", "authorization failed")})


def h_platforms(handler):
    cfg = C.load_config()
    if (handler._qs().get("probe") or [None])[0] == "0":
        rows = [_public_platform(n, p)
                for n, p in (cfg.get("platforms") or {}).items()]
        return handler._json({"platforms": rows})
    rows = _cached("platforms", 15,
                   lambda: _probe_all(C.load_config()), [])
    handler._json({"platforms": rows})


def h_platform_add(handler):
    b = handler._body()
    name = (b.get("name") or "").strip()
    ptype = (b.get("type") or "").strip()
    if not _valid_name(name):
        raise _Http(400, "invalid platform name")
    if ptype not in C.PLATFORM_TYPES:
        raise _Http(400, "unknown platform type")
    if not _valid_platform_url(b.get("url", "")):
        raise _Http(400, "url must be http(s) with a bare host")
    if ptype == "local":
        if not _valid_local_path(b.get("path", "")):
            raise _Http(400, "local path must be a writable, non-system folder")
    elif not (b.get("path") or "").strip() and ptype == "local":
        raise _Http(400, "local platforms need a path")

    def do(cfg):
        if name in (cfg.get("platforms") or {}):
            raise _Http(400, "platform '%s' already exists" % name)
        spec = C.platform_template(
            ptype, name, url=b.get("url"), token=b.get("token"),
            owner=b.get("owner"), path=b.get("path"),
            protocol=b.get("protocol"), ssh_port=b.get("ssh_port"))
        if b.get("url_template"):
            spec["url_template"] = b["url_template"]
        if b.get("user"):
            spec["user"] = b["user"]
        if b.get("ns_id"):
            spec["ns_id"] = b["ns_id"]
        if b.get("affiliation"):
            spec["affiliation"] = b["affiliation"]
        C.add_platform(cfg, name, spec)
        _audit("platform added: %s (%s)" % (name, ptype))
        return {"ok": True, "platform": _public_platform(name, spec)}

    payload, code = _locked(do)
    if code != 200:
        return handler._json(payload, code)
    _redactor().reload()
    _invalidate("platforms", "health")
    ok, info = False, ""
    try:
        ok, info = P.make(name, C.load_config()["platforms"][name]).test()
    except Exception as e:
        info = str(e)[:120]
    handler._json({"ok": True, "platform": payload["platform"],
                   "test": {"ok": bool(ok),
                            "info": _redactor().redact(info or "")}}, 201)


def h_platform_edit(handler, name):
    if not _valid_name(name):
        raise _Http(400, "invalid platform name")
    b = handler._body()

    def do(cfg):
        p = (cfg.get("platforms") or {}).get(name)
        if not p:
            raise _Http(404, "no such platform")
        if p.get("type") == "local":
            if b.get("path"):
                if not _valid_local_path(b["path"]):
                    raise _Http(400, "invalid local path")
                p["path"] = os.path.expanduser(b["path"])
        else:
            if b.get("url") is not None and b["url"] != "":
                if not _valid_platform_url(b["url"]):
                    raise _Http(400, "url must be http(s) with a bare host")
            # token is write-only: an empty/absent field means "keep it"
            if (b.get("token") or "").strip():
                p["token"] = b["token"].strip()
            for f in ("url", "owner", "protocol", "ns_id", "affiliation",
                      "url_template", "user"):
                if b.get(f):
                    p[f] = b[f]
                elif f in b and b[f] == "":        # explicit clear
                    p.pop(f, None)
            if b.get("ssh_port") is not None:
                try:
                    p["ssh_port"] = int(b["ssh_port"])
                except (TypeError, ValueError):
                    raise _Http(400, "ssh_port must be a number")
            if p.get("type") == "github" and not p.get("token"):
                p["auth"] = "gh"
        if "enabled" in b:
            p["enabled"] = bool(b["enabled"])
        _audit("platform edited: %s" % name)
        return {"ok": True, "platform": _public_platform(name, p)}

    payload, code = _locked(do)
    if code != 200:
        return handler._json(payload, code)
    _redactor().reload()
    _invalidate("platforms", "health")
    handler._json(payload)


def h_platform_delete(handler, name):
    force = (handler._qs().get("force") or [None])[0] == "1"

    def do(cfg):
        err = C.remove_platform(cfg, name, force=force)
        if err:
            if not force and C.platform_used_by(cfg, name):
                raise _Http(409, "%s — re-run with force=1 to remove anyway"
                            % err)
            raise _Http(400, err)
        _audit("platform removed: %s" % name)
        return {"ok": True}

    payload, code = _locked(do)
    _redactor().reload()
    _invalidate("platforms", "health")
    handler._json(payload, code)


def h_platform_test(handler, name):
    cfg = C.load_config()
    p = (cfg.get("platforms") or {}).get(name)
    if not p:
        return handler._err(404, "no such platform")
    try:
        ok, info = P.make(name, p).test()
    except Exception as e:
        ok, info = False, str(e)[:120]
    handler._json({"ok": bool(ok), "info": _redactor().redact(info or "")})


def h_platform_toggle(handler, name):
    def do(cfg):
        p = (cfg.get("platforms") or {}).get(name)
        if not p:
            raise _Http(404, "no such platform")
        p["enabled"] = not bool(p.get("enabled"))
        _audit("platform %s: %s" % (name, "enabled" if p["enabled"]
                                    else "disabled"))
        return {"ok": True, "enabled": p["enabled"]}

    payload, code = _locked(do)
    _invalidate("platforms", "health")
    handler._json(payload, code)


def h_repos(handler):
    cfg = C.load_config()
    name = (handler._qs().get("platform") or [None])[0]
    if not name:
        routes = C.routes(cfg)
        name = routes[0]["source"] if routes else None
    if not name:
        return handler._err(400, "no platform to list")
    p = C.platform(cfg, name)
    if not p:
        return handler._err(404, "no such platform: %s" % name)
    try:
        repos = P.make(name, p).list_repos()
    except Exception as e:
        return handler._err(502, "cannot list %s: %s"
                            % (name, _redactor().redact(str(e)[:160])))
    q = (handler._qs().get("q") or [""])[0].lower()
    forks = (handler._qs().get("forks") or [None])[0] == "1"
    ptype = p.get("type")
    out = []
    for r in repos:
        if not forks and r.get("fork"):
            continue
        if q and q not in r["name"].lower():
            continue
        # mirrors live at mirrors/{type}/{hub_rel(id)}.git now, so a
        # name+".git" check against the hub root missed every current mirror
        try:
            mirrored = os.path.isdir(SYNC.mirror_path(r, ptype))
        except Exception:
            mirrored = False
        out.append({"name": r["name"], "id": r.get("id") or r["name"],
                    "private": bool(r.get("private")),
                    "fork": bool(r.get("fork")), "mirrored": mirrored})
    out.sort(key=lambda r: r["name"].lower())
    # the platform type travels with the list: the dashboard prints "local
    # folder" instead of "not mirrored" for a local path, which has no hub
    # mirror by design
    handler._json({"platform": name, "type": ptype, "repos": out})


def h_selection_file(handler):
    b = handler._body()
    repos = b.get("repos") or []
    platform = (b.get("platform") or "").strip()
    if not repos:
        raise _Http(400, "no repos selected")
    # the platform name becomes part of the default file name, so it must
    # be a real platform name — not a path fragment
    if platform and not _valid_name(platform):
        raise _Http(400, "invalid platform name")
    for r in repos:
        if not isinstance(r, str) or not re.match(r"^[A-Za-z0-9._-]{1,128}$", r):
            raise _Http(400, "bad repo name: %r" % (r,)[:40])

    def do(cfg):
        # find the route first: a request that cannot attach the file must
        # not leave a stray file behind
        routes = [r for r in C.routes(cfg) if r.get("source") == platform]
        if not routes:
            # do not silently attach to an unrelated route
            raise _Http(400, "no route has '%s' as its source; nothing attached"
                        % (platform or "?"))
        # confine the output to the config dir — never allow a caller-chosen
        # absolute path (it could clobber config.json / web.token)
        rel = (b.get("path") or "").strip()
        default = os.path.join(C.CONFIG_DIR, "repos-%s.txt"
                               % (platform or "selection"))
        target = _confined_path(rel, C.CONFIG_DIR) if rel else default
        if target is None:
            target = default
        # even the default must be inside the config dir
        if _confined_path(target, C.CONFIG_DIR) is None:
            raise _Http(400, "output path outside the config dir")
        d = os.path.dirname(target)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = target + ".tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("# repos synced by gitsync — one per line, # comments\n")
            for r in repos:
                f.write(r + "\n")
        os.replace(tmp, target)
        for r in routes:
            r["include_file"] = target
            r.pop("include", None)
        _audit("selection file written: %s (%d repos) -> %s"
               % (target, len(repos), ", ".join(x.get("name") for x in routes)))
        return {"ok": True, "path": target, "count": len(repos),
                "routes": [r.get("name") for r in routes]}

    handler._json(*_locked(do))


def h_routes(handler):
    cfg = C.load_config()
    handler._json({"routes": [_route_view(cfg, r) for r in C.routes(cfg)]})


def h_route_add(handler):
    b = handler._body()
    name = (b.get("name") or "").strip()
    source = (b.get("source") or "").strip()
    targets = [t.strip() for t in (b.get("targets") or []) if t.strip()]
    if not _valid_name(name):
        raise _Http(400, "invalid route name")
    for n in [source] + targets:
        if not _valid_name(n):
            raise _Http(400, "invalid platform name: %r" % n)
    if not targets:
        raise _Http(400, "a route needs at least one target")
    if b.get("mode") not in (None, "", "inherit", "full", "safe"):
        raise _Http(400, "invalid mode")
    if b.get("mesh_conflict") not in (None, "", "inherit", "newest", "skip"):
        raise _Http(400, "invalid mesh_conflict")
    if b.get("schedule") not in (None, "", "inherit") + tuple(C.SCHEDULES):
        raise _Http(400, "invalid schedule")

    def do(cfg):
        if any(r.get("name") == name for r in cfg.get("routes", [])):
            raise _Http(400, "route '%s' already exists" % name)
        if source not in (cfg.get("platforms") or {}):
            raise _Http(400, "unknown source platform: %s" % source)
        for t in targets:
            if t not in (cfg.get("platforms") or {}):
                raise _Http(400, "unknown target platform: %s" % t)
        route = {"name": name, "source": source, "targets": targets}
        if b.get("direction") == "mesh":
            route["direction"] = "mesh"
        for key in ("workers", "mode", "mesh_conflict", "schedule"):
            v = b.get(key)
            if v and v != "inherit":
                if key == "workers":
                    try:
                        route[key] = int(v)
                    except (TypeError, ValueError):
                        raise _Http(400, "workers must be a number")
                else:
                    route[key] = v
        cfg.setdefault("routes", []).append(route)
        _audit("route added: %s" % name)
        _drop_compare_cache()
        return {"ok": True, "route": _route_view(cfg, route)}

    payload, code = _locked(do)
    handler._json(payload, 201 if code == 200 else code)


def h_route_edit(handler, name):
    b = handler._body()

    def do(cfg):
        route = next((r for r in cfg.get("routes", [])
                      if r.get("name") == name), None)
        if not route:
            raise _Http(404, "no such route")
        if b.get("clear_filters"):
            route.pop("include", None)
            route.pop("include_file", None)
            route.pop("exclude", None)
        if "include" in b:
            route["include"] = [s.strip() for s in (b.get("include") or [])
                                if s.strip()]
            route.pop("include_file", None)
        if "exclude" in b:
            route["exclude"] = [s.strip() for s in (b.get("exclude") or [])
                                if s.strip()]
        for key in ("workers", "mode", "mesh_conflict"):
            if key in b:
                v = b[key]
                if v in (None, "", "inherit"):
                    route.pop(key, None)
                elif key == "workers":
                    try:
                        route[key] = int(v)
                    except (TypeError, ValueError):
                        raise _Http(400, "workers must be a number")
                elif key == "mode" and v not in ("full", "safe"):
                    raise _Http(400, "invalid mode")
                elif key == "mesh_conflict" and v not in ("newest", "skip"):
                    raise _Http(400, "invalid mesh_conflict")
                else:
                    route[key] = v
        if "schedule" in b:
            v = b["schedule"]
            if v in (None, "", "inherit"):
                route.pop("schedule", None)
            elif v in C.SCHEDULES:
                route["schedule"] = v
            else:
                raise _Http(400, "invalid schedule")
        if "enabled" in b:
            route["enabled"] = bool(b["enabled"])
        if "prune_repos" in b:
            # per-target opt-in for whole-repository deletion; only names
            # that are actually targets of this route are accepted
            want = b.get("prune_repos") or []
            if isinstance(want, str):
                want = [want]
            want = [t for t in want if isinstance(t, str)]
            bad = [t for t in want if t not in (route.get("targets") or [])]
            if bad:
                raise _Http(400, "not a target of this route: %s"
                            % ", ".join(bad)[:80])
            if want:
                route["prune_repos"] = sorted(set(want))
            else:
                route.pop("prune_repos", None)
        _audit("route edited: %s" % name)
        _drop_compare_cache()
        return {"ok": True, "route": _route_view(cfg, route)}

    payload, code = _locked(do)
    handler._json(payload, code)


def h_route_delete(handler, name):
    def do(cfg):
        before = len(cfg.get("routes", []))
        cfg["routes"] = [r for r in cfg.get("routes", [])
                         if r.get("name") != name]
        if len(cfg["routes"]) == before:
            raise _Http(404, "no such route: %s" % name)
        _audit("route removed: %s" % name)
        _drop_compare_cache()
        return {"ok": True}

    payload, code = _locked(do)
    handler._json(payload, code)


def _pause_resume(handler, name, pause):
    b = handler._body()
    want = [t.strip() for t in (b.get("targets") or []) if t.strip()]

    def do(cfg):
        route = next((r for r in cfg.get("routes", [])
                      if r.get("name") == name), None)
        if not route:
            raise _Http(404, "no such route")
        if not want:
            route["enabled"] = not pause
            if not pause:
                route.pop("paused_targets", None)
        else:
            known = set(route.get("targets") or [])
            unknown = [t for t in want if t not in known]
            if unknown:
                raise _Http(400, "%s has no target(s): %s"
                            % (name, ", ".join(unknown)))
            cur = [t for t in (route.get("paused_targets") or []) if t in known]
            if pause:
                cur = sorted(set(cur) | set(want))
            else:
                cur = [t for t in cur if t not in want]
            if cur:
                route["paused_targets"] = cur
            else:
                route.pop("paused_targets", None)
        _drop_compare_cache()
        _audit("route %s: %s %s" % (name, "paused" if pause else "resumed",
                                    ",".join(want) or "(whole)"))
        return {"ok": True, "route": _route_view(cfg, route)}

    payload, code = _locked(do)
    handler._json(payload, code)


def h_route_pause(handler, name):
    return _pause_resume(handler, name, pause=True)


def h_route_resume(handler, name):
    return _pause_resume(handler, name, pause=False)


def h_stars(handler):
    cfg = C.load_config()
    name = (handler._qs().get("platform") or [None])[0]
    if not name:
        routes = C.routes(cfg)
        name = routes[0]["source"] if routes else None
    p = C.platform(cfg, name) if name else None
    if not p:
        return handler._err(404, "no platform to read stars from")
    try:
        rows = ST.starred(P.make(name, p))
    except Exception as e:
        return handler._err(502, "cannot read stars on %s: %s"
                            % (name, _redactor().redact(str(e)[:160])))
    handler._json({"platform": name,
                   "stars": [{"name": n, "url": u} for n, u in rows]})


def h_stars_copy(handler):
    b = handler._body()
    source = (b.get("source") or "").strip()
    targets = [t.strip() for t in (b.get("targets") or []) if t.strip()]
    if not source or not targets:
        raise _Http(400, "pick a source and at least one target")
    cfg = C.load_config()
    if source not in (cfg.get("platforms") or {}):
        raise _Http(400, "unknown source platform: %s" % source)
    unknown = [t for t in targets if t not in (cfg.get("platforms") or {})]
    if unknown:
        raise _Http(400, "unknown target(s): %s" % ", ".join(unknown))
    only = b.get("repos") or None
    lines = []
    t = ST.copy_stars(cfg, source, targets,
                      dry_run=bool(b.get("dry_run")), only=only,
                      log=lambda m: lines.append(_redactor().redact(m)))
    handler._json({"ok": True, "summary": t, "log": lines})


def h_log(handler):
    tail = (handler._qs().get("tail") or ["400"])[0]
    try:
        tail = int(tail)
    except ValueError:
        raise _Http(400, "tail must be a number")
    q = (handler._qs().get("q") or [""])[0].lower()
    lines = []
    try:
        with open(C.log_file()) as f:
            lines = f.read().splitlines()
    except OSError:
        pass
    total = len(lines)
    if q:
        lines = [l for l in lines if q in l.lower()]
    lines = lines[-tail:] if tail > 0 else []
    handler._json({"lines": [_redactor().redact(l) for l in lines],
                   "total": total, "shown": len(lines)})


def h_history(handler):
    n = (handler._qs().get("n") or ["40"])[0]
    try:
        n = int(n)
    except ValueError:
        raise _Http(400, "n must be a number")
    recs = SYNC.last_records(n)
    handler._json({
        "records": [_public_record(r) for r in recs],
        "spark": [r.get("elapsed_sec", 0) or 0 for r in recs[-30:]],
    })


def h_settings_get(handler):
    cfg = C.load_config()
    st = _public_settings(cfg)
    a = get_auth()
    st["paths"] = {"config": C.CONFIG_FILE, "mirror": C.MIRROR_DIR,
                   "log": C.log_file(), "data": C.DATA_DIR}
    st["schedules"] = {k: v["label"] for k, v in C.SCHEDULES.items()}
    st["themes"] = list(C.THEMES)
    st["scheduler"] = _cached_scheduler()
    st["next_run"] = _cached_next_run()
    st["version"] = __version__
    st["auth"] = {"password_set": bool(a),
                  "username": a.get("username") if a else ""}
    handler._json({"settings": st})


def _apply_setting(st, key, body, cast, choices=None):
    if key not in body or body[key] in (None, ""):
        return
    try:
        val = cast(body[key])
    except (TypeError, ValueError):
        raise _Http(400, "invalid %s" % key)
    if choices and val not in choices:
        raise _Http(400, "invalid %s: %r" % (key, body[key]))
    st[key] = val


def h_settings_put(handler):
    b = handler._body()

    def do(cfg):
        st = cfg.setdefault("settings", {})
        _apply_setting(st, "workers", b, int, [1, 2, 4, 8])
        _apply_setting(st, "mode", b, str, ["safe", "full"])
        _apply_setting(st, "mesh_conflict", b, str, ["newest", "skip"])
        _apply_setting(st, "schedule", b, str, list(C.SCHEDULES))
        _apply_setting(st, "include_forks", b, bool)
        _apply_setting(st, "pace", b, float, [0, 1, 2, 5, 10])
        _apply_setting(st, "relax_protection", b, bool)
        _apply_setting(st, "token_warn_days", b, int, [7, 14, 30, 60, 90])
        _apply_setting(st, "theme", b, str, list(C.THEMES))
        notify = st.setdefault("notify", {})
        if "on" in b:
            if b["on"] not in ("failure", "success", "always", "never"):
                raise _Http(400, "bad notify mode")
            notify["on"] = b["on"]
        # explicit removal: the fields are write-only, so a blank value
        # means "keep"; only a named clear removes a stored secret
        for what in (b.get("clear_notify") or []):
            if what == "discord":
                notify.pop("discord_webhook", None)
            elif what == "telegram":
                notify.pop("telegram_token", None)
                notify.pop("telegram_chat_id", None)
            else:
                raise _Http(400, "unknown notify target: %r" % (what,))
        if (b.get("discord_webhook") or "").strip():
            url = b["discord_webhook"].strip()
            if not url.startswith(NT.DISCORD_PREFIXES):
                raise _Http(400, "that is not a Discord webhook URL")
            notify["discord_webhook"] = url
        if (b.get("telegram_token") or "").strip():
            tok = b["telegram_token"].strip()
            chat = (b.get("telegram_chat_id") or "").strip()
            if not chat:
                raise _Http(400, "telegram needs a chat id too")
            notify["telegram_token"] = tok
            notify["telegram_chat_id"] = chat
        _audit("settings updated")
        return {"ok": True}

    payload, code = _locked(do)
    _redactor().reload()
    _invalidate("health", "next", "sched")
    handler._json(payload, code)


def h_notify_test(handler):
    cfg = C.load_config()
    ok, msg = NT.notify_test(cfg)
    handler._json({"ok": bool(ok), "message": _redactor().redact(msg or "")})


def h_scopes(handler):
    out = []
    for ptype in C.PLATFORM_TYPES:
        g = PERM.guide(ptype)
        out.append({"type": ptype, "title": g["title"],
                    "token_url": g.get("token_url", ""),
                    "minimum": g["minimum"],
                    "scopes": [{"scope": s, "why": w} for s, w in
                               g.get("scopes", [])],
                    "alt_auth": g.get("alt_auth", ""),
                    "env": g.get("env", "")})
    handler._json({"scopes": out})


def h_health(handler):
    def compute():
        cfg = C.load_config()
        try:
            tok = TK.check(cfg)
            tokens = [{"platform": t["platform"], "type": t.get("type"),
                       "level": t.get("level"), "note": t.get("note", ""),
                       "days_left": t.get("days_left"),
                       "expires_at": t.get("expires_at")} for t in tok]
            tok_summary = TK.summary(tok)
            tok_bad = bool(TK.bad(tok))
        except Exception:
            tokens, tok_summary, tok_bad = [], "tokens: unknown", False
        disk = {"mirrors": 0, "size": 0, "free": None, "total": None,
                "pct": None}
        try:
            ms = DK.mirrors()
            free, tot = DK.fs_free(C.MIRROR_DIR)
            disk["mirrors"] = len(ms)
            disk["size"] = sum(m[2] for m in ms)
            disk["free"], disk["total"] = free, tot
            if tot:
                disk["pct"] = int(100.0 * (tot - free) / tot)
        except Exception:
            pass
        wh = {"secret_set": bool(WH.get_secret()),
              "active": S.webhooks_active()}
        return {"tokens": tokens, "token_summary": tok_summary,
                "token_bad": tok_bad, "disk": disk, "webhooks": wh,
                "disk_human": {"size": DK.human(disk["size"]),
                               "free": DK.human(disk["free"]) if disk["free"]
                               else None,
                               "total": DK.human(disk["total"]) if disk["total"]
                               else None}}
    handler._json(_cached("health", 15, compute, {}))


def h_scheduler_install(handler):
    b = handler._body()
    cfg = C.load_config()
    key = b.get("schedule") or C.finest_schedule(cfg) \
        or C.setting(cfg, "schedule", "hourly")
    if key != "off" and key not in C.SCHEDULES:
        raise _Http(400, "unknown schedule '%s' (choose from: %s)"
                    % (key, ", ".join(C.SCHEDULES)))
    if key == "off":
        # persist the choice, or the dashboard keeps showing the old
        # schedule and its "Install timer" button re-enables hourly
        C.set_setting(cfg, "schedule", "off")
        S.uninstall_systemd(system=True)
        S.uninstall_systemd()
        S.install_cron("off")
        _audit("scheduler disabled")
        _invalidate("next", "sched")
        return handler._json({"ok": True, "status": "scheduler disabled"})
    if b.get("schedule"):
        C.set_setting(cfg, "schedule", key)
    _invalidate("next", "sched")
    if b.get("cron") or not S.have_systemd():
        S.install_cron(key)
        _audit("scheduler installed (cron): %s" % key)
        return handler._json({"ok": True,
                              "status": "cron: %s" % C.SCHEDULES[key]["label"]})
    if S.install_systemd(key):
        _audit("scheduler installed (systemd): %s" % key)
        return handler._json({"ok": True,
                              "status": "timer: %s"
                              % C.SCHEDULES[key]["label"]})
    S.install_cron(key)
    _audit("scheduler installed (cron fallback): %s" % key)
    handler._json({"ok": True,
                   "status": "cron (systemd failed): %s"
                   % C.SCHEDULES[key]["label"]})


def h_webhooks_get(handler):
    cfg = C.load_config()
    endpoints = []
    for name, p in (cfg.get("platforms") or {}).items():
        if p.get("enabled") and p.get("type") in WH.AUTH_STYLE:
            endpoints.append({"name": name, "type": p.get("type"),
                              "path": "/hook/%s" % name})
    handler._json({"secret_set": bool(WH.get_secret()),
                   "active": S.webhooks_active(), "port": WH.DEFAULT_PORT,
                   "endpoints": endpoints})


def h_webhooks_action(handler):
    b = handler._body()
    action = b.get("action")
    if action == "rotate":
        WH.rotate_secret()
        _say("  %s new webhook secret generated — read it with: "
             "gitsync webhooks --secret --show" % _c("33", "[web]"))
        _audit("webhook secret rotated")
        return handler._json({"ok": True, "secret_set": True,
                              "note": "secret generated; view it with "
                              "'gitsync webhooks --secret --show'"})
    if action == "install":
        # match the CLI default (127.0.0.1); front it with a proxy/tunnel to
        # let platforms reach it — one exposure policy, not two
        ok = S.install_webhooks(port=WH.DEFAULT_PORT, bind="127.0.0.1")
        _audit("webhook service install: %s" % ("ok" if ok else "failed"))
        return handler._json({"ok": bool(ok),
                              "status": "installed" if ok
                              else "install failed (needs systemd)"})
    if action == "stop":
        ok = S.uninstall_webhooks()
        _audit("webhook service removed")
        return handler._json({"ok": True,
                              "status": "removed" if ok else "not installed"})
    raise _Http(400, "unknown webhook action")


def h_sync_start(handler):
    if _engine_sync_busy():
        _audit("409 sync start refused (engine lock held) from %s"
               % handler._client_ip())
        return handler._err(409, "a sync is already running")
    b = handler._body()
    argv = ["sync"]
    if b.get("route"):
        if not _valid_name(b["route"]):
            raise _Http(400, "invalid route name")
        argv += ["--route", b["route"]]
    if b.get("to"):
        if not _valid_name(b["to"]):
            raise _Http(400, "invalid target name")
        argv += ["--to", b["to"]]
    repos = _clean_repo_list(b.get("repos"))
    if repos:
        argv += ["--repos", ",".join(repos)]
    dry = bool(b.get("dry_run"))
    if dry:
        argv += ["--dry-run"]
    if b.get("retry_failed"):
        argv += ["--retry-failed"]
    if b.get("force"):
        # the TUI and the CLI can run while globally paused; without this
        # the WebUI's Pause button was a one-way door
        argv += ["--force"]
    try:
        job = start_job("sync", [sys.executable, "-m", "gitsync"] + argv,
                        mutating=True)
    except Exception:
        return handler._err(500, "could not start the sync")
    if job is None:
        return handler._err(409, "another job is already running")
    global _current_sync
    _current_sync = job
    _audit("sync started (%s) route=%s to=%s repos=%d dry=%s from %s"
           % (job.id, b.get("route") or "*", b.get("to") or "*",
              len(repos), dry, handler._client_ip()))
    handler._json({"ok": True, "job_id": job.id, "dry_run": dry,
                   "kind": "sync"}, 202)


def h_sync_cancel(handler):
    """stop the running sync/job — the WebUI had no equivalent of the TUI's
    X or Ctrl-C, so a runaway sync could only be stopped from a terminal."""
    job = _current_sync
    proc = getattr(job, "proc", None) if job else None
    if not job or job.done or proc is None or proc.poll() is not None:
        return handler._err(409, "nothing is running")
    try:
        proc.terminate()
    except Exception as e:
        return handler._err(500, "could not stop it: %s" % str(e)[:80])
    _audit("sync cancelled from %s" % handler._client_ip())
    handler._json({"ok": True, "job_id": job.id})


def h_sync_state(handler):
    job = _current_sync
    if not job:
        busy = _engine_sync_busy()
        return handler._json({"syncing": busy, "job_id": None,
                              "progress": {"done": 0, "total": 0}})
    snap = job.snapshot()
    handler._json({"syncing": not job.done, "job_id": job.id,
                   "progress": snap["progress"], "done": job.done,
                   "exit": job.exit, "kind": job.kind})


def h_sync_stream(handler):
    jid = (handler._qs().get("job_id") or [None])[0]
    job = _jobs.get(jid) if jid else _current_sync
    if not job:
        return handler._err(404, "no such job")
    handler._sse(job)


def h_compare_cached(handler):
    """the last compare matrices, so the Repos page renders without a click.

    One matrix per route: a config can have several (one-way and mesh), and
    the page needs the one it is showing, not whichever ran last.
    """
    data = _CMP_CACHE["data"]
    if not data or (time.time() - _CMP_CACHE["at"]) > _CMP_TTL:
        return handler._json({"cached": False, "routes": []})
    handler._json({"cached": True,
                   "age_sec": int(time.time() - _CMP_CACHE["at"]),
                   "routes": [data[k] for k in sorted(data)]})


def h_job_start(handler):
    b = handler._body()
    kind = (b.get("kind") or "").strip()
    try:
        argv, mutating = _tool_argv(kind, b)
    except (ValueError, TypeError) as e:
        return handler._err(400, str(e)[:160])
    try:
        job = start_job(kind, [sys.executable, "-m", "gitsync"] + argv,
                        mutating=mutating)
    except Exception:
        return handler._err(500, "could not start the job")
    if job is None:
        return handler._err(409, "another job is already running")
    _audit("tool '%s' started (%s) from %s" % (kind, job.id,
                                               handler._client_ip()))
    handler._json({"ok": True, "job_id": job.id, "kind": kind}, 202)


def h_job_state(handler, job_id):
    job = _jobs.get(job_id)
    if not job:
        return handler._err(404, "no such job")
    snap = job.snapshot()
    with job.cond:
        snap["lines"] = [_redactor().redact(l) for l in job.lines[-2000:]]
    handler._json(snap)


def h_job_stream(handler, job_id):
    job = _jobs.get(job_id)
    if not job:
        return handler._err(404, "no such job")
    handler._sse(job)


def _locked(fn):
    """Run fn(cfg)->payload under the config process-lock + flock (via
    C.update), then save. fn may raise _Http(code,msg) to abort without
    saving. Returns (payload, http_code)."""
    try:
        return C.update(fn), 200
    except _Http as h:
        return {"error": h.msg}, h.code


# ---------------------------------------------------------------- routing table

ROUTES = [
    ("GET", re.compile(r"^/api/auth$"), lambda h: h_auth_info(h)),
    ("POST", re.compile(r"^/api/auth/password$"), lambda h: h_set_password(h)),
    ("POST", re.compile(r"^/api/session$"), lambda h: h_login(h)),
    ("GET", re.compile(r"^/api/session$"), lambda h: h_session(h)),
    ("GET", re.compile(r"^/api/logout$"), lambda h: h_logout(h)),
    ("GET", re.compile(r"^/api/status$"), lambda h: h_status(h)),
    ("POST", re.compile(r"^/api/pause$"), lambda h: h_pause(h)),
    ("POST", re.compile(r"^/api/auth/github/device/start$"),
     lambda h: h_github_device_start(h)),
    ("POST", re.compile(r"^/api/auth/github/device/poll$"),
     lambda h: h_github_device_poll(h)),
    ("GET", re.compile(r"^/api/platforms$"), lambda h: h_platforms(h)),
    ("POST", re.compile(r"^/api/platforms$"), lambda h: h_platform_add(h)),
    ("PUT", re.compile(r"^/api/platforms/(?P<name>[^/]+)$"),
     lambda h, name: h_platform_edit(h, name)),
    ("DELETE", re.compile(r"^/api/platforms/(?P<name>[^/]+)$"),
     lambda h, name: h_platform_delete(h, name)),
    ("POST", re.compile(r"^/api/platforms/(?P<name>[^/]+)/test$"),
     lambda h, name: h_platform_test(h, name)),
    ("POST", re.compile(r"^/api/platforms/(?P<name>[^/]+)/toggle$"),
     lambda h, name: h_platform_toggle(h, name)),
    ("GET", re.compile(r"^/api/repos$"), lambda h: h_repos(h)),
    ("POST", re.compile(r"^/api/repos/selection-file$"),
     lambda h: h_selection_file(h)),
    ("GET", re.compile(r"^/api/routes$"), lambda h: h_routes(h)),
    ("POST", re.compile(r"^/api/routes$"), lambda h: h_route_add(h)),
    ("PUT", re.compile(r"^/api/routes/(?P<name>[^/]+)$"),
     lambda h, name: h_route_edit(h, name)),
    ("DELETE", re.compile(r"^/api/routes/(?P<name>[^/]+)$"),
     lambda h, name: h_route_delete(h, name)),
    ("POST", re.compile(r"^/api/routes/(?P<name>[^/]+)/pause$"),
     lambda h, name: h_route_pause(h, name)),
    ("POST", re.compile(r"^/api/routes/(?P<name>[^/]+)/resume$"),
     lambda h, name: h_route_resume(h, name)),
    ("GET", re.compile(r"^/api/stars$"), lambda h: h_stars(h)),
    ("POST", re.compile(r"^/api/stars/copy$"), lambda h: h_stars_copy(h)),
    ("GET", re.compile(r"^/api/log$"), lambda h: h_log(h)),
    ("GET", re.compile(r"^/api/history$"), lambda h: h_history(h)),
    ("GET", re.compile(r"^/api/settings$"), lambda h: h_settings_get(h)),
    ("PUT", re.compile(r"^/api/settings$"), lambda h: h_settings_put(h)),
    ("POST", re.compile(r"^/api/notify/test$"), lambda h: h_notify_test(h)),
    ("GET", re.compile(r"^/api/scopes$"), lambda h: h_scopes(h)),
    ("GET", re.compile(r"^/api/health$"), lambda h: h_health(h)),
    ("POST", re.compile(r"^/api/scheduler/install$"),
     lambda h: h_scheduler_install(h)),
    ("GET", re.compile(r"^/api/webhooks$"), lambda h: h_webhooks_get(h)),
    ("POST", re.compile(r"^/api/webhooks$"), lambda h: h_webhooks_action(h)),
    ("POST", re.compile(r"^/api/sync$"), lambda h: h_sync_start(h)),
    ("POST", re.compile(r"^/api/sync/cancel$"), lambda h: h_sync_cancel(h)),
    ("GET", re.compile(r"^/api/sync/state$"), lambda h: h_sync_state(h)),
    ("GET", re.compile(r"^/api/sync/stream$"), lambda h: h_sync_stream(h)),
    ("GET", re.compile(r"^/api/compare$"), lambda h: h_compare_cached(h)),
    ("POST", re.compile(r"^/api/jobs$"), lambda h: h_job_start(h)),
    ("GET", re.compile(r"^/api/jobs/(?P<job_id>[^/]+)$"),
     lambda h, job_id: h_job_state(h, job_id)),
    ("GET", re.compile(r"^/api/jobs/(?P<job_id>[^/]+)/stream$"),
     lambda h, job_id: h_job_stream(h, job_id)),
]


# ---------------------------------------------------------------- server

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, token, bind_host, extra_hosts):
        self.token = token
        self.bind_host = bind_host
        self.extra_hosts = extra_hosts
        super().__init__(addr, handler)
        self.port = self.server_address[1]   # actual bound port (may be 0→ephemeral)

    def handle_error(self, request, client_address):
        _cls, exc, _tb = sys.exc_info()
        if exc and isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def serve(port=DEFAULT_PORT, bind=DEFAULT_BIND, allowed_hosts=None):
    token, generated = get_web_token()
    _redactor().reload()
    extra = set(h.lower() for h in (allowed_hosts or []))
    env_hosts = (os.environ.get("GITSYNC_ALLOWED_HOSTS") or "").split(",")
    extra |= {h.strip().lower() for h in env_hosts if h.strip()}
    handler = make_handler()
    try:
        httpd = _Server((bind, port), handler, token, bind, extra)
    except OSError as e:
        _say("cannot bind %s:%d — %s" % (bind, port, e))
        _say("another gitsync web instance may be running, or the port is "
             "taken. Pick another with --port.")
        return 1

    loop = _is_loopback(bind)
    _say("%s web UI on port %d" % (_c("32", "gitsync"), port))
    _say("  this machine : http://127.0.0.1:%d" % port)
    if loop:
        _say("  bound to %s — reachable from this machine only." % bind)
        _say("  to reach it from other devices: gitsync web --bind 0.0.0.0 "
             "(or: gitsync web --install for always-on)")
    else:
        for ip in _local_ips():
            _say("  your network : http://%s:%d" % (ip, port))
        _say("%s bound to %s — reachable on your network. Authentication is "
             "REQUIRED on every request. Restrict with: gitsync web "
             "--bind 127.0.0.1" % (_c("33", "note:"), bind))
    if generated:
        _say("")
        # never print the token into the journal / a pipe — read the file
        if _TTY and not _UNDER_SYSTEMD:
            _say("  %s %s" % (_c("1", "access token (shown once):"), token))
        else:
            _say("  %s a new access token was generated; read it from:"
                 % _c("1", "note:"))
            _say("      %s   (mode 0600)" % _token_path())
        _say("  open the URL and paste this token to sign in.")
        _say("")
    else:
        src = "$GITSYNC_WEB_TOKEN" if os.environ.get("GITSYNC_WEB_TOKEN") \
            else _token_path()
        _say("  access token: from %s" % src)
        _say("  (lost it? delete that file and restart, or set "
             "GITSYNC_WEB_TOKEN)")
    if get_auth():
        _say("  sign in with username '%s' + password (or the access token)"
             % get_auth().get("username"))
    else:
        _say("  sign in with the access token — or set a username & password "
             "in Settings (or: gitsync web --set-password USER)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _say("\nstopped")
    finally:
        httpd.server_close()
    return 0
