"""Local-file semantic search ("RAG over my footage").
Index lives in data/index/index.db (sqlite: files, docs, FTS5) with float32 embedding blobs loaded into numpy at query time.
Signals per file: filename/folder text, ASR transcript windows (faster-whisper, when the model is cached), vision-LLM captions of
1-3 keyframes (Ollama vision model, when installed), open_clip frame embeddings (when importable + weights cached),
Ollama text embeddings over every text doc. Query = BM25 + text-cosine + CLIP-cosine fused by reciprocal rank fusion."""
from __future__ import annotations

import base64
import importlib.util
import json
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import httpx
import numpy as np

from .. import config, db
from ..ffutil import VIDEO_EXT, AUDIO_EXT, IMAGE_EXT, _NOWIN, kind_of, probe
from ..ingest import ingest_file
from ..jobs import JobContext
from . import asr

DB_FILE = config.INDEX_DIR / "index.db"
INDEX_VERSION = 2
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_cache: dict = {}          # numpy matrices, invalidated on write


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript("""
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, kind TEXT, ver INTEGER, opts TEXT, indexed REAL, root TEXT);
CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, kind TEXT, source TEXT, start REAL, end REAL,
                                text TEXT, tok TEXT, emb BLOB, clip BLOB);
CREATE INDEX IF NOT EXISTS idx_docs_path ON docs(path);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(tok, content='docs', content_rowid='id');
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
""")
        _conn.commit()
    return _conn


# ------------------------------------------------------------------ text helpers ---------------------------
_CJK = re.compile(r"([㐀-鿿豈-﫿぀-ヿ가-힯])")


