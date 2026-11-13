"""Debug overlay video: detected 2D keypoints (green = confident, red = low confidence), the re-projected 3D fit
(orange) and two small inset views of the fitted 3D pose (side + top) with foot-contact markers."""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from ...config import FFMPEG
from ...ffutil import _NOWIN, has_nvenc
from .skeletons import draw_edges


def _inset(img, x0, y0, size, pts, edges, contacts, feet, axes, title):
    """Draw a small orthographic view; pts (J,3) in the canonical frame (x=left, y=up, z=forward)."""
    cv2.rectangle(img, (x0, y0), (x0 + size, y0 + size), (30, 30, 30), -1)
    cv2.rectangle(img, (x0, y0), (x0 + size, y0 + size), (90, 90, 90), 1)
    h_axis, v_axis, h_sign, v_sign = axes
    h = pts[:, h_axis] * h_sign
    v = pts[:, v_axis] * v_sign
    ext = 1.3                                              # metres half-range, adaptive
    ext = max(ext, float(np.abs(h).max()) * 1.1 + 0.1, float(np.abs(v).max()) * 1.1 + 0.1) if len(pts) else ext
    sc = (size * 0.45) / ext
    cx, cy = x0 + size / 2, y0 + size / 2
    P = np.stack([cx + sc * (h - h.mean()), cy - sc * (v - (v.min() + v.max()) / 2)], -1)
    for a, b in edges:
        cv2.line(img, tuple(int(c) for c in P[a]), tuple(int(c) for c in P[b]), (200, 200, 200), 1, cv2.LINE_AA)
    for j in range(len(P)):
        cv2.circle(img, tuple(int(c) for c in P[j]), 2, (80, 200, 255), -1, cv2.LINE_AA)
    if contacts is not None:
        for fi, j in enumerate(feet):
            if contacts[fi] > 0.5:
                cv2.circle(img, tuple(int(c) for c in P[j]), 5, (60, 220, 60), 1, cv2.LINE_AA)
    cv2.putText(img, title, (x0 + 4, y0 + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1, cv2.LINE_AA)


def render_overlay(frameset, out_path: Path, skeleton: str, kp: np.ndarray, conf: np.ndarray,
                   boxes: np.ndarray | None = None, kp_proj: np.ndarray | None = None,
                   local3d: np.ndarray | None = None, contacts: np.ndarray | None = None,
                   feet: list[int] | None = None, thr: float = 0.15, ctx=None) -> Path:
    edges = draw_edges(skeleton)
    W, H = frameset.width, frameset.height
    enc = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"] if has_nvenc() else ["-c:v", "libx264", "-crf", "22", "-preset", "veryfast"]
    cmd = [FFMPEG, "-hide_banner", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", f"{frameset.fps}", "-i", "-", *enc, "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, **_NOWIN)
    if ctx:
        ctx.register_process(proc)
    ins = max(120, min(W, H) // 4)
    try:
        for i in range(frameset.n):
            img = frameset.read(i)
            if boxes is not None:
                b = boxes[i].astype(int)
                cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), (160, 160, 160), 1)
            for a, b in edges:
                ok = conf[i, a] >= thr and conf[i, b] >= thr
                cv2.line(img, tuple(int(c) for c in kp[i, a]), tuple(int(c) for c in kp[i, b]),
                         (60, 220, 60) if ok else (70, 70, 230), 2 if ok else 1, cv2.LINE_AA)
            for j in range(kp.shape[1]):
                col = (60, 220, 60) if conf[i, j] >= thr else (70, 70, 230)
                cv2.circle(img, tuple(int(c) for c in kp[i, j]), 4, col, -1, cv2.LINE_AA)
            if kp_proj is not None:
                for a, b in edges:
                    cv2.line(img, tuple(int(c) for c in kp_proj[i, a]), tuple(int(c) for c in kp_proj[i, b]), (0, 150, 255), 1, cv2.LINE_AA)
                for j in range(kp_proj.shape[1]):
                    cv2.circle(img, tuple(int(c) for c in kp_proj[i, j]), 2, (0, 150, 255), -1, cv2.LINE_AA)
            if local3d is not None:
                ct = contacts[i] if contacts is not None else None
                _inset(img, W - ins - 6, 6, ins, local3d[i], edges, ct, feet or [], (2, 1, 1, 1), "side (fwd right)")
                _inset(img, W - ins - 6, ins + 12, ins, local3d[i], edges, ct, feet or [], (2, 0, 1, 1), "top (fwd up)")
            cv2.putText(img, f"{i + 1}/{frameset.n}  t={frameset.start + i / frameset.fps:.2f}s", (8, H - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            proc.stdin.write(np.ascontiguousarray(img).tobytes())
            if ctx and i % 10 == 0:
                ctx.progress(0.9 + 0.08 * i / max(frameset.n, 1), "Rendering debug overlay")
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
    except Exception:
        proc.kill()
        raise
    if proc.returncode != 0:
        raise RuntimeError("overlay encode failed: " + err[-600:])
    return out_path
