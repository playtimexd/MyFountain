"""Known source skeletons (motion.npz `skeleton` field), bone/joint NAME parsing and role tables.

Pure standard library (+ lazy numpy) on purpose: this file is loaded both by the FastAPI services and, by file path,
inside Blender's bundled Python (backend/blender/*.py), so it must not import anything from the `app` package.
"""
from __future__ import annotations

import re

# ----------------------------------------------------------------------------------------------- skeletons
COCO17 = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow",
          "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee",
          "left_ankle", "right_ankle"]
# COCO has no pelvis/neck/head joints: they are synthesised (see augment()).
COCO17_VIRTUAL = {"pelvis": ("left_hip", "right_hip"), "neck": ("left_shoulder", "right_shoulder"), "head": ("left_ear", "right_ear")}
COCO17_AUG = COCO17 + list(COCO17_VIRTUAL)
COCO17_AUG_PARENTS = [19, 19, 19, 19, 19, 18, 18, 5, 6, 7, 8, 17, 17, 11, 12, 13, 14, -1, 17, 18]
#                      nose eyeL eyeR earL earR shL shR elL elR wrL wrR hipL hipR knL knR anL anR pelvis neck head
COCO17_AUG_PARENTS = [19, 19, 19, 19, 19, 18, 18, 5, 6, 7, 8, 17, 17, 11, 12, 13, 14, -1, 17, 18]
# neck -> pelvis (17), head -> neck (18)  (indexes above: pelvis=17, neck=18, head=19)

AP10K17 = ["left_eye", "right_eye", "nose", "neck", "root_of_tail", "left_shoulder", "left_elbow", "left_front_paw",
           "right_shoulder", "right_elbow", "right_front_paw", "left_hip", "left_knee", "left_back_paw",
           "right_hip", "right_knee", "right_back_paw"]
#                       eyeL eyeR nose neck rootT shL elL pawFL shR elR pawFR hipL knL pawBL hipR knR pawBR
AP10K17_PARENTS = [2, 2, 3, 4, -1, 3, 5, 6, 3, 8, 9, 4, 11, 12, 4, 14, 15]

SMPL24 = ["pelvis", "left_hip", "right_hip", "spine1", "left_knee", "right_knee", "spine2", "left_ankle", "right_ankle",
          "spine3", "left_foot", "right_foot", "neck", "left_collar", "right_collar", "head", "left_shoulder",
          "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hand", "right_hand"]
SMPL24_PARENTS = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19, 20, 21]

BUILTIN = {
    "coco17": (COCO17_AUG, COCO17_AUG_PARENTS),
    "ap10k17": (AP10K17, AP10K17_PARENTS),
    "smpl24": (SMPL24, SMPL24_PARENTS),
}

QUADRUPED_SKELETONS = {"ap10k17"}


def builtin_parents(skeleton: str, joint_names: list[str]) -> list[int] | None:
    """Parent index per joint (-1 = root) for a known skeleton whose names match, else None."""
    b = BUILTIN.get(skeleton)
    if not b:
        return None
    names, parents = b
    if list(joint_names) == names:
        return list(parents)
    idx = {n: i for i, n in enumerate(names)}
    if all(n in idx for n in joint_names):       # subset / re-ordered
        pos = {n: i for i, n in enumerate(joint_names)}
        out = []
        for n in joint_names:
            p = parents[idx[n]]
            # climb to the first ancestor that is present
            while p >= 0 and names[p] not in pos:
                p = parents[p]
            out.append(pos[names[p]] if p >= 0 else -1)
        return out
    return None


def augment(skeleton: str, joint_names: list[str], P, conf=None):
    """Add synthetic joints (COCO pelvis / neck / head). P is (T,J,3) numpy. Returns (names, P, conf)."""
    import numpy as np
    names = list(joint_names)
    if skeleton == "coco17" and len(names) == 17:
        P = np.asarray(P, dtype=np.float64)
        extra = []
        for v, (a, b) in COCO17_VIRTUAL.items():
            extra.append((P[:, names.index(a)] + P[:, names.index(b)]) * 0.5)
            names.append(v)
        P = np.concatenate([P, np.stack(extra, axis=1)], axis=1)
        if conf is not None:
            c = np.asarray(conf)
            ce = [np.minimum(c[:, names.index(a)], c[:, names.index(b)]) for a, b in COCO17_VIRTUAL.values()]
            conf = np.concatenate([c, np.stack(ce, axis=1)], axis=1)
    return names, P, conf


