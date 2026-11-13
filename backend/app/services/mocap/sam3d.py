"""Optional humanoid backend: Meta SAM 3D Body (single-image human mesh recovery, MHR rig, 70 keypoints).
https://github.com/facebookresearch/sam-3d-body   (checkpoints: facebook/sam-3d-body-dinov3, facebook/sam-3d-body-vith)

STATUS: written from the public README + source (`notebook/utils.py: setup_sam_3d_body`,
`SAM3DBodyEstimator.process_one_image(img, bboxes=...)` -> list of dicts with `pred_keypoints_3d`, `pred_cam_t`, `focal_length`, ...).
It could NOT be run here (weights are gated: manual approval on Hugging Face; ~2.8 GB; separate environment).
The converter `mhr70_to_motion` is unit-tested on synthetic arrays; the worker is untested.

SAM 3D Body is a per-image model: we run it on every extracted frame with OUR tracked subject box as prompt
(skipping its own detector), then smooth the sequence and place it on the ground.  Expect more frame-to-frame jitter and a
noisy global trajectory (depth from `pred_cam_t`) compared to a video model such as GEM.

Layout (override with CUTLAB_SAM3D_HOME):  <home>/repo (git clone) + <home>/repo/.venv + <home>/repo/checkpoints/sam-3d-body-dinov3/
{model.ckpt, assets/mhr_model.pt} (after `hf download facebook/sam-3d-body-dinov3 --local-dir checkpoints/sam-3d-body-dinov3`).
Licence: SAM License (code + checkpoints); check its terms for commercial/game use.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from ...config import MODELS_DIR
from ...ffutil import _NOWIN

SIZE_BYTES = 2_109_129_346 + 696_110_248
SIZE_TEXT = "~2.8 GB (DINOv3-H+ checkpoint 2.1 GB + MHR 0.7 GB; ViT-H variant 2.4 GB); gated - request access on Hugging Face"
LICENSE_NOTE = "SAM License (see the LICENSE file of facebook/sam-3d-body-*). Gated weights, manual approval."
HOWTO = ("1) Request access to facebook/sam-3d-body-dinov3 on Hugging Face and `hf auth login`.  2) git clone "
         "https://github.com/facebookresearch/sam-3d-body into <home>/repo, create its venv and follow INSTALL.md.  "
         "3) hf download facebook/sam-3d-body-dinov3 --local-dir checkpoints/sam-3d-body-dinov3.  "
         "4) set CUTLAB_SAM3D_HOME if the folder is not data/models/mocap/sam-3d-body.")

MHR70 = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow",
         "right_elbow", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
         "left_big_toe", "left_small_toe", "left_heel", "right_big_toe", "right_small_toe", "right_heel",
         "right_thumb4", "right_thumb3", "right_thumb2", "right_thumb_third_joint",
         "right_forefinger4", "right_forefinger3", "right_forefinger2", "right_forefinger_third_joint",
         "right_middle_finger4", "right_middle_finger3", "right_middle_finger2", "right_middle_finger_third_joint",
         "right_ring_finger4", "right_ring_finger3", "right_ring_finger2", "right_ring_finger_third_joint",
         "right_pinky_finger4", "right_pinky_finger3", "right_pinky_finger2", "right_pinky_finger_third_joint",
         "right_wrist",
         "left_thumb4", "left_thumb3", "left_thumb2", "left_thumb_third_joint",
         "left_forefinger4", "left_forefinger3", "left_forefinger2", "left_forefinger_third_joint",
         "left_middle_finger4", "left_middle_finger3", "left_middle_finger2", "left_middle_finger_third_joint",
         "left_ring_finger4", "left_ring_finger3", "left_ring_finger2", "left_ring_finger_third_joint",
         "left_pinky_finger4", "left_pinky_finger3", "left_pinky_finger2", "left_pinky_finger_third_joint",
         "left_wrist", "left_olecranon", "right_olecranon", "left_cubital_fossa", "right_cubital_fossa",
         "left_acromion", "right_acromion", "neck"]
assert len(MHR70) == 70


def home() -> Path:
    return Path(os.environ.get("CUTLAB_SAM3D_HOME") or (MODELS_DIR / "mocap" / "sam-3d-body"))


def _paths():
    repo = home() / "repo"
    scripts = repo / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    py = next((p for p in (scripts / "python.exe", scripts / "python") if p.exists()), None)
    ck = repo / "checkpoints" / "sam-3d-body-dinov3"
    return repo, py, ck


def status() -> dict:
    repo, py, ck = _paths()
    miss = []
    if not repo.exists():
        miss.append("repo clone")
    if py is None:
        miss.append("python env")
    if not (ck / "model.ckpt").exists():
        miss.append("model.ckpt")
    if not (ck / "assets" / "mhr_model.pt").exists():
        miss.append("assets/mhr_model.pt")
    if not miss:
        return {"available": True, "note": "SAM 3D Body found (untested in this build)."}
    return {"available": False, "note": f"Not installed ({', '.join(miss)}). " + HOWTO}


def smooth_gauss(x: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or len(x) < 3:
        return x
    r = int(np.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(x, [(r, r)] + [(0, 0)] * (x.ndim - 1), mode="edge")
    out = np.zeros_like(x, dtype=np.float64)
    for i, w in enumerate(k):
        out += w * pad[i:i + len(x)]
    return out


def mhr70_to_motion(kp3d: np.ndarray, cam_t: np.ndarray | None, fps: float, *, valid: np.ndarray | None = None,
                    smooth_sigma: float = 1.5, cam_is_opencv: bool = True) -> dict:
    """kp3d (T,70,3) per-frame body-centred keypoints in the CAMERA frame (OpenCV: +x right, +y down, +z forward),
    cam_t (T,3) translation of the body in the camera frame, valid (T,) bool (frames where a person was found).
    Returns a motion dict (Y up, metres, ground at y=0, camera-aligned world: +X right, +Z toward the viewer)."""
    T = len(kp3d)
    k = np.asarray(kp3d, np.float64).copy()
    valid = np.ones(T, bool) if valid is None else np.asarray(valid, bool)
    if not valid.any():
        raise ValueError("SAM 3D Body found no person in any frame.")
    t = np.arange(T)
    # fill invalid frames by linear interpolation
    if not valid.all():
        flat = k.reshape(T, -1)
        for c in range(flat.shape[1]):
            flat[:, c] = np.interp(t, t[valid], flat[valid, c])
        k = flat.reshape(k.shape)
    trans = np.zeros((T, 3)) if cam_t is None else np.asarray(cam_t, np.float64).copy()
    if cam_t is not None and not valid.all():
        for c in range(3):
            trans[:, c] = np.interp(t, t[valid], trans[valid, c])
    P = k + trans[:, None, :]
    if cam_is_opencv:                                       # (x right, y down, z fwd) -> (x right, y up, z toward viewer)
        P = P * np.array([1.0, -1.0, -1.0])
    P = smooth_gauss(P, smooth_sigma)
    # ground: lowest foot/heel keypoints over the clip -> y = 0
    feet = [MHR70.index(n) for n in ("left_heel", "right_heel", "left_big_toe", "right_big_toe", "left_ankle", "right_ankle")]
    P[..., 1] -= np.percentile(P[:, feet, 1], 2)
    root = 0.5 * (P[:, MHR70.index("left_hip")] + P[:, MHR70.index("right_hip")])
    conf = np.where(valid[:, None], 1.0, 0.2) * np.ones((T, 70))
    return {"skeleton": "mhr70", "joint_names": list(MHR70), "joints3d": P, "root": root, "conf": conf, "fps": float(fps)}


def run(frameset, boxes: np.ndarray, det_mask: np.ndarray, workdir: Path, ctx=None) -> dict:
    repo, py, ck = _paths()
    st = status()
    if not st["available"]:
        raise RuntimeError("Backend sam3d needs weights/installation. " + st["note"])
    spec = workdir / "sam3d_in.json"
    spec.write_text(json.dumps({"frames_dir": str(frameset.dir), "n": frameset.n, "boxes": boxes.tolist(),
                                "valid": det_mask.tolist(), "checkpoint": str(ck / "model.ckpt"),
                                "mhr": str(ck / "assets" / "mhr_model.pt")}), "utf-8")
    out = workdir / "sam3d_out.npz"
    worker = Path(__file__).parent / "workers" / "sam3d_worker.py"
    if ctx:
        ctx.progress(0.35, "Running SAM 3D Body on every frame (slow: seconds per frame)")
    r = subprocess.run([str(py), str(worker), str(spec), str(out)], cwd=str(repo), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **_NOWIN)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("SAM 3D Body failed: " + (r.stderr or r.stdout)[-800:])
    z = np.load(out, allow_pickle=False)
    return {"kp3d": z["kp3d"], "cam_t": z["cam_t"], "valid": z["valid"].astype(bool), "kp2d": z["kp2d"]}
