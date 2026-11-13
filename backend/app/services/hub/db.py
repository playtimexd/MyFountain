"""SQLite store for hub assets (data/hub/hub.db): records, files, FTS5 keyword index, embeddings, facets."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

import numpy as np

from . import categories, licenses, paths

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_vec_cache: dict[str, Any] = {}

KINDS = ["model", "animation", "texture", "material", "hdri", "sfx", "music", "voice", "sprite", "image", "video", "font", "prefab"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, description TEXT DEFAULT '', tags TEXT DEFAULT '[]',
  source TEXT NOT NULL, source_id TEXT NOT NULL, source_url TEXT, author TEXT, author_url TEXT,
  license TEXT NOT NULL, license_url TEXT, license_class TEXT, attribution TEXT,
  commercial_ok INTEGER DEFAULT 0, modifiable INTEGER DEFAULT 0,
  stats TEXT DEFAULT '{}', thumb TEXT, media_id TEXT, formats TEXT DEFAULT '', rigged INTEGER DEFAULT 0, animated INTEGER DEFAULT 0,
  total_bytes INTEGER DEFAULT 0, embedding BLOB, clip BLOB, extra TEXT DEFAULT '{}', created REAL, updated REAL,
  UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_assets_source ON assets(source);
CREATE TABLE IF NOT EXISTS files (
  asset_id TEXT NOT NULL, role TEXT NOT NULL, path TEXT NOT NULL, format TEXT, bytes INTEGER, sha256 TEXT, resolution TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_asset ON files(asset_id);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);
CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(id UNINDEXED, name, description, tags, author, source);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            paths.HUB_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(paths.DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
            _conn.commit()
        return _conn


def _migrate(c: sqlite3.Connection) -> None:
    """Add the library category columns to databases created before they existed, then fill them."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(assets)")}
    for col in ("category", "collection"):
        if col not in cols:
            c.execute(f"ALTER TABLE assets ADD COLUMN {col} TEXT DEFAULT ''")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_collection ON assets(collection)")
    if c.execute("SELECT 1 FROM assets WHERE category IS NULL OR category='' LIMIT 1").fetchone():
        _recategorize(c, only_missing=True)


