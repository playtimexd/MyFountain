"""Text-to-speech / voice-over: providers (sapi, kokoro, stubs), voices, generate, batch, prepare (explicit model download)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..jobs import jobs
from ..services import tts, tts_kokoro

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/providers")
def providers():
    return tts.providers()


@router.get("/voices")
def voices(provider: str | None = None):
    return tts.voices(provider)


class GenReq(BaseModel):
    text: str
    voice: str | None = None            # 'sapi:Microsoft Huihui Desktop' | 'kokoro:zf_001' | bare name
    speed: float = Field(1.0, ge=0.5, le=2.0)
    lang: str | None = None             # 'zh' | 'en' (auto-detected from text when omitted)
    provider: str | None = "auto"


def _check(provider: str | None):
    if provider and provider not in ("auto", "sapi", "kokoro", "cosyvoice", "f5tts"):
        raise HTTPException(400, f"unknown provider {provider}")


@router.post("/generate")
def generate(req: GenReq):
    if not req.text.strip():
        raise HTTPException(400, "text must not be empty")
    _check(req.provider)
    d = req.model_dump()
    return jobs.submit("tts", f"Text to speech: {req.text.strip()[:16]}", lambda ctx: tts.generate(ctx, d)).public()


class Item(BaseModel):
    id: str | int | None = None
    text: str
    maxDuration: float | None = None    # optional: speed the line up (max 1.6x) so it fits a subtitle slot


class BatchReq(BaseModel):
    items: list[Item]
    voice: str | None = None
    speed: float = Field(1.0, ge=0.5, le=2.0)
    lang: str | None = None
    provider: str | None = "auto"


@router.post("/batch")
def batch(req: BatchReq):
    if not any(i.text.strip() for i in req.items):
        raise HTTPException(400, "items must not be empty")
    if len(req.items) > 500:
        raise HTTPException(400, "At most 500 items per batch")
    _check(req.provider)
    d = req.model_dump()
    return jobs.submit("tts-batch", f"Batch voice-over: {len(req.items)} item(s)", lambda ctx: tts.batch(ctx, d)).public()


class PrepReq(BaseModel):
    pack: str = "zh"                    # 'zh' (Mandarin + a few English voices) | 'en' (English/British, 8 languages via espeak)
    variant: str = "int8"               # int8 ~114MB | fp16 ~164MB | fp32 ~326MB (model only; voices +54MB zh / +28MB en)


@router.post("/prepare")
def prepare(req: PrepReq | None = None):
    """Downloads the Kokoro weights. Only ever runs when called explicitly."""
    req = req or PrepReq()
    st = tts_kokoro.status()
    if not st["package"]:
        raise HTTPException(400, st["message"])
    return jobs.submit("tts-prepare", f"Download Kokoro model ({req.pack}/{req.variant})",
                       lambda ctx: tts_kokoro.prepare(ctx, req.pack, req.variant)).public()
