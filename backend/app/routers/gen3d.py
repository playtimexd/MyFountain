"""3D character generation (text/image -> GLB, optional auto-rig) via Meshy or Tripo (paid cloud APIs) or locally with
TRELLIS.2 in CutLab's ComfyUI (provider 'local': free, unrigged, image or text via Z-Image Turbo). For the cloud providers
`confirm_cost:true` is mandatory (HTTP 409 + estimate otherwise, no network call); `dry_run:true` shows the requests that
would be sent (keys redacted). API keys are never returned. Results land in data/anim/incoming/."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..jobs import jobs
from ..services.providers import gen3d as g3
from ..services.providers import meshy, tripo
from ..services.providers.common import ProviderError

router = APIRouter(prefix="/api/gen3d", tags=["gen3d"])


class RigOpt(BaseModel):
    type: str = "biped"                     # meshy: biped | tripo: auto|biped|quadruped|hexapod|octopod|avian|serpentine|aquatic|others
    spec: str | None = None                 # tripo biped skeleton naming: tripo|mixamo|actorcore|unity|unreal|vrm
    height_meters: float | None = None      # meshy rig scaling (default 1.7)


class Gen3DReq(BaseModel):
    prompt: str | None = None
    image_mediaId: str | None = None
    provider: str | None = "auto"           # auto | local (TRELLIS.2 in ComfyUI, free) | meshy | tripo
    rig: RigOpt | None = None
    quality: str = "draft"                  # meshy: draft (meshy-6-lite) | standard (meshy-7.1); local: draft | standard | high
    animation: str | None = None            # walk | run (basic preset clip, needs rig)
    name: str | None = None
    confirm_cost: bool = False
    dry_run: bool = False


class RigImportReq(BaseModel):
    file: str                               # a file name returned by /api/gen3d (inside data/anim/incoming)


@router.get("/providers")
def providers():
    return g3.providers()


@router.get("/balance")
def balance(provider: str):
    try:
        if provider == "meshy":
            return meshy.balance()
        if provider == "tripo":
            return tripo.balance()
    except ProviderError as e:
        raise HTTPException(e.status, str(e)) from None
    raise HTTPException(400, "provider must be meshy or tripo")


@router.post("")
def generate(req: Gen3DReq):
    d = req.model_dump()
    try:
        pid = g3.resolve_provider(d)
        g3.validate(pid, d)
        if req.dry_run:
            return {"dry_run": True, **g3.dry_run(pid, d)}
        est = g3.estimate(pid, d)
    except ProviderError as e:
        raise HTTPException(e.status, str(e)) from None
    if not req.confirm_cost and pid not in g3.FREE:
        return JSONResponse(status_code=409, content={
            "detail": "This is a paid cloud generation. Confirm the cost (confirm_cost: true) to proceed.", "estimate": est, "provider": pid})
    title = f"Generate 3D ({pid}): {(req.prompt or 'from image')[:24]}"
    return jobs.submit("gen3d", title, lambda ctx: g3.run_job(ctx, pid, d)).public()


@router.post("/rig-import")
def rig_import(req: RigImportReq):
    """Send an already generated GLB to the rig library (anim-core) - used by the panel's 'Add to rig library' button."""
    try:
        p = g3.safe_file(req.file)
    except ProviderError as e:
        raise HTTPException(e.status, str(e)) from None
    return jobs.submit("gen3d-import", f"Import rig: {p.name}", lambda ctx: g3.import_rig(ctx, str(p), p.stem)).public()


@router.get("/file/{name}")
def get_file(name: str):
    try:
        p = g3.safe_file(name)
    except ProviderError as e:
        raise HTTPException(e.status, str(e)) from None
    mt = "model/gltf-binary" if p.suffix.lower() == ".glb" else "image/png"
    return FileResponse(p, media_type=mt)
