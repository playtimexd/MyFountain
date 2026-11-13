"""ffmpeg / ffprobe helpers shared by every service."""
from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

from .config import CACHE_DIR, FFMPEG, FFPROBE, THUMB_DIR
from .jobs import JobContext

_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v", ".ts", ".wmv", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def kind_of(path: str | Path) -> str | None:
    e = Path(path).suffix.lower()
    if e in VIDEO_EXT: return "video"
    if e in AUDIO_EXT: return "audio"
    if e in IMAGE_EXT: return "image"
    return None


def probe(path: str | Path) -> dict:
    """Return normalized media info: duration,width,height,fps,has_audio,kind."""
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", **_NOWIN)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()[:300]}")
    j = json.loads(out.stdout or "{}")
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(j.get("format", {}).get("duration") or (v or a or {}).get("duration") or 0)
    fps = 0.0
    if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else 0.0
    kind = kind_of(path)
    if kind is None:
        kind = "video" if v else "audio"
    # still images report as "video" streams with 1 frame
    if kind == "image":
        dur = 0.0
    # honour rotation metadata
    w, h = int(v.get("width", 0)) if v else 0, int(v.get("height", 0)) if v else 0
    rot = 0
    if v:
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
    if abs(rot) in (90, 270):
        w, h = h, w
    return {"kind": kind, "duration": dur, "width": w, "height": h, "fps": round(fps, 3),
            "has_audio": a is not None, "size": int(j.get("format", {}).get("size") or 0),
            "video_codec": (v or {}).get("codec_name"), "audio_codec": (a or {}).get("codec_name")}


@functools.lru_cache(maxsize=1)
def encoders() -> set[str]:
    out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, **_NOWIN)
    return set(re.findall(r"^\s[VAS][\.\w]{5}\s(\S+)", out.stdout, re.M))


@functools.lru_cache(maxsize=1)
def has_nvenc() -> bool:
    if "h264_nvenc" not in encoders():
        return False
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                        "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, **_NOWIN)
    return r.returncode == 0


@functools.lru_cache(maxsize=1)
def filters() -> set[str]:
    out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True, **_NOWIN)
    return set(re.findall(r"^\s[\.\w]{3}\s(\S+)", out.stdout, re.M))


def run_ffmpeg(args: Sequence[str], *, total_duration: float | None = None, ctx: JobContext | None = None,
               progress_scale: tuple[float, float] = (0.0, 1.0)) -> str:
    """Run ffmpeg, streaming `-progress` so the job progress bar moves. Raises on failure."""
    cmd = [FFMPEG, "-hide_banner", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *map(str, args)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", **_NOWIN)
    if ctx:
        ctx.register_process(proc)
    lo, hi = progress_scale
    err_chunks: list[str] = []
    import threading

    t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)  # type: ignore[union-attr]
    t.start()
    for line in proc.stdout:  # type: ignore[union-attr]
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                sec = int(line.split("=")[1]) / 1_000_000
            except ValueError:
                continue
            if ctx and total_duration:
                ctx.progress(lo + (hi - lo) * min(sec / total_duration, 1.0))
        if ctx and ctx.cancelled:
            proc.terminate()
    proc.wait()
    t.join(timeout=2)
    for pipe in (proc.stdout, proc.stderr):      # close pipes explicitly (avoids leaked handles / ResourceWarning)
        try:
            pipe and pipe.close()
        except Exception:
            pass
    if ctx:
        ctx.check()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + ("".join(err_chunks)[-1500:] or f"exit {proc.returncode}"))
    return "".join(err_chunks)


def make_thumb(path: str | Path, kind: str, mid: str, duration: float = 0) -> Path:
    out = THUMB_DIR / f"{mid}.jpg"
    if out.exists():
        return out
    if kind == "audio":
        # simple waveform picture
        args = ["-i", str(path), "-filter_complex", "showwavespic=s=480x270:colors=#7c5cff", "-frames:v", "1", str(out)]
    elif kind == "image":
        args = ["-i", str(path), "-vf", "scale=480:-2", "-frames:v", "1", "-q:v", "4", str(out)]
    else:
        ss = min(1.0, duration / 3) if duration else 0
        args = ["-ss", f"{ss:.2f}", "-i", str(path), "-vf", "scale=480:-2", "-frames:v", "1", "-q:v", "4", str(out)]
    try:
        run_ffmpeg(args)
    except Exception:
        pass
    return out


def waveform_peaks(path: str | Path, mid: str, buckets: int = 800) -> list[float]:
    """Downsampled peak envelope (0..1) for drawing audio clips on the timeline. Cached on disk."""
    cache = CACHE_DIR / f"{mid}.peaks.json"
    if cache.exists():
        return json.loads(cache.read_text())
    import array
    proc = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                          capture_output=True, **_NOWIN)
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if not samples:
        return []
    step = max(1, len(samples) // buckets)
    peaks = [max(abs(x) for x in samples[i:i + step]) / 32768 for i in range(0, len(samples), step)]
    cache.write_text(json.dumps([round(p, 3) for p in peaks]))
    return peaks
