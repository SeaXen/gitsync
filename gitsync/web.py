"""gitsync web UI — a browser dashboard for everything the TUI does.

    gitsync web                       # http://127.0.0.1:7765
    gitsync web --port 7765 --bind 127.0.0.1
    gitsync web --bind 0.0.0.0        # LAN/proxy — auth is always required

Zero dependencies: Python standard library only (http.server, like
webhooks.py). The engine (config/sync/platforms/stars/tokencheck/diskutil/
scheduler) is called directly for fast, read-only and config-mutating
actions; long-running actions (sync, compare, doctor, fix, restore, mirror,
disk) are spawned as `python3 -m gitsync <subcommand>` subprocesses — the
same command the timer runs — and streamed to the browser over Server-Sent
Events. No sync logic is reimplemented here.

Security model (the config holds platform tokens, so this is the priority):
  * binds 127.0.0.1 by default; any other bind needs an explicit --bind
  * a random access token (CONFIG_DIR/web.token, 0600) guards every /api
    call — via `Authorization: Bearer` or an HttpOnly, SameSite=Strict cookie
  * a token is NEVER returned in any response, log line or page; responses
    only ever carry token *state* (set/missing/gh/glab)
  * state-changing requests are CSRF-guarded (custom header for cookie
    sessions) and DNS-rebinding-guarded (Host allowlist)
  * every outgoing body/SSE line is passed through a redactor that strips
    any known token, on top of the engine's own _redact()
  * all subprocesses are argv lists (never a shell); all names/paths are
    validated; static files and local paths are confined (no traversal)
"""

import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
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
WEBUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")
MAX_BODY = 1 << 20                      # 1 MB request cap
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# "[12/54] OK   repo -> targets" — the progress line the sync engine prints
_PROGRESS_RE = re.compile(r"\[(\d+)\s*/\s*(\d+)\]\s+(\S+)\s+(\S+)")

_TTY = sys.stdout.isatty()


def _c(code, text):
    """terminal colour, disabled when stdout is not a TTY (docker logs)"""
    if not _TTY:
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def _say(msg):
    print(msg, flush=True)


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
        with open(_token_path(), "w") as f:
            f.write(tok)
        os.chmod(_token_path(), 0o600)
    except OSError:
        pass
    return tok, True


# ---------------------------------------------------------------- password auth
#
# Optional username + password sign-in, stored as a PBKDF2 hash (never the
# plaintext, never returned). If none is set, the access token is the only
# way in — so existing installs are unaffected until a password is chosen.

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
    with open(tmp, "w") as f:
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
    if not hmac.compare_digest(str(username or ""),
                               str(a.get("username", ""))):
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
    """in-memory brake on password guessing: lock after max_fails in window."""

    def __init__(self, max_fails=8, window=120):
        self.max_fails = max_fails
        self.window = window
        self._fails = []
        self._lock = threading.Lock()

    def locked(self):
        with self._lock:
            now = time.time()
            self._fails = [t for t in self._fails if now - t < self.window]
            return len(self._fails) >= self.max_fails

    def fail(self):
        with self._lock:
            self._fails.append(time.time())

    def reset(self):
        with self._lock:
            self._fails = []


_login_throttle = _Throttle()


# ---------------------------------------------------------------- redaction

