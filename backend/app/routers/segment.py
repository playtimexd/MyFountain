"""Video/audio segmentation: scene cuts (PySceneDetect), silence/speech (ffmpeg), fixed-length, ASR sentences.
`/cut` writes the chosen segments out as new library assets."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..config import FFMPEG, MEDIA_DIR
from ..ffutil import _NOWIN, has_nvenc, run_ffmpeg
from ..ingest import ingest_file
from ..jobs import JobContext, jobs

router = APIRouter(prefix="/api/segment", tags=["segment"])


class SegReq(BaseModel):
    mediaId: str
    method: str = "scene"            # scene | silence | fixed | asr
    threshold: float | None = None   # scene: content threshold (default 27, lower = more cuts) ; silence: dB (default -35)
    min_len: float | None = None     # seconds
    every: float | None = None       # fixed: seconds
    language: str | None = None


def scene_segments(path: str, threshold: float, min_len: float, ctx: JobContext | None = None) -> list[dict]:
    try:
        from scenedetect import ContentDetector, detect
        ctx and ctx.progress(0.1, "Detecting scene cuts…")
        scenes = detect(path, ContentDetector(threshold=threshold, min_scene_len=int(max(min_len, 0.2) * 25)), show_progress=False)
        segs = [{"start": round(s.get_seconds(), 3), "end": round(e.get_seconds(), 3)} for s, e in scenes]
        return segs or [{"start": 0.0, "end": round(_duration(path), 3)}]     # no cuts found -> one scene
    except ImportError:
        pass
    # ffmpeg fallback
    ctx and ctx.progress(0.1, "Detecting scene cuts (ffmpeg)…")
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", path, "-vf", f"select='gt(scene,{threshold / 100:.3f})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **_NOWIN)
    cuts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", p.stderr)]
    return _cuts_to_segments(cuts, _duration(path))


def _duration(path: str) -> float:
    from ..ffutil import probe
    return probe(path)["duration"]


def _cuts_to_segments(cuts: list[float], dur: float) -> list[dict]:
    pts = [0.0, *sorted(cuts), dur]
    return [{"start": round(a, 3), "end": round(b, 3)} for a, b in zip(pts, pts[1:]) if b - a > 0.05]


def silence_segments(path: str, db_thresh: float, min_silence: float, dur: float) -> list[dict]:
    """Return the *non-silent* (speech/active) ranges."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", path, "-af", f"silencedetect=noise={db_thresh}dB:d={min_silence}", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **_NOWIN)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.\-]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    sil: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        sil.append((max(s, 0.0), ends[i] if i < len(ends) else dur))
    out, cur = [], 0.0
    for s, e in sil:
        if s - cur > 0.15:
            out.append({"start": round(cur, 3), "end": round(s, 3)})
        cur = e
    if dur - cur > 0.15:
        out.append({"start": round(cur, 3), "end": round(dur, 3)})
    return out


@router.post("")
def segment(req: SegReq):
    m = db.get_media(req.mediaId)
    if not m:
        raise HTTPException(404, "media not found")
    if m["kind"] == "image":
        raise HTTPException(400, "Images cannot be split")
    if req.method == "scene" and m["kind"] != "video":
        raise HTTPException(400, "Scene detection only works on video")

    def work(ctx: JobContext):
        path, dur = m["path"], m["duration"]
        if req.method == "scene":
            segs = scene_segments(path, req.threshold or 27.0, req.min_len or 0.6, ctx)
            label = "Shot"
        elif req.method == "silence":
            ctx.progress(0.2, "Analyzing silence…")
            segs = silence_segments(path, req.threshold if req.threshold is not None else -35.0, req.min_len or 0.5, dur)
            label = "Speech"
        elif req.method == "fixed":
            step = max(0.5, req.every or 10.0)
            segs, t = [], 0.0
            while t < dur - 0.05:
                segs.append({"start": round(t, 3), "end": round(min(t + step, dur), 3)}); t += step
            label = "Clip"
        elif req.method == "asr":
            try:
                from ..services import asr          # provided by the backend-ai module
                res = asr.transcribe(path, language=req.language, ctx=ctx, duration=dur)
                segs = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in res["segments"]]
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"Speech segmentation unavailable: {e}")
            label = "Sentence"
        else:
            raise ValueError(f"unknown method {req.method}")
        for i, s in enumerate(segs, 1):
            s["label"] = f"{label} {i}"
        return {"segments": segs, "count": len(segs), "duration": dur}

    return jobs.submit("segment", f"Split · {m['name']}", work).public()


