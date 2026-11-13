"""Clip search: text embeddings through Ollama (settings `embed_model`) + keyword score; keyword-only when Ollama is unavailable."""
from __future__ import annotations

import re

import httpx
import numpy as np

from ... import config
from . import db


def embed(texts: list[str], kind: str = "document", timeout: float = 20.0) -> list[np.ndarray] | None:
    s = config.load_settings()
    prefix = "search_query: " if kind == "query" else "search_document: "
    try:
        r = httpx.post(f"{s['ollama_url']}/api/embed", json={"model": s["embed_model"], "input": [prefix + t for t in texts]}, timeout=timeout)
        if r.status_code >= 400:
            return None
        embs = r.json().get("embeddings")
        if not embs or len(embs) != len(texts):
            return None
        return [np.asarray(e, np.float32) for e in embs]
    except Exception:
        return None


def clip_text(c: dict, rig_name: str | None = None) -> str:
    parts = [c["name"]]
    if c.get("role"):
        parts.append(c["role"])
    if c.get("tags"):
        parts.append("tags: " + ", ".join(c["tags"]))
    if c.get("caption"):
        parts.append(c["caption"])
    if rig_name:
        parts.append(f"character: {rig_name}")
    return ". ".join(parts)


def _rig_name(c: dict) -> str | None:
    if not c.get("rig_id"):
        return None
    r = db.get_rig(c["rig_id"])
    return r["name"] if r else None


def ensure_embedding(cid: str) -> bool:
    c = db.get_clip(cid)
    if not c:
        return False
    e = embed([clip_text(c, _rig_name(c))])
    if e is None:
        return False
    db.update_clip(cid, embedding=e[0])
    return True


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 1]


def keyword_score(q: str, c: dict) -> float:
    qt = _tokens(q)
    if not qt:
        return 0.0
    name = " ".join(_tokens(c["name"]))
    tags = " ".join(_tokens(" ".join(c.get("tags", []))))
    cap = " ".join(_tokens(c.get("caption", "")))
    role = " ".join(_tokens(c.get("role", "")))
    sc = 0.0
    for t in qt:
        sc += 1.0 if t in name else 0.0
        sc += 1.0 if t in role else 0.0
        sc += 0.8 if t in tags else 0.0
        sc += 0.4 if t in cap else 0.0
    return min(1.0, sc / (len(qt) * 1.6))


def search(q: str, limit: int = 30, kind: str | None = None) -> dict:
    clips = db.list_clips(kind=kind, with_embedding=True)
    q = (q or "").strip()
    if not q:
        return {"mode": "list", "hits": [{"clip": _pub(c), "score": 0.0} for c in clips[:limit]]}
    qe = embed([q], "query", timeout=8)
    mode = "keyword"
    if qe is not None:
        for c in [c for c in clips if c.get("_embedding") is None][:40]:          # lazily embed what is missing
            if ensure_embedding(c["id"]):
                c["_embedding"] = np.asarray(db.get_clip(c["id"], True)["_embedding"])  # type: ignore[index]
        mode = "semantic+keyword"
    hits = []
    for c in clips:
        kw = keyword_score(q, c)
        sem = 0.0
        if qe is not None and c.get("_embedding") is not None:
            a, b = qe[0], c["_embedding"]
            sem = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        score = (0.55 * max(sem, 0) + 0.45 * kw) if qe is not None else kw
        if score > (0.18 if qe is not None else 0.01):
            hits.append({"clip": _pub(c), "score": round(score, 4), "keyword": round(kw, 3), "semantic": round(sem, 3)})
    hits.sort(key=lambda h: -h["score"])
    return {"mode": mode, "hits": hits[:limit]}


def _pub(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_")}
