"""Backend registry, availability, listing and 'auto' selection."""
from __future__ import annotations

import os

from . import gem, models, sam3d

DEV_ENV = "CUTLAB_MOCAP_DEV"


def dev_enabled() -> bool:
    return os.environ.get(DEV_ENV, "") not in ("", "0", "false")


def _mb(n: int) -> str:
    return f"{n / 2**20:.0f} MB" if n < 2**30 else f"{n / 2**30:.1f} GB"


def list_backends() -> list[dict]:
    vs = models.vitpose_status()
    vit_note = ("Ready. 2D pose with ViTPose++ (human COCO-17 / animal AP-10K) + CutLab's own GPU 3D fitting."
                if vs["available"] else
                "Needs weights (~%s): %s. Run prepare." % (_mb(vs["size_bytes"]), "; ".join(vs["missing"])))
    out = [
        {"id": "vitpose", "name": "ViTPose++ 2D + CutLab 3D fit (humans and animals)", "available": vs["available"],
         "note": vit_note, "size": _mb(vs["size_bytes"]),
         "license_note": "ViTPose++ and RT-DETRv2 weights: Apache-2.0 (Hugging Face model cards). CutLab fitter: own code.",
         "subjects": ["humanoid", "quadruped"], "kind": "lift2d", "skeleton": {"humanoid": "coco17", "quadruped": "ap10k17"},
         "installed": vs["installed"]},
    ]
    g = gem.status()
    out.append({"id": "gem", "name": "NVIDIA GEM-X (SOMA 77 joints, video model)", "available": g["available"], "note": g["note"],
                "size": gem.SIZE_TEXT, "license_note": gem.LICENSE_NOTE, "subjects": ["humanoid"], "kind": "direct3d",
                "skeleton": {"humanoid": "soma77"}, "experimental": True})
    s = sam3d.status()
    out.append({"id": "sam3d", "name": "Meta SAM 3D Body (per-frame HMR, MHR 70 keypoints)", "available": s["available"],
                "note": s["note"], "size": sam3d.SIZE_TEXT, "license_note": sam3d.LICENSE_NOTE, "subjects": ["humanoid"],
                "kind": "direct3d", "skeleton": {"humanoid": "mhr70"}, "experimental": True})
    if dev_enabled():
        out.append({"id": "mock", "name": "Mock 2D pose (dev only, replays a keypoint sidecar)", "available": True,
                    "note": "Development/testing backend (CUTLAB_MOCAP_DEV=1).", "size": "0", "license_note": "n/a",
                    "subjects": ["humanoid", "quadruped"], "kind": "lift2d", "skeleton": {"humanoid": "coco17", "quadruped": "ap10k17"}})
    return out


def get(bid: str) -> dict | None:
    return next((b for b in list_backends() if b["id"] == bid), None)


def resolve(requested: str | None, subject: str) -> str:
    """requested: auto|vitpose|gem|sam3d|mock|None; subject: humanoid|quadruped|auto."""
    req = (requested or "auto").lower()
    bs = {b["id"]: b for b in list_backends()}
    if req != "auto":
        if req not in bs:
            raise ValueError(f"Unknown mocap backend {req!r}. Available ids: {', '.join(bs)}.")
        if subject != "auto" and subject not in bs[req]["subjects"]:
            raise ValueError(f"Backend {req} does not support subject '{subject}' (supports: {', '.join(bs[req]['subjects'])}).")
        return req
    if "mock" in bs:
        return "mock"
    if subject == "humanoid" and bs["gem"]["available"]:
        return "gem"
    return "vitpose"
