"""Paths and settings. The local data folder comes from system.data_home()."""
import base64
import json
import os

from . import system

HOME = system.data_home()
SERVER_DIR = os.path.join(HOME, "server")
TMP_DIR = os.path.join(HOME, "tmp")
GROUP_CACHE = os.path.join(HOME, "group.cache")
LEGACY_GROUP_CACHE = os.path.join(HOME, "group.dpapi")  # v1 name (Windows only)
SETTINGS = os.path.join(HOME, "settings.json")
SESSION = os.path.join(HOME, "session.json")   # crash-recovery marker
LOG_FILE = os.path.join(HOME, "launcher.log")

DEFAULT_SETTINGS = {"ram_gb": 3, "autosave_min": 20, "eula": False}

# Files that make up "the world" in an archive (everything else is re-creatable).
WORLD_ITEMS = ["world", "server.properties", "ops.json", "whitelist.json",
               "banned-players.json", "banned-ips.json", "usercache.json"]

HEARTBEAT_SEC = 60
STALE_SEC = 10 * 60
CLAIM_SETTLE_SEC = 6


def ensure_dirs():
    for d in (HOME, SERVER_DIR, TMP_DIR):
        os.makedirs(d, exist_ok=True)


def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_settings():
    return {**DEFAULT_SETTINGS, **_read(SETTINGS, {})}


def save_settings(s):
    _write(SETTINGS, s)


def load_session():
    return _read(SESSION, None)


def save_session(s):
    if s is None:
        try:
            os.remove(SESSION)
        except OSError:
            pass
    else:
        _write(SESSION, s)


# ---- group (shared secrets) ----

def cached_group():
    for path in (GROUP_CACHE, LEGACY_GROUP_CACHE):
        try:
            with open(path, "rb") as f:
                group = json.loads(system.unprotect_local(f.read()))
        except Exception:  # missing, from another user/machine, or damaged
            continue
        if path == LEGACY_GROUP_CACHE:
            cache_group(group)
            os.remove(LEGACY_GROUP_CACHE)
        return group
    return None


def cache_group(group):
    with open(GROUP_CACHE, "wb") as f:
        f.write(system.protect_local(json.dumps(group).encode()))


def forget_group():
    try:
        os.remove(GROUP_CACHE)
    except OSError:
        pass


def world_key(group) -> bytes:
    return base64.b64decode(group["world_key"])
