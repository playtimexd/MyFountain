"""Reference-video generation (source clips for motion capture). Cloud: Seedance (BytePlus ModelArk). Local: ComfyUI.

Every cloud generation spends the user's money: `confirm_cost:true` is mandatory (HTTP 409 + estimate otherwise, no network
call), `dry_run:true` returns the request that would be sent (keys redacted). API keys are never returned.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..jobs import jobs
from ..services.providers import videogen as vg
from ..services.providers.common import ProviderError

router = APIRouter(prefix="/api/videogen", tags=["videogen"])


class VideoGenReq(BaseModel):
    prompt: str = ""
    image_mediaId: str | None = None        # optional start frame from the media library (image-to-video)
    duration: float = 5                     # seconds; hard caps: Seedance 10 s, local 6 s
    aspect: str = "16:9"                    # 16:9 | 9:16 | 1:1 | 4:3 | 3:4 | 21:9
    resolution: str | None = None           # Seedance only: 480p (default) | 720p (cap)
    provider: str | None = "auto"           # auto | seedance | local
    variant: str | None = None              # local only: force a ComfyUI workflow id (see /providers -> workflows)
    capture_friendly: bool = False
    confirm_cost: bool = False
    dry_run: bool = False


@router.get("/providers")
def providers():
    return vg.providers()


@router.post("")
def generate(req: VideoGenReq):
    d = req.model_dump()
    try:
        pid = vg.resolve_provider(d)
        d["_orig_prompt"] = d["prompt"].strip()
        d["prompt"] = vg.build_prompt(d)
        vg.validate(pid, d)
        if req.dry_run:
            return {"dry_run": True, "provider": pid, **vg.dry_run(pid, d)}
        est = vg.estimate(pid, d)
    except ProviderError as e:
        raise HTTPException(e.status, str(e)) from None
    if pid in vg.CLOUD and not req.confirm_cost:
        return JSONResponse(status_code=409, content={
            "detail": "This is a paid cloud generation. Confirm the cost (confirm_cost: true) to proceed.", "estimate": est, "provider": pid})
    title = f"Generate video ({pid}): {d['_orig_prompt'][:24]}"
    return jobs.submit("videogen", title, lambda ctx: vg.run_job(ctx, pid, d)).public()