class Seg(BaseModel):
    start: float
    end: float
    label: str | None = None


class CutReq(BaseModel):
    mediaId: str
    segments: list[Seg]
    reencode: bool = False           # False = lossless stream copy (cuts snap to keyframes)


@router.post("/cut")
def cut(req: CutReq):
    m = db.get_media(req.mediaId)
    if not m:
        raise HTTPException(404, "media not found")
    if not req.segments or len(req.segments) > 500:
        raise HTTPException(400, "Send 1..500 segments.")
    dur = float(m.get("duration") or 0)
    for s in req.segments:
        if s.end - s.start < 0.05 or s.start < 0 or (dur and s.start >= dur):
            raise HTTPException(400, f"Bad range {s.start:.2f}-{s.end:.2f}s")
    base = re.sub(r"[^\w\-]+", "_", m["name"], flags=re.UNICODE).strip("_")[:40] or "clip"

    def work(ctx: JobContext):
        items = []
        n = len(req.segments)
        for i, s in enumerate(req.segments):
            ctx.progress(i / max(n, 1), f"Exporting segment {i + 1}/{n}")
            ext = Path(m["path"]).suffix if m["kind"] != "audio" else ".mp3"
            lab = re.sub(r"[^\w\-]+", "_", s.label or "", flags=re.UNICODE).strip("_")[:30]
            dst = MEDIA_DIR / f"{base}_{i + 1:03d}{'_' + lab if lab else ''}_{m['id'][:6]}{ext if not req.reencode else '.mp4' if m['kind'] == 'video' else ext}"
            args = ["-ss", f"{s.start:.3f}", "-to", f"{s.end:.3f}", "-i", m["path"]]
            if req.reencode and m["kind"] == "video":
                args += ["-c:v", "h264_nvenc", "-cq", "21", "-preset", "p4", "-c:a", "aac"] if has_nvenc() else \
                        ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-c:a", "aac"]
            else:
                args += ["-c", "copy"]
            run_ffmpeg([*args, str(dst)], total_duration=s.end - s.start, ctx=ctx, progress_scale=(i / n, (i + 1) / n))
            items.append(ingest_file(dst, origin="segment", name=f"{m['name']} · {s.label or i + 1}", tags=["segment"], dedupe=False,
                                     meta={"parent": m["id"], "start": s.start, "end": s.end}))
        return {"items": items}

    return jobs.submit("segment-cut", f"Export {len(req.segments)} segments", work).public()


# ---------------------------------------------------------------------------------------------- filmstrip (Video Segment timeline)
@router.get("/filmstrip/{mid}")
def filmstrip(mid: str, n: int = 24, h: int = 54):
    """One JPEG with `n` evenly spaced frames side by side (cached), for the segment timeline background."""
    from fastapi.responses import FileResponse

    from ..config import CACHE_DIR
    m = db.get_media(mid)
    if not m or m["kind"] != "video":
        raise HTTPException(404, "video not found")
    n, h = max(4, min(int(n), 60)), max(24, min(int(h), 160))
    out = CACHE_DIR / "filmstrip" / f"{mid}_{n}_{h}.jpg"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        dur = max(float(m.get("duration") or 0), 0.1)
        fps = n / dur
        p = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", m["path"], "-vf", f"fps={fps:.6f},scale=-2:{h},tile={n}x1", "-frames:v", "1",
                            "-q:v", "5", str(out)], capture_output=True, **_NOWIN)
        if p.returncode != 0 or not out.exists():
            raise HTTPException(500, f"filmstrip failed: {p.stderr.decode(errors='replace')[-200:]}")
    return FileResponse(out, media_type="image/jpeg")
