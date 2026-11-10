"""Configuration management for gitsync.

Config lives at ~/.config/gitsync/config.json (override with GITSYNC_CONFIG).
Runtime data (mirror hub, logs, run records) lives at ~/.local/share/gitsync
(override with GITSYNC_DATA).
"""

import json
import os
import threading

from . import __version__

APP = "gitsync"
VERSION = __version__      # one source of truth
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_DIR = os.environ.get(
    "GITSYNC_CONFIG_DIR",
    os.path.expanduser("~/.config/%s" % APP))
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DATA_DIR = os.environ.get(
    "GITSYNC_DATA_DIR",
    os.path.expanduser("~/.local/share/%s" % APP))
MIRROR_DIR = os.path.join(DATA_DIR, "mirrors")
LOG_FILE = os.path.join(DATA_DIR, "gitsync.log")
RECORDS_FILE = os.path.join(DATA_DIR, "records.json")

DEFAULT_CONFIG = {
    "version": 1,
    "platforms": {
        "github": {
            "type": "github", "enabled": True,
            "url": "https://github.com", "owner": "",
            "token": "", "auth": "gh",
        },
        "gitlab": {
            "type": "gitlab", "enabled": False,
            "url": "https://gitlab.com", "owner": "",
            "token": "", "ns_id": "",
        },
        "local": {
            "type": "local", "enabled": False, "path": "",
        },
    },
    "routes": [
        {"name": "main", "source": "github",
         "targets": ["gitlab", "local"]},
    ],
    "settings": {
        "workers": 4,
        # a new setup must not delete anything on a destination until the
        # user asks for it (P0-7); a mesh must not silently overwrite the
        # older side of a disagreement either (P0-8)
        "mode": "safe",          # full = exact mirror (prunes), safe = never delete
        "mesh_conflict": "skip", # newest | skip
        "schedule": "hourly",    # hourly | 6h | 12h | daily | off
        "include_forks": False,
        "notify": {              # Discord webhook alerts
            "discord_webhook": "",
            "on": "failure",     # failure | success | always | never
        },
    },
}

SCHEDULES = {
    "hourly": {"cron": "0 * * * *", "calendar": "hourly",
               "label": "Every hour"},
    "6h":     {"cron": "0 */6 * * *", "calendar": "*:0/6",
               "label": "Every 6 hours"},
    "12h":    {"cron": "0 */12 * * *", "calendar": "*:0/12",
               "label": "Every 12 hours"},
    "daily":  {"cron": "0 3 * * *", "calendar": "*-*-* 03:00:00",
               "label": "Once daily at 03:00"},
    "weekly": {"cron": "0 3 * * 0", "calendar": "Sun *-*-* 03:00:00",
               "label": "Once weekly, Sunday 03:00"},
    "off":    {"cron": None, "calendar": None, "label": "OFF (manual only)"},
}

# how often each schedule fires, in hours (for picking the finest one)
SCHEDULE_HOURS = {"hourly": 1, "6h": 6, "12h": 12, "daily": 24,
                  "weekly": 168, "off": None}



def route_schedule(cfg, route):
    """a route's own schedule, falling back to the global setting"""
    return route.get("schedule") or setting(cfg, "schedule", "hourly")


def finest_schedule(cfg):
    """the most frequent schedule any route wants (drives the systemd timer).

    Returns None when every route is off.
    """
    best = None
    best_h = None
    for r in routes(cfg):
        key = route_schedule(cfg, r)
        h = SCHEDULE_HOURS.get(key)
        if h is None:
            continue
        if best_h is None or h < best_h:
            best, best_h = key, h
    return best

def ensure_dirs():
    for d in (CONFIG_DIR, DATA_DIR, MIRROR_DIR):
        os.makedirs(d, exist_ok=True)


def load_config():
    ensure_dirs()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            # shallow-merge top level keys
            for k, v in saved.items():
                if k == "settings" and isinstance(v, dict):
                    cfg["settings"].update(v)
                else:
                    cfg[k] = v
        except (OSError, ValueError):
            backup = CONFIG_FILE + ".broken"
            try:
                os.replace(CONFIG_FILE, backup)
            except OSError:
                pass
    return cfg


