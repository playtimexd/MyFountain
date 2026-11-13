"""/api/anim - animation asset library: rigs, clips, bone maps, retargeting, previews, game exports (see docs/ANIMATION.md)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..jobs import jobs
from ..services.anim import blender, db, library, review, search

router = APIRouter(prefix="/api/anim", tags=["anim"])


def _pub(d: dict | None) -> dict | None:
    return None if d is None else {k: v for k, v in d.items() if not k.startswith("_")}


# --------------------------------------------------------------------------------------------- status
@router.get("/status")
def status():
    from .. import config as cfg
    return {"blender": blender.status(), "my_engine": library.my_engine_status(), "role_names": library.ROLE_NAMES,
            "rigs": len(db.list_rigs()), "clips": len(db.list_clips()), "llm_provider": cfg.load_settings().get("llm_provider")}


# --------------------------------------------------------------------------------------------- rigs
class RigImport(BaseModel):
    path: str | None = None
    mediaId: str | None = None
    name: str | None = None
    up_axis: str | None = None


@router.post("/rigs/import")
def rigs_import(req: RigImport):
    if not (req.path or req.mediaId):
        raise HTTPException(400, "path or mediaId is required")
    if not blender.find_exe():
        raise HTTPException(503, blender.status()["note"])
    return jobs.submit("anim-rig", f"Import rig: {Path(req.path).name if req.path else req.mediaId}",
                       lambda ctx: library.import_rig(ctx, req.path, req.mediaId, req.name, req.up_axis)).public()


@router.get("/rigs")
def rigs_list():
    return db.list_rigs()


@router.get("/rigs/{rid}")
def rigs_get(rid: str):
    return library.rig_public(rid)


@router.delete("/rigs/{rid}")
def rigs_delete(rid: str):
    return library.delete_rig(rid)


@router.get("/rigs/{rid}/thumb")
def rigs_thumb(rid: str):
    r = db.get_rig_raw(rid)
    if not r or not r["thumb"] or not Path(r["thumb"]).exists():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(r["thumb"], media_type="image/png")


class RigPatch(BaseModel):
    name: str | None = None


@router.patch("/rigs/{rid}")
def rigs_patch(rid: str, p: RigPatch):
    if not db.get_rig(rid):
        raise HTTPException(404, "rig not found")
    if p.name:
        db.update_rig(rid, name=p.name)
    return library.rig_public(rid)


@router.get("/rigs/{rid}/bonemap")
def bonemap_get(rid: str, skeleton: str | None = None, clipId: str | None = None):
    return library.get_bone_map(rid, skeleton, clipId)


class BoneMapPut(BaseModel):
    map: dict[str, str | None]
    skeleton: str | None = None
    clipId: str | None = None


@router.put("/rigs/{rid}/bonemap")
def bonemap_put(rid: str, body: BoneMapPut):
    return library.put_bone_map(rid, body.map, body.skeleton, body.clipId)


class SuggestReq(BaseModel):
    use_llm: bool = False
    skeleton: str | None = None
    clipId: str | None = None


@router.post("/rigs/{rid}/suggest-map")
def suggest_map(rid: str, body: SuggestReq | None = None):
    body = body or SuggestReq()
    return library.suggest_map(rid, body.skeleton, body.clipId, body.use_llm)


# --------------------------------------------------------------------------------------------- clips
class ClipImport(BaseModel):
    path: str | None = None
    motion_file: str | None = None
    name: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/clips/import")
def clips_import(req: ClipImport):
    p = req.path or req.motion_file
    if not p:
        raise HTTPException(400, "path (BVH/FBX/GLB/BLEND/motion.npz) is required")
    if Path(p).suffix.lower() != ".npz" and not blender.find_exe():
        raise HTTPException(503, blender.status()["note"])
    return jobs.submit("anim-clip", f"Import motion: {Path(p).name}", lambda ctx: library.import_clip(ctx, p, req.name, req.options)).public()


class DemoReq(BaseModel):
    kind: str = "quadruped_walk"
    name: str | None = None


@router.post("/clips/demo")
def clips_demo(req: DemoReq):
    return jobs.submit("anim-clip", f"Demo motion: {req.kind}", lambda ctx: library.demo_clip(ctx, req.kind, req.name)).public()


@router.get("/clips")
def clips_list(rigId: str | None = None, kind: str | None = None, q: str | None = None):
    return [_pub(c) for c in db.list_clips(rig_id=rigId, kind=kind, q=q)]


@router.get("/clips/{cid}")
def clips_get(cid: str):
    c = db.get_clip(cid)
    if not c:
        raise HTTPException(404, "clip not found")
    out = _pub(c)
    if c.get("preview_media_id"):
        from .. import db as media_db
        out["preview_media"] = media_db.get_media(c["preview_media_id"])
    if c["rig_id"]:
        r = db.get_rig(c["rig_id"])
        out["rig"] = {"id": r["id"], "name": r["name"], "kind": r["kind"]} if r else None
    return out


@router.delete("/clips/{cid}")
def clips_delete(cid: str):
    if not library.delete_clip(cid):
        raise HTTPException(404, "clip not found")
    return {"deleted": cid}


class ClipPatch(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    caption: str | None = None
    role: str | None = None
    loop: bool | None = None


@router.patch("/clips/{cid}")
def clips_patch(cid: str, p: ClipPatch):
    c = db.get_clip(cid)
    if not c:
        raise HTTPException(404, "clip not found")
    f = {k: v for k, v in p.model_dump().items() if v is not None}
    if "role" in f:
        f["role"] = f["role"].strip().lower()
        if f["role"] and f["role"] not in library.ROLE_NAMES:
            raise HTTPException(400, "role must be one of: " + ", ".join(sorted(library.ROLE_NAMES)) + " (or empty)")
    db.update_clip(cid, **f)
    if {"name", "tags", "caption", "role"} & set(f):
        search.ensure_embedding(cid)
    return _pub(db.get_clip(cid))


@router.get("/search")
def clips_search(q: str = "", limit: int = 30, kind: str | None = None):
    return search.search(q, limit, kind)


# --------------------------------------------------------------------------------------------- pipeline jobs
class RetargetReq(BaseModel):
    clipId: str
    rigId: str
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/retarget")
def retarget(req: RetargetReq):
    c, r = db.get_clip(req.clipId), db.get_rig(req.rigId)
    if not c:
        raise HTTPException(404, "clip not found")
    if not r:
        raise HTTPException(404, "rig not found")
    return jobs.submit("anim-retarget", f"Retarget {c['name']} -> {r['name']}", lambda ctx: library.retarget(ctx, req.clipId, req.rigId, req.options)).public()


class ExportReq(BaseModel):
    clipId: str | None = None
    clipIds: list[str] | None = None
    rigId: str | None = None
    format: str = "glb"
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/export")
def export(req: ExportReq):
    if not (req.clipId or req.clipIds or req.rigId):
        raise HTTPException(400, "clipId, clipIds or rigId is required")
    return jobs.submit("anim-export", f"Export {req.format.upper()}", lambda ctx: library.export(ctx, req.clipId, req.format, req.options, req.clipIds, req.rigId)).public()


class PreviewReq(BaseModel):
    clipId: str
    camera: str = "side"
    size: int | list[int] = 384
    bg: str = "transparent"
    frames: int | None = None
    engine: str = "workbench"
    in_place: bool | None = None


@router.post("/preview")
def preview(req: PreviewReq):
    c = db.get_clip(req.clipId)
    if not c:
        raise HTTPException(404, "clip not found")
    if req.camera not in ("side", "front", "back", "three_quarter", "orbit"):
        raise HTTPException(400, "camera must be side, front, back, three_quarter or orbit")
    opts = {k: v for k, v in req.model_dump().items() if k != "clipId" and v is not None}
    return jobs.submit("anim-preview", f"Preview {c['name']}", lambda ctx: library.render_preview(ctx, req.clipId, opts)).public()


class AnnotateReq(BaseModel):
    clipId: str
    use_cloud: bool = False


@router.post("/annotate")
def annotate(req: AnnotateReq):
    c = db.get_clip(req.clipId)
    if not c:
        raise HTTPException(404, "clip not found")
    return jobs.submit("anim-annotate", f"Annotate {c['name']}", lambda ctx: review.annotate(ctx, req.clipId, req.use_cloud)).public()


class ReviewReq(BaseModel):
    clipId: str
    mode: str | None = None            # host | local | cloud
    use_cloud: bool = False
    frames: int = 8


@router.post("/review")
def review_clip(req: ReviewReq):
    c = db.get_clip(req.clipId)
    if not c:
        raise HTTPException(404, "clip not found")
    if not c["rig_id"]:
        raise HTTPException(400, "Review needs a retargeted clip.")
    mode = req.mode or ("cloud" if req.use_cloud else "local")
    if mode not in ("host", "local", "cloud"):
        raise HTTPException(400, "mode must be host, local or cloud")
    return jobs.submit("anim-review", f"Review {c['name']} ({mode})", lambda ctx: review.review(ctx, req.clipId, mode, req.frames, req.use_cloud)).public()


# --------------------------------------------------------------------------------------------- my-engine
class SendReq(BaseModel):
    clipIds: list[str] | None = None
    rigId: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


@router.get("/send/my-engine/status")
def my_engine_status():
    return library.my_engine_status()


@router.post("/send/my-engine")
def send_my_engine(req: SendReq):
    if not (req.clipIds or req.rigId):
        raise HTTPException(400, "clipIds or rigId is required")
    st = library.my_engine_status()
    if not st["reachable"]:
        raise HTTPException(503, "my-engine asset server is not running at " + st["url"] + ". Start it with `pnpm assets` in D:\\THreejsEngine\\my-engine.")
    return jobs.submit("anim-send", "Send to my-engine", lambda ctx: library.send_to_my_engine(ctx, req.clipIds, req.rigId, req.options)).public()


# --------------------------------------------------------------------------------------------- files
_MIME = {"glb": "model/gltf-binary", "fbx": "application/octet-stream", "webm": "video/webm", "mp4": "video/mp4", "png": "image/png",
         "blend": "application/octet-stream", "json": "application/json", "npz": "application/octet-stream"}


@router.get("/file/{name}")
def get_file(name: str, download: bool = Query(False)):
    key, _, ext = name.rpartition(".")
    if not key or ext not in _MIME:
        raise HTTPException(404, "unknown file")
    path: str | None = None
    exp = db.get_export(key)
    if exp:
        path = exp["sidecar"] if ext == "json" else (exp["file"] if exp["format"] == ext else None)
    else:
        c = db.get_clip(key)
        if c:
            path = c["_files"].get(ext) if ext != "json" else None
            if ext == "npz":
                path = c["_files"].get("motion") or c["_files"].get("bake")
    if not path or not Path(path).exists():
        raise HTTPException(404, "file not available (export or render it first)")
    return FileResponse(path, media_type=_MIME[ext], filename=Path(path).name if download else None)
