"""Motion capture: monocular video -> 3D skeletal motion (humanoid + quadruped) -> motion.npz.  See docs/MOCAP.md."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db
from ..jobs import jobs
from ..services.mocap import backends, gem, models, motion_io, sam3d
from ..services.mocap.pipeline import MOTION_DIR, MocapError, MocapRequest, run_mocap

router = APIRouter(prefix="/api/mocap", tags=["mocap"])


class PrepareReq(BaseModel):
    backend: str = "vitpose"
    variant: str | None = None            # vitpose only: small | base (default) | large | huge


class MocapReq(BaseModel):
    mediaId: str
    start: float | None = None
    end: float | None = None
    subject: str = "auto"                 # humanoid | quadruped | auto
    backend: str | None = "auto"          # auto | vitpose | gem | sam3d  (| mock when CUTLAB_MOCAP_DEV=1)
    rigId: str | None = None
    fps: float | None = None
    box: list[float] | None = None        # first-frame subject box [x, y, w, h] in source pixels
    size_m: float | None = None           # real body size, metres (human height / quadruped shoulder height)
    pose_variant: str | None = None
    overlay: bool = True
    camera_pitch: float | None = None     # degrees the camera looks down; omit to estimate automatically


@router.get("/backends")
def list_backends():
    return backends.list_backends()


@router.post("/prepare")
def prepare(req: PrepareReq):
    """Downloads model weights. Only ever called on explicit user request."""
    bid = req.backend
    if bid == "vitpose":
        variant = req.variant or models.DEFAULT_POSE
        if variant not in models.POSE_MODELS:
            raise HTTPException(400, f"Unknown variant {variant!r}; choose one of {', '.join(models.POSE_MODELS)}.")

        def work(ctx):
            return models.download(variant, models.DEFAULT_DET, progress=ctx.progress)
        return jobs.submit("mocap_prepare", f"Download mocap weights (vitpose-plus-{variant} + RT-DETRv2)", work).public()
    if bid in ("gem", "sam3d"):
        def work2(ctx):
            return _prepare_direct(bid, ctx)
        return jobs.submit("mocap_prepare", f"Prepare {bid}", work2).public()
    raise HTTPException(400, f"Unknown backend {bid!r}.")


def _prepare_direct(bid: str, ctx) -> dict:
    """GEM-X / SAM 3D Body live in their own environments which we do not create; we only fetch weights into an
    existing clone, and explain the manual steps otherwise."""
    from huggingface_hub import snapshot_download
    if bid == "gem":
        repo = gem.home() / "repo"
        if not repo.exists():
            raise RuntimeError("GEM-X is not installed. " + gem.HOWTO)
        ctx.progress(0.05, "Downloading nvidia/GEM-X weights (~7 GB)")
        snapshot_download("nvidia/GEM-X", local_dir=str(repo / "weights"),
                          allow_patterns=["body_s240000.ckpt", "body_config.json", "motion_native77_e774_s292175.ckpt",
                                          "motion_stats_raw.npz", "motion_native77.yaml", "body_video_native77.yaml", "vitpose.pth"])
        return {"weights": str(repo / "weights"), "note": "Also needs SOMA assets + yolox_humanart.onnx (see docs/MOCAP.md)."}
    repo = sam3d.home() / "repo"
    if not repo.exists():
        raise RuntimeError("SAM 3D Body is not installed. " + sam3d.HOWTO)
    ctx.progress(0.05, "Downloading facebook/sam-3d-body-dinov3 (~2.8 GB, gated)")
    snapshot_download("facebook/sam-3d-body-dinov3", local_dir=str(repo / "checkpoints" / "sam-3d-body-dinov3"))
    return {"weights": str(repo / "checkpoints" / "sam-3d-body-dinov3")}


@router.post("")
def start_mocap(req: MocapReq):
    row = db.get_media(req.mediaId)
    if not row:
        raise HTTPException(404, "Media not found.")
    if row["kind"] != "video":
        raise HTTPException(400, "Motion capture needs a video file.")
    if req.subject not in ("humanoid", "quadruped", "auto"):
        raise HTTPException(400, "subject must be 'humanoid', 'quadruped' or 'auto'.")
    if req.start is not None and req.end is not None and req.end <= req.start:
        raise HTTPException(400, "end must be greater than start.")
    try:
        bid = backends.resolve(req.backend, req.subject)
    except ValueError as e:
        raise HTTPException(400, str(e))
    mr = MocapRequest(**req.model_dump())

    def work(ctx):
        try:
            return run_mocap(ctx, mr)
        except MocapError as e:
            raise RuntimeError(str(e)) from None

    return jobs.submit("mocap", f"Motion capture ({bid}) - {row.get('name', '')}"[:100], work).public()


_SAFE = re.compile(r"^[A-Za-z0-9_\-]+\.(npz|json)$")


@router.get("/motion/{fname}")
def get_motion(fname: str):
    """Download motion_<id>.npz, or motion_<id>.json for a human-readable summary (meta, no arrays)."""
    if not _SAFE.match(fname):
        raise HTTPException(400, "bad file name")
    stem, ext = fname.rsplit(".", 1)
    p = MOTION_DIR / f"{stem}.npz"
    if not p.exists():
        raise HTTPException(404, "motion file not found")
    if ext == "npz":
        return FileResponse(p, media_type="application/octet-stream", filename=p.name)
    m = motion_io.load_motion(p)
    return {"fps": m["fps"], "skeleton": m["skeleton"], "joint_names": m["joint_names"],
            "frames": int(m["joints3d"].shape[0]), "meta": m["meta"]}


@router.get("/motions")
def list_motions():
    out = []
    for p in sorted(MOTION_DIR.glob("motion_*.npz"), key=lambda q: -q.stat().st_mtime):
        out.append({"name": p.name, "file": str(p), "size": p.stat().st_size, "url": f"/api/mocap/motion/{p.name}",
                    "modified": p.stat().st_mtime})
    return out
