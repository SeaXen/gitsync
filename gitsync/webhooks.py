"""Webhook receiver: let platforms push sync events instead of polling.

    gitsync webhooks --serve              # run in the foreground
    gitsync webhooks --install            # systemd service (survives reboot)
    gitsync webhooks --secret --show      # print the current shared secret
    gitsync webhooks --secret --rotate    # generate a new one

Then point each platform's webhook at:

    http://<this box>:<port>/hook/<platform-name>

e.g. http://10.0.0.5:8791/hook/github

Every request is authenticated with a per-install shared secret before any
sync is triggered, and the listener binds to 127.0.0.1 unless you say
otherwise — front it with a reverse proxy or a tunnel for remote platforms.

Zero dependencies: Python standard library only.
"""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as C

DEFAULT_PORT = 8791
DEDUPE_SECONDS = 30
MAX_BODY = 1 << 20          # 1 MB is plenty for a push event

# platform type -> how its webhook authenticates
AUTH_STYLE = {
    "github": "hmac-sha256",     # X-Hub-Signature-256: sha256=<hex>
    "gitlab": "token",           # X-Gitlab-Token: <secret>
    "forgejo": "token",          # X-Gitea-Token: <secret>
    "gitea": "token",
    "bitbucket": "token",        # X-Hub-Uuid + secret in the URL
}


