"""Evaluation metrics for fitted motion (used by tests and by the job's quality stats)."""
from __future__ import annotations

import numpy as np


def procrustes(src: np.ndarray, dst: np.ndarray, allow_scale: bool = True, allow_reflection: bool = False):
    """Least-squares similarity transform mapping src (N,3) onto dst (N,3). Returns (aligned_src, R, s, t)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    H = a.T @ b
    U, S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1, 1, d if not allow_reflection else 1])
    R = Vt.T @ D @ U.T
    s = (S * np.diag(D)).sum() / max((a ** 2).sum(), 1e-12) if allow_scale else 1.0
    t = mu_d - s * (R @ mu_s)
    return (s * (R @ src.T)).T + t, R, s, t


def mpjpe(pred: np.ndarray, gt: np.ndarray, root_idx=None) -> float:
    """Mean per-joint position error after removing the per-frame root (mean of all joints if root_idx None)."""
    if root_idx is None:
        p = pred - pred.mean(1, keepdims=True)
        g = gt - gt.mean(1, keepdims=True)
    else:
        p = pred - pred[:, root_idx:root_idx + 1]
        g = gt - gt[:, root_idx:root_idx + 1]
    return float(np.linalg.norm(p - g, axis=-1).mean())


def pa_mpjpe_seq(pred: np.ndarray, gt: np.ndarray) -> float:
    """Sequence-level Procrustes (one similarity transform for the whole clip, no reflection)."""
    T, J, _ = pred.shape
    al, *_ = procrustes(pred.reshape(-1, 3), gt.reshape(-1, 3))
    return float(np.linalg.norm(al.reshape(T, J, 3) - gt, axis=-1).mean())


def pa_mpjpe_frame(pred: np.ndarray, gt: np.ndarray) -> float:
    """Frame-wise Procrustes (the usual PA-MPJPE of the HMR literature; forgiving of global rotation)."""
    errs = []
    for t in range(len(pred)):
        al, *_ = procrustes(pred[t], gt[t])
        errs.append(np.linalg.norm(al - gt[t], axis=-1).mean())
    return float(np.mean(errs))


def bone_lengths(j3d: np.ndarray, edges: list[tuple[int, int]]) -> np.ndarray:
    return np.stack([np.linalg.norm(j3d[:, a] - j3d[:, b], axis=-1) for a, b in edges], 1)   # (T,E)


def bone_length_error(pred: np.ndarray, gt: np.ndarray, edges) -> dict:
    bp, bg = bone_lengths(pred, edges), bone_lengths(gt, edges)
    rel = np.abs(bp.mean(0) - bg.mean(0)) / np.maximum(bg.mean(0), 1e-6)
    cv = bp.std(0) / np.maximum(bp.mean(0), 1e-6)
    return {"mean_rel_err": float(rel.mean()), "max_rel_err": float(rel.max()), "mean_cv_over_time": float(cv.mean())}


def foot_sliding(j3d: np.ndarray, feet: list[int], contacts: np.ndarray, fps: float) -> float:
    """Mean horizontal foot speed (m/s) over frames flagged as in contact."""
    v = np.linalg.norm((j3d[1:, feet] - j3d[:-1, feet])[..., [0, 2]], axis=-1) * fps     # (T-1,F)
    m = contacts[1:] & contacts[:-1]
    return float(v[m].mean()) if m.any() else float("nan")


def jitter(j3d: np.ndarray, fps: float, size: float) -> float:
    """Mean jerk-like roughness: |third difference| * fps^3 / size  (m/s^3 per metre of body size)."""
    if len(j3d) < 5:
        return 0.0
    d3 = j3d[3:] - 3 * j3d[2:-1] + 3 * j3d[1:-2] - j3d[:-3]
    return float(np.linalg.norm(d3, axis=-1).mean() * fps ** 3 / size)


def limb_phase_error(pred: np.ndarray, gt: np.ndarray, a: int, b: int) -> float:
    """Gait phase check: correlation of the *difference signal* x_a - x_b (fore/aft position of two feet, in the
    body direction) between prediction and ground truth; 1 = same alternation, -1 = swapped legs."""
    pa = pred[:, a, 0] - pred[:, b, 0]
    ga = gt[:, a, 0] - gt[:, b, 0]
    pa, ga = pa - pa.mean(), ga - ga.mean()
    den = np.linalg.norm(pa) * np.linalg.norm(ga)
    return float((pa * ga).sum() / den) if den > 1e-9 else 0.0
