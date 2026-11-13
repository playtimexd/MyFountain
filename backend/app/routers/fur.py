"""Fur: Blender strand groom + fur-ready GLB for real-time shell fur.  See app/services/anim/fur.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..jobs import jobs
from ..services.anim import fur

router = APIRouter(prefix="/api/anim/fur", tags=["fur"])

_MIME = {".glb": "model/gltf-binary", ".png": "image/png", ".jpg": "image/jpeg", ".json": "application/json", ".blend": "application/octet-stream",
         ".mp4": "video/mp4", ".webm": "video/webm"}


class GroomReq(BaseModel):
    source: str                                   # "export:<id>" | "rig:<id>"  (GET /api/anim/fur/sources)
    name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)    # strand groom: length_m, density_per_m2, comb, lift, gravity, frizz, curl, melanin, redness, seed
    render: dict[str, Any] = Field(default_factory=dict)    # enabled, size [w,h], samples, camera, frame
    shell: dict[str, Any] = Field(default_factory=dict)     # real-time shell settings saved into fur.json


@router.get("/sources")
def list_sources():
    return fur.sources()


@router.get("")
def list_grooms():
    return fur.list_grooms()


@router.post("")
def start_groom(req: GroomReq):
    try:
        src = fur._source(req.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    r = req.render or {}
    if r.get("samples") is not None and not (1 <= int(r["samples"]) <= 1024):
        raise HTTPException(400, "render.samples must be 1..1024")
    if r.get("size") is not None and (len(r["size"]) != 2 or not all(64 <= int(x) <= 4096 for x in r["size"])):
        raise HTTPException(400, "render.size must be [w, h] with 64..4096 px")
    return jobs.submit("anim-fur", f"Fur groom - {src['name']}"[:100],
                       lambda ctx: fur.groom(ctx, req.source, req.params, req.render, req.shell, req.name)).public()


@router.get("/{fid}")
def get_groom(fid: str):
    rec = fur.get(fid)
    if not rec:
        raise HTTPException(404, "fur groom not found")
    return rec


@router.delete("/{fid}")
def delete_groom(fid: str):
    if not fur.delete(fid):
        raise HTTPException(404, "fur groom not found")
    return {"ok": True}


class VideoReq(BaseModel):
    camera: str = "three_quarter"
    size: list[int] = Field(default_factory=lambda: [960, 540])
    samples: int = 32
    transparent: bool = False
    max_frames: int = 240


@router.post("/{fid}/video")
def render_video(fid: str, req: VideoReq):
    rec = fur.get(fid)
    if not rec:
        raise HTTPException(404, "fur groom not found")
    if req.camera not in ("three_quarter", "side", "front"):
        raise HTTPException(400, "camera must be three_quarter, side or front")
    if len(req.size) != 2 or not all(64 <= x <= 4096 for x in req.size):
        raise HTTPException(400, "size must be [w, h] with 64..4096 px")
    if not (1 <= req.samples <= 1024) or not (1 <= req.max_frames <= 2000):
        raise HTTPException(400, "samples must be 1..1024 and max_frames 1..2000")
    if not (rec.get("source") or {}).get("animated"):
        raise HTTPException(400, "This groom has no animation. Groom a GLB export that contains a clip.")
    return jobs.submit("anim-fur-video", f"Fur video - {rec['name']}"[:100], lambda ctx: fur.render_video(ctx, fid, req.model_dump())).public()


class ShellPut(BaseModel):
    shell: dict[str, Any]


@router.put("/{fid}/shell")
def put_shell(fid: str, body: ShellPut):
    try:
        return fur.save_shell(fid, body.shell)
    except FileNotFoundError:
        raise HTTPException(404, "fur groom not found")


@router.get("/{fid}/file/{name}")
def get_file(fid: str, name: str):
    p = fur.file_path(fid, name)
    if not p or p.suffix.lower() not in _MIME:
        raise HTTPException(404, "file not found")
    return FileResponse(p, media_type=_MIME[p.suffix.lower()], filename=p.name if p.suffix.lower() == ".blend" else None)