def log(msg):
    line = "[%s] webhook: %s" % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        C.ensure_dirs()
        with open(C.LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- secrets

def _secret_path():
    return os.path.join(C.CONFIG_DIR, "webhook.secret")


def get_secret():
    """the shared secret, or '' if unset"""
    try:
        with open(_secret_path()) as f:
            return f.read().strip()
    except OSError:
        return ""


def set_secret(value):
    os.makedirs(C.CONFIG_DIR, exist_ok=True)
    with open(_secret_path(), "w") as f:
        f.write(value.strip())
    os.chmod(_secret_path(), 0o600)


def rotate_secret():
    """generate and store a fresh secret. Returns it."""
    import secrets as _s
    val = _s.token_hex(24)
    set_secret(val)
    return val


# ---------------------------------------------------------------- helpers

def _repo_name(payload):
    for key in ("repository", "project"):
        r = payload.get(key)
        if isinstance(r, dict):
            n = r.get("name") or r.get("path")
            if n:
                return n
    return None


def _event(payload, header_event):
    """'push' | 'ping' | other, from the payload or the platform's header"""
    if header_event and header_event.strip():
        h = header_event.strip().lower()
        # GitLab/Forgejo use human-readable names; GitHub uses bare 'push'
        if h in ("push", "push hook", "push hooks", "tag push hook"):
            return "push"
        if h == "ping":
            return "ping"
        return "other"
    if "commits" in payload or "after" in payload:
        return "push"
    if payload.get("zen") is not None:
        return "ping"
    return "other"


def _platform_for(path_segment, cfg):
    """resolve /hook/<name> to a configured platform"""
    seg = (path_segment or "").strip("/").lower()
    plats = cfg.get("platforms") or {}
    if seg in plats:
        return seg, plats[seg]
    # fall back to the first enabled platform of that type
    for name, p in plats.items():
        if p.get("type", "").lower() == seg and p.get("enabled"):
            return name, p
    return None, None


def _verify(style, secret, headers, body):
    """constant-time check of the platform's signature"""
    if not secret:
        return False, "no shared secret configured"
    if style == "hmac-sha256":
        sig = headers.get("X-Hub-Signature-256") or ""
        if not sig.startswith("sha256="):
            return False, "missing X-Hub-Signature-256"
        want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest("sha256=" + want, sig):
            return True, ""
        return False, "signature mismatch"
    # plain shared-token platforms (GitLab, Forgejo, Gitea)
    got = (headers.get("X-Gitlab-Token") or headers.get("X-Gitea-Token")
           or headers.get("X-Hub-Token") or "")
    if got and hmac.compare_digest(got, secret):
        return True, ""
    return False, "token mismatch"


class _Deduper:
    """collapse bursts of events for the same repo"""

    def __init__(self, window=DEDUPE_SECONDS):
        self.window = window
        self._seen = {}
        self._lock = threading.Lock()

    def fresh(self, key):
        now = time.time()
        with self._lock:
            last = self._seen.get(key, 0)
            if now - last < self.window:
                return False
            self._seen[key] = now
            return True


_dedupe = _Deduper()


def _run_sync(platform, repo, routes):
    """spawn a detached sync for one repo on the matching routes"""
    env = dict(os.environ)
    env["PYTHONPATH"] = _app_dir()
    for r in routes:
        cmd = [sys.executable, "-m", "gitsync", "sync",
               "--route", r.get("name"), "--repos", repo, "--quiet"]
        try:
            subprocess.Popen(cmd, env=env, cwd=_app_dir(),
                             start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            log("triggered %s via route '%s'" % (repo, r.get("name")))
        except Exception as e:
            log("failed to spawn sync for %s: %s" % (repo, e))


def _app_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- handler

def make_handler(cfg, secret, bind_allow=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "gitsync-webhook"
        protocol_version = "HTTP/1.1"

        # ---- quiet logging (one line per request, no tracebacks) ----
        def log_message(self, fmt, *args):
            log(fmt % args)

        def _reply(self, code, text):
            body = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = self.path.rstrip("/")
            if path in ("/healthz", "/health"):
                self._reply(200, "ok\n")
                return
            if path == "":
                self._reply(200, "gitsync webhook receiver\n")
                return
            self._reply(404, "not found\n")

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                self._reply(400, "bad content-length\n")
                return
            try:
                body = self.rfile.read(length)
            except (BrokenPipeError, ConnectionResetError):
                return

            # optional IP allowlist
            if bind_allow:
                ip = self.client_address[0] if self.client_address else ""
                if ip not in bind_allow:
                    self._reply(403, "forbidden\n")
                    return

            seg = self.path.strip("/").split("/")
            if len(seg) < 2 or seg[0] != "hook":
                self._reply(404, "use /hook/<platform-name>\n")
                return

            pname, pcfg = _platform_for(seg[1], cfg)
            if not pcfg:
                self._reply(404, "unknown platform '%s'\n" % seg[1])
                return
            if not pcfg.get("enabled"):
                self._reply(409, "platform '%s' is disabled\n" % pname)
                return

            style = AUTH_STYLE.get(pcfg.get("type", ""), "token")
            hdrs = {k: v for k, v in self.headers.items()}
            ok, why = _verify(style, secret, hdrs, body)
            if not ok:
                log("rejected %s (%s): %s" % (pname,
                                              self.client_address[0], why))
                self._reply(401, why + "\n")
                return

            try:
                payload = json.loads(body.decode() or "{}")
            except ValueError:
                self._reply(400, "invalid json\n")
                return

            ev = _event(payload, self.headers.get("X-Gitlab-Event")
                        or self.headers.get("X-GitHub-Event")
                        or self.headers.get("X-Gitea-Event"))
            if ev == "ping":
                log("ping from %s — ok" % pname)
                self._reply(200, "pong\n")
                return
            if ev != "push":
                log("ignored %s event from %s" % (ev, pname))
                self._reply(202, "ignored (%s)\n" % ev)
                return

            repo = _repo_name(payload)
            if not repo:
                self._reply(400, "no repository in payload\n")
                return
            if not _dedupe.fresh((pname, repo.lower())):
                log("deduped %s/%s (synced moments ago)" % (pname, repo))
                self._reply(202, "deduped\n")
                return

            # a mesh route is triggered by a push on ANY of its peers, not
            # only the one listed as its source. The config is re-read per
            # request so a route added while the receiver runs is picked up.
            cfg = C.load_config()
            routes = [r for r in C.routes(cfg)
                      if r.get("source") == pname
                      or (r.get("direction") == "mesh"
                          and pname in (r.get("targets") or []))]
            if not routes:
                log("no route uses '%s' as a source — nothing to do" % pname)
                self._reply(200, "no route for %s\n" % pname)
                return

            log("push on %s/%s -> %d route(s)" % (pname, repo, len(routes)))
            _run_sync(pname, repo, routes)
            self._reply(200, "sync triggered\n")

    return Handler


# ---------------------------------------------------------------- server

def serve(port=DEFAULT_PORT, bind="127.0.0.1", allow=None, cfg=None):
    cfg = cfg or C.load_config()
    secret = get_secret()
    if not secret:
        print("no webhook secret set. Create one first:")
        print("  gitsync webhooks --secret --rotate")
        return 2

    allow_set = None
    if allow:
        allow_set = {a.strip() for a in allow.split(",") if a.strip()}

    handler = make_handler(cfg, secret, allow_set)
    try:
        httpd = ThreadingHTTPServer((bind, port), handler)
    except OSError as e:
        print("cannot bind %s:%d — %s" % (bind, port, e))
        print("another gitsync webhooks instance may be running, or the")
        print("port is taken. Pick another with --port.")
        return 1

    log("listening on %s:%d (%d route(s) registered)"
        % (bind, port, len(C.routes(cfg))))
    print("webhook receiver on %s:%d — Ctrl-C to stop" % (bind, port))
    print("URLs to configure:")
    for name, p in (cfg.get("platforms") or {}).items():
        if p.get("enabled") and p.get("type") in AUTH_STYLE:
            print("  %-16s http://%s:%d/hook/%s"
                  % (name, bind if bind != "0.0.0.0" else "<this-box>",
                     port, name))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