def save_config(cfg):
    ensure_dirs()
    tmp = CONFIG_FILE + ".tmp"
    # the temp file holds every platform token, so create it 0600 from the
    # start — the process umask would otherwise leave it 0644 until the
    # chmod below, exposing secrets in that window (the web UI saves often)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.replace(tmp, CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


_save_lock = threading.Lock()


def update(fn):
    """Atomically read-modify-write the config.

    Holds a process lock (the web server is multi-threaded) *and* an flock on
    config.lock (so a web write and a TUI/CLI write in another process never
    clobber each other's in-memory copy). fn(cfg) mutates cfg in place and may
    return a value, which update() passes back.
    """
    try:
        import fcntl
    except ImportError:
        fcntl = None
    ensure_dirs()
    lock_path = os.path.join(DATA_DIR, "config.lock")
    with _save_lock:
        fh = open(lock_path, "a+")
        try:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX)
            cfg = load_config()
            result = fn(cfg)
            save_config(cfg)
            return result
        finally:
            if fcntl:
                try:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                fh.close()
            except OSError:
                pass


def platform(cfg, name):
    return cfg.get("platforms", {}).get(name)


def enabled_platforms(cfg):
    return {n: p for n, p in cfg.get("platforms", {}).items()
            if p.get("enabled")}


# ---------------------------------------------------------------- platforms

DEFAULT_URLS = {
    "github": "https://github.com",
    "gitlab": "https://gitlab.com",
    "bitbucket": "https://bitbucket.org",
    "forgejo": "http://localhost:3000",
    "gitea": "http://localhost:3000",
    "generic": "",
}

# colour themes: the four curses pairs, in order
# (accent / ok / warning / bad). "mono" is for terminals where colour is
# noise; the rest are just different accents on the same semantics.
THEMES = {
    "default": ["cyan", "green", "yellow", "red"],
    "mono": ["white", "white", "white", "white"],
    "ocean": ["blue", "cyan", "yellow", "red"],
    "forest": ["green", "green", "yellow", "red"],
    "sunset": ["magenta", "yellow", "green", "red"],
}

PLATFORM_TYPES = ["github", "gitlab", "forgejo", "gitea", "bitbucket",
                  "generic", "local"]


def platform_template(ptype, name, url=None, token=None, owner=None,
                      path=None, protocol=None, ssh_port=None):
    """a new platform config with the right defaults for its type.

    One source of truth for the CLI (`platforms add`) and the TUI's add/edit
    forms, so the two can never drift apart.
    """
    p = {"type": ptype, "enabled": True}
    if ptype == "local":
        p["path"] = os.path.expanduser(path or "~/git-mirrors")
        return p
    p["url"] = (url or "").strip() or DEFAULT_URLS.get(ptype,
                                                       "http://localhost:3000")
    if token:
        p["token"] = token
    if owner:
        p["owner"] = owner
    if ptype == "github" and not token:
        p["auth"] = "gh"              # fall back to the gh CLI
    if protocol:
        p["protocol"] = protocol
    if ssh_port:
        try:
            p["ssh_port"] = int(ssh_port)
        except (TypeError, ValueError):
            pass
    return p


def add_platform(cfg, name, p):
    """returns None on success, else an error string"""
    if not name:
        return "a platform needs a name"
    plats = cfg.setdefault("platforms", {})
    if name in plats:
        return "platform '%s' already exists (edit it instead)" % name
    plats[name] = p
    return None


def remove_platform(cfg, name, force=False):
    """returns None on success, else an error string"""
    plats = cfg.get("platforms") or {}
    if name not in plats:
        return "no such platform: %s" % name
    used = [r.get("name") for r in routes(cfg)
            if r.get("source") == name or name in (r.get("targets") or [])]
    if used and not force:
        return ("used by route(s): %s" % ", ".join(used))
    del plats[name]
    return None


def platform_used_by(cfg, name):
    """route names that reference this platform"""
    return [r.get("name") for r in routes(cfg)
            if r.get("source") == name or name in (r.get("targets") or [])]


def routes(cfg):
    return cfg.get("routes", [])


def setting(cfg, key, default=None):
    return cfg.get("settings", {}).get(key, default)


def set_setting(cfg, key, value):
    cfg.setdefault("settings", {})[key] = value
    save_config(cfg)
