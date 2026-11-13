"""Optional humanoid backend: NVIDIA GEM-X (SOMA 77 joints).  https://github.com/NVlabs/gem-x  (older: NVlabs/GENMO)

STATUS: adapter written from the public README / docs (DEMO.md, INSTALL.md); it could NOT be run here (no weights, GEM
needs Python 3.10+ / torch 2.10 / `py-soma-x==0.3.3`, validated by NVIDIA on Linux + A100).  The pure converter
(`soma77_to_motion`) is unit-tested on synthetic arrays; the subprocess + `motion.pt` reading is untested.

How it runs: GEM lives in its OWN environment (it pins torch 2.10), we call its CLI as a subprocess:
    <home>/.venv/Scripts/gem-infer(.exe) motion-video --task body --input VIDEO --config <cfg> --device cuda
        --postprocess --soma-assets <home>/repo/assets/soma --camera-mode static --output motion.pt
and then `workers/gem_worker.py` (run inside GEM's python) turns motion.pt into a plain npz of joints.

Layout expected (override the root with env CUTLAB_GEM_HOME):
    <home>/repo/                    git clone https://github.com/NVlabs/gem-x
    <home>/repo/.venv/              python -m venv + pip install -e '.[video]' (+ py-soma-x==0.3.3)
    <home>/repo/weights/            body_s240000.ckpt, motion_native77_e774_s292175.ckpt, motion_stats_raw.npz, vitpose.pth,
                                    yolox_humanart.onnx   (hf download nvidia/GEM-X --local-dir weights)
    <home>/repo/assets/soma/        SOMA_neutral.npz, SOMA_template_rig.usda, SOMA_procedural_transforms.json, correctives_model.pt
                                    (hf download nvidia/SOMA-X ... --revision v0.3.3)
Licences: GEM-X code Apache-2.0; GEM-X weights NVIDIA Open Model License (model card: commercial use allowed);
SOMA-X assets and the SMPL variant ("GEM-SMPL") carry their own terms; the older NVlabs/GENMO repo states a
non-commercial licence - check before shipping a game.
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

SIZE_BYTES = 3_538_753_341 + 187_134_331 + 3_388_483_384 + 21_320      # body ckpt + motion ckpt + vitpose.pth + stats
SIZE_TEXT = "~7.1 GB of GEM-X weights (+ SOMA assets, YOLOX detector, a separate Python env with torch 2.10)"
LICENSE_NOTE = ("Code Apache-2.0; weights NVIDIA Open Model License (model card: commercial + non-commercial use). "
                "SOMA assets / SMPL variant have their own terms. NVlabs/GENMO (older repo) says non-commercial.")
HOWTO = ("1) git clone https://github.com/NVlabs/gem-x into <home>/repo and create its venv "
         "(python 3.10+, torch 2.10, pip install -e '.[video]', pip install py-soma-x==0.3.3).  "
         "2) hf download nvidia/GEM-X --local-dir weights ; hf download nvidia/SOMA-X SOMA_neutral.npz SOMA_template_rig.usda "
         "SOMA_procedural_transforms.json correctives_model.pt --revision v0.3.3 --local-dir assets/soma ; place yolox_humanart.onnx "
         "in weights/ (docs/PRETRAINED_MODELS.md).  3) set CUTLAB_GEM_HOME if the folder is not data/models/mocap/gem-x.")


def home() -> Path:
    return Path(os.environ.get("CUTLAB_GEM_HOME") or (MODELS_DIR / "mocap" / "gem-x"))


def _paths():
    h = home()
    repo = h / "repo"
    scripts = repo / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    exe = next((p for p in (scripts / "gem-infer.exe", scripts / "gem-infer") if p.exists()), None)
    py = next((p for p in (scripts / "python.exe", scripts / "python") if p.exists()), None)
    w = repo / "weights"
    need = [w / "body_s240000.ckpt", w / "motion_native77_e774_s292175.ckpt", w / "motion_stats_raw.npz",
            w / "vitpose.pth", w / "yolox_humanart.onnx", repo / "assets" / "soma" / "SOMA_neutral.npz"]
    return repo, exe, py, need


def status() -> dict:
    repo, exe, py, need = _paths()
    miss = []
    if not repo.exists():
        miss.append("repo clone")
    if exe is None:
        miss.append("GEM python env with gem-infer")
    miss += [p.name for p in need if not p.exists()]
    if not miss:
        return {"available": True, "note": "GEM-X found (untested in this build; see docs/MOCAP.md)."}
    return {"available": False,
            "note": f"Not installed ({', '.join(miss[:4])}{'...' if len(miss) > 4 else ''}). " + HOWTO}


# --------------------------------------------------------------------------------------------- converter (tested)
def soma77_to_motion(joints: np.ndarray, fps: float, *, joint_names: list[str] | None = None, up: str = "+y",
                     conf: np.ndarray | None = None) -> dict:
    """joints (T,77,3) world-space SOMA joints -> motion dict compatible with motion_io.save_motion (Y up, metres,
    facing +Z).  `up` is the up axis of the source ('+y','-y','+z','-z')."""
    j = np.asarray(joints, np.float64)
    if j.ndim != 3 or j.shape[2] != 3:
        raise ValueError("joints must be (T,J,3)")
    ax = {"x": 0, "y": 1, "z": 2}[up[1]]
    sign = 1.0 if up[0] == "+" else -1.0
    # build a rotation taking the source up axis to +Y (proper rotation)
    R = np.eye(3)
    if up != "+y":
        e = np.zeros(3)
        e[ax] = sign
        v = np.cross(e, [0, 1, 0])
        c = float(e @ np.array([0, 1, 0]))
        if np.linalg.norm(v) < 1e-9:                       # -y: rotate 180 deg about X
            R = np.diag([1, -1, -1.0])
        else:
            vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
            R = np.eye(3) + vx + vx @ vx / (1 + c)
    j = j @ R.T
    ext = (j.max((0, 1)) - j.min((0, 1)))
    if ext[1] < 0.6 * max(ext[0], ext[2]) and ext[1] < 1.0:
        raise ValueError("GEM output does not look Y-up after conversion; set CUTLAB_GEM_UP (e.g. +z).")
    j[..., 1] -= np.percentile(j[..., 1], 1)                # feet on the ground
    T, J, _ = j.shape
    names = joint_names or [f"soma_{i:02d}" for i in range(J)]
    root = j[:, 0].copy()
    return {"skeleton": "soma77", "joint_names": names, "joints3d": j, "root": root,
            "conf": np.ones((T, J)) if conf is None else conf, "fps": float(fps)}


# --------------------------------------------------------------------------------------------- runner (untested)
def run(video: Path, start: float, end: float | None, workdir: Path, ctx=None, camera_mode: str = "static") -> dict:
    repo, exe, py, need = _paths()
    st = status()
    if not st["available"]:
        raise RuntimeError("Backend gem needs weights/installation. " + st["note"])
    out_pt = workdir / "motion.pt"
    cmd = [str(exe), "motion-video", "--task", "body", "--input", str(video),
           "--config", str(repo / "docs" / "configs" / "body_video_native77.yaml"), "--device", "cuda",
           "--postprocess", "--soma-assets", str(repo / "assets" / "soma"), "--camera-mode", camera_mode,
           "--start", f"{start:.3f}", "--output", str(out_pt)]
    if end:
        cmd += ["--duration", f"{min(end - start, 60.0):.3f}"]
    if ctx:
        ctx.progress(0.1, "Running GEM-X (motion-video)")
    r = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace", **_NOWIN)
    if r.returncode != 0 or not out_pt.exists():
        raise RuntimeError("GEM-X failed: " + (r.stderr or r.stdout)[-800:])
    out_npz = workdir / "gem_joints.npz"
    worker = Path(__file__).parent / "workers" / "gem_worker.py"
    r = subprocess.run([str(py), str(worker), str(out_pt), str(out_npz)], cwd=str(repo), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", **_NOWIN)
    if r.returncode != 0 or not out_npz.exists():
        raise RuntimeError("Could not read GEM-X output: " + (r.stderr or r.stdout)[-800:])
    z = np.load(out_npz, allow_pickle=False)
    meta = json.loads(str(z["info"]))
    return {"joints": z["joints"], "fps": float(z["fps"]), "info": meta,
            "up": os.environ.get("CUTLAB_GEM_UP", "+y")}
