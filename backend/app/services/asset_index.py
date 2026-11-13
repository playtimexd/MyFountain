"""Local 3D / animation asset index: finds .glb/.gltf/.fbx/.blend/.bvh/.dae files on this PC (e.g. the game projects on D:\\ and
Mixamo downloads), inspects them without Blender, merges duplicate copies, and stores one searchable row per unique file.
Files stay where they are (indexed in place); importing into the Animation workspace is on demand.

kind:  animation          clips, no mesh            (Mixamo "without skin", BVH)
       animated-character clips + skinned mesh      (Mixamo "with skin", game characters)
       rigged             skinned mesh, no clips
       model              static mesh
       scene              .blend (not inspected)
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import re
import sqlite3
import struct
import threading
import time
from pathlib import Path

from .. import config
from ..jobs import JobContext

DB_FILE = config.DATA / "models" / "asset_index.db"
DB_FILE.parent.mkdir(parents=True, exist_ok=True)
EXTS = {".glb", ".gltf", ".fbx", ".blend", ".bvh", ".dae"}
EXCLUDE = re.compile(r"[\\/](node_modules|\.git|\.venv|venv|site-packages|__pycache__|\$RECYCLE\.BIN|System Volume Information|"
                     r"worktrees|dist(-[^\\/]*)?|build|\.cache|Temp|PackageCache|Library[\\/]Artifacts)([\\/]|$)", re.I)
SKIP_PREFIX = [str(config.DATA).lower()]
_lock = threading.RLock()
_conn = sqlite3.connect(DB_FILE, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.executescript("""
CREATE TABLE IF NOT EXISTS assets(
  id TEXT PRIMARY KEY, path TEXT UNIQUE, name TEXT, ext TEXT, size INTEGER, mtime REAL, dupkey TEXT, project TEXT, kind TEXT,
  n_anims INTEGER DEFAULT 0, clips TEXT DEFAULT '[]', has_skin INTEGER DEFAULT 0, has_mesh INTEGER DEFAULT 0, tags TEXT DEFAULT '[]',
  copies INTEGER DEFAULT 1, other_paths TEXT DEFAULT '[]', info TEXT DEFAULT '{}', scanned REAL);
