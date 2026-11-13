"""motion.npz  - the interchange format between mocap (this package) and anim-core (Blender retargeting).

Keys (see docs/ANIMATION.md section 3 and docs/MOCAP.md):
  fps (float)  skeleton (str)  joint_names (J,) str  joints3d (T,J,3) m, Y up  conf (T,J)  root (T,3)
  [rot (T,J,4) quaternion, optional]  meta (JSON string)
Extra (additive, safe to ignore): joints3d_local (T,J,3) pelvis-relative & facing +Z, contacts (T,F),
contact_joints (F,) str, heading (T,), interpolated (T,J) bool.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def save_motion(path: str | Path, *, fps: float, skeleton: str, joint_names: list[str], joints3d: np.ndarray,
                conf: np.ndarray, root: np.ndarray, meta: dict, rot: np.ndarray | None = None,
                extras: dict | None = None) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    T, J, _ = joints3d.shape
    assert conf.shape == (T, J) and root.shape == (T, 3) and len(joint_names) == J
    data = dict(fps=np.float64(fps), skeleton=np.array(skeleton), joint_names=np.array(joint_names, dtype=str),
                joints3d=joints3d.astype(np.float32), conf=conf.astype(np.float32), root=root.astype(np.float32),
                meta=np.array(json.dumps(meta, ensure_ascii=False, default=_jsonable)))
    if rot is not None:
        data["rot"] = rot.astype(np.float32)
    for k, v in (extras or {}).items():
        data[k] = np.asarray(v)
    np.savez_compressed(p, **data)
    return p


def _jsonable(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def load_motion(path: str | Path) -> dict:
    z = np.load(path, allow_pickle=False)
    out = {k: z[k] for k in z.files}
    out["fps"] = float(out["fps"])
    out["skeleton"] = str(out["skeleton"])
    out["joint_names"] = [str(x) for x in out["joint_names"]]
    out["meta"] = json.loads(str(out["meta"]))
    return out


def validate_motion(m: dict) -> list[str]:
    """Return a list of problems (empty = valid)."""
    errs = []
    for k in ("fps", "skeleton", "joint_names", "joints3d", "conf", "root", "meta"):
        if k not in m:
            errs.append(f"missing key {k}")
    if errs:
        return errs
    T, J, _ = m["joints3d"].shape
    if m["conf"].shape != (T, J):
        errs.append("conf shape")
    if m["root"].shape != (T, 3):
        errs.append("root shape")
    if len(m["joint_names"]) != J:
        errs.append("joint_names length")
    if not np.isfinite(m["joints3d"]).all():
        errs.append("non-finite joints3d")
    if m["fps"] <= 0:
        errs.append("fps")
    return errs
