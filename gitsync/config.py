"""Configuration management for gitsync.

Config lives at ~/.config/gitsync/config.json (override with GITSYNC_CONFIG).
Runtime data (mirror hub, logs, run records) lives at ~/.local/share/gitsync
(override with GITSYNC_DATA).
"""

import json
import os

APP = "gitsync"
VERSION = "1.0.0"

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
        "mode": "full",          # full = exact mirror (prunes), safe = never delete
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
    "off":    {"cron": None, "calendar": None, "label": "OFF (manual only)"},
}


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
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def platform(cfg, name):
    return cfg.get("platforms", {}).get(name)


def enabled_platforms(cfg):
    return {n: p for n, p in cfg.get("platforms", {}).items()
            if p.get("enabled")}


def routes(cfg):
    return cfg.get("routes", [])


def setting(cfg, key, default=None):
    return cfg.get("settings", {}).get(key, default)


def set_setting(cfg, key, value):
    cfg.setdefault("settings", {})[key] = value
    save_config(cfg)
