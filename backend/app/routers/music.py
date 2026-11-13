from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..jobs import jobs
from ..services import music_providers as mp

router = APIRouter(prefix="/api/music", tags=["music"])


class GenReq(BaseModel):
    prompt: str = ""
    duration: int = 30
    instrumental: bool = True
    lyrics: str | None = None
    bpm: int | None = None
    mood: str | None = None
    genre: str | None = None
    provider: str | None = "auto"
    seed: int | None = None


@router.get("/providers")
def providers():
    return mp.providers()


@router.post("/generate")
def generate(req: GenReq):
    if req.provider and req.provider not in ("auto", "acestep", "procedural", "musicgen"):
        raise HTTPException(400, f"unknown provider {req.provider}")
    d = req.model_dump()
    return jobs.submit("music", f"Generate music: {req.prompt[:20] or req.mood or 'BGM'}", lambda ctx: mp.generate(ctx, d)).public()
