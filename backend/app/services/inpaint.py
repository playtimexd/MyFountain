"""Watermark removal / detection, still-image inpainting, background removal. OpenCV based, LaMa / rembg when importable."""
from __future__ import annotations

import base64
import importlib.util
import subprocess
import uuid
from pathlib import Path

import cv2
import numpy as np

from .. import config
from ..ffutil import _NOWIN, has_nvenc, run_ffmpeg
from ..ingest import ingest_file
from ..jobs import JobContext


def has_lama() -> bool:
    """LaMa is used only when the package AND big-lama.pt are already cached (we never trigger a download)."""
    import os
    if importlib.util.find_spec("simple_lama_inpainting") is None:
        return False
    ckpt = os.environ.get("LAMA_MODEL") or str(Path(os.environ.get("TORCH_HOME") or (Path.home() / ".cache" / "torch")) / "hub" / "checkpoints" / "big-lama.pt")
    return Path(ckpt).exists()


def _u2net_path() -> Path:
    import os
    return Path(os.environ.get("U2NET_HOME") or (Path.home() / ".u2net")) / "u2net.onnx"


def has_rembg() -> bool:
    """rembg is usable only when the package AND the u2net weights are already on disk (we never trigger a download)."""
    return importlib.util.find_spec("rembg") is not None and _u2net_path().exists()


# ------------------------------------------------------------------ base64 helpers ----------------------

def b64_to_img(s: str, flags=cv2.IMREAD_UNCHANGED) -> np.ndarray:
    if "," in s[:100]:
        s = s.split(",", 1)[1]
    arr = np.frombuffer(base64.b64decode(s), np.uint8)
    img = cv2.imdecode(arr, flags)
    if img is None:
        raise ValueError("Cannot decode image")
    return img


def img_to_b64(img: np.ndarray, ext=".png") -> str:
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise ValueError("encode failed")
    return base64.b64encode(buf.tobytes()).decode()


_LAMA = None
_REMBG = None


def _lama():
    global _LAMA
    if _LAMA is None:
        from simple_lama_inpainting import SimpleLama  # type: ignore
        _LAMA = SimpleLama()
    return _LAMA


# ------------------------------------------------------------------ still-image inpaint ------------------

def inpaint_image(img: np.ndarray, mask: np.ndarray, method: str = "auto", radius: int = 4) -> tuple[np.ndarray, str]:
    """img: BGR or BGRA, mask: uint8 (255 = fill). Returns (img, engine)."""
    alpha = None
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        img = img[:, :, :3]
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if mask.ndim == 3:
        mask = mask[:, :, 3] if mask.shape[2] == 4 and mask[:, :, 3].min() < 255 else cv2.cvtColor(mask[:, :, :3], cv2.COLOR_BGR2GRAY)
    if mask.shape[:2] != img.shape[:2]:
        mask = cv2.resize(mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
    mask = ((mask > 127).astype(np.uint8)) * 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    engine = "telea"
    if method in ("auto", "lama") and has_lama():
        try:
            from PIL import Image
            res = _lama()(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), Image.fromarray(mask))
            out = cv2.cvtColor(np.array(res), cv2.COLOR_RGB2BGR)
            if out.shape[:2] != img.shape[:2]:
                out = out[:img.shape[0], :img.shape[1]]
            engine = "lama"
        except Exception:
            out = cv2.inpaint(img, mask, radius, cv2.INPAINT_TELEA)
    else:
        flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
        engine = "ns" if method == "ns" else "telea"
        out = cv2.inpaint(img, mask, radius, flag)
    if alpha is not None:
        out = np.dstack([out, alpha])
    return out, engine


# ------------------------------------------------------------------ remove background -------------------

def remove_bg(img: np.ndarray) -> tuple[np.ndarray, str]:
    """Returns BGRA image with the background made transparent, engine name."""
    if img.ndim == 3 and img.shape[2] == 4:
        img = img[:, :, :3]
    if has_rembg():
        try:
            from PIL import Image
            from rembg import new_session, remove  # type: ignore
            global _REMBG
            if _REMBG is None:
                _REMBG = new_session("u2net")
            res = remove(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), session=_REMBG)
            arr = np.array(res.convert("RGBA"))
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA), "rembg"
        except Exception:
            pass
    return _grabcut(img), "grabcut"


