"""Search / RAG over local files."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..jobs import jobs
from ..services import rag

router = APIRouter(prefix="/api/search", tags=["search"])


class IndexReq(BaseModel):
    paths: list[str]
    recursive: bool = True
    kinds: list[str] | None = None
    asr: bool | None = None          # None = auto (on when a Whisper model is cached)
    caption: bool | None = None      # None = auto (on only if a small (<9GB) Ollama vision model is installed); True = use any vision model (slow)
    caption_frames: int = 1          # 1-3 keyframes captioned per video
    force: bool = False


@router.post("/index")
def index(req: IndexReq):
    paths = [p for p in req.paths if p.strip()]
    if not paths:
        raise HTTPException(400, "paths must not be empty")
    bad = [p for p in paths if not Path(p).exists()]
    if bad:
        raise HTTPException(400, f"Path does not exist: {bad[0]}")
    return jobs.submit("index", f"Indexing {len(paths)} location(s)",
                       lambda ctx: rag.index_paths(ctx, paths, req.recursive, req.kinds, req.asr, req.caption,
                                                   req.caption_frames, req.force)).public()


@router.get("/status")
def status():
    return rag.status()


class QueryReq(BaseModel):
    q: str
    kinds: list[str] | None = None
    limit: int = 20


@router.post("/query")
def query(req: QueryReq):
    return rag.query(req.q, req.kinds, max(1, min(100, req.limit)))
