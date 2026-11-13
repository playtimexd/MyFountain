"""Stage (e): gait-cycle detection, loop points and quality metrics."""
from __future__ import annotations

import numpy as np

from . import metrics


def _autocorr(x: np.ndarray) -> np.ndarray:
    """x (T,F) -> mean normalised autocorrelation over F for lags 0..T-1."""
    x = x - x.mean(0, keepdims=True)
    T = len(x)
    ac = np.zeros(T)
    for f in range(x.shape[1]):
        c = np.correlate(x[:, f], x[:, f], mode="full")[T - 1:]
        c = c / max(c[0], 1e-12)
        ac += c
    return ac / x.shape[1]


def detect_gait(local: np.ndarray, feet: list[int], fps: float, size: float, min_period_s: float = 0.25) -> dict:
    """local: (T,J,3) pelvis-relative joints in the canonical frame (forward = +Z, up = +Y).
    Returns period_s, cycles, confidence and a suggested seamless loop [loop_start, loop_end] (frames)."""
    T = len(local)
    if T < int(fps * 0.8):
        return {"period_s": None, "cycles": 0.0, "confidence": 0.0, "loop_start": None, "loop_end": None,
                "loop_cycles": 0, "loop_error": None}
    sig = local[:, feet, 2]                                    # fore/aft foot position relative to the pelvis
    sig = np.concatenate([sig, local[:, feet, 1]], 1) if local.shape[1] > 0 else sig
    sig = sig / max(size, 1e-6)
    ac = _autocorr(sig)
    lo = max(2, int(min_period_s * fps))
    hi = T // 2
    # first zero crossing, then the strongest peak after it
    neg = np.where(ac[1:hi] < 0)[0]
    start = int(neg[0]) + 1 if len(neg) else lo
    start = max(start, lo)
    empty = {"period_s": None, "cycles": 0.0, "confidence": 0.0, "loop_start": None, "loop_end": None,
             "loop_cycles": 0, "loop_error": None}
    peaks = [l for l in range(max(start, 1), min(hi, len(ac) - 1))
             if ac[l] >= ac[l - 1] and ac[l] >= ac[l + 1] and ac[l] > 0.2]
    if not peaks:
        return empty
    mx = max(ac[l] for l in peaks)
    best_lag = next(l for l in peaks if ac[l] >= 0.8 * mx)    # first strong peak (not a multiple of the period)
    best = float(ac[best_lag])
    # parabolic refinement
    l = best_lag
    if 1 <= l < len(ac) - 1:
        a, b, c = ac[l - 1], ac[l], ac[l + 1]
        den = a - 2 * b + c
        off = 0.5 * (a - c) / den if abs(den) > 1e-9 else 0.0
        period = (l + float(np.clip(off, -1, 1))) / fps
    else:
        period = l / fps
    P = period * fps
    loop = _find_loop(local, P, size)
    return {"period_s": float(period), "cycles": float(T / fps / period), "confidence": float(np.clip(best, 0, 1)),
            **loop}


def _find_loop(local: np.ndarray, P: float, size: float) -> dict:
    T = len(local)
    best = None
    for s in range(0, int(np.ceil(P))):
        kmax = int((T - 1 - s) // P)
        for k in range(kmax, 0, -1):
            e = int(round(s + k * P))
            if e >= T:
                continue
            cost = np.linalg.norm(local[s] - local[e], axis=-1).mean() / size
            if s + 1 < T and e + 1 < T:      # velocity continuity (the loop must also match in motion)
                cost += 0.5 * np.linalg.norm((local[s + 1] - local[s]) - (local[e + 1] - local[e]), axis=-1).mean() / size
            key = (cost < 0.05, k if cost < 0.05 else 0, -cost)
            if best is None or key > best[0]:
                best = (key, s, e, k, cost)
    if best is None:
        return {"loop_start": None, "loop_end": None, "loop_cycles": 0, "loop_error": None}
    _, s, e, k, cost = best
    return {"loop_start": int(s), "loop_end": int(e), "loop_cycles": int(k), "loop_error": float(cost)}


def quality(joints3d: np.ndarray, conf: np.ndarray, edges, fps: float, size: float, reproj_px: float,
            det_mask: np.ndarray | None = None, interpolated: np.ndarray | None = None,
            contacts: np.ndarray | None = None, feet: list[int] | None = None, conf_thresh: float = 0.15) -> dict:
    T = len(joints3d)
    if edges:
        bl = metrics.bone_lengths(joints3d, edges)
        cv = float((bl.std(0) / np.maximum(bl.mean(0), 1e-6)).mean())
    else:
        cv = 0.0
    out = {
        "frames": int(T), "fps": float(fps),
        "mean_conf": float(conf.mean()),
        "jitter": metrics.jitter(joints3d, fps, size),
        "coverage": float((conf >= conf_thresh).mean()),
        "subject_coverage": float(det_mask.mean()) if det_mask is not None else 1.0,
        "bone_length_cv": cv,
        "reproj_px": float(reproj_px),
    }
    if contacts is not None and feet is not None:
        out["foot_slide_mps"] = metrics.foot_sliding(joints3d, feet, contacts > 0.5, fps)
    return out
