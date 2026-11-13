from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import EXPORT_DIR
from ..ingest import ingest_file
from ..jobs import JobContext, jobs
from ..services import render

router = APIRouter(prefix="/api/export", tags=["export"])


class Preset(BaseModel):
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    format: str = "mp4"              # mp4 | webm | gif | mp3
    quality: str = "normal"          # draft | normal | high
    hwaccel: bool = True


class ExportReq(BaseModel):
    project: dict
    preset: Preset = Preset()


@router.post("")
def export(req: ExportReq):
    if not any(t.get("clips") for t in req.project.get("tracks", [])):
        raise HTTPException(400, "The timeline is empty")

    def work(ctx: JobContext):
        path = render.render(req.project, req.preset.model_dump(exclude_none=True), ctx)
        row = ingest_file(path, origin="export", name=path.stem, dedupe=False)
        return {"media": row, "file": str(path)}

    return jobs.submit("export", f"Export {req.project.get('name', 'Project')}", work).public()


class FrameReq(BaseModel):
    project: dict
    t: float = 0.0
    width: int = 960


@router.post("/frame")
def export_frame(req: FrameReq):
    """Render ONE frame of the project at time t with the real export pipeline (JPEG). Used by MCP / agents
    to look at the composition exactly as it will export."""
    import subprocess
    import uuid

    from fastapi.responses import Response

    from ..config import CACHE_DIR, FFMPEG
    from ..ffutil import _NOWIN
    proj = req.project
    w = max(64, min(int(req.width), int(proj.get("width") or 1920)))
    h = int(round(w * (proj.get("height") or 1080) / (proj.get("width") or 1920) / 2) * 2)
    b = render.Builder(proj, {"width": w, "height": h})
    try:
        vout, _, _ = b.build(want_video=True)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    t = max(0.0, min(req.t, max(b.dur - 1 / b.fps, 0.0)))
    script = CACHE_DIR / f"frame_{uuid.uuid4().hex[:8]}.txt"
    script.write_text(";\n".join(b.chains), encoding="utf-8")
    out = CACHE_DIR / f"frame_{uuid.uuid4().hex[:8]}.jpg"
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    for grp in b.inputs:
        args += grp
    args += ["-/filter_complex", str(script), "-map", f"[{vout}]", "-ss", f"{t:.3f}", "-frames:v", "1", "-q:v", "3", str(out)]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=120, **_NOWIN)
        if p.returncode != 0 or not out.exists():
            raise HTTPException(500, f"frame render failed: {p.stderr[-500:]}")
        return Response(out.read_bytes(), media_type="image/jpeg")
    finally:
        script.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


@router.get("/{name}")
def get_export(name: str):
    p = EXPORT_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)
