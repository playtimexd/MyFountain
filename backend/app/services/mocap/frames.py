"""Stage (a): frame extraction at the target fps via ffmpeg (resolution capped)."""
from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...config import CACHE_DIR
from ...ffutil import probe, run_ffmpeg

MAX_FRAMES = 1800            # 60 s at 30 fps


@dataclass
class FrameSet:
    dir: Path
    n: int
    fps: float
    width: int
    height: int
    start: float
    scale: float             # extracted size / source size

    def path(self, i: int) -> Path:
        return self.dir / f"{i + 1:06d}.jpg"

    def read(self, i: int) -> np.ndarray:
        import cv2
        img = cv2.imread(str(self.path(i)))
        if img is None:
            raise IOError(f"missing frame {i}")
        return img

    def times(self) -> np.ndarray:
        return self.start + np.arange(self.n) / self.fps

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def extract_frames(video: str | Path, *, start: float = 0.0, end: float | None = None, fps: float | None = None,
                   max_side: int = 1280, ctx=None, out_dir: Path | None = None) -> FrameSet:
    info = probe(video)
    if info["kind"] != "video" or not info["width"]:
        raise ValueError("The selected media is not a video.")
    src_fps = info["fps"] or 30.0
    fps = float(fps or min(src_fps, 30.0))
    fps = max(1.0, min(fps, 60.0))
    dur = float(info["duration"] or 0)
    start = max(0.0, float(start or 0.0))
    end = min(float(end), dur) if end else dur
    if end <= start:
        raise ValueError("Invalid time range (end must be after start).")
    length = end - start
    if length * fps > MAX_FRAMES:
        length = MAX_FRAMES / fps
    w, h = info["width"], info["height"]
    if max(w, h) > max_side:
        k = max_side / max(w, h)
        vf_scale = f"scale={max_side}:-2" if w >= h else f"scale=-2:{max_side}"
    else:
        k = 1.0
        vf_scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    out = out_dir or (CACHE_DIR / f"mocap_{uuid.uuid4().hex[:8]}")
    out.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(video), "-vf", f"fps={fps},{vf_scale}",
                "-q:v", "2", "-an", str(out / "%06d.jpg")], total_duration=length, ctx=ctx, progress_scale=(0.0, 1.0))
    files = sorted(out.glob("*.jpg"))
    if not files:
        raise RuntimeError("ffmpeg produced no frames for the selected range.")
    import cv2
    first = cv2.imread(str(files[0]))
    return FrameSet(dir=out, n=len(files), fps=fps, width=int(first.shape[1]), height=int(first.shape[0]),
                    start=start, scale=k)
