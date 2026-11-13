"""Configuration management for gitsync.

Config lives at ~/.config/gitsync/config.json (override with GITSYNC_CONFIG).
Runtime data (mirror hub, logs, run records) lives at ~/.local/share/gitsync
(override with GITSYNC_DATA).
"""

import json
import os
import threading
import time

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


def log_file():
    """the run log, resolved at call time.

    LOG_FILE/RECORDS_FILE used to be module constants bound at import, so a
    caller (or a test) that pointed DATA_DIR at a temp dir still wrote runs
    into the real log and the real records.json — the live dashboard then
    showed a fake "last run". Every reader and writer resolves now.
    """
    return os.path.join(DATA_DIR, "gitsync.log")


def records_file():
    """the run history, resolved at call time (see log_file())."""
    return os.path.join(DATA_DIR, "records.json")

DEFAULT_MESH_CONFLICT = "skip"

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
        # refuse to start a sync when the mirror disk has less than this free
        # (0 disables); settings.hooks.pre/post run before/after a real run
        "min_free_gb": 0.5,
        "hooks": {"pre": "", "post": ""},
        # One or more channels; any that are filled in are used. Everything
        # here is standard library: urllib for HTTP, smtplib for mail.
        "notify": {
            "discord_webhook": "",
            "telegram_token": "",
            "telegram_chat_id": "",
            "ntfy_url": "",       # https://ntfy.sh/<private-topic>
            "ntfy_token": "",
            "webhook_url": "",    # receives {"title","text","fields",...}
            "smtp_host": "", "smtp_port": 587,
            "smtp_user": "", "smtp_password": "",
            "smtp_from": "", "smtp_to": "",
            "smtp_tls": True,
            "on": "failure",      # failure | success | always | never
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


DEFAULT_MODE = "safe"


def route_mode(cfg, route=None):
    """the push mode a route actually runs with.

    A route's own "mode" wins, otherwise the global setting. The fallback
    has to be the same everywhere: the sync engine used to default to
    "full" while the fresh-config template and restore both said "safe",
    so a config without settings["mode"] force-pushed and pruned.
    """
    return (route or {}).get("mode") or setting(cfg, "mode", DEFAULT_MODE)


def prune_targets(cfg, route=None):
    """targets of this route that may have whole repositories deleted.

    Opt-in per target, never global and never implied by "full": a full
    mirror can only express refs, so deleting a repository (with its
    issues, MRs, wiki and stars) is a separate, explicit choice.
    """
    want = (route or {}).get("prune_repos") or []
    if isinstance(want, str):
        want = [want]
    return [t for t in want if t in ((route or {}).get("targets") or [])]


# ------------------------------------------------------- export / import

# every platform key that holds a credential (never exported)
_PLATFORM_SECRETS = ("token", "password", "api_key", "app_password",
                     "webhook_secret", "ssh_passphrase")


def _looks_secret(key):
    k = str(key).lower()
    # discord_webhook is a credential: anyone holding it can post as us.
    # A generic webhook URL (Slack, Gotify, n8n) usually embeds its own token,
    # and an ntfy topic name is the only thing protecting the subscription.
    if k in ("webhook_url", "ntfy_url"):
        return True
    return k in _PLATFORM_SECRETS or k.endswith("_token") or \
        k.endswith("_secret") or k.endswith("_password") or \
        k.endswith("_webhook")


def strip_secrets(cfg):
    """(config without credentials, [names of what was removed]).

    A config export is meant to be pasted into an issue, committed to a dotfiles
    repo, or moved between machines. It keeps every platform, route and
    setting — the shape people actually want to copy — and leaves the secrets
    empty so they can be re-entered on the other side.
    """
    import copy
    out = copy.deepcopy(cfg)
    removed = []
    for name, plat in (out.get("platforms") or {}).items():
        if not isinstance(plat, dict):
            continue
        for key in list(plat):
            if _looks_secret(key) and plat.get(key):
                plat[key] = ""
                removed.append("%s.%s" % (name, key))
    st = out.get("settings") or {}
    notify = st.get("notify")
    if isinstance(notify, dict):
        for key in list(notify):
            if _looks_secret(key) and notify.get(key):
                notify[key] = ""
                removed.append("settings.notify.%s" % key)
    for key in ("web_token", "webhook_secret", "password_hash"):
        if st.get(key):
            st[key] = ""
            removed.append("settings.%s" % key)
    return out, sorted(removed)


def export_config(path=None):
    """write the redacted config (0600); returns (text, removed)."""
    cfg = load_config()
    clean, removed = strip_secrets(cfg)
    text = json.dumps(clean, indent=2, sort_keys=True) + "\n"
    if path:
        tmp = path + ".tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp, flags, 0o600)      # it can still name internal hosts
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    return text, removed


class ConfigImportError(ValueError):
    pass


def import_config(path, cfg=None, replace=False):
    """import a (redacted) config, keeping the secrets already on this box.

    Importing must never silently wipe credentials: a platform that already
    exists keeps its token, and a new platform is reported as needing one.
    The current config is backed up first, so this is reversible.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            incoming = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigImportError("cannot read %s: %s" % (path, e))
    if not isinstance(incoming, dict):
        raise ConfigImportError("not a gitsync config (expected an object)")
    if not isinstance(incoming.get("platforms"), dict):
        raise ConfigImportError("not a gitsync config (no platforms)")
    if not isinstance(incoming.get("routes", []), list):
        raise ConfigImportError("not a gitsync config (routes must be a list)")
    for route in incoming.get("routes") or []:
        if not isinstance(route, dict) or not route.get("name"):
            raise ConfigImportError("a route without a name")

    current = cfg if cfg is not None else load_config()
    merged = json.loads(json.dumps(incoming))
    kept, needs_token = [], []
    old_plats = current.get("platforms") or {}
    for name, plat in (merged.get("platforms") or {}).items():
        if not isinstance(plat, dict):
            raise ConfigImportError("platform %r is not an object" % name)
        old = old_plats.get(name)
        if replace:
            continue
        if isinstance(old, dict):
            for key in list(plat):
                if _looks_secret(key) and not plat.get(key) and old.get(key):
                    plat[key] = old[key]
                    kept.append("%s.%s" % (name, key))
        if plat.get("type") != "local" and not any(
                plat.get(k) for k in _PLATFORM_SECRETS):
            needs_token.append(name)
    notify = (merged.setdefault("settings", {}).setdefault("notify", {}))
    old_notify = (current.get("settings") or {}).get("notify") or {}
    # every notify key that holds a credential (a URL can embed one) is
    # carried over from the live config when the incoming file left it blank
    for key in list(old_notify):
        if (_looks_secret(key) and not notify.get(key)
                and old_notify.get(key) and not replace):
            notify[key] = old_notify[key]
            kept.append("settings.notify.%s" % key)

    if os.path.exists(CONFIG_FILE):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            with open(CONFIG_FILE, "rb") as src, \
                    open(CONFIG_FILE + ".bak-" + stamp, "wb") as dst:
                dst.write(src.read())
        except OSError:
            pass
    save_config(merged)
    return {"platforms": len(merged.get("platforms") or {}),
            "routes": len(merged.get("routes") or []),
            "kept_secrets": sorted(set(kept)),
            "needs_token": sorted(needs_token)}