CREATE INDEX IF NOT EXISTS ix_assets_dup ON assets(dupkey);
CREATE INDEX IF NOT EXISTS ix_assets_kind ON assets(kind);
""")
_conn.commit()


# ---------------------------------------------------------------------------------------------- inspection
def _dupkey(p: Path, size: int) -> str:
    h = hashlib.sha1(str(size).encode())
    with p.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), 2)
            h.update(f.read(1 << 20))
    return h.hexdigest()


def _inspect_gltf_json(j: dict) -> dict:
    anims = [a.get("name") or f"anim_{i}" for i, a in enumerate(j.get("animations") or [])]
    meshes = j.get("meshes") or []
    verts = 0
    acc = j.get("accessors") or []
    for m in meshes:
        for pr in m.get("primitives") or []:
            i = (pr.get("attributes") or {}).get("POSITION")
            if isinstance(i, int) and i < len(acc):
                verts += int(acc[i].get("count") or 0)
    gen = ((j.get("asset") or {}).get("generator") or "")
    return {"clips": anims, "has_mesh": bool(meshes), "has_skin": bool(j.get("skins")), "vertices": verts, "generator": gen[:60],
            "joints": max((len(s.get("joints") or []) for s in j.get("skins") or []), default=0)}


def _inspect_glb(p: Path) -> dict:
    with p.open("rb") as f:
        head = f.read(20)
        if len(head) < 20 or head[:4] != b"glTF":
            return {"error": "not a GLB"}
        n = struct.unpack("<I", head[12:16])[0]
        if n > 64 << 20:
            return {"error": "JSON chunk too large"}
        return _inspect_gltf_json(json.loads(f.read(n)))


_STACK = re.compile(rb"([\x20-\x7e]{1,80})\x00\x01AnimStack")


def _inspect_fbx(p: Path) -> dict:
    with p.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        binary = mm[:20] == b"Kaydara FBX Binary  "
        if binary:
            stacks = [m.group(1).decode("latin-1") for m in _STACK.finditer(mm)]
            has_curves = mm.find(b"AnimationCurve") >= 0
            has_mesh = mm.find(b"\x00\x01Geometry") >= 0 or mm.find(b"Geometry::") >= 0
            has_skin = mm.find(b"\x00\x01Deformer") >= 0 and mm.find(b"Skin") >= 0
        else:
            txt = mm[: min(len(mm), 64 << 20)]
            stacks = [m.decode("latin-1") for m in re.findall(rb'AnimationStack: [^,]*, "AnimStack::([^"]{1,80})"', txt)]
            has_curves = b"AnimationCurve:" in txt
            has_mesh = b'Geometry: ' in txt
            has_skin = b'"Skin"' in txt or b"Deformer::" in txt
        mixamo = mm.find(b"mixamorig") >= 0 or mm.find(b"mixamo.com") >= 0
    clips = [s for s in stacks if s] if has_curves else []
    return {"clips": clips or (["Take 001"] if has_curves and not stacks else []), "has_mesh": has_mesh, "has_skin": has_skin,
            "mixamo": mixamo, "binary": binary}


def inspect(p: Path) -> dict:
    e = p.suffix.lower()
    try:
        if e == ".glb":
            return _inspect_glb(p)
        if e == ".gltf":
            return _inspect_gltf_json(json.loads(p.read_text("utf-8", errors="replace")))
        if e == ".fbx":
            return _inspect_fbx(p)
        if e == ".bvh":
            frames = 0
            with p.open("r", errors="replace") as f:
                for line in f:
                    if line.startswith("Frames:"):
                        frames = int(line.split(":")[1]); break
            return {"clips": [p.stem], "has_mesh": False, "has_skin": False, "frames": frames}
        if e == ".dae":
            t = p.read_bytes()[: 32 << 20]
            return {"clips": ["animation"] if b"<library_animations" in t else [], "has_mesh": b"<library_geometries" in t,
                    "has_skin": b"<skin " in t}
    except Exception as ex:  # noqa: BLE001
        return {"error": str(ex)[:200]}
    return {}


def kind_of(info: dict, ext: str) -> str:
    if ext == ".blend":
        return "scene"
    if info.get("error"):
        return "unknown"
    clips, mesh, skin = bool(info.get("clips")), bool(info.get("has_mesh")), bool(info.get("has_skin"))
    if clips and not mesh:
        return "animation"
    if clips and mesh:
        return "animated-character" if skin else "animated-model"
    if skin:
        return "rigged"
    return "model" if mesh else "unknown"


def _project(p: Path) -> str:
    parts = p.parts
    return str(Path(*parts[:2])) if len(parts) > 2 else str(p.parent)


# ---------------------------------------------------------------------------------------------- scan
def scan(ctx: JobContext, paths: list[str], max_files: int = 100000) -> dict:
    roots = [Path(x) for x in paths if Path(x).exists()]
    found: list[Path] = []
    ctx.progress(0.01, "Listing files")
    for r in roots:
        for dirpath, dirnames, filenames in os.walk(r):
            if EXCLUDE.search(dirpath + os.sep) or any(dirpath.lower().startswith(s) for s in SKIP_PREFIX):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if not EXCLUDE.search(os.sep + d + os.sep)]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in EXTS:
                    found.append(Path(dirpath) / fn)
            if len(found) > max_files:
                break
            ctx.check()
    stats = {"files": len(found), "new": 0, "updated": 0, "duplicates": 0, "unchanged": 0, "errors": 0, "kinds": {}}
    t0 = time.time()
    for i, p in enumerate(found):
        if i % 25 == 0:
            ctx.progress(0.05 + 0.93 * i / max(len(found), 1), f"{i}/{len(found)} {p.name}")
            ctx.check()
        try:
            st = p.stat()
            with _lock:
                row = _conn.execute("SELECT id,size,mtime FROM assets WHERE path=?", (str(p),)).fetchone()
            if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1:
                stats["unchanged"] += 1
                continue
            key = _dupkey(p, st.st_size)
            with _lock:
                dup = _conn.execute("SELECT id,path,other_paths FROM assets WHERE dupkey=? AND path<>?", (key, str(p))).fetchone()
            if dup and not row:
                others = json.loads(dup["other_paths"] or "[]")
                if str(p) not in others:
                    others = (others + [str(p)])[-50:]
                with _lock:
                    _conn.execute("UPDATE assets SET copies=copies+1, other_paths=? WHERE id=?", (json.dumps(others), dup["id"]))
                stats["duplicates"] += 1
                continue
            info = inspect(p)
            k = kind_of(info, p.suffix.lower())
            tags = []
            if info.get("mixamo"):
                tags.append("mixamo")
            if info.get("error"):
                stats["errors"] += 1
            aid = row["id"] if row else hashlib.sha1(str(p).lower().encode()).hexdigest()[:12]
            with _lock:
                _conn.execute("""INSERT INTO assets(id,path,name,ext,size,mtime,dupkey,project,kind,n_anims,clips,has_skin,has_mesh,tags,info,scanned)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                 ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, dupkey=excluded.dupkey, kind=excluded.kind,
                                 n_anims=excluded.n_anims, clips=excluded.clips, has_skin=excluded.has_skin, has_mesh=excluded.has_mesh,
                                 tags=excluded.tags, info=excluded.info, scanned=excluded.scanned""",
                              (aid, str(p), p.stem, p.suffix.lower(), st.st_size, st.st_mtime, key, _project(p), k, len(info.get("clips") or []),
                               json.dumps((info.get("clips") or [])[:200]), int(bool(info.get("has_skin"))), int(bool(info.get("has_mesh"))),
                               json.dumps(tags), json.dumps({x: v for x, v in info.items() if x not in ("clips",)}), time.time()))
            stats["updated" if row else "new"] += 1
            stats["kinds"][k] = stats["kinds"].get(k, 0) + 1
            if i % 200 == 0:
                with _lock:
                    _conn.commit()
        except Exception:  # noqa: BLE001
            stats["errors"] += 1
    # rows whose files disappeared
    removed = 0
    with _lock:
        for r in _conn.execute("SELECT id,path FROM assets").fetchall():
            if any(str(r["path"]).lower().startswith(str(x).lower()) for x in roots) and not Path(r["path"]).exists():
                _conn.execute("DELETE FROM assets WHERE id=?", (r["id"],)); removed += 1
        _conn.commit()
    stats["removed"] = removed
    stats["seconds"] = round(time.time() - t0, 1)
    stats["total_indexed"] = count()
    return stats


# ---------------------------------------------------------------------------------------------- queries
def _pub(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("clips", "tags", "other_paths", "info"):
        try:
            d[k] = json.loads(d[k] or ("{}" if k == "info" else "[]"))
        except ValueError:
            d[k] = {} if k == "info" else []
    d["has_skin"], d["has_mesh"] = bool(d["has_skin"]), bool(d["has_mesh"])
    d["view_url"] = f"/api/models/local/{d['id']}/file" if d["ext"] in (".glb", ".gltf", ".fbx") else None
    d.pop("dupkey", None)
    return d


def count() -> int:
    with _lock:
        return _conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]


def query(kind: str | None = None, q: str | None = None, project: str | None = None, tag: str | None = None, ext: str | None = None,
          limit: int = 200, offset: int = 0) -> dict:
    sql, args = " FROM assets WHERE 1=1", []
    if kind:
        kinds = kind.split(",")
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"; args += kinds
    if project:
        sql += " AND project=?"; args.append(project)
    if ext:
        sql += " AND ext=?"; args.append(ext if ext.startswith(".") else "." + ext)
    if tag:
        sql += " AND tags LIKE ?"; args.append(f'%"{tag}"%')
    if q:
        for w in q.split():
            sql += " AND (name LIKE ? OR path LIKE ? OR clips LIKE ?)"; args += [f"%{w}%"] * 3
    with _lock:
        total = _conn.execute("SELECT COUNT(*)" + sql, args).fetchone()[0]
        rows = _conn.execute("SELECT *" + sql + " ORDER BY kind, name LIMIT ? OFFSET ?", [*args, max(1, min(limit, 1000)), max(0, offset)]).fetchall()
        kinds = dict(_conn.execute("SELECT kind, COUNT(*) FROM assets GROUP BY kind").fetchall())
        projects = _conn.execute("SELECT project, COUNT(*) n FROM assets GROUP BY project ORDER BY n DESC LIMIT 40").fetchall()
        mixamo = _conn.execute("SELECT COUNT(*) FROM assets WHERE tags LIKE '%\"mixamo\"%'").fetchone()[0]
    return {"total": total, "items": [_pub(r) for r in rows], "facets": {"kinds": kinds, "projects": [{"project": p, "count": n} for p, n in projects],
                                                                          "mixamo": mixamo}}


def get(aid: str) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    return _pub(r) if r else None
