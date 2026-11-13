"""SQLite media library. `media` is the single source of truth for every asset the UI can use."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import DB_PATH

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.executescript(
    """
CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,            -- video | audio | image
  name TEXT NOT NULL,
  path TEXT NOT NULL,            -- absolute path on disk
  origin TEXT NOT NULL DEFAULT 'import',  -- import | download | music | export | ai | segment | upload
  source_url TEXT,
  duration REAL DEFAULT 0,
  width INTEGER DEFAULT 0,
  height INTEGER DEFAULT 0,
  fps REAL DEFAULT 0,
  size INTEGER DEFAULT 0,
  has_audio INTEGER DEFAULT 0,
  tags TEXT DEFAULT '[]',
  meta TEXT DEFAULT '{}',
  created REAL
);
CREATE INDEX IF NOT EXISTS idx_media_kind ON media(kind);
"""
)
_conn.commit()

_JSON_COLS = ("tags", "meta")


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for c in _JSON_COLS:
        try:
            d[c] = json.loads(d[c] or ("[]" if c == "tags" else "{}"))
        except Exception:
            d[c] = [] if c == "tags" else {}
    d["has_audio"] = bool(d["has_audio"])
    d["url"] = f"/api/media/{d['id']}/file"
    d["thumb"] = f"/api/media/{d['id']}/thumb"
    return d


def add_media(*, kind: str, name: str, path: str | Path, origin: str = "import",
              source_url: str | None = None, duration: float = 0, width: int = 0, height: int = 0,
              fps: float = 0, size: int = 0, has_audio: bool = False, tags: list[str] | None = None,
              meta: dict[str, Any] | None = None) -> dict:
    mid = uuid.uuid4().hex[:12]
    with _lock:
        _conn.execute(
            "INSERT INTO media(id,kind,name,path,origin,source_url,duration,width,height,fps,size,has_audio,tags,meta,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, kind, name, str(path), origin, source_url, duration, width, height, fps, size,
             int(has_audio), json.dumps(tags or [], ensure_ascii=False),
             json.dumps(meta or {}, ensure_ascii=False), time.time()),
        )
        _conn.commit()
    return get_media(mid)  # type: ignore[return-value]


def get_media(mid: str) -> dict | None:
    with _lock:
        return _row(_conn.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone())


def find_by_path(path: str | Path) -> dict | None:
    with _lock:
        return _row(_conn.execute("SELECT * FROM media WHERE path=?", (str(path),)).fetchone())


def list_media(kind: str | None = None, q: str | None = None, origin: str | None = None,
               tag: str | None = None, limit: int = 500, offset: int = 0) -> list[dict]:
    sql, args = "SELECT * FROM media WHERE 1=1", []
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if origin:
        sql += " AND origin=?"; args.append(origin)
    if q:
        sql += " AND (name LIKE ? OR tags LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    if tag:
        sql += " AND tags LIKE ?"; args.append(f'%"{tag}"%')
    sql += " ORDER BY created DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _lock:
        return [_row(r) for r in _conn.execute(sql, args).fetchall()]  # type: ignore[misc]


def update_media(mid: str, **fields: Any) -> dict | None:
    allowed = {"name", "tags", "meta", "duration", "width", "height", "fps", "size", "has_audio"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in _JSON_COLS:
            v = json.dumps(v, ensure_ascii=False)
        if k == "has_audio":
            v = int(bool(v))
        sets.append(f"{k}=?"); args.append(v)
    if sets:
        with _lock:
            _conn.execute(f"UPDATE media SET {','.join(sets)} WHERE id=?", [*args, mid])
            _conn.commit()
    return get_media(mid)


def delete_media(mid: str) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM media WHERE id=?", (mid,))
        _conn.commit()
        return cur.rowcount > 0
