"""Model registry for the vitpose backend: real Hugging Face ids, sizes, local folders, availability and the
`prepare` (download) helper.  NOTHING is downloaded unless `download()` is called from the prepare job."""
from __future__ import annotations

from pathlib import Path

from ...config import MODELS_DIR

MOCAP_DIR = MODELS_DIR / "mocap"

# Verified on huggingface.co (sizes = model.safetensors in the repo trees):
POSE_MODELS = {
    # ViTPose++ (multi-dataset MoE): expert 0 = COCO (human), 3 = AP-10K (animals), 4 = APT-36K (animals)
    "small": {"repo": "usyd-community/vitpose-plus-small", "bytes": 132_619_932, "params": "~33M"},
    "base": {"repo": "usyd-community/vitpose-plus-base", "bytes": 501_627_628, "params": "~0.1B"},
    "large": {"repo": "usyd-community/vitpose-plus-large", "bytes": 1_737_705_492, "params": "~0.4B"},
    "huge": {"repo": "usyd-community/vitpose-plus-huge", "bytes": 3_597_730_876, "params": "~0.9B"},
}
DETECTOR_MODELS = {
    "r50": {"repo": "PekingU/rtdetr_v2_r50vd", "bytes": 172_176_856},     # COCO-80 classes, Apache-2.0
    "r18": {"repo": "PekingU/rtdetr_v2_r18vd", "bytes": 80_904_640},
}
DEFAULT_POSE = "base"
DEFAULT_DET = "r50"
# ViTPose++ expert ids: COCO=0, AIC=1, MPII=2, AP-10K=3, APT-36K=4, COCO-WholeBody=5
DATASET_INDEX = {"humanoid": 0, "quadruped": 3}
PREFERRED_ORDER = ["huge", "large", "base", "small"]   # most accurate installed model first


def pose_dir(variant: str) -> Path:
    return MOCAP_DIR / POSE_MODELS[variant]["repo"].split("/")[1]


def det_dir(variant: str) -> Path:
    return MOCAP_DIR / DETECTOR_MODELS[variant]["repo"].split("/")[1]


def _has_weights(d: Path) -> bool:
    return d.is_dir() and (d / "config.json").exists() and any(d.glob("*.safetensors"))


def installed_pose_variants() -> list[str]:
    return [v for v in PREFERRED_ORDER if _has_weights(pose_dir(v))]


def installed_det_variant() -> str | None:
    for v in (DEFAULT_DET, "r18"):
        if _has_weights(det_dir(v)):
            return v
    return None


def vitpose_status() -> dict:
    pv = installed_pose_variants()
    dv = installed_det_variant()
    need = POSE_MODELS[DEFAULT_POSE]["bytes"] + DETECTOR_MODELS[DEFAULT_DET]["bytes"]
    miss = []
    if not pv:
        miss.append(f"pose model ({POSE_MODELS[DEFAULT_POSE]['repo']}, {POSE_MODELS[DEFAULT_POSE]['bytes'] // 2**20} MB)")
    if not dv:
        miss.append(f"detector ({DETECTOR_MODELS[DEFAULT_DET]['repo']}, {DETECTOR_MODELS[DEFAULT_DET]['bytes'] // 2**20} MB)")
    return {"available": not miss, "size_bytes": need, "installed": {"pose": pv, "detector": dv}, "missing": miss}


def download(variant: str = DEFAULT_POSE, det: str = DEFAULT_DET, progress=None) -> dict:
    """Download the pose model + detector (only ever called by the prepare job)."""
    from huggingface_hub import snapshot_download
    out = {}
    todo = [("pose", POSE_MODELS[variant]["repo"], pose_dir(variant)),
            ("detector", DETECTOR_MODELS[det]["repo"], det_dir(det))]
    for i, (what, repo, dst) in enumerate(todo):
        if progress:
            progress(i / len(todo), f"Downloading {what}: {repo}")
        if _has_weights(dst):
            out[what] = str(dst)
            continue
        dst.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=repo, local_dir=str(dst), allow_patterns=["*.json", "*.safetensors", "*.txt"])
        out[what] = str(dst)
    if progress:
        progress(1.0, "Weights ready")
    return out