def tok(text: str) -> str:
    """Whitespace-tokenised text for FTS5: every CJK char becomes its own token; latin words stay whole; '_' '-' split."""
    t = _CJK.sub(r" \1 ", (text or "").lower())
    t = re.sub(r"[_\-\.\\/]+", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def fts_query(q: str) -> str:
    q = q.strip()
    parts = []
    for m in re.finditer(r"([㐀-鿿豈-﫿぀-ヿ가-힯]+)|([A-Za-z0-9]+)", q):
        if m.group(1):
            chars = list(m.group(1))
            parts.append('"' + " ".join(chars) + '"' if len(chars) > 1 else chars[0])
            # also individual chars for partial matches (lower weight in bm25 by sheer rarity)
            if len(chars) > 1:
                parts += chars[:6]
        else:
            parts.append('"' + m.group(2).lower() + '"')
    return " OR ".join(dict.fromkeys(parts))


# ------------------------------------------------------------------ capability probes ---------------------
def ollama_models() -> list[dict]:
    s = config.load_settings()
    try:
        r = httpx.get(s["ollama_url"] + "/api/tags", timeout=3)
        return r.json().get("models", [])
    except Exception:
        return []


def embed_available() -> bool:
    s = config.load_settings()
    return any(m["name"].split(":")[0] == s["embed_model"].split(":")[0] for m in ollama_models())


_VISION_HINT = ("qwen2.5vl", "qwen2-vl", "qwen3-vl", "qwen3.8", "llava", "gemma3", "minicpm-v", "bakllava", "llama3.2-vision", "moondream", "granite3.2-vision", "mistral-small3")


def vision_model(max_gb: float | None = None) -> str | None:
    """Pick an installed Ollama model that can look at images (prefers the smallest; `max_gb` caps its size)."""
    cands = []
    for m in ollama_models():
        caps = m.get("capabilities") or []
        name = m["name"]
        if "embedding" in caps and "completion" not in caps:
            continue
        if "vision" in caps or any(h in name.lower() for h in _VISION_HINT):
            if max_gb is None or m.get("size", 0) <= max_gb * 1e9:
                cands.append((m.get("size", 0), name))
    if not cands:
        return None
    # /api/tags in some versions omits capabilities -> verify via /api/show for hint-only matches
    cands.sort()
    return cands[0][1]


CLIP_REPOS = [("ViT-B-32", "laion2b_s34b_b79k", "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"),
              ("ViT-B-32", "openai", "timm/vit_base_patch32_clip_224.openai")]


def clip_status() -> dict:
    if importlib.util.find_spec("open_clip") is None:
        return {"package": False, "cached": False, "ready": False, "message": "open_clip_torch is not installed (uv pip install --no-deps open_clip_torch)"}
    try:
        from huggingface_hub import snapshot_download
        for arch, pre, repo in CLIP_REPOS:
            try:
                p = snapshot_download(repo, cache_dir=None, local_files_only=True)
                if not any((Path(p) / n).exists() for n in ("open_clip_pytorch_model.bin", "open_clip_model.safetensors")):
                    continue
                return {"package": True, "cached": True, "ready": True, "arch": arch, "pretrained": pre, "repo": repo, "path": p, "message": "Ready"}
            except Exception:
                continue
    except Exception:
        pass
    return {"package": True, "cached": False, "ready": False, "message": "open_clip is installed but the model weights are not cached (laion/CLIP-ViT-B-32-laion2B-s34B-b79K, ~600 MB)"}


_CLIP = {}


def _clip():
    if "m" in _CLIP:
        return _CLIP["m"]
    st = clip_status()
    if not st["ready"]:
        return None
    import open_clip
    import torch
    snap = Path(st["path"])
    weights = next((snap / n for n in ("open_clip_pytorch_model.bin", "open_clip_model.safetensors") if (snap / n).exists()), None)
    if weights is None:
        return None
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        model, _, pre = open_clip.create_model_and_transforms(st["arch"], pretrained=str(weights), device=dev)   # local file: never downloads
    except Exception:
        dev = "cpu"
        model, _, pre = open_clip.create_model_and_transforms(st["arch"], pretrained=str(weights), device=dev)
    model.eval()
    tk = open_clip.get_tokenizer(st["arch"])
    _CLIP["m"] = (model, pre, tk, dev)
    return _CLIP["m"]


def clip_embed_images(jpgs: list[bytes]) -> np.ndarray | None:
    c = _clip()
    if c is None:
        return None
    import io
    import torch
    from PIL import Image
    model, pre, _, dev = c
    out = []
    with torch.no_grad():
        for b in jpgs:
            im = pre(Image.open(io.BytesIO(b)).convert("RGB")).unsqueeze(0).to(dev)
            f = model.encode_image(im).float()
            out.append(torch.nn.functional.normalize(f, dim=-1).cpu().numpy()[0])
    return np.array(out, np.float32)


def clip_embed_text(q: str) -> np.ndarray | None:
    c = _clip()
    if c is None:
        return None
    import torch
    model, _, tk, dev = c
    with torch.no_grad():
        f = model.encode_text(tk([q]).to(dev)).float()
        return torch.nn.functional.normalize(f, dim=-1).cpu().numpy()[0]


def text_embed(texts: list[str], prefix: str) -> np.ndarray | None:
    s = config.load_settings()
    if not texts:
        return np.zeros((0, 1), np.float32)
    try:
        r = httpx.post(s["ollama_url"] + "/api/embed", json={"model": s["embed_model"], "input": [prefix + t[:1500] for t in texts]}, timeout=120)
        r.raise_for_status()
        a = np.array(r.json()["embeddings"], np.float32)
        a /= np.linalg.norm(a, axis=1, keepdims=True) + 1e-9
        return a
    except Exception:
        return None


def caption_image(jpg: bytes, model: str) -> str | None:
    s = config.load_settings()
    try:
        r = httpx.post(s["ollama_url"] + "/api/chat", timeout=240, json={
            "model": model, "stream": False, "think": False,
            "messages": [{"role": "user", "content": "Describe in one or two short sentences what is in this image / video frame (subject, scene, action, colors). Output only the description.",
                          "images": [base64.b64encode(jpg).decode()]}],
            "options": {"temperature": 0.2, "num_predict": 100}})
        r.raise_for_status()
        return (r.json()["message"]["content"] or "").strip() or None
    except Exception:
        return None


# ------------------------------------------------------------------ frames -------------------------------
def grab_frame(path: str, t: float, size: int = 512) -> bytes | None:
    p = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", path, "-frames:v", "1",
                        "-vf", f"scale='if(gt(iw,ih),{size},-2)':'if(gt(iw,ih),-2,{size})'", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
                       capture_output=True, **_NOWIN)
    return p.stdout or None


# ------------------------------------------------------------------ indexing -----------------------------
def _walk(paths: list[str], recursive: bool, kinds: set[str]) -> list[Path]:
    exts = set()
    if "video" in kinds: exts |= VIDEO_EXT
    if "audio" in kinds: exts |= AUDIO_EXT
    if "image" in kinds: exts |= IMAGE_EXT
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_file() and p.suffix.lower() in exts:
            out.append(p)
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            out += [f for f in it if f.is_file() and f.suffix.lower() in exts]
    return sorted(set(out))


