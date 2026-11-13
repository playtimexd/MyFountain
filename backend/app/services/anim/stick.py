"""Cheap preview for source motions (no rig needed): a side-view stick figure rendered with PIL and encoded by ffmpeg."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ... import config
from . import skeletons as sk

_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def load_motion(path: str | Path) -> dict:
    d = np.load(path, allow_pickle=False)
    skel = str(d["skeleton"]) if "skeleton" in d.files else "custom"
    names = [str(x) for x in d["joint_names"]]
    P = np.asarray(d["joints3d"], float)
    parents = [int(x) for x in d["parents"]] if "parents" in d.files else None
    names, P, _ = sk.augment(skel, names, P)
    if parents is None or len(parents) != len(names):
        parents = sk.builtin_parents(skel, names) or sk.infer_parents(names, P)
    root = np.asarray(d["root"], float) if "root" in d.files else P[:, 0]
    return {"fps": float(d["fps"]), "skeleton": skel, "names": names, "P": P, "parents": parents, "root": root}


def render(npz: str | Path, out_mp4: str | Path, size: tuple[int, int] = (384, 288), max_frames: int = 240) -> Path:
    m = load_motion(npz)
    P, parents, fps = m["P"], m["parents"], m["fps"]
    T = P.shape[0]
    step = max(1, int(np.ceil(T / max_frames)))
    P = P[::step]
    fps = fps / step
    rel = P - P[:, [next((i for i, p in enumerate(parents) if p < 0), 0)]]
    # side view: horizontal axis with the largest extent (sign: direction of travel)
    xz = rel[:, :, [0, 2]].reshape(-1, 2)
    u, s, vt = np.linalg.svd(xz - xz.mean(0), full_matrices=False)
    ax = vt[0]
    trav = P[-1, 0, [0, 2]] - P[0, 0, [0, 2]]
    if np.dot(trav, ax) < 0:
        ax = -ax
    X = rel[:, :, [0, 2]] @ ax
    Y = P[:, :, 1] - P[:, :, 1].min()
    h = max(float(Y.max()), 1e-3)
    w_, h_ = size
    wext = max(float(X.max() - X.min()), 1e-3)
    scale = min(0.78 * h_ / h, 0.9 * w_ / wext)
    cx, ground = w_ / 2 - float(X.min() + X.max()) / 2 * scale, h_ * 0.92
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [config.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w_}x{h_}", "-r", f"{fps:.3f}",
           "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, **_NOWIN)
    names = m["names"]
    for t in range(P.shape[0]):
        im = Image.new("RGB", (w_, h_), (27, 30, 38))
        dr = ImageDraw.Draw(im)
        dr.line([(0, ground), (w_, ground)], fill=(60, 66, 80), width=2)
        pts = [(cx + X[t, j] * scale, ground - Y[t, j] * scale) for j in range(P.shape[1])]
        for j, p in enumerate(parents):
            if p >= 0:
                col = (124, 92, 255) if "left" in names[j].lower() or names[j].endswith(("L", "_l", ".L")) else (91, 140, 255)
                dr.line([pts[p], pts[j]], fill=col, width=3)
        for (x, y) in pts:
            dr.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(232, 234, 240))
        proc.stdin.write(im.tobytes())  # type: ignore[union-attr]
    proc.stdin.close()  # type: ignore[union-attr]
    err = proc.stderr.read().decode("utf-8", "replace")  # type: ignore[union-attr]
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + err[-400:])
    return out_mp4


def stats(npz: str | Path) -> dict:
    """Numbers describing a motion.npz (no Blender needed)."""
    m = load_motion(npz)
    P, fps, parents = m["P"], m["fps"], m["parents"]
    T = P.shape[0]
    ri = next((i for i, p in enumerate(parents) if p < 0), 0)
    root = P[:, ri]
    d = np.linalg.norm(np.diff(root[:, [0, 2]], axis=0), axis=1) if T > 1 else np.zeros(1)
    travel = float(d.sum())
    dur = (T - 1) / fps if T > 1 else 0.0
    height = float(np.ptp(P[0, :, 1]))
    rel = P - root[:, None, :]
    seam = float(np.linalg.norm(rel[0] - rel[-1], axis=1).mean() / max(height, 1e-6))
    step = float(np.linalg.norm(rel[1] - rel[0], axis=1).mean() / max(height, 1e-6)) if T > 1 else 1.0
    low = int(np.argmin(P[:, :, 1].min(axis=0)))
    sig = P[:, low, 1] - P[:, low, 1].mean()
    cyc = 0.0
    if T > 8 and sig.std() > 1e-5:
        sp = np.abs(np.fft.rfft(sig * np.hanning(T)))
        fr = np.fft.rfftfreq(T, 1 / fps)
        sp[fr < 0.3] = 0
        cyc = float(fr[int(sp.argmax())])
    return {"frames": int(T), "fps": fps, "duration": dur, "joints": len(m["names"]), "height_m": height, "root_travel_m": travel,
            "root_speed_mps": travel / max(dur, 1e-6), "root_speed_heights_per_s": travel / max(dur, 1e-6) / max(height, 1e-6),
            "loop_seam_pct_height": 100 * seam, "loop_seam_vs_step": seam / max(step, 1e-6), "cycle_hz": cyc, "skeleton": m["skeleton"]}
