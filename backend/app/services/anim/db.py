"""Animation asset database: data/anim/anim.db  (rigs, clips, exports).  Schema follows docs/ANIMATION.md section 3 plus a few
extra columns (info/stats/role/parent) that the pipeline needs."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from ... import config

ANIM_DIR = config.DATA / "anim"
RIGS_DIR, CLIPS_DIR, PREVIEWS_DIR, EXPORTS_DIR = (ANIM_DIR / n for n in ("rigs", "clips", "previews", "exports"))
for _d in (RIGS_DIR, CLIPS_DIR, PREVIEWS_DIR, EXPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
DB_FILE = ANIM_DIR / "anim.db"

_lock = threading.RLock()
_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.executescript(
    """
CREATE TABLE IF NOT EXISTS rigs(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, file TEXT NOT NULL, kind TEXT DEFAULT 'unknown',
  bones_json TEXT DEFAULT '[]', bone_map_json TEXT DEFAULT '{}', thumb TEXT, created REAL,
  info_json TEXT DEFAULT '{}', source_path TEXT
);
CREATE TABLE IF NOT EXISTS clips(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, rig_id TEXT, skeleton TEXT, source_json TEXT DEFAULT '{}',
  fps REAL DEFAULT 30, frames INTEGER DEFAULT 0, duration REAL DEFAULT 0, loop INTEGER DEFAULT 0, root_motion INTEGER DEFAULT 0,
  tags_json TEXT DEFAULT '[]', caption TEXT DEFAULT '', files_json TEXT DEFAULT '{}', preview_media_id TEXT,
  embedding BLOB, created REAL,
  stats_json TEXT DEFAULT '{}', role TEXT DEFAULT '', parent_clip_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_clips_rig ON clips(rig_id);
CREATE TABLE IF NOT EXISTS exports(
  id TEXT PRIMARY KEY, rig_id TEXT, clip_ids_json TEXT DEFAULT '[]', format TEXT, file TEXT, sidecar TEXT, info_json TEXT DEFAULT '{}', created REAL
);
"""
)
_conn.commit()


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _l(s: str | None, default: Any) -> Any:
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------------------------------------ rigs
def _rig(r: sqlite3.Row | None, full: bool = False) -> dict | None:
    if r is None:
        return None
    info = _l(r["info_json"], {})
    d = {"id": r["id"], "name": r["name"], "file": r["file"], "kind": r["kind"], "created": r["created"],
         "bone_count": info.get("bone_count", len(_l(r["bones_json"], []))), "mesh_count": info.get("mesh_count", 0),
         "height": info.get("height", 0), "notes": info.get("notes", []),
         "thumb_url": f"/api/anim/rigs/{r['id']}/thumb" if r["thumb"] else None,
         "mapped": {k: len(v.get("map", {})) for k, v in _l(r["bone_map_json"], {}).items()}}
    if full:
        d["bones"] = _l(r["bones_json"], [])
        d["bone_maps"] = _l(r["bone_map_json"], {})
        d["info"] = {k: v for k, v in info.items() if k != "bones"}
        d["source_path"] = r["source_path"]
    return d


def add_rig(*, name: str, file: str, kind: str, bones: list, info: dict, thumb: str | None, source_path: str | None, rid: str | None = None) -> dict:
    rid = rid or new_id()
    with _lock:
        _conn.execute("INSERT INTO rigs(id,name,file,kind,bones_json,bone_map_json,thumb,created,info_json,source_path) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (rid, name, file, kind, _j(bones), "{}", thumb, time.time(), _j(info), source_path))
        _conn.commit()
    return get_rig(rid, True)  # type: ignore[return-value]


def get_rig(rid: str, full: bool = False) -> dict | None:
    with _lock:
        return _rig(_conn.execute("SELECT * FROM rigs WHERE id=?", (rid,)).fetchone(), full)


def get_rig_raw(rid: str) -> sqlite3.Row | None:
    with _lock:
        return _conn.execute("SELECT * FROM rigs WHERE id=?", (rid,)).fetchone()


def list_rigs() -> list[dict]:
    with _lock:
        return [_rig(r) for r in _conn.execute("SELECT * FROM rigs ORDER BY created DESC").fetchall()]  # type: ignore[misc]


def update_rig(rid: str, **f: Any) -> dict | None:
    allowed = {"name": "name", "kind": "kind", "thumb": "thumb", "bone_maps": "bone_map_json", "info": "info_json", "bones": "bones_json"}
    sets, args = [], []
    for k, v in f.items():
        if k in allowed:
            sets.append(f"{allowed[k]}=?")
            args.append(_j(v) if k in ("bone_maps", "info", "bones") else v)
    if sets:
        with _lock:
            _conn.execute(f"UPDATE rigs SET {','.join(sets)} WHERE id=?", [*args, rid])
            _conn.commit()
    return get_rig(rid, True)


def delete_rig(rid: str) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM rigs WHERE id=?", (rid,))
        _conn.commit()
        return cur.rowcount > 0


# ------------------------------------------------------------------------------------------------ clips
def _clip(r: sqlite3.Row | None, with_embedding: bool = False) -> dict | None:
    if r is None:
        return None
    files = _l(r["files_json"], {})
    stats = _l(r["stats_json"], {})
    d = {"id": r["id"], "name": r["name"], "rig_id": r["rig_id"], "skeleton": r["skeleton"], "source": _l(r["source_json"], {}),
         "fps": r["fps"], "frames": r["frames"], "duration": r["duration"], "loop": bool(r["loop"]), "root_motion": bool(r["root_motion"]),
         "tags": _l(r["tags_json"], []), "caption": r["caption"] or "", "role": r["role"] or "", "parent_clip_id": r["parent_clip_id"],
         "kind": "retargeted" if r["rig_id"] else "source", "preview_media_id": r["preview_media_id"], "created": r["created"],
         "stats": stats, "score": stats.get("score"),
         "files": {k: f"/api/anim/file/{r['id']}.{k}" for k in files if k in ("glb", "fbx", "webm", "mp4", "png", "blend") and files[k]},
         "has_bake": bool(files.get("bake")), "has_motion": bool(files.get("motion")), "embedded": r["embedding"] is not None}
    if with_embedding:
        d["_embedding"] = np.frombuffer(r["embedding"], np.float32) if r["embedding"] else None
    d["_files"] = files
    return d


def add_clip(*, name: str, skeleton: str, rig_id: str | None = None, source: dict | None = None, fps: float = 30, frames: int = 0,
             duration: float = 0, loop: bool = False, root_motion: bool = False, tags: list[str] | None = None, caption: str = "",
             files: dict | None = None, stats: dict | None = None, role: str = "", parent_clip_id: str | None = None, cid: str | None = None) -> dict:
    cid = cid or new_id()
    with _lock:
        _conn.execute("INSERT INTO clips(id,name,rig_id,skeleton,source_json,fps,frames,duration,loop,root_motion,tags_json,caption,files_json,created,stats_json,role,parent_clip_id)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, name, rig_id, skeleton, _j(source or {}), fps, frames, duration, int(loop), int(root_motion), _j(tags or []), caption,
                       _j(files or {}), time.time(), _j(stats or {}), role, parent_clip_id))
        _conn.commit()
    return get_clip(cid)  # type: ignore[return-value]


def get_clip(cid: str, with_embedding: bool = False) -> dict | None:
    with _lock:
        return _clip(_conn.execute("SELECT * FROM clips WHERE id=?", (cid,)).fetchone(), with_embedding)


def list_clips(rig_id: str | None = None, kind: str | None = None, q: str | None = None, with_embedding: bool = False) -> list[dict]:
    sql, args = "SELECT * FROM clips WHERE 1=1", []
    if rig_id:
        sql += " AND rig_id=?"
        args.append(rig_id)
    if kind == "retargeted":
        sql += " AND rig_id IS NOT NULL"
    elif kind == "source":
        sql += " AND rig_id IS NULL"
    if q:
        sql += " AND (name LIKE ? OR tags_json LIKE ? OR caption LIKE ?)"
        args += [f"%{q}%"] * 3
    sql += " ORDER BY created DESC"
    with _lock:
        return [_clip(r, with_embedding) for r in _conn.execute(sql, args).fetchall()]  # type: ignore[misc]


def update_clip(cid: str, **f: Any) -> dict | None:
    cols = {"name": "name", "tags": "tags_json", "caption": "caption", "loop": "loop", "root_motion": "root_motion", "files": "files_json",
            "preview_media_id": "preview_media_id", "stats": "stats_json", "role": "role", "source": "source_json", "frames": "frames",
            "duration": "duration", "fps": "fps"}
    sets, args = [], []
    for k, v in f.items():
        if k == "embedding":
            sets.append("embedding=?")
            args.append(None if v is None else np.asarray(v, np.float32).tobytes())
        elif k in cols:
            sets.append(f"{cols[k]}=?")
            args.append(_j(v) if k in ("tags", "files", "stats", "source") else (int(v) if k in ("loop", "root_motion") else v))
    if sets:
        with _lock:
            _conn.execute(f"UPDATE clips SET {','.join(sets)} WHERE id=?", [*args, cid])
            _conn.commit()
    return get_clip(cid)


def set_file(cid: str, key: str, path: str | None) -> None:
    c = get_clip(cid)
    if not c:
        return
    files = dict(c["_files"])
    if path is None:
        files.pop(key, None)
    else:
        files[key] = path
    update_clip(cid, files=files)


def delete_clip(cid: str) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM clips WHERE id=?", (cid,))
        _conn.commit()
        return cur.rowcount > 0


def clip_dir(cid: str) -> Path:
    d = CLIPS_DIR / cid
    d.mkdir(parents=True, exist_ok=True)
    return d


def rig_dir(rid: str) -> Path:
    d = RIGS_DIR / rid
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------------------------------------ exports
def add_export(*, rig_id: str | None, clip_ids: list[str], fmt: str, file: str, sidecar: str | None, info: dict) -> dict:
    eid = new_id()
    with _lock:
        _conn.execute("INSERT INTO exports(id,rig_id,clip_ids_json,format,file,sidecar,info_json,created) VALUES(?,?,?,?,?,?,?,?)",
                      (eid, rig_id, _j(clip_ids), fmt, file, sidecar, _j(info), time.time()))
        _conn.commit()
    return get_export(eid)  # type: ignore[return-value]


def get_export(eid: str) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "rig_id": r["rig_id"], "clip_ids": _l(r["clip_ids_json"], []), "format": r["format"], "file": r["file"],
            "sidecar": r["sidecar"], "info": _l(r["info_json"], {}), "created": r["created"],
            "url": f"/api/anim/file/{r['id']}.{r['format']}", "sidecar_url": f"/api/anim/file/{r['id']}.json" if r["sidecar"] else None}


def list_exports(rig_id: str | None = None) -> list[dict]:
    with _lock:
        rows = _conn.execute("SELECT id FROM exports" + (" WHERE rig_id=?" if rig_id else "") + " ORDER BY created DESC", [rig_id] if rig_id else []).fetchall()
    return [get_export(r["id"]) for r in rows]  # type: ignore[misc]
