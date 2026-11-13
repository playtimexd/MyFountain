"""Four-step animation flow: inputs -> generate -> preview + LLM edit -> export.  See app/services/anim/quick.py."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config
from .. import db as media_db
from ..jobs import jobs
from ..services.anim import db, quick

router = APIRouter(prefix="/api/anim/quick", tags=["anim-quick"])


class GenReq(BaseModel):
    mediaId: str
    rigId: str
    start: float | None = None
    end: float | None = None
    subject: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/generate")
def generate(req: GenReq):
    m = media_db.get_media(req.mediaId)
    if not m or m["kind"] != "video":
        raise HTTPException(400, "Pick a reference video.")
    if not db.get_rig(req.rigId):
        raise HTTPException(400, "Pick a 3D character.")
    dur = float(m.get("duration") or 0)
    if req.start is not None and req.end is not None and req.end - req.start < 0.5:
        raise HTTPException(400, "The video range must be at least 0.5 s.")
    if dur and req.start is not None and req.start >= dur - 0.2:
        raise HTTPException(400, f"The start ({req.start:.1f}s) is past the end of the video ({dur:.1f}s).")
    if dur and req.end is not None and req.end > dur:
        req.end = None                                  # "to the end"
    return jobs.submit("anim-quick", f"Generate animation: {m['name'][:40]}",
                       lambda ctx: quick.generate(ctx, req.mediaId, req.rigId, req.start, req.end, req.options, req.subject)).public()


def _from_clip(clip_id: str | None, source_clip_id: str | None, rig_id: str | None, options: dict, metrics: dict | None = None):
    """Accept just the retargeted clip's id: its source motion, character, settings and metrics are read from the clip.
    Explicit sourceClipId / rigId / options win over what the clip remembers."""
    if clip_id:
        c = db.get_clip(clip_id)
        if not c or not c.get("rig_id") or not c.get("parent_clip_id"):
            raise HTTPException(404, "animation not found (pass the id of a clip made on a character)")
        src = db.get_clip(source_clip_id or c["parent_clip_id"]) or {}
        so = dict((c.get("source") or {}).get("options") or {})
        fps = float(src.get("fps") or c.get("fps") or 30)
        remembered = {k: so[k] for k in ("in_place", "loop", "foot_lock", "ground_fix", "smooth", "scale", "speed") if k in so}
        for k_frames, k_s in (("start", "start_s"), ("end", "end_s")):
            if so.get(k_frames) is not None:
                remembered[k_s] = round(int(so[k_frames]) / fps, 4)
        source_clip_id = source_clip_id or c["parent_clip_id"]
        rig_id = rig_id or c["rig_id"]
        options = {**remembered, **(options or {})}
        metrics = metrics or c.get("stats")
    if not source_clip_id or not rig_id:
        raise HTTPException(400, "Pass clipId, or sourceClipId and rigId.")
    if not db.get_clip(source_clip_id) or not db.get_rig(rig_id):
        raise HTTPException(404, "clip or character not found")
    return source_clip_id, rig_id, options, metrics


class EditReq(BaseModel):
    instruction: str
    clipId: str | None = None                 # the animation to change (alternative to sourceClipId + rigId + options)
    sourceClipId: str | None = None
    rigId: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/edit")
def edit(req: EditReq):
    if not req.instruction.strip():
        raise HTTPException(400, "Describe the change.")
    src, rig, opts, met = _from_clip(req.clipId, req.sourceClipId, req.rigId, req.options, req.metrics)
    return jobs.submit("anim-quick", f"Edit animation: {req.instruction[:40]}",
                       lambda ctx: quick.edit(ctx, src, rig, req.instruction, opts, met, req.history)).public()


class RerunReq(BaseModel):
    sourceClipId: str
    rigId: str
    options: dict[str, Any]
    history: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/rerun")
def rerun(req: RerunReq):
    if not db.get_clip(req.sourceClipId) or not db.get_rig(req.rigId):
        raise HTTPException(404, "clip or character not found")
    return jobs.submit("anim-quick", "Re-apply animation settings", lambda ctx: quick.rerun(ctx, req.sourceClipId, req.rigId, req.options, req.history)).public()


@router.get("/llm")
def llm_status():
    s = config.load_settings()
    prov = s.get("llm_provider") or "ollama"
    return {"provider": prov, "model": s.get("anthropic_model") if prov == "anthropic" else s.get("ollama_model") or s.get("llm_model"),
            "editable": {k: d for k, (_t, d) in quick.EDITABLE.items()}}


class TextReq(BaseModel):
    prompt: str
    rigId: str
    duration: float = 4.0
    seed: int | None = None
    options: dict[str, Any] = Field(default_factory=dict)


@router.get("/text/status")
def text_status():
    from ..services.anim import kimodo
    return kimodo.status()


@router.post("/generate-text")
def generate_text(req: TextReq):
    from ..services.anim import kimodo
    st = kimodo.status()
    if not st["available"]:
        raise HTTPException(503, st["note"])
    r = db.get_rig(req.rigId)
    if not r:
        raise HTTPException(400, "Pick a 3D character.")
    if r.get("kind") == "quadruped":
        raise HTTPException(400, "Text-to-motion makes humanoid motion only; use a reference video for four-legged characters.")
    if not req.prompt.strip() or len(req.prompt) > 500:
        raise HTTPException(400, "Describe the motion in 1-500 characters.")
    if not (1.0 <= req.duration <= 10.0):
        raise HTTPException(400, "Duration must be 1-10 seconds.")
    return jobs.submit("anim-quick", f"Text to animation: {req.prompt[:40]}",
                       lambda ctx: quick.generate_text(ctx, req.prompt, req.rigId, req.duration, req.seed, req.options)).public()


class TransitionReq(BaseModel):
    clipA: str
    clipB: str | None = None
    rigId: str | None = None
    loop: bool = False
    distance: float | None = None
    turnDeg: float = 0.0
    options: dict[str, Any] = Field(default_factory=dict)


@router.get("/transition/status")
def transition_status():
    from ..services.anim import motionbricks
    return motionbricks.status()


@router.post("/transition")
def transition(req: TransitionReq):
    """MotionBricks: join two text-to-motion (SOMA) clips with a generated transition, or close one into a seamless loop."""
    from ..services.anim import motionbricks
    st = motionbricks.status()
    if not st["available"]:
        raise HTTPException(503, st["note"])
    if not req.clipB and not req.loop:
        raise HTTPException(400, "Pass clipB, or loop=true to close clipA into a loop.")
    if req.distance is not None and not (0.0 <= req.distance <= 6.0):
        raise HTTPException(400, "distance must be 0-6 m.")
    if not (-180.0 <= req.turnDeg <= 180.0):
        raise HTTPException(400, "turnDeg must be -180..180.")
    try:
        for cid in filter(None, (req.clipA, req.clipB)):
            quick.soma_source(cid)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    if req.rigId and not db.get_rig(req.rigId):
        raise HTTPException(400, "character not found")
    nm = lambda cid: re.sub(r"^(Kimodo|MotionBricks):\s*|A person |\s+on .*$", "", (db.get_clip(cid) or {}).get("name", cid))[:32]
    title = f"loop {nm(req.clipA)}" if req.loop else f"{nm(req.clipA)} -> {nm(req.clipB)}"
    return jobs.submit("anim-quick", f"MotionBricks: {title}",
                       lambda ctx: quick.transition(ctx, req.clipA, req.clipB, req.rigId, req.loop, req.distance, req.turnDeg,
                                                    req.options)).public()


class TuneReq(BaseModel):
    clipId: str | None = None
    sourceClipId: str | None = None
    rigId: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    target: float = 90.0
    max_tries: int = 10
    history: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/autotune")
def autotune(req: TuneReq):
    src, rig, opts, _ = _from_clip(req.clipId, req.sourceClipId, req.rigId, req.options)
    if not (50 <= req.target <= 100) or not (1 <= req.max_tries <= 20):
        raise HTTPException(400, "target must be 50-100 and max_tries 1-20")
    return jobs.submit("anim-quick", f"Auto-tune to {req.target:.0f}+",
                       lambda ctx: quick.autotune(ctx, src, rig, opts, req.target, req.max_tries, req.history)).public()


class MultiReq(BaseModel):
    clipId: str
    size: int = 360


@router.post("/multiview")
def multiview(req: MultiReq):
    """Re-render the front | side | top preview video of a retargeted clip."""
    from ..services.anim import multiview as mv
    c = db.get_clip(req.clipId)
    if not c or not c.get("rig_id"):
        raise HTTPException(404, "retargeted clip not found")
    return jobs.submit("anim-preview", f"3-view preview: {c['name'][:40]}", lambda ctx: mv.render(ctx, req.clipId, max(160, min(720, req.size)))).public()