class Redactor:
    """Strip any known secret from text before it leaves the process.

    The engine already redacts git error text (sync._redact), and it never
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
            return SYNC._redact(text)
        except Exception:
            return text


REDACTOR = Redactor()


# ---------------------------------------------------------------- caches

_cache = {"sched": ("unknown", 0.0), "next": (None, 0.0), "health": (None, 0.0)}


def _cached_scheduler():
    now = time.time()
    if now - _cache["sched"][1] > 15:
        try:
            _cache["sched"] = (S.status(), now)
        except Exception:
            _cache["sched"] = ("unknown", now)
    return _cache["sched"][0]


def _cached_next_run():
    now = time.time()
    if now - _cache["next"][1] > 15:
        try:
            _cache["next"] = (S.next_run(), now)
        except Exception:
            _cache["next"] = (None, now)
    return _cache["next"][0]


# ---------------------------------------------------------------- jobs

class Job:
    """one subprocess run (sync or a tool), streamed to any number of
    SSE subscribers via a shared condition variable."""

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
                    "exit": self.exit, "error": self.error,
                    "elapsed": round((self.finished or time.time())
                                     - self.started, 1)}


_jobs = {}
_jobs_lock = threading.Lock()
_mutating_busy = threading.Lock()      # web-level: one mutating job at a time
_current_sync = None                   # most recent sync job (for /sync/state)


def _update_progress(job, line):
    m = _PROGRESS_RE.search(line)
    if not m:
        return
    d, t = int(m.group(1)), int(m.group(2))
    if t:
        job.progress["total"] = t
    job.progress["done"] = max(job.progress["done"], d)
    job.progress["status"] = m.group(3)
    job.progress["repo"] = m.group(4)


def _run_job(job):
    try:
        env = dict(os.environ, PYTHONPATH=_app_dir())
        job.proc = subprocess.Popen(job.argv, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    env=env, cwd=_app_dir())
        for line in job.proc.stdout:
            line = REDACTOR.redact(line.rstrip("\n"))
            with job.cond:
                job.lines.append(line)
                _update_progress(job, line)
                job.cond.notify_all()
        job.proc.wait()
        job.exit = job.proc.returncode
    except Exception as e:                                  # noqa: BLE001
        job.error = str(e)[:200]
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
            except Exception:                               # noqa: BLE001
                pass
        REDACTOR.reload()


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
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


def _engine_sync_busy():
    """is a sync (timer or another process) holding the engine lock?"""
    import fcntl
    path = os.path.join(C.DATA_DIR, "sync.lock")
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


def _app_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        if not re.match(r"^[A-Za-z0-9._/*?-]{1,128}$", name):
            raise ValueError("bad repo name: %r" % name[:40])
        out.append(name)
    return out


# ---------------------------------------------------------------- public views

def _token_state(p):
    """token *state* only — never the token itself."""
    if p.get("type") == "local":
        return "local"
    if p.get("token"):
        return "set"
    if p.get("auth") == "gh":
        return "gh"
    if p.get("auth") == "glab":
        return "glab"
    return "missing"


def _public_platform(name, p):
    return {"name": name, "type": p.get("type", "?"),
            "enabled": bool(p.get("enabled")),
            "token_state": _token_state(p),
            "url": p.get("url", ""), "owner": p.get("owner", ""),
            "path": p.get("path", ""), "protocol": p.get("protocol", "http"),
            "ssh_port": p.get("ssh_port"),
            "url_template": p.get("url_template", ""),
            "user": p.get("user", ""), "ns_id": p.get("ns_id", ""),
            "affiliation": p.get("affiliation", "")}


def _probe(name, p):
    try:
        plat = P.make(name, p)
        ok, info = plat.test()
        count = None
        if ok and p.get("enabled"):
            try:
                count = len(plat.list_repos())
            except Exception:                               # noqa: BLE001
                count = None
    except Exception as e:                                  # noqa: BLE001
        ok, info, count = False, REDACTOR.redact(str(e)[:120]), None
    row = _public_platform(name, p)
    row.update(ok=bool(ok), info=REDACTOR.redact(info or ""), repo_count=count)
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
    return [r for r in rows if r]


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
            "mesh_conflict": r.get("mesh_conflict")}


# ---------------------------------------------------------------- handler

def make_handler(token, bind, extra_hosts=None):
    extra_hosts = set(h.lower() for h in (extra_hosts or []))

    class Handler(BaseHTTPRequestHandler):
        server_version = "gitsync-web"
        protocol_version = "HTTP/1.1"

        # ---- plumbing ----
        def log_message(self, *a):        # silence per-request stderr noise
            pass

        @property
        def _token(self):
            return self.server.token

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode() or "{}")
            except ValueError:
                return {}

        def _qs(self):
            import urllib.parse
            if "?" not in self.path:
                return {}
            return urllib.parse.parse_qs(self.path.split("?", 1)[1])

        def _send(self, code, body_bytes, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body_bytes)
            except (BrokenPipeError, ConnectionResetError):
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
            return bool(tok) and hmac.compare_digest(tok, self._token)

        def _host_ok(self):
            host = self.headers.get("Host", "")
            h = host.split(":")[0].strip().strip("[]").lower()
            if h in ("localhost", "127.0.0.1", "::1", self.server.bind_host,
                     *extra_hosts):
                return True
            try:
                ip = ipaddress.ip_address(h)
                return ip.is_loopback or ip.is_private
            except ValueError:
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
                    oh = origin.split("//", 1)[-1].split(":")[0].lower()
                    try:
                        ip = ipaddress.ip_address(oh)
                        if not (ip.is_loopback or ip.is_private):
                            return 403, "bad Origin"
                    except ValueError:
                        if oh not in ("localhost", self.server.bind_host,
                                      *extra_hosts):
                            return 403, "bad Origin"
            return None

        # ---- dispatch ----
        def _route(self, method):
            path = self.path.split("?", 1)[0]
            path = re.sub(r"/+$", "", path) or "/"
            if path == "/healthz":
                if method == "GET":
                    return self._send(200, b"ok\n", "text/plain")
                return self._err(405, "method not allowed")
            if path.startswith("/api"):
                is_public = ((method == "POST" and path == "/api/session")
                             or (method == "GET" and path == "/api/auth"))
                if not is_public:
                    if not self._authed():
                        return self._err(401, "unauthorized")
                    g = self._guard(method)
                    if g:
                        return self._err(g[0], g[1])
                for m, rgx, fn in ROUTES:
                    if m != method:
                        continue
                    mt = rgx.match(path)
                    if mt:
                        try:
                            return fn(self, **mt.groupdict())
                        except ValueError as e:
                            return self._err(400, str(e))
                        except Exception as e:              # noqa: BLE001
                            return self._err(500, REDACTOR.redact(
                                str(e)[:200]))
                return self._err(404, "no such endpoint")
            if method != "GET":
                return self._err(405, "method not allowed")
            return self._serve_static(path)

        def do_GET(self):
            self._route("GET")

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
            full = os.path.realpath(os.path.join(root,
                                                 os.path.normpath(path).lstrip("/")))
            if full != root and not full.startswith(root + os.sep):
                return self._err(403, "forbidden")
            if not os.path.isfile(full):
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
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.close_connection = True

            def send(obj):
                chunk = ("data: " + json.dumps(obj) + "\n\n").encode()
                try:
                    self.wfile.write(("%x\r\n" % len(chunk)).encode())
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return False

            sent = 0
            with job.cond:
                while True:
                    for line in job.lines[sent:]:
                        if not send({"line": line}):
                            return
                    sent = len(job.lines)
                    if not send({"progress": dict(job.progress),
                                 "done": job.done, "exit": job.exit,
                                 "error": job.error,
                                 "elapsed": round((job.finished or time.time())
                                                  - job.started, 1)}):
                        return
                    if job.done:
                        break
                    if not job.cond.wait(timeout=15):
                        if not send({"ping": True}):
                            return
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass

    # ---- endpoint handlers (closures over token/bind) ----

    def h_healthz(self):
        self._send(200, b"ok\n", "text/plain")

    def h_login(self):
        b = self._body()
        secure = "; Secure" \
            if self.headers.get("X-Forwarded-Proto") == "https" else ""
        cookie = "gitsync_session=%s; Path=/; HttpOnly; SameSite=Strict%s" \
            % (self._token, secure)
        # username + password sign-in (when a password has been set)
        if "username" in b or "password" in b:
            if not get_auth():
                return self._err(400, "password sign-in is not set up; "
                                      "use the access token")
            if _login_throttle.locked():
                return self._err(429, "too many attempts — wait a minute")
            if check_password(b.get("username", ""), b.get("password", "")):
                _login_throttle.reset()
                return self._send(200, b'{"ok":true}', "application/json",
                                  {"Set-Cookie": cookie})
            _login_throttle.fail()
            time.sleep(0.25)              # slow online guessing
            return self._err(401, "invalid username or password")
        # access-token sign-in (always available; also the API credential)
        tok = (b.get("token") or "").strip()
        if tok and hmac.compare_digest(tok, self._token):
            return self._send(200, b'{"ok":true}', "application/json",
                              {"Set-Cookie": cookie})
        return self._err(401, "invalid token")

    def h_auth_info(self):
        a = get_auth()
        self._json({"password_set": bool(a),
                    "username": a.get("username") if a else "",
                    "token_login": True})

    def h_set_password(self):
        b = self._body()
        if b.get("disable"):
            clear_password()
            return self._json({"ok": True, "password_set": False,
                               "message": "password sign-in disabled"})
        try:
            set_password(b.get("username", ""), b.get("password", ""))
        except ValueError as e:
            return self._err(400, str(e))
        return self._json({"ok": True, "password_set": True,
                           "message": "password set — sign in with it next time"})

    def h_logout(self):
        self._send(200, b'{"ok":true}', "application/json",
                   {"Set-Cookie": "gitsync_session=; Path=/; HttpOnly; "
                                  "SameSite=Strict; Max-Age=0"})

    def h_session(self):
        self._json({"authed": True, "version": __version__})

    def h_status(self):
        cfg = C.load_config()
        recs = SYNC.last_records(1)
        last = recs[-1] if recs else None
        syncing = bool(_current_sync and not _current_sync.done)
        self._json({
            "version": __version__,
            "syncing": syncing,
            "schedule": C.setting(cfg, "schedule", "hourly"),
            "scheduler": _cached_scheduler(),
            "next_run": _cached_next_run(),
            "config_file": C.CONFIG_FILE, "mirror_dir": C.MIRROR_DIR,
            "log_file": C.LOG_FILE, "bind": self.server.bind_host,
            "port": self.server.port,
            "last_run": _public_record(last) if last else None,
            "counts": {"platforms": len(cfg.get("platforms") or {}),
                       "routes": len(C.routes(cfg))},
        })

    def h_platforms(self):
        cfg = C.load_config()
        if (self._qs().get("probe") or [None])[0] == "0":
            # lightweight: names/types/enabled only, no network — for forms
            rows = [_public_platform(n, p)
                    for n, p in (cfg.get("platforms") or {}).items()]
            return self._json({"platforms": rows})
        self._json({"platforms": _probe_all(cfg)})

    def h_platform_add(self):
        b = self._body()
        name = (b.get("name") or "").strip()
        ptype = (b.get("type") or "").strip()
        if not _valid_name(name):
            raise ValueError("invalid platform name")
        if ptype not in C.PLATFORM_TYPES:
            raise ValueError("unknown platform type")
        cfg = C.load_config()
        if name in (cfg.get("platforms") or {}):
            raise ValueError("platform '%s' already exists" % name)
        if ptype == "local" and not (b.get("path") or "").strip():
            raise ValueError("local platforms need a path")
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
        err = C.add_platform(cfg, name, spec)
        if err:
            raise ValueError(err)
        C.save_config(cfg)
        REDACTOR.reload()
        ok, info = False, ""
        try:
            ok, info = P.make(name, spec).test()
        except Exception as e:                              # noqa: BLE001
            info = str(e)[:120]
        self._json({"ok": True, "platform": _public_platform(name, spec),
                    "test": {"ok": bool(ok),
                             "info": REDACTOR.redact(info or "")}}, 201)

    def h_platform_edit(self, name):
        if not _valid_name(name):
            raise ValueError("invalid platform name")
        b = self._body()
        cfg = C.load_config()
        p = (cfg.get("platforms") or {}).get(name)
        if not p:
            return self._err(404, "no such platform")
        if p.get("type") == "local":
            if b.get("path"):
                p["path"] = os.path.expanduser(b["path"])
        else:
            # token is write-only: an empty/absent field means "keep it"
            if (b.get("token") or "").strip():
                p["token"] = b["token"].strip()
            for f in ("url", "owner", "protocol", "ns_id", "affiliation",
                      "url_template", "user"):
                if b.get(f) is not None and b.get(f) != "":
                    p[f] = b[f]
            if b.get("ssh_port") is not None:
                try:
                    p["ssh_port"] = int(b["ssh_port"])
                except (TypeError, ValueError):
                    pass
            if p.get("type") == "github" and not p.get("token"):
                p["auth"] = "gh"
        if "enabled" in b:
            p["enabled"] = bool(b["enabled"])
        C.save_config(cfg)
        REDACTOR.reload()
        self._json({"ok": True, "platform": _public_platform(name, p)})

    def h_platform_delete(self, name):
        cfg = C.load_config()
        force = (self._qs().get("force") or [None])[0] == "1"
        err = C.remove_platform(cfg, name, force=force)
        if err:
            used = C.platform_used_by(cfg, name)
            return self._err(409, err if force or not used else
                             "%s — re-run with force=1 to remove anyway" % err)
        C.save_config(cfg)
        REDACTOR.reload()
        self._json({"ok": True})

    def h_platform_test(self, name):
        cfg = C.load_config()
        p = (cfg.get("platforms") or {}).get(name)
        if not p:
            return self._err(404, "no such platform")
        try:
            ok, info = P.make(name, p).test()
        except Exception as e:                              # noqa: BLE001
            ok, info = False, str(e)[:120]
        self._json({"ok": bool(ok), "info": REDACTOR.redact(info or "")})

    def h_platform_toggle(self, name):
        cfg = C.load_config()
        p = (cfg.get("platforms") or {}).get(name)
        if not p:
            return self._err(404, "no such platform")
        p["enabled"] = not bool(p.get("enabled"))
        C.save_config(cfg)
        self._json({"ok": True, "enabled": p["enabled"]})

    def h_repos(self):
        cfg = C.load_config()
        name = (self._qs().get("platform") or [None])[0]
        if not name:
            routes = C.routes(cfg)
            name = routes[0]["source"] if routes else None
        if not name:
            return self._err(400, "no platform to list")
        p = C.platform(cfg, name)
        if not p:
            return self._err(404, "no such platform: %s" % name)
        try:
            repos = P.make(name, p).list_repos()
        except Exception as e:                              # noqa: BLE001
            return self._err(502, "cannot list %s: %s"
                             % (name, REDACTOR.redact(str(e)[:160])))
        q = (self._qs().get("q") or [""])[0].lower()
        forks = (self._qs().get("forks") or [None])[0] == "1"
        out = []
        for r in repos:
            if not forks and r.get("fork"):
                continue
            if q and q not in r["name"].lower():
                continue
            out.append({"name": r["name"], "private": bool(r.get("private")),
                        "fork": bool(r.get("fork"))})
        out.sort(key=lambda r: r["name"].lower())
        self._json({"platform": name, "repos": out})

    def h_selection_file(self):
        b = self._body()
        repos = b.get("repos") or []
        platform = (b.get("platform") or "").strip()
        if not repos:
            raise ValueError("no repos selected")
        for r in repos:
            if not re.match(r"^[A-Za-z0-9._-]{1,128}$", r):
                raise ValueError("bad repo name: %r" % r[:40])
        path = os.path.expanduser((b.get("path") or "").strip()
                                  or os.path.join(
                                      C.CONFIG_DIR,
                                      "repos-%s.txt" % (platform or "selection")))
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w") as f:
            f.write("# repos synced by gitsync — one per line, # comments\n")
            for r in repos:
                f.write(r + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        cfg = C.load_config()
        routes = [r for r in C.routes(cfg) if r.get("source") == platform] \
            or C.routes(cfg)[:1]
        for r in routes:
            r["include_file"] = path
            r.pop("include", None)
        C.save_config(cfg)
        self._json({"ok": True, "path": path, "count": len(repos),
                    "routes": [r.get("name") for r in routes]})

    def h_routes(self):
        cfg = C.load_config()
        self._json({"routes": [_route_view(cfg, r) for r in C.routes(cfg)]})

    def h_route_add(self):
        b = self._body()
        name = (b.get("name") or "").strip()
        source = (b.get("source") or "").strip()
        targets = [t.strip() for t in (b.get("targets") or []) if t.strip()]
        if not _valid_name(name):
            raise ValueError("invalid route name")
        for n in [source] + targets:
            if not _valid_name(n):
                raise ValueError("invalid platform name: %r" % n)
        if not targets:
            raise ValueError("a route needs at least one target")
        cfg = C.load_config()
        if any(r.get("name") == name for r in cfg.get("routes", [])):
            raise ValueError("route '%s' already exists" % name)
        if source not in (cfg.get("platforms") or {}):
            raise ValueError("unknown source platform: %s" % source)
        for t in targets:
            if t not in (cfg.get("platforms") or {}):
                raise ValueError("unknown target platform: %s" % t)
        route = {"name": name, "source": source, "targets": targets}
        if b.get("direction") == "mesh":
            route["direction"] = "mesh"
        for key in ("workers", "mode", "mesh_conflict", "schedule"):
            v = b.get(key)
            if v and v != "inherit":
                route[key] = int(v) if key == "workers" else v
        cfg.setdefault("routes", []).append(route)
        C.save_config(cfg)
        self._json({"ok": True, "route": _route_view(cfg, route)}, 201)

    def h_route_edit(self, name):
        b = self._body()
        cfg = C.load_config()
        route = next((r for r in cfg.get("routes", [])
                      if r.get("name") == name), None)
        if not route:
            return self._err(404, "no such route")
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
                else:
                    route[key] = int(v) if key == "workers" else v
        if "schedule" in b:
            v = b["schedule"]
            if v in (None, "", "inherit"):
                route.pop("schedule", None)
            else:
                route["schedule"] = v
        if "enabled" in b:
            route["enabled"] = bool(b["enabled"])
        C.save_config(cfg)
        self._json({"ok": True, "route": _route_view(cfg, route)})

    def h_route_delete(self, name):
        cfg = C.load_config()
        before = len(cfg.get("routes", []))
        cfg["routes"] = [r for r in cfg.get("routes", [])
                         if r.get("name") != name]
        C.save_config(cfg)
        self._json({"ok": len(cfg["routes"]) < before})

    def h_route_pause(self, name):
        return self._pause_resume(name, pause=True)

    def h_route_resume(self, name):
        return self._pause_resume(name, pause=False)

    def _pause_resume(self, name, pause):
        b = self._body()
        want = [t.strip() for t in (b.get("targets") or []) if t.strip()]
        cfg = C.load_config()
        route = next((r for r in cfg.get("routes", [])
                      if r.get("name") == name), None)
        if not route:
            return self._err(404, "no such route")
        if not want:
            route["enabled"] = not pause
            if not pause:
                route.pop("paused_targets", None)
        else:
            known = set(route.get("targets") or [])
            unknown = [t for t in want if t not in known]
            if unknown:
                return self._err(400, "%s has no target(s): %s"
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
        C.save_config(cfg)
        self._json({"ok": True, "route": _route_view(cfg, route)})

    def h_stars(self):
        cfg = C.load_config()
        name = (self._qs().get("platform") or [None])[0]
        if not name:
            routes = C.routes(cfg)
            name = routes[0]["source"] if routes else None
        p = C.platform(cfg, name) if name else None
        if not p:
            return self._err(404, "no platform to read stars from")
        try:
            rows = ST.starred(P.make(name, p))
        except Exception as e:                              # noqa: BLE001
            return self._err(502, "cannot read stars on %s: %s"
                             % (name, REDACTOR.redact(str(e)[:160])))
        self._json({"platform": name,
                    "stars": [{"name": n, "url": u} for n, u in rows]})

    def h_stars_copy(self):
        b = self._body()
        source = (b.get("source") or "").strip()
        targets = [t.strip() for t in (b.get("targets") or []) if t.strip()]
        if not source or not targets:
            raise ValueError("pick a source and at least one target")
        cfg = C.load_config()
        if source not in (cfg.get("platforms") or {}):
            raise ValueError("unknown source platform: %s" % source)
        unknown = [t for t in targets if t not in (cfg.get("platforms") or {})]
        if unknown:
            raise ValueError("unknown target(s): %s" % ", ".join(unknown))
        only = b.get("repos") or None
        lines = []
        t = ST.copy_stars(cfg, source, targets,
                          dry_run=bool(b.get("dry_run")), only=only,
                          log=lambda m: lines.append(REDACTOR.redact(m)))
        self._json({"ok": True, "summary": t, "log": lines})

    def h_log(self):
        tail = int((self._qs().get("tail") or ["400"])[0])
        q = (self._qs().get("q") or [""])[0].lower()
        lines = []
        try:
            with open(C.LOG_FILE) as f:
                lines = f.read().splitlines()
        except OSError:
            pass
        total = len(lines)
        if q:
            lines = [l for l in lines if q in l.lower()]
        lines = lines[-tail:]
        self._json({"lines": [REDACTOR.redact(l) for l in lines],
                    "total": total, "shown": len(lines)})

    def h_history(self):
        n = int((self._qs().get("n") or ["40"])[0])
        recs = SYNC.last_records(n)
        self._json({
            "records": [_public_record(r) for r in recs],
            "spark": [r.get("elapsed_sec", 0) or 0 for r in recs[-30:]],
        })

    def h_settings_get(self):
        cfg = C.load_config()
        st = dict(cfg.get("settings") or {})
        notify = dict(st.get("notify") or {})
        notify_public = {
            "on": notify.get("on", "failure"),
            "discord_set": bool(notify.get("discord_webhook")),
            "telegram_set": bool(notify.get("telegram_token")
                                 and notify.get("telegram_chat_id")),
        }
        st["notify"] = notify_public
        st["paths"] = {"config": C.CONFIG_FILE, "mirror": C.MIRROR_DIR,
                       "log": C.LOG_FILE, "data": C.DATA_DIR}
        st["schedules"] = {k: v["label"] for k, v in C.SCHEDULES.items()}
        st["themes"] = list(C.THEMES)
        self._json({"settings": st})

    def h_settings_put(self):
        b = self._body()
        cfg = C.load_config()
        st = cfg.setdefault("settings", {})
        _apply_setting(st, "workers", b, int, [1, 2, 4, 8])
        _apply_setting(st, "mode", b, str, ["safe", "full"])
        _apply_setting(st, "mesh_conflict", b, str, ["newest", "skip"])
        _apply_setting(st, "schedule", b, str, list(C.SCHEDULES))
        _apply_setting(st, "include_forks", b, bool)
        _apply_setting(st, "pace", b, lambda v: float(v), [0, 1, 2, 5, 10])
        _apply_setting(st, "relax_protection", b, bool)
        _apply_setting(st, "token_warn_days", b, int, [7, 14, 30, 60, 90])
        _apply_setting(st, "theme", b, str, list(C.THEMES))
        notify = st.setdefault("notify", {})
        if "on" in b:
            if b["on"] not in ("failure", "success", "always", "never"):
                raise ValueError("bad notify mode")
            notify["on"] = b["on"]
        if (b.get("discord_webhook") or "").strip():
            url = b["discord_webhook"].strip()
            if not url.startswith(("https://discord.com/api/webhooks/",
                                   "https://discordapp.com/api/webhooks/")):
                raise ValueError("that is not a Discord webhook URL")
            notify["discord_webhook"] = url
        if (b.get("telegram_token") or "").strip():
            tok = b["telegram_token"].strip()
            chat = (b.get("telegram_chat_id") or "").strip()
            if not chat:
                raise ValueError("telegram needs a chat id too")
            notify["telegram_token"] = tok
            notify["telegram_chat_id"] = chat
        C.save_config(cfg)
        REDACTOR.reload()
        self._json({"ok": True})

    def h_notify_test(self):
        cfg = C.load_config()
        ok, msg = NT.notify_test(cfg)
        self._json({"ok": bool(ok), "message": REDACTOR.redact(msg or "")})

    def h_scopes(self):
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
        self._json({"scopes": out})

    def h_health(self):
        now = time.time()
        if _cache["health"][0] is not None and now - _cache["health"][1] < 15:
            return self._json(_cache["health"][0])
        cfg = C.load_config()
        try:
            tok = TK.check(cfg)
            tokens = [{"platform": t["platform"], "type": t.get("type"),
                       "level": t.get("level"), "note": t.get("note", ""),
                       "days_left": t.get("days_left"),
                       "expires_at": t.get("expires_at")} for t in tok]
            tok_summary = TK.summary(tok)
            tok_bad = bool(TK.bad(tok))
        except Exception:                                   # noqa: BLE001
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
        except Exception:                                   # noqa: BLE001
            pass
        wh = {"secret_set": bool(WH.get_secret()),
              "active": S.webhooks_active()}
        out = {"tokens": tokens, "token_summary": tok_summary,
               "token_bad": tok_bad, "disk": disk, "webhooks": wh,
               "disk_human": {"size": DK.human(disk["size"]),
                              "free": DK.human(disk["free"]) if disk["free"]
                              else None,
                              "total": DK.human(disk["total"]) if disk["total"]
                              else None}}
        _cache["health"] = (out, now)
        self._json(out)

    def h_scheduler_install(self):
        b = self._body()
        cfg = C.load_config()
        key = b.get("schedule") or C.finest_schedule(cfg) \
            or C.setting(cfg, "schedule", "hourly")
        if key == "off":
            S.uninstall_systemd(system=True)
            S.uninstall_systemd()
            S.install_cron("off")
            return self._json({"ok": True, "status": "scheduler disabled"})
        if b.get("schedule"):
            C.set_setting(cfg, "schedule", key)
        if b.get("cron") or not S.have_systemd():
            S.install_cron(key)
            return self._json({"ok": True,
                               "status": "cron: %s" % C.SCHEDULES[key]["label"]})
        if S.install_systemd(key):
            return self._json({"ok": True,
                               "status": "timer: %s"
                               % C.SCHEDULES[key]["label"]})
        S.install_cron(key)
        return self._json({"ok": True,
                           "status": "cron (systemd failed): %s"
                           % C.SCHEDULES[key]["label"]})

    def h_webhooks_get(self):
        cfg = C.load_config()
        endpoints = []
        for name, p in (cfg.get("platforms") or {}).items():
            if p.get("enabled") and p.get("type") in WH.AUTH_STYLE:
                endpoints.append({"name": name, "type": p.get("type"),
                                  "path": "/hook/%s" % name})
        self._json({"secret_set": bool(WH.get_secret()),
                    "active": S.webhooks_active(), "port": WH.DEFAULT_PORT,
                    "endpoints": endpoints})

    def h_webhooks_action(self):
        b = self._body()
        action = b.get("action")
        if action == "rotate":
            WH.rotate_secret()
            _say("  %s new webhook secret generated — read it with: "
                 "gitsync webhooks --secret --show"
                 % _c("33", "[web]"))
            return self._json({"ok": True, "secret_set": True,
                               "note": "secret generated; view it with "
                               "'gitsync webhooks --secret --show'"})
        if action == "install":
            ok = S.install_webhooks(port=WH.DEFAULT_PORT, bind="0.0.0.0")
            return self._json({"ok": bool(ok),
                               "status": "installed" if ok
                               else "install failed (needs systemd)"})
        if action == "stop":
            ok = S.uninstall_webhooks()
            return self._json({"ok": True,
                               "status": "removed" if ok else "not installed"})
        raise ValueError("unknown webhook action")

    def h_sync_start(self):
        if _engine_sync_busy():
            return self._err(409, "a sync is already running")
        b = self._body()
        argv = ["sync"]
        if b.get("route"):
            if not _valid_name(b["route"]):
                raise ValueError("invalid route name")
            argv += ["--route", b["route"]]
        if b.get("to"):
            if not _valid_name(b["to"]):
                raise ValueError("invalid target name")
            argv += ["--to", b["to"]]
        repos = _clean_repo_list(b.get("repos"))
        if repos:
            argv += ["--repos", ",".join(repos)]
        dry = bool(b.get("dry_run"))
        if dry:
            argv += ["--dry-run"]
        job = start_job("sync", [sys.executable, "-m", "gitsync"] + argv,
                        mutating=True)
        if job is None:
            return self._err(409, "another job is already running")
        global _current_sync
        _current_sync = job
        _say("  %s %s started (%s)"
             % (_c("36", "[web]"), "dry run" if dry else "sync", job.id))
        self._json({"ok": True, "job_id": job.id, "dry_run": dry,
                    "kind": "sync"}, 202)

    def h_sync_state(self):
        job = _current_sync
        if not job:
            return self._json({"syncing": False, "job_id": None,
                               "progress": {"done": 0, "total": 0}})
        snap = job.snapshot()
        self._json({"syncing": not job.done, "job_id": job.id,
                    "progress": snap["progress"], "done": job.done,
                    "exit": job.exit, "kind": job.kind})

    def h_sync_stream(self):
        jid = (self._qs().get("job_id") or [None])[0]
        job = _jobs.get(jid) if jid else _current_sync
        if not job:
            return self._err(404, "no such job")
        self._sse(job)

    def h_job_start(self):
        b = self._body()
        kind = (b.get("kind") or "").strip()
        argv, mutating = _tool_argv(kind, b)
        job = start_job(kind, [sys.executable, "-m", "gitsync"] + argv,
                        mutating=mutating)
        if job is None:
            return self._err(409, "another job is already running")
        _say("  %s tool '%s' started (%s)" % (_c("36", "[web]"), kind, job.id))
        self._json({"ok": True, "job_id": job.id, "kind": kind}, 202)

    def h_job_state(self, job_id):
        job = _jobs.get(job_id)
        if not job:
            return self._err(404, "no such job")
        snap = job.snapshot()
        with job.cond:
            snap["lines"] = list(job.lines)
        snap["lines"] = [REDACTOR.redact(l) for l in snap["lines"]]
        self._json(snap)

    def h_job_stream(self, job_id):
        job = _jobs.get(job_id)
        if not job:
            return self._err(404, "no such job")
        self._sse(job)

    ROUTES = [
        ("GET", re.compile(r"^/api/auth$"), lambda s: h_auth_info(s)),
        ("POST", re.compile(r"^/api/auth/password$"),
         lambda s: h_set_password(s)),
        ("POST", re.compile(r"^/api/session$"), lambda s: h_login(s)),
        ("GET", re.compile(r"^/api/session$"), lambda s: h_session(s)),
        ("GET", re.compile(r"^/api/logout$"), lambda s: h_logout(s)),
        ("GET", re.compile(r"^/api/status$"), lambda s: h_status(s)),
        ("GET", re.compile(r"^/api/platforms$"), lambda s: h_platforms(s)),
        ("POST", re.compile(r"^/api/platforms$"), lambda s: h_platform_add(s)),
        ("PUT", re.compile(r"^/api/platforms/(?P<name>[^/]+)$"),
         lambda s, name: h_platform_edit(s, name)),
        ("DELETE", re.compile(r"^/api/platforms/(?P<name>[^/]+)$"),
         lambda s, name: h_platform_delete(s, name)),
        ("POST", re.compile(r"^/api/platforms/(?P<name>[^/]+)/test$"),
         lambda s, name: h_platform_test(s, name)),
        ("POST", re.compile(r"^/api/platforms/(?P<name>[^/]+)/toggle$"),
         lambda s, name: h_platform_toggle(s, name)),
        ("GET", re.compile(r"^/api/repos$"), lambda s: h_repos(s)),
        ("POST", re.compile(r"^/api/repos/selection-file$"),
         lambda s: h_selection_file(s)),
        ("GET", re.compile(r"^/api/routes$"), lambda s: h_routes(s)),
        ("POST", re.compile(r"^/api/routes$"), lambda s: h_route_add(s)),
        ("PUT", re.compile(r"^/api/routes/(?P<name>[^/]+)$"),
         lambda s, name: h_route_edit(s, name)),
        ("DELETE", re.compile(r"^/api/routes/(?P<name>[^/]+)$"),
         lambda s, name: h_route_delete(s, name)),
        ("POST", re.compile(r"^/api/routes/(?P<name>[^/]+)/pause$"),
         lambda s, name: h_route_pause(s, name)),
        ("POST", re.compile(r"^/api/routes/(?P<name>[^/]+)/resume$"),
         lambda s, name: h_route_resume(s, name)),
        ("GET", re.compile(r"^/api/stars$"), lambda s: h_stars(s)),
        ("POST", re.compile(r"^/api/stars/copy$"), lambda s: h_stars_copy(s)),
        ("GET", re.compile(r"^/api/log$"), lambda s: h_log(s)),
        ("GET", re.compile(r"^/api/history$"), lambda s: h_history(s)),
        ("GET", re.compile(r"^/api/settings$"), lambda s: h_settings_get(s)),
        ("PUT", re.compile(r"^/api/settings$"), lambda s: h_settings_put(s)),
        ("POST", re.compile(r"^/api/notify/test$"),
         lambda s: h_notify_test(s)),
        ("GET", re.compile(r"^/api/scopes$"), lambda s: h_scopes(s)),
        ("GET", re.compile(r"^/api/health$"), lambda s: h_health(s)),
        ("POST", re.compile(r"^/api/scheduler/install$"),
         lambda s: h_scheduler_install(s)),
        ("GET", re.compile(r"^/api/webhooks$"), lambda s: h_webhooks_get(s)),
        ("POST", re.compile(r"^/api/webhooks$"),
         lambda s: h_webhooks_action(s)),
        ("POST", re.compile(r"^/api/sync$"), lambda s: h_sync_start(s)),
        ("GET", re.compile(r"^/api/sync/state$"), lambda s: h_sync_state(s)),
        ("GET", re.compile(r"^/api/sync/stream$"), lambda s: h_sync_stream(s)),
        ("POST", re.compile(r"^/api/jobs$"), lambda s: h_job_start(s)),
        ("GET", re.compile(r"^/api/jobs/(?P<job_id>[^/]+)$"),
         lambda s, job_id: h_job_state(s, job_id)),
        ("GET", re.compile(r"^/api/jobs/(?P<job_id>[^/]+)/stream$"),
         lambda s, job_id: h_job_stream(s, job_id)),
    ]

    return Handler


def _apply_setting(st, key, body, cast, choices=None):
    if key not in body or body[key] in (None, ""):
        return
    val = cast(body[key])
    if choices and val not in choices:
        raise ValueError("invalid %s: %r" % (key, body[key]))
    st[key] = val


def _tool_argv(kind, b):
    """(argv_after_'gitsync', mutating) for a Tools action — reuses the CLI."""
    route = (b.get("route") or "").strip()
    if kind == "compare":
        argv = ["compare"] + (["--route", route] if route else [])
        return argv, False
    if kind == "doctor":
        argv = ["doctor"] + (["--fix"] if b.get("fix") else [])
        return argv, False
    if kind == "fix":
        argv = ["fix"] + (["--route", route] if route else [])
        return argv, True
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


def _public_record(r):
    if not r:
        return None
    fails = [{"route": f.get("route"), "repo": f.get("repo"),
              "error": REDACTOR.redact(str(f.get("error", ""))[:200])}
             for f in (r.get("failures") or [])]
    return {"finished_at": r.get("finished_at"), "ok": r.get("ok", 0),
            "partial": r.get("partial", 0), "failed": r.get("failed", 0),
            "repos": r.get("repos", 0), "elapsed_sec": r.get("elapsed_sec"),
            "routes": r.get("routes", 0), "failures": fails}


# ---------------------------------------------------------------- server

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, token, bind_host, extra_hosts):
        self.token = token
        self.bind_host = bind_host
        self.port = addr[1]
        self.extra_hosts = extra_hosts
        super().__init__(addr, handler)


def serve(port=DEFAULT_PORT, bind="127.0.0.1", allowed_hosts=None):
    token, generated = get_web_token()
    REDACTOR.reload()
    extra = set(h.lower() for h in (allowed_hosts or []))
    env_hosts = (os.environ.get("GITSYNC_ALLOWED_HOSTS") or "").split(",")
    extra |= {h.strip().lower() for h in env_hosts if h.strip()}
    handler = make_handler(token, bind, extra)
    try:
        httpd = _Server((bind, port), handler, token, bind, extra)
    except OSError as e:
        _say("cannot bind %s:%d — %s" % (bind, port, e))
        _say("another gitsync web instance may be running, or the port is "
             "taken. Pick another with --port.")
        return 1

    loop = _is_loopback(bind)
    _say("%s web UI on http://%s:%d" % (_c("32", "gitsync"), bind, port))
    if not loop:
        _say("%s bound to %s (non-loopback). Authentication is REQUIRED and "
             "enforced on every request." % (_c("33", "WARNING:"), bind))
    if generated:
        _say("")
        _say("  %s %s" % (_c("1", "access token (shown once):"), token))
        _say("  stored in %s (mode 0600)" % _token_path())
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