def _delete_file_docs(c: sqlite3.Connection, path: str) -> None:
    rows = c.execute("SELECT id,tok FROM docs WHERE path=?", (path,)).fetchall()
    for r in rows:
        c.execute("INSERT INTO docs_fts(docs_fts,rowid,tok) VALUES('delete',?,?)", (r["id"], r["tok"]))
    c.execute("DELETE FROM docs WHERE path=?", (path,))


def _add_doc(c, path, kind, source, start, end, text, emb=None, clip=None) -> int:
    t = tok(text)
    cur = c.execute("INSERT INTO docs(path,kind,source,start,end,text,tok,emb,clip) VALUES(?,?,?,?,?,?,?,?,?)",
                    (path, kind, source, start, end, text, t, emb.tobytes() if emb is not None else None,
                     clip.tobytes() if clip is not None else None))
    c.execute("INSERT INTO docs_fts(rowid,tok) VALUES(?,?)", (cur.lastrowid, t))
    return cur.lastrowid  # type: ignore[return-value]


def _group_asr(segs: list[dict], win: float = 25.0) -> list[dict]:
    out, cur = [], None
    for s in segs:
        if cur and s["end"] - cur["start"] <= win:
            cur["end"] = s["end"]; cur["text"] += " " + s["text"]
        else:
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
            out.append(cur)
    return out


