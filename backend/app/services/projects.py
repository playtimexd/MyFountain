"""Project store: the saved work of each editor area (video timelines, image documents, animation workspaces).

Layout on disk (everything under <DATA>/projects):
    projects/<kind>/<id>/doc.json     the editor's own document, stored verbatim (the store never parses it)
    projects/<kind>/<id>/thumb.<ext>  optional preview image (jpg / png / webp)
    projects/_trash/<kind>-<id>-<ts>/ deleted projects are moved here, not erased
The index lives in the media database (data/cutlab.db, table `projects`).

The row is deliberately shaped like a future asset record: free-form `tags` and `meta` (facets such as
canvas size, fps, character, style), plus `parent_id` for lineage (a duplicate points at its original).
Activity: finished background jobs are recorded in `activity` so tool areas (Download, AI Tools, Library,
Animation) keep a history across engine restarts.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import DATA, DB_PATH

PROJECTS_DIR = DATA / "projects"
TRASH_DIR = PROJECTS_DIR / "_trash"
KINDS = ("video", "image", "anim", "model", "segment")
THUMB_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_DOC_BYTES = 1_000_000_000  # 1 GB: image documents embed their layers as PNG data URLs

for _k in KINDS:
    (PROJECTS_DIR / _k).mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.executescript(
    """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- video | image | anim | model | segment
  name TEXT NOT NULL,
  created REAL, updated REAL, opened REAL,
  rev INTEGER DEFAULT 0,           -- bumped on every document save
  size INTEGER DEFAULT 0,          -- bytes of doc.json
  thumb TEXT,                      -- file name inside the project folder
  tags TEXT DEFAULT '[]',
  meta TEXT DEFAULT '{}',          -- facets / summary (width, height, fps, duration, rig, view state ...)
  parent_id TEXT                   -- lineage: duplicated / imported from
);
CREATE INDEX IF NOT EXISTS idx_projects_kind ON projects(kind, opened);
CREATE TABLE IF NOT EXISTS activity (
  id TEXT PRIMARY KEY,             -- job id
  area TEXT NOT NULL,              -- download | ai | library | anim | video | other
  kind TEXT, title TEXT, status TEXT, error TEXT,
  result TEXT DEFAULT '{}',        -- trimmed: media rows + a few scalar fields
  created REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS idx_activity_area ON activity(area, finished);
"""
)
_conn.commit()


class NotFound(KeyError):
    pass


def _now() -> float:
    return time.time()


def _dir(kind: str, pid: str) -> Path:
    return PROJECTS_DIR / kind / pid


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown project kind {kind!r} (expected one of {', '.join(KINDS)})")
    return kind


def _check_id(pid: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{6,32}", pid or ""):
        raise NotFound(pid)
    return pid


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for c, dv in (("tags", []), ("meta", {})):
        try:
            d[c] = json.loads(d[c] or json.dumps(dv))
        except Exception:
            d[c] = dv
    v = d.get("updated") or 0
    d["thumb_url"] = (f"/api/projects/{d['id']}/thumb?v={int(v)}" if d.get("thumb")
                      else (d["meta"].get("thumb_url") if isinstance(d["meta"], dict) else None))
    return d


def get(pid: str) -> dict:
    _check_id(pid)
    with _lock:
        r = _row(_conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
    if not r:
        raise NotFound(pid)
    return r


def list_projects(kind: str | None = None, q: str | None = None, limit: int = 200, offset: int = 0, sort: str = "opened") -> list[dict]:
    sql, args = "SELECT * FROM projects WHERE 1=1", []
    if kind:
        sql += " AND kind=?"; args.append(_check_kind(kind))
    if q:
        sql += " AND (name LIKE ? OR tags LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    order = {"opened": "COALESCE(opened, updated) DESC", "updated": "updated DESC", "created": "created DESC", "name": "name COLLATE NOCASE ASC"}.get(sort, "COALESCE(opened, updated) DESC")
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    args += [max(1, min(limit, 1000)), max(0, offset)]
    with _lock:
        return [_row(r) for r in _conn.execute(sql, args).fetchall()]  # type: ignore[misc]


def _write_doc(kind: str, pid: str, data: bytes) -> int:
    d = _dir(kind, pid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "doc.json.tmp"
    tmp.write_bytes(data)
    tmp.replace(d / "doc.json")       # atomic: a crash mid-write never leaves a half document
    return len(data)


def create(kind: str, name: str, doc: Any = None, meta: dict | None = None, tags: list[str] | None = None, parent_id: str | None = None) -> dict:
    _check_kind(kind)
    pid = uuid.uuid4().hex[:12]
    now = _now()
    size = _write_doc(kind, pid, json.dumps(doc, ensure_ascii=False).encode("utf-8")) if doc is not None else 0
    with _lock:
        _conn.execute("INSERT INTO projects(id,kind,name,created,updated,opened,rev,size,thumb,tags,meta,parent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, kind, (name or "Untitled project").strip()[:200] or "Untitled project", now, now, now, 1 if doc is not None else 0, size, None,
                       json.dumps(tags or [], ensure_ascii=False), json.dumps(meta or {}, ensure_ascii=False), parent_id))
        _conn.commit()
    return get(pid)


def doc_path(pid: str) -> Path | None:
    p = get(pid)
    f = _dir(p["kind"], pid) / "doc.json"
    return f if f.is_file() else None


def save_doc(pid: str, data: bytes, name: str | None = None, meta: dict | None = None, base_rev: int | None = None) -> dict:
    """Store the document bytes verbatim. `meta` is merged into the existing meta (shallow).
    `base_rev` (optional) guards against a stale tab overwriting newer work: raises ValueError on mismatch."""
    p = get(pid)
    if len(data) > MAX_DOC_BYTES:
        raise ValueError(f"document too large ({len(data) / 1e6:.0f} MB)")
    head = data.lstrip()[:1]
    if head not in (b"{", b"["):
        raise ValueError("document must be JSON (object or array)")
    if base_rev is not None and base_rev != p["rev"]:
        raise ValueError(f"conflict: project was saved elsewhere (rev {p['rev']}, yours {base_rev})")
    size = _write_doc(p["kind"], pid, data)
    m = {**p["meta"], **(meta or {})}
    with _lock:
        _conn.execute("UPDATE projects SET size=?, rev=rev+1, updated=?, meta=?, name=COALESCE(?, name) WHERE id=?",
                      (size, _now(), json.dumps(m, ensure_ascii=False), (name.strip()[:200] or None) if name else None, pid))
        _conn.commit()
    return get(pid)


def patch(pid: str, name: str | None = None, tags: list[str] | None = None, meta: dict | None = None, replace_meta: bool = False) -> dict:
    p = get(pid)
    sets, args = [], []
    if name is not None and name.strip():
        sets.append("name=?"); args.append(name.strip()[:200])
    if tags is not None:
        sets.append("tags=?"); args.append(json.dumps([str(t)[:64] for t in tags][:100], ensure_ascii=False))
    if meta is not None:
        sets.append("meta=?"); args.append(json.dumps(meta if replace_meta else {**p["meta"], **meta}, ensure_ascii=False))
    if sets:
        sets.append("updated=?"); args.append(_now())
        with _lock:
            _conn.execute(f"UPDATE projects SET {','.join(sets)} WHERE id=?", [*args, pid])
            _conn.commit()
    return get(pid)


def touch(pid: str) -> dict:
    get(pid)
    with _lock:
        _conn.execute("UPDATE projects SET opened=? WHERE id=?", (_now(), pid))
        _conn.commit()
    return get(pid)


def save_thumb(pid: str, data: bytes, content_type: str) -> dict:
    p = get(pid)
    ext = THUMB_EXT.get((content_type or "").split(";")[0].strip().lower())
    if not ext:
        raise ValueError("thumbnail must be image/jpeg, image/png or image/webp")
    if len(data) > 8_000_000:
        raise ValueError("thumbnail too large")
    d = _dir(p["kind"], pid)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("thumb.*"):
        old.unlink(missing_ok=True)
    (d / f"thumb.{ext}").write_bytes(data)
    with _lock:
        _conn.execute("UPDATE projects SET thumb=?, updated=? WHERE id=?", (f"thumb.{ext}", _now(), pid))
        _conn.commit()
    return get(pid)


def thumb_path(pid: str) -> Path | None:
    p = get(pid)
    if not p.get("thumb"):
        return None
    f = _dir(p["kind"], pid) / p["thumb"]
    return f if f.is_file() else None


def duplicate(pid: str, name: str | None = None) -> dict:
    p = get(pid)
    src = _dir(p["kind"], pid)
    q = create(p["kind"], name or f"{p['name']} copy", meta=p["meta"], tags=p["tags"], parent_id=pid)
    dst = _dir(p["kind"], q["id"])
    dst.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        for f in src.iterdir():
            if f.is_file() and not f.name.endswith(".tmp"):
                shutil.copy2(f, dst / f.name)
    with _lock:
        _conn.execute("UPDATE projects SET size=?, rev=?, thumb=? WHERE id=?", (p["size"], max(1, p["rev"]), p["thumb"], q["id"]))
        _conn.commit()
    return get(q["id"])


def delete(pid: str, purge: bool = False) -> dict:
    """Remove from the index and move the folder to projects/_trash (recoverable by hand); purge=True erases it."""
    p = get(pid)
    src = _dir(p["kind"], pid)
    moved = None
    if src.is_dir() and purge:
        shutil.rmtree(src, ignore_errors=True)
    elif src.is_dir():
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        dst = TRASH_DIR / f"{p['kind']}-{pid}-{int(_now())}"
        (src / "project.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), "utf-8")  # keep the index row with the files
        shutil.move(str(src), str(dst))
        moved = str(dst)
    with _lock:
        _conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        _conn.commit()
    return {"ok": True, "id": pid, "trash": moved}


# ------------------------------------------------------------------------------------------------ activity
AREA_BY_KIND: list[tuple[str, str]] = [
    # (job kind prefix, area)
    ("download", "download"), ("stock", "download"),
    ("anim-", "anim"), ("gen3d", "anim"), ("mocap", "anim"),
    ("import", "library"), ("index", "library"), ("segment", "segment"), ("hub.", "library"),
    ("export", "video"),
    ("upscale", "ai"), ("watermark", "ai"), ("subtitles", "ai"), ("auto-cut", "ai"), ("asr", "ai"), ("music", "ai"),
    ("tts", "ai"), ("videogen", "ai"), ("agent", "ai"),
]


def area_for(kind: str) -> str:
    for prefix, area in AREA_BY_KIND:
        if kind == prefix or kind.startswith(prefix):
            return area
    return "other"


_MEDIA_KEYS = ("id", "kind", "name", "url", "thumb", "duration", "width", "height", "origin", "source_url")


def _trim_result(res: Any) -> dict:
    """Keep what a history list needs: media rows (id/name/thumb...) and short scalar fields."""
    media: list[dict] = []
    scalars: dict[str, Any] = {}

    def walk(v: Any, depth: int) -> None:
        if depth > 4 or len(media) >= 24:
            return
        if isinstance(v, dict):
            if "id" in v and "url" in v and "kind" in v and str(v.get("url", "")).startswith("/api/media/"):
                media.append({k: v.get(k) for k in _MEDIA_KEYS if k in v})
                return
            for k, x in v.items():
                if depth == 0 and isinstance(x, (str, int, float, bool)) and len(scalars) < 12 and (not isinstance(x, str) or len(x) < 300):
                    scalars[k] = x
                walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v[:50]:
                walk(x, depth + 1)

    walk(res, 0)
    return {"media": media, **({"info": scalars} if scalars else {})}


def record_job(job: Any) -> None:
    """Job listener (see jobs.JobManager.listeners): persist a finished job."""
    try:
        pub = job.public()
        if pub.get("status") not in ("done", "error", "cancelled"):
            return
        with _lock:
            _conn.execute("INSERT OR REPLACE INTO activity(id,area,kind,title,status,error,result,created,finished) VALUES(?,?,?,?,?,?,?,?,?)",
                          (pub["id"], area_for(pub.get("kind") or ""), pub.get("kind"), pub.get("title"), pub.get("status"),
                           (pub.get("error") or None) and str(pub["error"])[:2000], json.dumps(_trim_result(pub.get("result")), ensure_ascii=False, default=str),
                           pub.get("created"), pub.get("finished") or _now()))
            _conn.commit()
    except Exception:  # history must never break a job
        import traceback
        traceback.print_exc()


def list_activity(area: str | None = None, limit: int = 50, status: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM activity WHERE 1=1", []
    if area:
        areas = [a for a in area.split(",") if a]
        sql += f" AND area IN ({','.join('?' * len(areas))})"; args += areas
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY finished DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    with _lock:
        rows = _conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["result"] = json.loads(d["result"] or "{}")
        except Exception:
            d["result"] = {}
        out.append(d)
    return out


def delete_activity(aid: str | None = None, area: str | None = None) -> int:
    with _lock:
        if aid:
            cur = _conn.execute("DELETE FROM activity WHERE id=?", (aid,))
        elif area:
            cur = _conn.execute("DELETE FROM activity WHERE area=?", (area,))
        else:
            return 0
        _conn.commit()
        return cur.rowcount