def _grabcut(img: np.ndarray) -> np.ndarray:
    H, W = img.shape[:2]
    scale = 640 / max(H, W)
    small = cv2.resize(img, (max(2, int(W * scale)), max(2, int(H * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else img.copy()
    h, w = small.shape[:2]
    ring = max(2, int(min(h, w) * 0.03))
    border = np.concatenate([small[:ring].reshape(-1, 3), small[-ring:].reshape(-1, 3),
                             small[:, :ring].reshape(-1, 3), small[:, -ring:].reshape(-1, 3)]).astype(np.float32)
    bg_col = np.median(border, axis=0)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    bg_lab = cv2.cvtColor(np.uint8([[bg_col]]), cv2.COLOR_BGR2LAB).astype(np.float32)[0, 0]
    dist = np.linalg.norm(lab - bg_lab, axis=2)
    dthr = max(18.0, float(np.percentile(dist[: ring * 2], 95)) * 1.2)
    mask = np.full((h, w), cv2.GC_PR_BGD, np.uint8)
    mask[dist > dthr] = cv2.GC_PR_FGD
    # centre ellipse -> probable foreground (only where it differs from the background colour)
    yy, xx = np.ogrid[:h, :w]
    ell = ((xx - w / 2) / (w * 0.32)) ** 2 + ((yy - h / 2) / (h * 0.32)) ** 2 < 1
    mask[ell & (dist > dthr * 0.6)] = cv2.GC_FGD
    mask[:ring] = cv2.GC_BGD; mask[-ring:] = cv2.GC_BGD; mask[:, :ring] = cv2.GC_BGD; mask[:, -ring:] = cv2.GC_BGD
    mask[(dist < dthr * 0.4)] = cv2.GC_BGD
    if not (mask == cv2.GC_FGD).any() and not (mask == cv2.GC_PR_FGD).any():
        mask[h // 4: 3 * h // 4, w // 4: 3 * w // 4] = cv2.GC_PR_FGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small, mask, None, bgd, fgd, 6, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        mask[:] = cv2.GC_PR_BGD
        mask[h // 6: 5 * h // 6, w // 6: 5 * w // 6] = cv2.GC_PR_FGD
        cv2.grabCut(small, mask, (w // 8, h // 8, w * 3 // 4, h * 3 // 4), bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    # keep the biggest blobs, fill holes, smooth edge
    n, lab_cc, stats, _ = cv2.connectedComponentsWithStats(fg)
    if n > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        keep = [i + 1 for i, a in enumerate(areas) if a >= areas.max() * 0.15]
        fg = np.where(np.isin(lab_cc, keep), 255, 0).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(fg)
    cv2.drawContours(filled, cnts, -1, 255, -1)
    alpha = cv2.GaussianBlur(filled, (0, 0), 1.2)
    alpha = cv2.resize(alpha, (W, H), interpolation=cv2.INTER_LINEAR)
    # guided refinement of the edge using the full-res image
    try:
        alpha = cv2.ximgproc.guidedFilter(img, alpha, 8, 100) if hasattr(cv2, "ximgproc") else cv2.bilateralFilter(alpha, 7, 40, 7)
    except Exception:
        pass
    return np.dstack([img, alpha])


# ------------------------------------------------------------------ watermark ---------------------------

def _clamp_region(r: dict, W: int, H: int) -> tuple[int, int, int, int]:
    x, y, w, h = int(round(r["x"])), int(round(r["y"])), int(round(r["w"])), int(round(r["h"]))
    x = max(0, min(W - 2, x)); y = max(0, min(H - 2, y))
    w = max(2, min(W - x, w)); h = max(2, min(H - y, h))
    return x, y, w, h


def _region_mask(regions: list[dict], W: int, H: int, grow: int = 3) -> np.ndarray:
    m = np.zeros((H, W), np.uint8)
    for r in regions:
        x, y, w, h = _clamp_region(r, W, H)
        m[y:y + h, x:x + w] = 255
    if grow:
        m = cv2.dilate(m, np.ones((grow * 2 + 1, grow * 2 + 1), np.uint8))
    return m


def watermark(ctx: JobContext, media: dict, regions: list[dict], method: str, start: float | None, end: float | None) -> dict:
    src = Path(media["path"])
    W, H = media["width"], media["height"]
    if not regions:
        raise ValueError("Select the watermark region first")
    is_video = media["kind"] == "video"
    uid = uuid.uuid4().hex[:6]
    out = config.MEDIA_DIR / f"{src.stem}_clean_{uid}{'.mp4' if is_video else '.png'}"
    method = method or "auto"
    if method == "auto":
        method = "inpaint"
    note = ""
    engine = method
    if not is_video:
        img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise RuntimeError("Cannot read image")
        if method == "delogo":
            run_ffmpeg(["-i", str(src), "-vf", ",".join(_delogo(r, W, H, None, None) for r in regions), "-frames:v", "1", str(out)], ctx=ctx)
        else:
            res, engine = inpaint_image(img, _region_mask(regions, img.shape[1], img.shape[0]), "auto")
            cv2.imwrite(str(out), res)
    elif method == "delogo":
        vf = ",".join(_delogo(r, W, H, start, end) for r in regions)
        enc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19"] if has_nvenc() else ["-c:v", "libx264", "-crf", "18"]
        run_ffmpeg(["-i", str(src), "-vf", vf, *enc, "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)],
                   total_duration=media.get("duration") or None, ctx=ctx, progress_scale=(0.03, 0.98))
    else:
        engine = _inpaint_video(ctx, media, src, out, regions, start, end)
    row = ingest_file(out, origin="ai", name=f"{media['name']} (clean)", tags=["AI watermark removal"], meta={"watermark": {"engine": engine, "regions": regions}})
    return {"media": row, "engine": engine, "note": note}


def _delogo(r: dict, W: int, H: int, start, end) -> str:
    x, y, w, h = _clamp_region(r, W, H)
    x = max(1, x); y = max(1, y)
    w = max(2, min(w, W - x - 1)); h = max(2, min(h, H - y - 1))
    f = f"delogo=x={x}:y={y}:w={w}:h={h}"
    if start is not None or end is not None:
        s, e = (start or 0), (end if end is not None else 1e9)
        f += f":enable='between(t,{s},{e})'"
    return f


def _inpaint_video(ctx: JobContext, media: dict, src: Path, out: Path, regions: list[dict], start, end) -> str:
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError("Cannot open video")
    fps = cap.get(cv2.CAP_PROP_FPS) or media.get("fps") or 25
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or int((media.get("duration") or 1) * fps)
    ok, fr = cap.read()
    if not ok:
        raise RuntimeError("Failed to read video")
    H, W = fr.shape[:2]
    mask = _region_mask(regions, W, H, grow=3)
    ys, xs = np.where(mask > 0)
    pad = 24
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + 1 + pad)
    mroi = mask[y0:y1, x0:x1]
    use_lama = has_lama() and (y1 - y0) * (x1 - x0) < 400 * 400
    enc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19"] if has_nvenc() else ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]
    cmd = [config.FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-",
           "-i", str(src), "-map", "0:v", "-map", "1:a?", *enc, "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest",
           "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, **_NOWIN)
    ctx.register_process(proc)
    i = 0
    engine = "lama" if use_lama else "telea"
    try:
        while ok:
            t = i / fps
            if (start is None or t >= start) and (end is None or t <= end):
                roi = fr[y0:y1, x0:x1]
                if use_lama:
                    try:
                        from PIL import Image
                        res = _lama()(Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)), Image.fromarray(mroi))
                        res = cv2.cvtColor(np.array(res), cv2.COLOR_RGB2BGR)[: roi.shape[0], : roi.shape[1]]
                        fr[y0:y1, x0:x1] = res
                    except Exception:
                        fr[y0:y1, x0:x1] = cv2.inpaint(roi, mroi, 3, cv2.INPAINT_TELEA)
                else:
                    fr[y0:y1, x0:x1] = cv2.inpaint(roi, mroi, 3, cv2.INPAINT_TELEA)
            proc.stdin.write(fr.tobytes())  # type: ignore[union-attr]
            i += 1
            if i % 5 == 0:
                ctx.progress(0.02 + 0.95 * min(1.0, i / max(n, 1)), f"Inpainting frame {i}/{n}")
            ok, fr = cap.read()
        proc.stdin.close()  # type: ignore[union-attr]
        proc.wait()
        ctx.check()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg: " + proc.stderr.read().decode("utf-8", "replace")[-400:])  # type: ignore[union-attr]
    finally:
        cap.release()
        if proc.poll() is None:
            proc.kill()
    return engine


# ------------------------------------------------------------------ watermark detection ------------------

def read_frames(path: Path, n: int, duration: float, max_side: int = 480) -> tuple[list[np.ndarray], float]:
    """Sample n frames evenly; returns grayscale float32 frames and scale factor (small/orig)."""
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    frames: list[np.ndarray] = []
    scale = 1.0
    if total > 1:
        idxs = np.linspace(total * 0.03, total * 0.97, n).astype(int)
        for ix in idxs:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(ix))
            ok, fr = cap.read()
            if not ok:
                continue
            if not frames:
                scale = min(1.0, max_side / max(fr.shape[:2]))
            frames.append(cv2.cvtColor(cv2.resize(fr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32))
    cap.release()
    return frames, scale


def detect_watermark(media: dict, samples: int = 24) -> dict:
    """Propose static watermark rectangles: pixels that stay (almost) constant while the rest of the video changes AND
    carry high local contrast (text / logo edges). Boxes are in source-pixel coordinates."""
    if media["kind"] == "image":
        return _detect_image(media)
    frames, sc = read_frames(Path(media["path"]), samples, media.get("duration") or 0)
    if len(frames) < 6:
        return {"regions": [], "message": "Video too short to analyse"}
    stack = np.stack(frames)
    std = stack.std(axis=0)
    mean = stack.mean(axis=0)
    # edge strength of the temporal mean image
    gx = cv2.Sobel(mean, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(mean, cv2.CV_32F, 0, 1, ksize=3)
    edge = cv2.GaussianBlur(np.sqrt(gx ** 2 + gy ** 2), (0, 0), 1.0)
    global_motion = float(np.median(std))
    if global_motion < 3.0:
        return {"regions": [], "message": "The picture is almost static (temporal variance too low) so the watermark cannot be told apart from the background; select it manually.", "motion": round(global_motion, 2)}
    static = std < max(2.5, global_motion * 0.25)
    # per-frame persistent edges: edges present in most frames at the same place
    edge_cnt = np.zeros_like(mean)
    for f in frames:
        g1 = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3); g2 = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=3)
        edge_cnt += (np.sqrt(g1 ** 2 + g2 ** 2) > 60)
    persistent = edge_cnt >= len(frames) * 0.85
    cand = (static & (persistent | (edge > 55))).astype(np.uint8) * 255
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    cand = cv2.dilate(cand, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 5)), iterations=2)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand)
    h, w = mean.shape
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        frac = (bw * bh) / (w * h)
        if frac < 0.0008 or frac > 0.2 or bw < 8 or bh < 6:
            continue
        fill = area / max(1, bw * bh)
        if fill < 0.25:
            continue
        cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
        corner = max(abs(cx - 0.5), abs(cy - 0.5)) * 2  # 0 centre .. 1 edge
        contrast = float(edge[y:y + bh, x:x + bw].mean())
        score = (0.5 + 0.5 * corner) * min(1.0, contrast / 80.0) * min(1.0, fill * 1.6)
        pad = 3
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
        out.append({"x": int(x0 / sc), "y": int(y0 / sc), "w": int((x1 - x0) / sc), "h": int((y1 - y0) / sc), "score": round(float(score), 3)})
    out.sort(key=lambda r: -r["score"])
    out = [r for r in out if r["score"] > 0.12][:4]
    return {"regions": out, "motion": round(global_motion, 2),
            "message": f"Found {len(out)} likely static watermark region(s)" if out else "No obvious static watermark found; select it manually."}


def _detect_image(media: dict) -> dict:
    img = cv2.imread(media["path"], cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"regions": [], "message": "Cannot read image"}
    H, W = img.shape
    # look for high-contrast text-like blobs hugging a corner
    sc = min(1.0, 800 / max(H, W))
    g = cv2.resize(img, None, fx=sc, fy=sc)
    grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3)))
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = g.shape
    out = []
    for c in cnts:
        x, y, bw_, bh = cv2.boundingRect(c)
        near_edge = x < w * 0.25 or x + bw_ > w * 0.75
        near_edge = near_edge and (y < h * 0.2 or y + bh > h * 0.8)
        if near_edge and 0.0005 < bw_ * bh / (w * h) < 0.08 and bw_ > bh * 1.5:
            out.append({"x": int(x / sc), "y": int(y / sc), "w": int(bw_ / sc), "h": int(bh / sc), "score": 0.3})
    return {"regions": out[:3], "message": "Still images are only checked with a corner-text heuristic; please verify manually."}