def index_paths(ctx: JobContext, paths: list[str], recursive: bool = True, kinds: list[str] | None = None,
                use_asr: bool | None = None, caption: bool | None = None, caption_frames: int = 1, force: bool = False) -> dict:
    kinds_set = set(kinds or ["video", "audio", "image"])
    files = _walk(paths, recursive, kinds_set)
    asr_ok = asr.status()["ready"] if use_asr is None else (use_asr and asr.status()["ready"])
    vmodel = None if caption is False else vision_model(max_gb=9 if caption is None else None)   # auto only picks small VLMs
    clip_ok = clip_status()["ready"]
    embed_ok = embed_available()
    caption_frames = max(1, min(3, caption_frames))
    opts = json.dumps({"asr": bool(asr_ok), "cap": vmodel or "", "clip": clip_ok, "embed": embed_ok, "cf": caption_frames}, sort_keys=True)
    c = conn()
    stats = {"files": len(files), "indexed": 0, "skipped": 0, "removed": 0, "errors": [], "docs": 0,
             "asr": bool(asr_ok), "captions": vmodel, "clip": clip_ok, "embed": embed_ok}
    caption_failures = 0
    for i, f in enumerate(files):
        ctx.progress(i / max(len(files), 1), f"{i + 1}/{len(files)} {f.name}")
        try:
            st = f.stat()
        except OSError:
            continue
        sp = str(f)
        with _lock:
            row = c.execute("SELECT * FROM files WHERE path=?", (sp,)).fetchone()
        if row and not force and row["mtime"] == st.st_mtime and row["size"] == st.st_size and row["ver"] == INDEX_VERSION:
            # unchanged; only skip if we would not gain new signals (e.g. ASR became available)
            old = json.loads(row["opts"] or "{}")
            gain = (asr_ok and not old.get("asr")) or (vmodel and not old.get("cap")) or (clip_ok and not old.get("clip")) or (embed_ok and not old.get("embed"))
            if not gain:
                stats["skipped"] += 1
                continue
        try:
            kind = kind_of(f) or "video"
            info = {"duration": 0.0, "has_audio": False}
            if kind != "image":
                try:
                    info = probe(f)
                except Exception:
                    pass
            docs: list[tuple[str, float | None, float | None, str, bytes | None]] = []   # (source,start,end,text,jpg)
            parents = " ".join(f.parent.parts[-3:])
            tags = ""
            m = db.find_by_path(f)
            if m:
                tags = " ".join(m.get("tags", []))
            docs.append(("meta", None, None, f"{f.stem} {parents} {tags}".strip(), None))
            if kind in ("video", "audio") and asr_ok and info.get("has_audio") and 0 < info["duration"] <= 3600:
                if ctx:
                    ctx.progress(i / len(files), f"Transcribing {f.name}")
                try:
                    res = asr.transcribe(f, ctx=None)
                    for g in _group_asr(res["segments"]):
                        if g["text"].strip():
                            docs.append(("asr", g["start"], g["end"], g["text"], None))
                except Exception as e:  # noqa: BLE001
                    stats["errors"].append(f"{f.name}: ASR {e}")
            # keyframes
            frames: list[tuple[float, bytes]] = []
            if kind == "image":
                jb = grab_frame(sp, 0)
                if jb: frames.append((0.0, jb))
            elif kind == "video":
                d = info["duration"] or 1.0
                n_cap = caption_frames if vmodel else 0
                n_fr = max(n_cap, 3 if clip_ok else 0)
                for k in range(n_fr):
                    t = d * ((k + 0.5) / n_fr) if n_fr > 1 else d * 0.3
                    jb = grab_frame(sp, t)
                    if jb: frames.append((t, jb))
            clip_vecs = None
            if clip_ok and frames:
                try:
                    clip_vecs = clip_embed_images([jb for _, jb in frames])
                except Exception as e:  # noqa: BLE001
                    stats["errors"].append(f"{f.name}: CLIP {e}")
            for k, (t, jb) in enumerate(frames):
                cap_txt = ""
                if vmodel and caption_failures < 3 and (kind == "image" or k < caption_frames or len(frames) <= caption_frames):
                    ctx.progress(i / len(files), f"Captioning {f.name}")
                    cap_txt = caption_image(jb, vmodel) or ""
                    if not cap_txt:
                        caption_failures += 1
                cv = clip_vecs[k] if clip_vecs is not None else None
                if cap_txt or cv is not None:
                    docs.append(("caption" if cap_txt else "frame", t if kind == "video" else None, t if kind == "video" else None, cap_txt, None))
                    docs[-1] = (docs[-1][0], docs[-1][1], docs[-1][2], docs[-1][3], cv.tobytes() if cv is not None else None)  # type: ignore[arg-type]
            texts = [d[3] for d in docs]
            vecs = text_embed([t or " " for t in texts], "search_document: ") if embed_ok and texts else None
            with _lock:
                _delete_file_docs(c, sp)
                for j, (source, s0, e0, text, clipb) in enumerate(docs):
                    cl = np.frombuffer(clipb, np.float32) if clipb else None
                    _add_doc(c, sp, kind, source, s0, e0, text, vecs[j] if (vecs is not None and text.strip()) else None, cl)
                c.execute("INSERT OR REPLACE INTO files(path,mtime,size,kind,ver,opts,indexed,root) VALUES(?,?,?,?,?,?,?,?)",
                          (sp, st.st_mtime, st.st_size, kind, INDEX_VERSION, opts, time.time(), ""))
                c.commit()
            stats["indexed"] += 1
            stats["docs"] += len(docs)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "JobCancelled":
                raise
            stats["errors"].append(f"{f.name}: {e}")
    # drop entries for files that vanished from the indexed roots
    roots = [str(Path(p).resolve()) for p in paths if Path(p).is_dir()]
    if roots:
        with _lock:
            for r in c.execute("SELECT path FROM files").fetchall():
                p = r["path"]
                if any(p.startswith(rt) for rt in roots) and not Path(p).exists():
                    _delete_file_docs(c, p)
                    c.execute("DELETE FROM files WHERE path=?", (p,))
                    stats["removed"] += 1
            c.commit()
    with _lock:
        cur_roots = json.loads((c.execute("SELECT v FROM meta WHERE k='roots'").fetchone() or {"v": "[]"})["v"])
        for p in paths:
            rp = str(Path(p).resolve()) if Path(p).exists() else p
            if rp not in cur_roots:
                cur_roots.append(rp)
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('roots',?)", (json.dumps(cur_roots, ensure_ascii=False),))
        stats["finished"] = time.time()
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last',?)", (json.dumps(stats, ensure_ascii=False),))
        c.commit()
    _cache.clear()
    return stats


def status() -> dict:
    c = conn()
    with _lock:
        n = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        nd = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        r = c.execute("SELECT v FROM meta WHERE k='roots'").fetchone()
        last = c.execute("SELECT v FROM meta WHERE k='last'").fetchone()
    vm = vision_model()
    return {"indexed": n, "docs": nd, "roots": json.loads(r["v"]) if r else [], "last": json.loads(last["v"]) if last else None,
            "capabilities": {"asr": asr.status(), "clip": clip_status(), "vision_model": vm, "vision_auto": vision_model(max_gb=9), "embed": embed_available()}}


# ------------------------------------------------------------------ query --------------------------------
def _matrix(name: str):
    if name in _cache:
        return _cache[name]
    c = conn()
    col = "emb" if name == "emb" else "clip"
    with _lock:
        rows = c.execute(f"SELECT id,{col} AS v FROM docs WHERE {col} IS NOT NULL").fetchall()
    if not rows:
        _cache[name] = (np.zeros(0, np.int64), np.zeros((0, 1), np.float32))
    else:
        ids = np.array([r["id"] for r in rows], np.int64)
        mat = np.stack([np.frombuffer(r["v"], np.float32) for r in rows])
        _cache[name] = (ids, mat)
    return _cache[name]


