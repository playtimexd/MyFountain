"""Optional: derive bone lengths for the fitter from a target rig (anim endpoints).  Best effort, never fatal."""
from __future__ import annotations

import json
import urllib.request

import numpy as np

from .skeletons import SkeletonDef


def fetch_rig(rig_id: str, port: int, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/anim/rigs/{rig_id}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _bones_dict(rig: dict) -> dict[str, dict]:
    b = rig.get("bones_json", rig.get("bones"))
    if isinstance(b, str):
        b = json.loads(b)
    if isinstance(b, dict):
        b = [{"name": k, **(v if isinstance(v, dict) else {})} for k, v in b.items()]
    return {x["name"]: x for x in (b or []) if isinstance(x, dict) and "name" in x}


def _head(b: dict):
    for k in ("head", "head_world", "head_pos", "pos"):
        if k in b and b[k] is not None and len(b[k]) == 3:
            return np.asarray(b[k], float)
    return None


def bone_lengths_from_rig(rig: dict, sk: SkeletonDef) -> tuple[dict[str, float], str]:
    """Return ({skeleton joint name: bone length in rig units}, note).  Needs the rig's bone map
    (skeleton joint -> rig bone name) and bone head positions; otherwise returns ({}, reason)."""
    try:
        bm = rig.get("bone_map_json", rig.get("bone_map"))
        if isinstance(bm, str):
            bm = json.loads(bm)
        bones = _bones_dict(rig)
        if not bm or not bones:
            return {}, "rig has no bone map / bone list yet; using canonical proportions"
        heads = {}
        for joint, bname in bm.items():
            if joint in sk.all_names and bname in bones and _head(bones[bname]) is not None:
                heads[joint] = _head(bones[bname])
        out = {}
        for j, p in enumerate(sk.parent):
            if p < 0:
                continue
            a, b = sk.all_names[p], sk.all_names[j]
            if a in heads and b in heads:
                out[b] = float(np.linalg.norm(heads[b] - heads[a]))
        # virtual joints (pelvis/neck) are rarely mapped: derive from hips / shoulders when possible
        if "left_hip" in heads and "right_hip" in heads and "pelvis" not in heads:
            heads["pelvis"] = 0.5 * (heads["left_hip"] + heads["right_hip"])
            for ch in ("left_hip", "right_hip"):
                out[ch] = float(np.linalg.norm(heads[ch] - heads["pelvis"]))
        if len(out) < 4:
            return {}, "too few mapped bones to derive proportions; using canonical proportions"
        return out, f"bone lengths taken from the rig ({len(out)} bones)"
    except Exception as e:  # noqa: BLE001
        return {}, f"could not read rig proportions ({type(e).__name__}); using canonical proportions"
