"""Filesystem locations + API key storage for the hub (keys only ever leave this module as booleans)."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from ... import config

HUB_DIR = config.DATA / "hub"
FILES_DIR = HUB_DIR / "files"
CACHE_DIR = HUB_DIR / "cache"
HARVEST_DIR = HUB_DIR / "harvest"
TMP_DIR = HUB_DIR / "tmp"
DB_PATH = HUB_DIR / "hub.db"
KEYS_PATH = HUB_DIR / "keys.json"

for _d in (HUB_DIR, FILES_DIR, CACHE_DIR, HARVEST_DIR, TMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

KEY_NAMES = ("freesound", "polypizza", "sketchfab", "google_fonts")
_lock = threading.Lock()


def _read() -> dict:
    if KEYS_PATH.exists():
        try:
            return json.loads(KEYS_PATH.read_text("utf-8"))
        except Exception:
            return {}
    return {}


def get_key(name: str) -> str:
    with _lock:
        return str((_read().get("hub_keys") or {}).get(name) or "").strip()


def key_status() -> dict[str, bool]:
    with _lock:
        k = _read().get("hub_keys") or {}
    return {n: bool(str(k.get(n) or "").strip()) for n in KEY_NAMES}


def set_keys(patch: dict) -> dict[str, bool]:
    """`patch` values: a string sets the key, '' or None clears it; unknown names are ignored."""
    with _lock:
        data = _read()
        keys = dict(data.get("hub_keys") or {})
        for n, v in patch.items():
            if n not in KEY_NAMES:
                continue
            v = (v or "").strip() if isinstance(v, str) or v is None else ""
            if v:
                keys[n] = v
            else:
                keys.pop(n, None)
        data["hub_keys"] = keys
        KEYS_PATH.write_text(json.dumps(data, indent=2), "utf-8")
    return key_status()


def all_secret_values() -> list[str]:
    with _lock:
        k = _read().get("hub_keys") or {}
    return [str(v) for v in k.values() if v and len(str(v)) >= 4]


def redact(text: str) -> str:
    """Remove every stored key (and token-looking query params) from a message."""
    s = str(text)
    for v in all_secret_values():
        s = s.replace(v, "***")
    return re.sub(r"(?i)([?&](?:key|token|api_key|access_token)=)[^&\s'\"]+", r"\1***", s)


def safe_name(s: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^\w.\-]+", "_", str(s)).strip("._") or "item"
    return s[:maxlen]


def asset_dir(source: str, source_id: str) -> Path:
    d = FILES_DIR / safe_name(source) / safe_name(source_id, 120)
    d.mkdir(parents=True, exist_ok=True)
    return d
