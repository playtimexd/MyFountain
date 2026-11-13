"""AI tools: upscale, watermark removal/detection, remove-bg, inpaint, subtitles (ASR), auto-cut."""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import config, db
from ..ffutil import _NOWIN
from ..jobs import jobs
from ..services import asr, autocut, inpaint as ip, upscale as up

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _media(mid: str, kinds: tuple[str, ...] | None = None) -> dict:
    m = db.get_media(mid)
    if not m:
        raise HTTPException(404, f"media {mid} not found")
    if not Path(m["path"]).exists():
        raise HTTPException(404, f"File not found: {m['path']}")
    if kinds and m["kind"] not in kinds:
        raise HTTPException(400, f"Needs {'/'.join(kinds)} media, got {m['kind']}")
    return m


@router.get("/capabilities")
def capabilities():
    e = up.engines()
    return {
        "upscale": {**e, "engine": up.choose_engine("auto")},
        "inpaint": {"opencv": True, "lama": ip.has_lama()},
        "remove_bg": {"rembg": ip.has_rembg(), "grabcut": True},
        "asr": asr.status(),
    }


@router.get("/frame")
def frame(mediaId: str, t: float = 0.0, max: int = 1280):
    """One full-resolution (downscaled to `max` px) JPEG frame; used by the watermark region picker."""
    m = _media(mediaId, ("video", "image"))
    t = min(max_t(m), t) if m["kind"] == "video" else 0
    vf = f"scale='if(gt(iw,ih),min({max},iw),-2)':'if(gt(iw,ih),-2,min({max},ih))'"
    args = [config.FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", m["path"], "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"]
    p = subprocess.run(args, capture_output=True, **_NOWIN)
    if not p.stdout:
        raise HTTPException(500, "Failed to grab frame")
    return Response(p.stdout, media_type="image/jpeg")


def max_t(m: dict) -> float:
    return max(0.0, (m.get("duration") or 0) - 0.05)


# --------------------------------------------------------------------------------- upscale
class UpscaleReq(BaseModel):
    mediaId: str
    scale: int = 2
    model: str | None = "auto"
    denoise: bool | float | None = False


@router.post("/upscale")
def upscale(req: UpscaleReq):
    m = _media(req.mediaId, ("video", "image"))
    return jobs.submit("upscale", f"Upscale x{req.scale}: {m['name']}",
                       lambda ctx: up.upscale(ctx, m, req.scale, req.model, req.denoise)).public()


# --------------------------------------------------------------------------------- watermark
class Region(BaseModel):
    x: float
    y: float
    w: float
    h: float


class WatermarkReq(BaseModel):
    mediaId: str
    regions: list[Region]
    method: str = "auto"
    start: float | None = None
    end: float | None = None


@router.post("/watermark")
def watermark(req: WatermarkReq):
    m = _media(req.mediaId, ("video", "image"))
    if not req.regions:
        raise HTTPException(400, "regions must not be empty")
    regs = [r.model_dump() for r in req.regions]
    return jobs.submit("watermark", f"Remove watermark: {m['name']}",
                       lambda ctx: ip.watermark(ctx, m, regs, req.method, req.start, req.end)).public()


class DetectReq(BaseModel):
    mediaId: str
    samples: int = 24


@router.post("/watermark/detect")
def watermark_detect(req: DetectReq):
    m = _media(req.mediaId, ("video", "image"))
    r = ip.detect_watermark(m, req.samples)
    r["width"], r["height"] = m["width"], m["height"]
    return r


# --------------------------------------------------------------------------------- remove-bg / inpaint
class RemoveBgReq(BaseModel):
    mediaId: str | None = None
    imageBase64: str | None = None


@router.post("/remove-bg")
def remove_bg(req: RemoveBgReq):
    if req.imageBase64:
        img = ip.b64_to_img(req.imageBase64)
    elif req.mediaId:
        m = _media(req.mediaId, ("image", "video"))
        if m["kind"] == "video":
            cap = cv2.VideoCapture(m["path"])
            ok, img = cap.read()
            cap.release()
            if not ok:
                raise HTTPException(500, "Failed to read video frame")
        else:
            img = cv2.imread(m["path"], cv2.IMREAD_COLOR)
            if img is None:
                raise HTTPException(500, "Failed to read image")
    else:
        raise HTTPException(400, "mediaId or imageBase64 is required")
    out, engine = ip.remove_bg(img)
    return {"imageBase64": ip.img_to_b64(out), "engine": engine, "width": out.shape[1], "height": out.shape[0]}


class InpaintReq(BaseModel):
    imageBase64: str
    maskBase64: str
    prompt: str | None = None
    method: str = "auto"      # auto | telea | ns | lama
    radius: int = 4


@router.post("/inpaint")
def inpaint(req: InpaintReq):
    img = ip.b64_to_img(req.imageBase64)
    mask = ip.b64_to_img(req.maskBase64)
    out, engine = ip.inpaint_image(img, mask, req.method, req.radius)
    return {"imageBase64": ip.img_to_b64(out), "engine": engine}


# --------------------------------------------------------------------------------- subtitles
@router.get("/asr/status")
def asr_status():
    return asr.status()


@router.post("/subtitles/prepare")
def subtitles_prepare():
    st = asr.status()
    if not st["package"]:
        raise HTTPException(400, st["message"])
    return jobs.submit("asr-prepare", f"Download Whisper model {st['model']} ({st['size']})", lambda ctx: asr.prepare(ctx)).public()


class SubReq(BaseModel):
    mediaId: str
    language: str | None = None
    translate_to: str | None = None


@router.post("/subtitles")
def subtitles(req: SubReq):
    m = _media(req.mediaId, ("video", "audio"))
    st = asr.status()
    if not st["ready"]:
        raise HTTPException(409, st["message"])

    def work(ctx):
        ctx.progress(0.02, "Loading model")
        res = asr.transcribe(m["path"], language=req.language, ctx=ctx, duration=m.get("duration") or 0)
        segs = res["segments"]
        if req.translate_to and req.translate_to != res["language"]:
            segs = asr.translate_segments(segs, req.translate_to, ctx)
        srt = asr.to_srt(segs)
        out = config.EXPORT_DIR / f"{Path(m['path']).stem}.srt"
        out.write_text(srt, "utf-8")
        return {"segments": segs, "srt": srt, "srt_file": str(out), "language": res["language"]}

    return jobs.submit("subtitles", f"Subtitles: {m['name']}", work).public()


# --------------------------------------------------------------------------------- auto-cut
class CutReq(BaseModel):
    mediaId: str
    style: str = "tight"
    target_duration: float | None = None


@router.post("/auto-cut")
def auto_cut(req: CutReq):
    if req.style not in ("tight", "highlights", "talking-head"):
        raise HTTPException(400, "style must be tight|highlights|talking-head")
    m = _media(req.mediaId, ("video", "audio"))
    return jobs.submit("auto-cut", f"Auto-cut ({req.style}): {m['name']}",
                       lambda ctx: autocut.auto_cut(ctx, m, req.style, req.target_duration)).public()