def _row_rec(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k, dflt in (("tags", "[]"), ("stats", "{}"), ("extra", "{}")):
        try:
            d[k] = json.loads(d.get(k) or dflt)
        except Exception:
            d[k] = json.loads(dflt)
    return d


def _category_for(rec: dict) -> str:
    manual = (rec.get("extra") or {}).get("category_manual")
    if isinstance(manual, str) and manual in categories.VALID:
        return manual
    return categories.categorize(rec)


def _recategorize(c: sqlite3.Connection, only_missing: bool = False) -> dict:
    where = "WHERE category IS NULL OR category=''" if only_missing else ""
    changed = 0
    rows = c.execute(f"SELECT id,kind,name,description,tags,source,stats,extra,category,collection FROM assets {where}").fetchall()
    for r in rows:
        rec = _row_rec(r)
        cat, col = _category_for(rec), categories.collection(rec)
        if cat != r["category"] or col != r["collection"]:
            c.execute("UPDATE assets SET category=?, collection=? WHERE id=?", (cat, col, r["id"]))
            changed += 1
    return {"scanned": len(rows), "changed": changed}


def recategorize(only_missing: bool = False) -> dict:
    with _lock:
        c = conn()
        res = _recategorize(c, only_missing)
        c.commit()
        return res


def set_category(aid: str, category: str | None) -> dict | None:
    """Pin a category by hand (kept by re-runs and re-imports); None/'' hands the asset back to the rules."""
    if category and category not in categories.VALID:
        raise ValueError(f"Unknown category '{category}'")
    with _lock:
        c = conn()
        r = c.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
        if not r:
            return None
        rec = _row_rec(r)
        extra = dict(rec.get("extra") or {})
        if category:
            extra["category_manual"] = category
        else:
            extra.pop("category_manual", None)
        rec["extra"] = extra
        c.execute("UPDATE assets SET extra=?, category=?, updated=? WHERE id=?",
                  (json.dumps(extra, ensure_ascii=False), _category_for(rec), time.time(), aid))
        c.commit()
    return get(aid)


def merge_extra(aid: str, patch: dict) -> None:
    """Merge keys into extra{} and re-run the category rules for that asset."""
    with _lock:
        c = conn()
        r = c.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
        if not r:
            return
        rec = _row_rec(r)
        rec["extra"] = {**(rec.get("extra") or {}), **patch}
        c.execute("UPDATE assets SET extra=?, category=?, collection=?, updated=? WHERE id=?",
                  (json.dumps(rec["extra"], ensure_ascii=False), _category_for(rec), categories.collection(rec), time.time(), aid))
        c.commit()


def clean_auto_tags(kinds: tuple[str, ...] = ("sfx", "music", "voice", "font", "sprite")) -> int:
    """CLIP zero-shot guesses on waveforms, font specimens and white icon glyphs are noise ('Elf ear' -> fish,
    spaceship): drop them from the searchable tags of those kinds (they stay in stats.auto_tags)."""
    n = 0
    with _lock:
        rows = conn().execute(f"SELECT id,tags,stats FROM assets WHERE kind IN ({','.join('?' * len(kinds))})", kinds).fetchall()
    for r in rows:
        tags, st = json.loads(r["tags"] or "[]"), json.loads(r["stats"] or "{}")
        auto = set(st.get("auto_tags") or [])
        keep = [t for t in tags if t not in auto]
        if len(keep) != len(tags):
            update_fields(r["id"], tags=keep)
            n += 1
    return n


def category_counts(f: dict | None = None) -> dict[str, int]:
    with _lock:
        w, a = _where(f or {}, skip="category")
        return {r[0]: r[1] for r in conn().execute(f"SELECT category, COUNT(*) FROM assets WHERE {w} GROUP BY category", a).fetchall() if r[0]}


def reset_for_tests(db_path=None) -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        if db_path is not None:
            paths.DB_PATH = db_path
        _vec_cache.clear()


def _blob(v) -> bytes | None:
    if v is None:
        return None
    return np.asarray(v, np.float32).tobytes()


def _public(r: sqlite3.Row | None, files: list[dict] | None = None, full: bool = False) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for c, dflt in (("tags", []), ("stats", {}), ("extra", {})):
        try:
            d[c] = json.loads(d[c] or json.dumps(dflt))
        except Exception:
            d[c] = dflt
    d["commercial_ok"], d["modifiable"] = bool(d["commercial_ok"]), bool(d["modifiable"])
    d["rigged"], d["animated"] = bool(d["rigged"]), bool(d["animated"])
    d["has_embedding"] = d.pop("embedding") is not None
    d["has_clip"] = d.pop("clip") is not None
    d["formats"] = [f for f in (d.get("formats") or "").split(",") if f]
    d["thumb_url"] = f"/api/hub/file/{d['id']}/thumb" if d.get("thumb") else None
    d["thumb"] = d["thumb_url"]
    if files is not None:
        d["files"] = [{**f, "url": f"/api/hub/file/{d['id']}/{f['role']}"} for f in files]
    if not full:
        d.pop("extra", None)
    return d


def _files(aid: str) -> list[dict]:
    rows = conn().execute("SELECT role,path,format,bytes,sha256,resolution FROM files WHERE asset_id=?", (aid,)).fetchall()
    return [dict(x) for x in rows]


def get(aid: str, full: bool = True) -> dict | None:
    with _lock:
        r = conn().execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
        return _public(r, _files(aid) if r else None, full=full)


def get_by_source(source: str, source_id: str) -> dict | None:
    with _lock:
        r = conn().execute("SELECT id FROM assets WHERE source=? AND source_id=?", (source, str(source_id))).fetchone()
    return get(r["id"]) if r else None


def find_by_sha(sha: str) -> dict | None:
    with _lock:
        r = conn().execute("SELECT asset_id FROM files WHERE sha256=? AND role='main' LIMIT 1", (sha,)).fetchone()
    return get(r["asset_id"]) if r else None


def file_path(aid: str, role: str) -> str | None:
    with _lock:
        if role == "thumb":
            r = conn().execute("SELECT thumb FROM assets WHERE id=?", (aid,)).fetchone()
            return r["thumb"] if r and r["thumb"] else None
        r = conn().execute("SELECT path FROM files WHERE asset_id=? AND role=? LIMIT 1", (aid, role)).fetchone()
        return r["path"] if r else None


def upsert(rec: dict) -> dict:
    """Insert or update by (source, source_id). `rec` follows the unified record shape; files replace existing ones."""
    lic = rec.get("license") or "custom:unknown"
    li = licenses.info(lic)
    now = time.time()
    files = rec.get("files") or []
    formats = sorted({(f.get("format") or "").lower() for f in files if f.get("role") in ("main", "lod") and f.get("format")})
    stats = rec.get("stats") or {}
    rigged = bool(stats.get("rigged"))
    animated = bool(stats.get("clips")) or rec.get("kind") == "animation"
    total = sum({str(f["path"]): int(f.get("bytes") or 0) for f in files}.values())   # roles may share one file
    with _lock:
        c = conn()
        old = c.execute("SELECT id, created, extra FROM assets WHERE source=? AND source_id=?", (rec["source"], str(rec["source_id"]))).fetchone()
        if old:
            pinned = (json.loads(old["extra"] or "{}") or {}).get("category_manual")
            if pinned and not (rec.get("extra") or {}).get("category_manual"):
                rec = {**rec, "extra": {**(rec.get("extra") or {}), "category_manual": pinned}}
        aid = old["id"] if old else (rec.get("id") or uuid.uuid4().hex[:12])
        vals = dict(
            id=aid, kind=rec["kind"], name=rec.get("name") or rec["source_id"], description=rec.get("description") or "",
            tags=json.dumps(sorted(set(rec.get("tags") or [])), ensure_ascii=False), source=rec["source"], source_id=str(rec["source_id"]),
            source_url=rec.get("source_url"), author=rec.get("author"), author_url=rec.get("author_url"),
            license=lic, license_url=rec.get("license_url") or li["url"], license_class=li["class"],
            attribution=rec.get("attribution") or "", commercial_ok=int(li["commercial_ok"]), modifiable=int(li["modifiable"]),
            stats=json.dumps(stats, ensure_ascii=False), thumb=rec.get("thumb"), media_id=rec.get("media_id"),
            formats=",".join(formats), rigged=int(rigged), animated=int(animated), total_bytes=total,
            embedding=_blob(rec.get("embedding")), clip=_blob(rec.get("clip")),
            extra=json.dumps(rec.get("extra") or {}, ensure_ascii=False),
            category=_category_for({**rec, "stats": stats}), collection=categories.collection(rec),
            created=old["created"] if old else now, updated=now)
        cols = ",".join(vals)
        c.execute(f"INSERT OR REPLACE INTO assets({cols}) VALUES({','.join('?' * len(vals))})", list(vals.values()))
        c.execute("DELETE FROM files WHERE asset_id=?", (aid,))
        for f in files:
            c.execute("INSERT INTO files(asset_id,role,path,format,bytes,sha256,resolution) VALUES(?,?,?,?,?,?,?)",
                      (aid, f["role"], str(f["path"]), f.get("format"), int(f.get("bytes") or 0), f.get("sha256"), f.get("resolution")))
        c.execute("DELETE FROM assets_fts WHERE id=?", (aid,))
        c.execute("INSERT INTO assets_fts(id,name,description,tags,author,source) VALUES(?,?,?,?,?,?)",
                  (aid, vals["name"], vals["description"], " ".join(json.loads(vals["tags"])), vals["author"] or "", vals["source"]))
        c.commit()
        _vec_cache.clear()
    return get(aid)  # type: ignore[return-value]


def update_fields(aid: str, **fields) -> None:
    allowed = {"tags", "embedding", "clip", "thumb", "media_id", "stats", "description", "name"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("tags", "stats"):
            v = json.dumps(sorted(set(v)) if k == "tags" else v, ensure_ascii=False)
        if k in ("embedding", "clip"):
            v = _blob(v)
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return
    with _lock:
        c = conn()
        c.execute(f"UPDATE assets SET {','.join(sets)}, updated=? WHERE id=?", [*args, time.time(), aid])
        if "tags" in fields or "description" in fields or "name" in fields or "stats" in fields:
            r = c.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
            c.execute("UPDATE assets SET category=? WHERE id=?", (_category_for(_row_rec(r)), aid))
            c.execute("DELETE FROM assets_fts WHERE id=?", (aid,))
            c.execute("INSERT INTO assets_fts(id,name,description,tags,author,source) VALUES(?,?,?,?,?,?)",
                      (aid, r["name"], r["description"], " ".join(json.loads(r["tags"])), r["author"] or "", r["source"]))
        c.commit()
        _vec_cache.clear()


def delete(aid: str) -> dict | None:
    rec = get(aid)
    if not rec:
        return None
    with _lock:
        c = conn()
        c.execute("DELETE FROM assets WHERE id=?", (aid,))
        c.execute("DELETE FROM files WHERE asset_id=?", (aid,))
        c.execute("DELETE FROM assets_fts WHERE id=?", (aid,))
        c.commit()
        _vec_cache.clear()
    return rec


# ------------------------------------------------------------------ search / facets
FACET_COLS = {"kind": "kind", "source": "source", "license": "license_class", "collection": "collection"}


def _where(f: dict, skip: str | None = None) -> tuple[str, list]:
    sql, args = ["1=1"], []
    for key, col in FACET_COLS.items():
        if key != skip and f.get(key):
            vals = [v for v in str(f[key]).split(",") if v]
            sql.append(f"{col} IN ({','.join('?' * len(vals))})")
            args += vals
    if skip != "category" and f.get("category"):
        cats = [v for v in str(f["category"]).split(",") if v and "%" not in v]
        if cats:
            sql.append("(" + " OR ".join("(category=? OR substr(category,1,?)=?)" for _ in cats) + ")")
            for v in cats:
                args += [v, len(v) + 1, v + "/"]
    if skip != "rigged" and f.get("rigged") in (True, "1", "true", 1):
        sql.append("rigged=1")
    if skip != "animated" and f.get("animated") in (True, "1", "true", 1):
        sql.append("animated=1")
    if skip != "format" and f.get("format"):
        fm = [v for v in str(f["format"]).lower().split(",") if v]
        sql.append("(" + " OR ".join("(','||formats||',') LIKE ?" for _ in fm) + ")")
        args += [f"%,{x},%" for x in fm]
    if f.get("commercial_only") in (True, "1", "true", 1):
        sql.append("commercial_ok=1")
    return " AND ".join(sql), args


def _fts_query(q: str) -> str:
    toks = [t for t in "".join(ch if ch.isalnum() else " " for ch in q.lower()).split() if t]
    return " OR ".join(f'"{t}"*' for t in toks)


def _vectors(col: str) -> tuple[list[str], np.ndarray | None]:
    key = f"vec:{col}"
    with _lock:
        if key not in _vec_cache:
            rows = conn().execute(f"SELECT id,{col} FROM assets WHERE {col} IS NOT NULL").fetchall()
            ids = [r["id"] for r in rows]
            if rows:
                dim = len(rows[0][col]) // 4
                arr = np.stack([np.frombuffer(r[col], np.float32) for r in rows if len(r[col]) // 4 == dim])
                ids = [r["id"] for r in rows if len(r[col]) // 4 == dim]
            else:
                arr = None
            _vec_cache[key] = (ids, arr)
        return _vec_cache[key]


def search(f: dict, page: int = 1, page_size: int = 48, text_vec=None, clip_vec=None) -> dict:
    """Hybrid search: FTS5 bm25 + text-embedding cosine + CLIP text->image cosine, fused with reciprocal rank
    fusion. Filters: kind, source, license (class), rigged, animated, format. Facet counts ignore their own filter."""
    page, page_size = max(1, int(page)), max(1, min(200, int(page_size)))
    where, args = _where(f)
    q = (f.get("q") or "").strip()
    with _lock:
        c = conn()
        allowed_ids = None
        if q:
            allowed_ids = {r["id"] for r in c.execute(f"SELECT id FROM assets WHERE {where}", args).fetchall()}
            scores: dict[str, float] = {}
            fq = _fts_query(q)
            if fq:
                try:
                    rows = c.execute("SELECT id FROM assets_fts WHERE assets_fts MATCH ? ORDER BY bm25(assets_fts, 0, 10, 2, 5, 1, 1) LIMIT 1000",
                                     (fq,)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                for rank, r in enumerate(rows):
                    scores[r["id"]] = scores.get(r["id"], 0) + 1.0 / (30 + rank)
            for col, vec, w, floor in (("embedding", text_vec, 0.7, 0.6), ("clip", clip_vec, 0.7, 0.25)):
                if vec is None:
                    continue
                ids, arr = _vectors(col)
                if arr is None or arr.shape[1] != len(vec):
                    continue
                sims = arr @ np.asarray(vec, np.float32)
                order = np.argsort(-sims)[:300]
                rank = 0
                for i in order:
                    if sims[i] < floor:
                        break
                    scores[ids[i]] = scores.get(ids[i], 0) + w / (30 + rank)
                    rank += 1
            ranked = [i for i, _ in sorted(scores.items(), key=lambda kv: -kv[1]) if i in allowed_ids]
            total = len(ranked)
            page_ids = ranked[(page - 1) * page_size: page * page_size]
            rows = c.execute(f"SELECT * FROM assets WHERE id IN ({','.join('?' * len(page_ids))})", page_ids).fetchall() if page_ids else []
            by = {r["id"]: r for r in rows}
            items = [_public(by[i]) for i in page_ids if i in by]
            for it, i in zip(items, page_ids):
                it["score"] = round(scores[i], 5)
        else:
            sort = {"name": "name COLLATE NOCASE", "size": "total_bytes DESC", "oldest": "created"}.get(f.get("sort") or "", "created DESC")
            total = c.execute(f"SELECT COUNT(*) FROM assets WHERE {where}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM assets WHERE {where} ORDER BY {sort} LIMIT ? OFFSET ?",
                             [*args, page_size, (page - 1) * page_size]).fetchall()
            items = [_public(r) for r in rows]
        facets = _facets(f, allowed_ids if q else None)
    return {"items": items, "total": total, "page": page, "page_size": page_size, "has_more": page * page_size < total,
            "facets": facets}


def _facets(f: dict, restrict: set | None) -> dict:
    c = conn()
    out: dict[str, Any] = {}
    for key, col in FACET_COLS.items():
        w, a = _where(f, skip=key)
        out[key] = {r[0]: r[1] for r in c.execute(f"SELECT {col}, COUNT(*) FROM assets WHERE {w} GROUP BY {col}", a).fetchall() if r[0]}
    w, a = _where(f, skip="format")
    fmt: dict[str, int] = {}
    for r in c.execute(f"SELECT formats FROM assets WHERE {w}", a).fetchall():
        for x in (r[0] or "").split(","):
            if x:
                fmt[x] = fmt.get(x, 0) + 1
    out["format"] = fmt
    w, a = _where(f, skip="category")
    out["category"] = {r[0]: r[1] for r in c.execute(f"SELECT category, COUNT(*) FROM assets WHERE {w} GROUP BY category", a).fetchall() if r[0]}
    w, a = _where(f)
    r = c.execute(f"SELECT SUM(rigged), SUM(animated) FROM assets WHERE {w}", a).fetchone()
    out["rigged"], out["animated"] = int(r[0] or 0), int(r[1] or 0)
    return out


def stats() -> dict:
    with _lock:
        c = conn()
        total = c.execute("SELECT COUNT(*), COALESCE(SUM(total_bytes),0) FROM assets").fetchone()
        by = lambda col: {r[0]: r[1] for r in c.execute(f"SELECT {col}, COUNT(*) FROM assets GROUP BY {col}").fetchall()}  # noqa: E731
        bytes_by_source = {r[0]: r[1] for r in c.execute("SELECT source, COALESCE(SUM(total_bytes),0) FROM assets GROUP BY source").fetchall()}
        emb = c.execute("SELECT SUM(embedding IS NOT NULL), SUM(clip IS NOT NULL), SUM(media_id IS NOT NULL), SUM(rigged), SUM(animated) FROM assets").fetchone()
        return {"total": total[0], "bytes": total[1], "by_kind": by("kind"), "by_source": by("source"),
                "by_license": by("license"), "by_license_class": by("license_class"), "bytes_by_source": bytes_by_source,
                "by_category": by("category"), "by_collection": by("collection"),
                "with_text_embedding": int(emb[0] or 0), "with_clip": int(emb[1] or 0), "in_media_library": int(emb[2] or 0),
                "rigged": int(emb[3] or 0), "animated": int(emb[4] or 0)}


def counts_by_source() -> dict[str, int]:
    with _lock:
        return {r[0]: r[1] for r in conn().execute("SELECT source, COUNT(*) FROM assets GROUP BY source").fetchall()}


def ids_without(col: str, limit: int = 1000) -> list[str]:
    with _lock:
        return [r[0] for r in conn().execute(f"SELECT id FROM assets WHERE {col} IS NULL LIMIT ?", (limit,)).fetchall()]