def _top_cos(name: str, qv: np.ndarray, k: int, min_sim: float) -> list[int]:
    ids, mat = _matrix(name)
    if ids.size == 0 or mat.shape[1] != qv.shape[0]:
        return []
    sims = mat @ qv
    order = np.argsort(-sims)[:k]
    return [int(ids[i]) for i in order if sims[i] >= min_sim]


def query(q: str, kinds: list[str] | None = None, limit: int = 20) -> dict:
    q = (q or "").strip()
    if not q:
        return {"hits": [], "signals": {}}
    c = conn()
    K = 60
    lists: dict[str, list[int]] = {}
    fq = fts_query(q)
    if fq:
        with _lock:
            try:
                rows = c.execute("SELECT rowid FROM docs_fts WHERE docs_fts MATCH ? ORDER BY bm25(docs_fts) LIMIT 80", (fq,)).fetchall()
                lists["bm25"] = [r[0] for r in rows]
            except sqlite3.OperationalError:
                lists["bm25"] = []
    qv = text_embed([q], "search_query: ")
    if qv is not None and qv.shape[0]:
        lists["embed"] = _top_cos("emb", qv[0], 80, 0.35)
    if clip_status()["ready"]:
        try:
            cv = clip_embed_text(q)
            if cv is not None:
                lists["clip"] = _top_cos("clip", cv, 60, 0.18)
        except Exception:
            pass
    scores: dict[int, float] = {}
    W = {"bm25": 1.0, "embed": 1.0, "clip": 0.8}
    for name, ids in lists.items():
        for rank, did in enumerate(ids):
            scores[did] = scores.get(did, 0.0) + W[name] / (K + rank + 1)
    if not scores:
        return {"hits": [], "signals": {k: len(v) for k, v in lists.items()}}
    top = sorted(scores, key=lambda d: -scores[d])[:200]
    with _lock:
        rows = c.execute(f"SELECT id,path,kind,source,start,end,text FROM docs WHERE id IN ({','.join('?' * len(top))})", top).fetchall()
    by_file: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: -scores[r["id"]]):
        if kinds and r["kind"] not in kinds:
            continue
        if not Path(r["path"]).exists():
            continue
        h = by_file.get(r["path"])
        sc = scores[r["id"]]
        if h is None:
            snippet = r["text"] if r["source"] != "frame" else "Visual match"
            if r["source"] == "meta":
                snippet = "File name: " + Path(r["path"]).name
            by_file[r["path"]] = {"path": r["path"], "kind": r["kind"], "score": sc, "snippet": (snippet or "")[:200], "source": r["source"],
                                  "start": r["start"], "end": r["end"]}
        else:
            h["score"] += 0.25 * sc      # more matching docs in the same file -> small boost
    hits = sorted(by_file.values(), key=lambda h: -h["score"])[:limit]
    mx = hits[0]["score"] if hits else 1.0
    for h in hits:
        h["score"] = round(h["score"] / mx, 4)
        try:
            row = db.find_by_path(h["path"]) or ingest_file(h["path"], origin="index", tags=["indexed"])
            h["mediaId"] = row["id"]
            h["media"] = row
        except Exception as e:  # noqa: BLE001
            h["error"] = str(e)
        if h["start"] is None:
            h.pop("start"); h.pop("end")
    return {"hits": hits, "signals": {k: len(v) for k, v in lists.items()}}


def search_library(q: str, limit: int = 8) -> list[dict]:
    """Search both the index and the media library (name/tags) - used by the agent tool."""
    out = []
    try:
        res = query(q, None, limit)
        for h in res["hits"]:
            out.append({"mediaId": h.get("mediaId"), "name": (h.get("media") or {}).get("name") or Path(h["path"]).stem, "kind": h["kind"],
                        "score": h["score"], "snippet": h["snippet"], **({"start": h["start"], "end": h["end"]} if "start" in h else {})})
    except Exception:
        pass
    seen = {o["mediaId"] for o in out}
    for m in db.list_media(q=q, limit=limit):
        if m["id"] not in seen:
            out.append({"mediaId": m["id"], "name": m["name"], "kind": m["kind"], "score": 0.3, "snippet": "Library name/tag match", "duration": m["duration"]})
    return out[:limit]