def infer_parents(joint_names: list[str], P) -> list[int]:
    """Last-resort hierarchy for unknown skeletons: minimum spanning tree over mean joint distance, rooted at the
    joint that looks most like a pelvis (name) or else the joint closest to the centroid of the lowest third."""
    import numpy as np
    J = len(joint_names)
    mean = np.asarray(P).mean(axis=0)
    root = 0
    for i, n in enumerate(joint_names):
        if re.search(r"pelvis|hips?$|root", n, re.I):
            root = i
            break
    else:
        root = int(np.argmin(np.linalg.norm(mean - mean.mean(axis=0), axis=1)))
    d = np.linalg.norm(mean[:, None] - mean[None], axis=2)
    parent = [-1] * J
    seen = {root}
    best = {j: (d[root, j], root) for j in range(J) if j != root}
    while len(seen) < J:
        j = min(best, key=lambda k: best[k][0])
        parent[j] = best[j][1]
        seen.add(j)
        del best[j]
        for k in best:
            if d[j, k] < best[k][0]:
                best[k] = (d[j, k], j)
    return parent


# ----------------------------------------------------------------------------------------------- name parsing
_PREFIX = re.compile(r"^(mixamorig\d*|bip0*1|bip|def|org|mch|ctrl|tweak|jnt|joint|j|b|bone|cc_base|skel(eton)?|armature)[:_.\-| ]+", re.I)


def tokens(name: str) -> list[str]:
    s = name.split("|")[-1].split(":")[-1]
    s = _PREFIX.sub("", s) if len(s) > 4 else s
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", s)
    s = re.sub(r"([A-Za-z])(\d)", r"\1_\2", s)
    s = re.sub(r"(\d)([A-Za-z])", r"\1_\2", s)
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", s) if t]


_SIDE_L = {"l", "left", "lf", "lh", "lt"}
_SIDE_R = {"r", "right", "rt"}
_FRONT = {"front", "fore", "forelimb", "frontleg", "foreleg", "fr", "f", "arm", "arms", "hand", "forepaw", "forefoot"}
_HIND = {"hind", "rear", "back", "behind", "bk", "b", "hindleg", "backleg", "rearleg", "hindpaw", "backpaw", "hindfoot", "leg", "legs", "foot"}


