"""Three-view preview of a retargeted clip: front | side | top in one video (labels, shared timing), for judging motion quality.

Each view is rendered by blender/render_preview.py (Workbench, orthographic, auto-framed, camera follows the root when the clip
travels) on a dark background, then ffmpeg stacks the three PNG sequences side by side. Registered in the media library (origin
'anim', tag 'multiview') so it can also go on the video timeline.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from ... import config, ingest
from ...ffutil import run_ffmpeg
from ...jobs import JobContext
from . import blender, db

VIEWS = (("front", "FRONT"), ("side", "SIDE"), ("top", "TOP"))
BG = "#1d2027"


def render(ctx: JobContext, clip_id: str, size: int = 360, lo: float = 0.0, hi: float = 1.0) -> dict:
    c = db.get_clip(clip_id)
    if not c or not c.get("rig_id"):
        raise RuntimeError("three-view preview needs a retargeted clip")
    rig = db.get_rig(c["rig_id"], True)
    if not rig:
        raise RuntimeError("the clip's character was deleted")
    size = int(size) // 2 * 2
    out_dir = db.PREVIEWS_DIR / clip_id / f"multiview_{int(time.time())}"
    seqs, fps, n = [], 30.0, 0
    for i, (cam, _label) in enumerate(VIEWS):
        seq = out_dir / cam
        a = lo + (hi - lo) * i / len(VIEWS)
        res = blender.run_script("render_preview.py", {
            "rig_blend": rig["file"], "bake": c["_files"]["bake"], "out_dir": str(seq), "camera": cam, "size": [size, size],
            "transparent": False, "bg": BG, "engine": "workbench", "in_place": not c["root_motion"], "step": 1,
            "armature": (rig.get("info") or {}).get("armature")}, ctx, (a, a + (hi - lo) / len(VIEWS) * 0.95))
        fps, n = float(res.get("fps") or fps), int(res.get("frames") or n)
        seqs.append(seq)
    ctx.progress(lo + (hi - lo) * 0.97, "Stacking front | side | top")
    font = "C\\:/Windows/Fonts/arial.ttf"
    inputs, labels = [], []
    for k, (seq, (_cam, label)) in enumerate(zip(seqs, VIEWS)):
        inputs += ["-framerate", f"{fps:.3f}", "-i", str(seq / "f_%04d.png")]
        labels.append(f"[{k}:v]drawtext=fontfile='{font}':text='{label}':x=10:y=8:fontsize={max(12, size // 22)}:fontcolor=white@0.8[v{k}]")
    graph = ";".join(labels) + ";" + "".join(f"[v{k}]" for k in range(len(VIEWS))) + f"hstack=inputs={len(VIEWS)},format=yuv420p[out]"
    from .library import safe_name
    mp4 = out_dir / f"{safe_name(c['name'])}_3view.mp4"
    run_ffmpeg([*inputs, "-filter_complex", graph, "-map", "[out]", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-movflags", "+faststart", "-an",
                str(mp4)], total_duration=n / max(fps, 1), ctx=ctx)
    for seq in seqs:
        shutil.rmtree(seq, ignore_errors=True)
    row = ingest.ingest_file(mp4, origin="anim", name=f"{c['name']} (front | side | top)"[:120], tags=["anim", "multiview", rig["name"]],
                             meta={"anim": {"clipId": clip_id, "rigId": c["rig_id"], "camera": "multiview", "fps": fps, "frames": n}})
    files = dict(c["_files"])
    files["multiview"] = str(mp4)
    db.update_clip(clip_id, files=files)
    return {"media": row, "file": str(mp4)}
