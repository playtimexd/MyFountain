"""Super-resolution for images and video.
Engines (auto-detected, best first): realesrgan-ncnn-vulkan binary -> python `realesrgan` (+ local weights) -> ffmpeg lanczos+unsharp.
A missing model never raises: we fall back and say so in job.message / result.engine."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .. import config
from ..ffutil import _NOWIN, has_nvenc, probe, run_ffmpeg
from ..ingest import ingest_file
from ..jobs import JobContext

MAX_OUT_PIXELS = 7680 * 4320


def find_ncnn() -> Path | None:
    cands = list(config.MODELS_DIR.glob("**/realesrgan-ncnn-vulkan.exe")) + list(config.MODELS_DIR.glob("**/realesrgan-ncnn-vulkan"))
    if cands:
        return cands[0]
    w = shutil.which("realesrgan-ncnn-vulkan")
    return Path(w) if w else None


def find_py_weights() -> Path | None:
    for pat in ("**/RealESRGAN_x4plus*.pth", "**/realesr-general*.pth", "**/RealESRGAN_x2plus*.pth"):
        r = list(config.MODELS_DIR.glob(pat))
        if r:
            return r[0]
    return None


def engines() -> dict:
    import importlib.util as u
    py = u.find_spec("realesrgan") is not None
    return {"ncnn": str(find_ncnn()) if find_ncnn() else None, "python": py and find_py_weights() is not None,
            "python_pkg": py, "lanczos": True}


def choose_engine(model: str | None) -> str:
    model = model or "auto"
    e = engines()
    if model == "lanczos":
        return "lanczos"
    if e["ncnn"]:
        return "ncnn"
    if e["python"]:
        return "python"
    return "lanczos"


def _ncnn_model(exe: Path, scale: int, is_video: bool) -> tuple[str, int]:
    """Pick (model name, native scale) from the models folder next to the binary."""
    mdir = exe.parent / "models"
    names = {p.stem for p in mdir.glob("*.param")} if mdir.exists() else set()
    if is_video and f"realesr-animevideov3-x{scale}" in names:
        return f"realesr-animevideov3-x{scale}", scale
    if "realesrgan-x4plus" in names:
        return "realesrgan-x4plus", 4
    for n in sorted(names):
        if n.endswith("-x4"):
            return n, 4
    return (sorted(names)[0], 4) if names else ("realesrgan-x4plus", 4)


def _lanczos_filter(scale: int, denoise: bool | float | None) -> str:
    f = []
    if denoise:
        f.append("hqdn3d=2:1.5:3:3" if denoise is True or float(denoise) > 0 else "")
    f.append(f"scale=iw*{scale}:ih*{scale}:flags=lanczos")
    f.append("unsharp=5:5:0.9:5:5:0.0")
    return ",".join(x for x in f if x)


def _watch_count(dir_: Path, total: int, ctx: JobContext, lo: float, hi: float, stop: threading.Event, label: str):
    while not stop.is_set():
        try:
            n = sum(1 for _ in dir_.glob("*.png"))
            ctx.job.progress = lo + (hi - lo) * min(1.0, n / max(total, 1))
            ctx.job.message = f"{label} {n}/{total}"
        except Exception:
            pass
        stop.wait(0.7)


def upscale(ctx: JobContext, media: dict, scale: int, model: str | None, denoise: bool | float | None) -> dict:
    src = Path(media["path"])
    if scale not in (2, 3, 4):
        scale = 2
    w, h = media.get("width") or 0, media.get("height") or 0
    if w and h and w * h * scale * scale > MAX_OUT_PIXELS:
        raise RuntimeError(f"Output resolution too large ({w * scale}x{h * scale}); use a smaller scale")
    engine = choose_engine(model)
    note = ""
    if (model or "auto") == "realesrgan" and engine == "lanczos":
        note = "Real-ESRGAN not found; fell back to Lanczos + sharpening."
    elif engine == "lanczos" and (model or "auto") == "auto":
        note = "Using Lanczos + sharpening (install realesrgan-ncnn-vulkan into data/models/realesrgan to enable AI upscaling)."
    ctx.progress(0.02, note or f"Engine: {engine}")
    uid = uuid.uuid4().hex[:6]
    is_video = media["kind"] == "video"
    ext = ".mp4" if is_video else ".png"
    out = config.MEDIA_DIR / f"{src.stem}_x{scale}_{uid}{ext}"
    if engine == "ncnn":
        try:
            _ncnn(ctx, media, src, out, scale, is_video, denoise)
        except Exception as e:  # noqa: BLE001
            if ctx.cancelled:
                raise
            note = f"Real-ESRGAN failed ({str(e)[:120]}); fell back to Lanczos."
            engine = "lanczos"
            ctx.progress(0.05, note)
    elif engine == "python":
        try:
            _python_esrgan(ctx, media, src, out, scale, is_video)
        except Exception as e:  # noqa: BLE001
            if ctx.cancelled:
                raise
            note = f"python realesrgan failed ({str(e)[:120]}); fell back to Lanczos."
            engine = "lanczos"
    if engine == "lanczos":
        vf = _lanczos_filter(scale, denoise)
        if is_video:
            enc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "18"] if has_nvenc() else ["-c:v", "libx264", "-crf", "17", "-preset", "medium"]
            run_ffmpeg(["-i", str(src), "-vf", vf, *enc, "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)],
                       total_duration=media.get("duration") or None, ctx=ctx, progress_scale=(0.05, 0.97))
        else:
            run_ffmpeg(["-i", str(src), "-vf", vf, "-frames:v", "1", str(out)], ctx=ctx)
    row = ingest_file(out, origin="ai", name=f"{media['name']} x{scale}",
                      tags=["AI upscale", f"x{scale}"], meta={"upscale": {"engine": engine, "scale": scale, "source": media["id"]}})
    return {"media": row, "engine": engine, "note": note}


def _ncnn(ctx, media, src: Path, out: Path, scale: int, is_video: bool, denoise) -> None:
    exe = find_ncnn()
    assert exe
    name, mscale = _ncnn_model(exe, scale, is_video)
    args_base = [str(exe), "-n", name, "-s", str(mscale), "-m", str(exe.parent / "models"), "-f", "png"]
    if not is_video:
        r = subprocess.run([*args_base, "-i", str(src), "-o", str(out)], capture_output=True, text=True, **_NOWIN)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(r.stderr[-300:])
        if mscale != scale:  # model scale differs from requested -> resize
            tmp = out.with_suffix(".tmp.png")
            out.replace(tmp)
            run_ffmpeg(["-i", str(tmp), "-vf", f"scale=iw*{scale}/{mscale}:-2:flags=lanczos", str(out)])
            tmp.unlink(missing_ok=True)
        return
    work = config.CACHE_DIR / f"up_{uuid.uuid4().hex[:8]}"
    fin, fout = work / "in", work / "out"
    fin.mkdir(parents=True)
    fout.mkdir()
    try:
        fps = media.get("fps") or 25
        total = max(1, int((media.get("duration") or 1) * fps))
        run_ffmpeg(["-i", str(src), "-fps_mode", "passthrough", str(fin / "%08d.png")], total_duration=media.get("duration"), ctx=ctx, progress_scale=(0.03, 0.2))
        total = sum(1 for _ in fin.glob("*.png"))
        stop = threading.Event()
        th = threading.Thread(target=_watch_count, args=(fout, total, ctx, 0.2, 0.85, stop, "Real-ESRGAN"), daemon=True)
        th.start()
        proc = subprocess.Popen([*args_base, "-i", str(fin), "-o", str(fout)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, **_NOWIN)
        ctx.register_process(proc)
        while proc.poll() is None:
            time.sleep(0.4)
        stop.set()
        ctx.check()
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr.read() if proc.stderr else "")[-300:])
        post = [] if mscale == scale else ["-vf", f"scale=iw*{scale}/{mscale}:-2:flags=lanczos"]
        enc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "18"] if has_nvenc() else ["-c:v", "libx264", "-crf", "17"]
        run_ffmpeg(["-framerate", str(fps), "-i", str(fout / "%08d.png"), "-i", str(src), "-map", "0:v", "-map", "1:a?", *post, *enc,
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out)],
                   total_duration=media.get("duration"), ctx=ctx, progress_scale=(0.85, 0.99))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _python_esrgan(ctx, media, src: Path, out: Path, scale: int, is_video: bool) -> None:
    import cv2
    import numpy as np
    import torch
    from basicsr.archs.rrdbnet_arch import RRDBNet  # type: ignore
    from realesrgan import RealESRGANer  # type: ignore

    wp = find_py_weights()
    assert wp
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
    up = RealESRGANer(scale=4, model_path=str(wp), model=model, tile=400, half=torch.cuda.is_available())
    if not is_video:
        img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
        res, _ = up.enhance(img, outscale=scale)
        cv2.imwrite(str(out), res)
        return
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    ow, oh = int(cap.get(3)) * scale, int(cap.get(4)) * scale
    enc = ["-c:v", "libx264", "-crf", "17"]
    cmd = [config.FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{ow}x{oh}", "-r", str(fps), "-i", "-",
           "-i", str(src), "-map", "0:v", "-map", "1:a?", *enc, "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, **_NOWIN)
    ctx.register_process(p)
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        res, _ = up.enhance(fr, outscale=scale)
        p.stdin.write(np.ascontiguousarray(res).tobytes())  # type: ignore[union-attr]
        i += 1
        ctx.progress(0.05 + 0.9 * i / n, f"Real-ESRGAN {i}/{n}")
    p.stdin.close()  # type: ignore[union-attr]
    p.wait()