def parse_name(name: str) -> dict:
    """Heuristic anatomical parse of a bone / joint name.
    -> {role, side:'L'|'R'|None, limb:'front'|'hind'|None, idx:int|None, tokens}
    role in: pelvis root spine chest neck head jaw ear eye nose tail clav upper mid end toe finger other
    (upper/mid/end = shoulder-hip / elbow-knee / wrist-ankle-paw joints of a limb)."""
    toks = tokens(name)
    side = None
    rest = []
    for t in toks:
        if t in _SIDE_L and side is None:
            side = "L"
        elif t in _SIDE_R and side is None:
            side = "R"
        else:
            rest.append(t)
    idx = None
    words = []
    for t in rest:
        if t.isdigit():
            idx = int(t)
        else:
            words.append(t)
    W = set(words)
    joined = "".join(words)
    limb = None
    if W & {"front", "fore", "forelimb", "frontleg", "foreleg", "forepaw", "forefoot", "fr"} or "fore" in joined[:4]:
        limb = "front"
    elif W & {"hind", "rear", "hindleg", "backleg", "rearleg", "hindpaw", "backpaw", "hindfoot", "behind", "bk"} or ("back" in W and W & {"paw", "leg", "foot", "knee", "hip", "thigh", "upper", "lower", "ankle"}):
        limb = "hind"
    role = "other"

    def has(*xs):
        return any(x in W or (len(x) >= 5 and x in joined) for x in xs)

    limbword = bool(W & {"paw", "leg", "legs", "foot", "feet", "knee", "hip", "thigh", "ankle", "hoof", "calf", "shin"})

    if W & {"twist", "ik", "roll", "corrective", "helper", "pole", "ctrl", "target", "socket", "attach", "prop", "weapon"}:
        role = "other"          # engine helper bones (UE *_twist_01_l, ik_hand_gun, ...): never drive them from a source joint
    elif has("tail") and has("root", "base") or joined in ("roottail", "tailbase", "rootoftail"):
        role = "pelvis"
    elif has("tail"):
        role = "tail"
    elif has("pelvis", "hips") or joined in ("hip", "root_hip") and side is None:
        role = "pelvis"
    elif joined in ("root", "rootbone", "master", "armature", "origin", "trajectory", "world") or (W == {"root"}):
        role = "root"
    elif has("finger", "thumb", "index", "middle", "ring", "pinky", "pinkie") and not has("ringbone"):
        role = "finger"
    elif has("toe", "toes", "ball", "toebase", "hoof") and not has("ankle"):
        role = "toe"
    elif has("jaw", "chin", "mandible"):
        role = "jaw"
    elif has("ear", "ears"):
        role = "ear"
    elif has("eye", "eyes", "eyeball", "eyelid", "brow"):
        role = "eye"
    elif has("nose", "snout", "muzzle", "nostril", "beak", "trunk"):
        role = "nose"
    elif has("neck"):
        role = "neck"
    elif has("head", "skull", "cranium"):
        role = "head"
    elif has("clavicle", "collar", "scapula", "shouldersocket"):
        role = "clav"
    elif has("chest", "thorax", "ribs", "torso") or ("upper" in W and (has("spine") or ("back" in W and not limbword))):
        role = "chest"
    elif has("spine", "abdomen", "waist", "belly", "lumbar") or ("back" in W and not limbword):
        role = "spine"
    elif has("thigh", "upleg", "upperleg", "femur", "hip", "hips", "haunch") or ("upper" in W and has("leg", "legs")):
        role = "upper"
        limb = limb or "hind"
    elif has("calf", "shin", "lowerleg", "lowleg", "knee", "tibia", "lowleg", "hock", "stifle") or ("lower" in W and has("leg", "legs")):
        role = "mid"
        limb = limb or "hind"
    elif has("forearm", "lowerarm", "elbow", "radius", "lowarm") or ("lower" in W and has("arm", "arms")):
        role = "mid"
        limb = limb or "front"
    elif has("upperarm", "humerus", "shoulder", "upparm") or ("upper" in W and has("arm", "arms")):
        role = "upper"
        limb = limb or "front"
    elif has("hand", "wrist", "forepaw"):
        role = "end"
        limb = limb or "front"
    elif has("foot", "ankle", "paw", "feet", "hindpaw", "backpaw"):
        role = "end"
        if "foot" in W or "ankle" in W or "feet" in W:
            limb = limb or "hind"
    elif has("arm", "arms"):
        role = "upper"
        limb = limb or "front"
    elif has("leg", "legs"):
        role = "mid"            # a lone "Leg" is usually the lower leg (Mixamo: LeftLeg = calf)
        limb = limb or "hind"
    elif has("paw"):
        role = "end"
    # numbered limb tokens like "leg_1/2/3" are resolved geometrically in bonemap
    return {"role": role, "side": side, "limb": limb, "idx": idx, "tokens": toks}


def fix_chain_names(names: list[str], parsed: list[dict]) -> None:
    """Naming conventions that need the neighbours to decide (in place):
    - "Shoulder" next to an "Arm" on the same side is the clavicle (Mixamo, SOMA/Kimodo, BVH);
    - "Leg" next to a "Shin"/"Calf" on the same side is the thigh (SOMA/Kimodo: LeftLeg = thigh, LeftShin = calf)."""
    low = [n.lower() for n in names]
    for side in ("L", "R"):
        on = [i for i, p in enumerate(parsed) if p["side"] == side]
        has_arm = any("arm" in low[i] and "fore" not in low[i] and "lower" not in low[i] and parsed[i]["role"] == "upper" for i in on)
        for i in on:
            if parsed[i]["role"] == "upper" and "shoulder" in low[i] and has_arm:
                parsed[i]["role"] = "clav"
        has_shin = any(("shin" in low[i] or "calf" in low[i]) and parsed[i]["role"] == "mid" for i in on)
        for i in on:
            if parsed[i]["role"] == "mid" and "leg" in low[i] and "shin" not in low[i] and "calf" not in low[i] and "low" not in low[i] and has_shin:
                parsed[i]["role"] = "upper"
                parsed[i]["limb"] = parsed[i]["limb"] or "hind"


def source_roles(joint_names: list[str]) -> dict[str, dict]:
    """parse_name() for source joints, with joint-convention fixes (a joint called 'foot' in SMPL is the toe)."""
    out = {}
    for n in joint_names:
        r = parse_name(n)
        if n.endswith("_foot") and n.split("_")[0] in ("left", "right") and "paw" not in n:
            r["role"] = "toe"             # SMPL left_foot / right_foot = toe base
        if n.endswith("_hand"):
            r["role"] = "finger"
        out[n] = r
    fix_chain_names(list(out), list(out.values()))
    return out
