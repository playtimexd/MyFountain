"""Canonical skeleton definitions used by the 2D->3D fitter.

Coordinates of the *body frame*: +X forward, +Y up, +Z to the subject's RIGHT (right-handed: X x Y = Z), metres.

Two output skeletons are supported (the 2D pose model gives exactly these keypoints):
  * ``coco17``  - human (COCO keypoint order)
  * ``ap10k17`` - quadruped (AP-10K keypoint order)

Internally the fitter works on an augmented tree that also contains virtual joints (``pelvis`` = mid-hip,
``neck`` for the human = mid-shoulder).  Virtual joints are dropped from ``joints3d`` in the output file;
the pelvis is exported separately as ``root``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

COCO17 = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
          "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee",
          "right_knee", "left_ankle", "right_ankle"]

AP10K17 = ["left_eye", "right_eye", "nose", "neck", "root_of_tail", "left_shoulder", "left_elbow",
           "left_front_paw", "right_shoulder", "right_elbow", "right_front_paw", "left_hip", "left_knee",
           "left_back_paw", "right_hip", "right_knee", "right_back_paw"]


@dataclass
class SkeletonDef:
    name: str                       # 'coco17' | 'ap10k17'
    subject: str                    # 'humanoid' | 'quadruped'
    out_names: list[str]            # the 17 exported joints (order of the 2D keypoints)
    all_names: list[str]            # internal tree (root first), includes virtual joints
    parent: list[int]               # parent index in all_names (-1 for root)
    rest: np.ndarray                # (N,3) rest positions in the body frame, metres
    mirror: list[int]               # left<->right partner (self for centre joints)
    side: list[int]                 # -1 left, 0 centre, +1 right
    feet: list[str]                 # foot joint names (ground contact)
    hinges: list[tuple[str, str, str, int, float]]   # (a, b, c, bend_sign, max_bend_deg)
    bone_class: dict[str, str]      # child joint name -> 'torso' | 'head' | 'limb'
    rest_weight: dict[str, float]   # per-bone strength of the "stay near rest direction" prior
    abduct_limit: float             # max |lateral component| of the unit direction of limb bones
    size_ref: float                 # nominal body size (human height / quadruped shoulder height), metres
    out_idx: np.ndarray = field(default=None)   # indices of out_names inside all_names

    def __post_init__(self):
        self.out_idx = np.array([self.all_names.index(n) for n in self.out_names], dtype=np.int64)

    @property
    def n(self) -> int:
        return len(self.all_names)

    def idx(self, name: str) -> int:
        return self.all_names.index(name)

    def rest_dirs_lengths(self) -> tuple[np.ndarray, np.ndarray]:
        """Unit rest direction and length of the bone ending at each joint (root: zeros)."""
        d = np.zeros((self.n, 3))
        ln = np.zeros(self.n)
        for j, p in enumerate(self.parent):
            if p < 0:
                continue
            v = self.rest[j] - self.rest[p]
            ln[j] = np.linalg.norm(v)
            d[j] = v / max(ln[j], 1e-9)
        return d, ln

    def order(self) -> list[int]:
        """Topological order (parents before children)."""
        seen, out = set(), []

        def visit(j):
            if j in seen:
                return
            if self.parent[j] >= 0:
                visit(self.parent[j])
            seen.add(j)
            out.append(j)
        for j in range(self.n):
            visit(j)
        return out

    def length_groups(self) -> np.ndarray:
        """Group id per joint: left/right bones share one length factor."""
        g = np.arange(self.n)
        for j in range(self.n):
            g[j] = min(j, self.mirror[j])
        return g


def _mk(name, subject, out_names, tree, rest, mirror_pairs, feet, hinges, bone_class, rest_weight,
        abduct_limit, size_ref) -> SkeletonDef:
    all_names = [t[0] for t in tree]
    parent = [(-1 if t[1] is None else all_names.index(t[1])) for t in tree]
    pos = np.array([rest[n] for n in all_names], dtype=np.float64)
    mirror = list(range(len(all_names)))
    for a, b in mirror_pairs:
        ia, ib = all_names.index(a), all_names.index(b)
        mirror[ia], mirror[ib] = ib, ia
    side = [0] * len(all_names)
    for a, b in mirror_pairs:
        side[all_names.index(a)] = -1
        side[all_names.index(b)] = +1
    return SkeletonDef(name=name, subject=subject, out_names=out_names, all_names=all_names, parent=parent,
                       rest=pos, mirror=mirror, side=side, feet=feet, hinges=hinges, bone_class=bone_class,
                       rest_weight=rest_weight, abduct_limit=abduct_limit, size_ref=size_ref)


# ------------------------------------------------------------------------------------------ human
def _human() -> SkeletonDef:
    rest = {
        "pelvis": (0, 0.95, 0),
        "left_hip": (0, 0.93, -0.09), "right_hip": (0, 0.93, 0.09),
        "left_knee": (0.01, 0.50, -0.09), "right_knee": (0.01, 0.50, 0.09),
        "left_ankle": (0, 0.07, -0.09), "right_ankle": (0, 0.07, 0.09),
        "neck": (0, 1.45, 0),
        "left_shoulder": (0, 1.42, -0.19), "right_shoulder": (0, 1.42, 0.19),
        "left_elbow": (0, 1.14, -0.22), "right_elbow": (0, 1.14, 0.22),
        "left_wrist": (0.02, 0.88, -0.22), "right_wrist": (0.02, 0.88, 0.22),
        "nose": (0.09, 1.62, 0),
        "left_eye": (0.07, 1.65, -0.03), "right_eye": (0.07, 1.65, 0.03),
        "left_ear": (-0.01, 1.63, -0.08), "right_ear": (-0.01, 1.63, 0.08),
    }
    tree = [("pelvis", None), ("left_hip", "pelvis"), ("right_hip", "pelvis"), ("left_knee", "left_hip"),
            ("right_knee", "right_hip"), ("left_ankle", "left_knee"), ("right_ankle", "right_knee"),
            ("neck", "pelvis"), ("left_shoulder", "neck"), ("right_shoulder", "neck"),
            ("left_elbow", "left_shoulder"), ("right_elbow", "right_shoulder"),
            ("left_wrist", "left_elbow"), ("right_wrist", "right_elbow"), ("nose", "neck"),
            ("left_eye", "nose"), ("right_eye", "nose"), ("left_ear", "nose"), ("right_ear", "nose")]
    pairs = [("left_hip", "right_hip"), ("left_knee", "right_knee"), ("left_ankle", "right_ankle"),
             ("left_shoulder", "right_shoulder"), ("left_elbow", "right_elbow"), ("left_wrist", "right_wrist"),
             ("left_eye", "right_eye"), ("left_ear", "right_ear")]
    hinges = [("left_hip", "left_knee", "left_ankle", -1, 150.0), ("right_hip", "right_knee", "right_ankle", -1, 150.0),
              ("left_shoulder", "left_elbow", "left_wrist", +1, 150.0), ("right_shoulder", "right_elbow", "right_wrist", +1, 150.0)]
    bc = {n: "limb" for n in ("left_knee", "right_knee", "left_ankle", "right_ankle", "left_elbow", "right_elbow",
                              "left_wrist", "right_wrist")}
    bc.update({n: "torso" for n in ("left_hip", "right_hip", "neck", "left_shoulder", "right_shoulder")})
    bc.update({n: "head" for n in ("nose", "left_eye", "right_eye", "left_ear", "right_ear")})
    rw = {"left_hip": 6.0, "right_hip": 6.0, "left_shoulder": 6.0, "right_shoulder": 6.0, "neck": 1.5,
          "nose": 0.4, "left_eye": 3.0, "right_eye": 3.0, "left_ear": 3.0, "right_ear": 3.0,
          "left_knee": 0.02, "right_knee": 0.02, "left_ankle": 0.02, "right_ankle": 0.02,
          "left_elbow": 0.08, "right_elbow": 0.08, "left_wrist": 0.02, "right_wrist": 0.02}
    return _mk("coco17", "humanoid", COCO17, tree, rest, pairs, ["left_ankle", "right_ankle"], hinges, bc, rw,
               0.55, 1.70)


# ------------------------------------------------------------------------------------------ quadruped
def _quadruped() -> SkeletonDef:
    rest = {
        "pelvis": (-0.55, 0.88, 0),
        "left_hip": (-0.55, 0.86, -0.13), "right_hip": (-0.55, 0.86, 0.13),
        "left_knee": (-0.45, 0.50, -0.14), "right_knee": (-0.45, 0.50, 0.14),
        "left_back_paw": (-0.60, 0.03, -0.14), "right_back_paw": (-0.60, 0.03, 0.14),
        "root_of_tail": (-0.72, 0.92, 0),
        "neck": (0.55, 0.97, 0),
        "left_shoulder": (0.55, 0.80, -0.17), "right_shoulder": (0.55, 0.80, 0.17),
        "left_elbow": (0.45, 0.45, -0.17), "right_elbow": (0.45, 0.45, 0.17),
        "left_front_paw": (0.55, 0.03, -0.17), "right_front_paw": (0.55, 0.03, 0.17),
        "nose": (0.98, 0.85, 0),
        "left_eye": (0.88, 0.93, -0.07), "right_eye": (0.88, 0.93, 0.07),
    }
    tree = [("pelvis", None), ("left_hip", "pelvis"), ("right_hip", "pelvis"), ("left_knee", "left_hip"),
            ("right_knee", "right_hip"), ("left_back_paw", "left_knee"), ("right_back_paw", "right_knee"),
            ("root_of_tail", "pelvis"), ("neck", "pelvis"), ("left_shoulder", "neck"), ("right_shoulder", "neck"),
            ("left_elbow", "left_shoulder"), ("right_elbow", "right_shoulder"),
            ("left_front_paw", "left_elbow"), ("right_front_paw", "right_elbow"), ("nose", "neck"),
            ("left_eye", "nose"), ("right_eye", "nose")]
    pairs = [("left_hip", "right_hip"), ("left_knee", "right_knee"), ("left_back_paw", "right_back_paw"),
             ("left_shoulder", "right_shoulder"), ("left_elbow", "right_elbow"),
             ("left_front_paw", "right_front_paw"), ("left_eye", "right_eye")]
    hinges = [("left_hip", "left_knee", "left_back_paw", -1, 120.0), ("right_hip", "right_knee", "right_back_paw", -1, 120.0),
              ("left_shoulder", "left_elbow", "left_front_paw", +1, 120.0),
              ("right_shoulder", "right_elbow", "right_front_paw", +1, 120.0)]
    bc = {n: "limb" for n in ("left_knee", "right_knee", "left_back_paw", "right_back_paw", "left_elbow",
                              "right_elbow", "left_front_paw", "right_front_paw")}
    bc.update({n: "torso" for n in ("left_hip", "right_hip", "neck", "left_shoulder", "right_shoulder", "root_of_tail")})
    bc.update({n: "head" for n in ("nose", "left_eye", "right_eye")})
    rw = {"left_hip": 6.0, "right_hip": 6.0, "left_shoulder": 6.0, "right_shoulder": 6.0, "neck": 1.5,
          "root_of_tail": 1.0, "nose": 0.4, "left_eye": 3.0, "right_eye": 3.0,
          "left_knee": 0.02, "right_knee": 0.02, "left_back_paw": 0.02, "right_back_paw": 0.02,
          "left_elbow": 0.02, "right_elbow": 0.02, "left_front_paw": 0.02, "right_front_paw": 0.02}
    return _mk("ap10k17", "quadruped", AP10K17, tree, rest, pairs, ["left_front_paw", "right_front_paw", "left_back_paw", "right_back_paw"],
               hinges, bc, rw, 0.50, 0.97)


_CACHE: dict[str, SkeletonDef] = {}


def get_skeleton(name: str) -> SkeletonDef:
    if name not in _CACHE:
        if name == "coco17":
            _CACHE[name] = _human()
        elif name == "ap10k17":
            _CACHE[name] = _quadruped()
        else:
            raise KeyError(f"unknown skeleton {name!r}")
    return _CACHE[name]


def skeleton_for_subject(subject: str) -> str:
    return "coco17" if subject == "humanoid" else "ap10k17"


# edges between OUTPUT joints for drawing
def draw_edges(name: str) -> list[tuple[int, int]]:
    sk = get_skeleton(name)
    names = sk.out_names
    out = []
    for j, p in enumerate(sk.parent):
        if p < 0:
            continue
        a, b = sk.all_names[p], sk.all_names[j]
        if a in names and b in names:
            out.append((names.index(a), names.index(b)))
    if name == "coco17":
        out += [(names.index("left_shoulder"), names.index("right_shoulder")),
                (names.index("left_hip"), names.index("right_hip")),
                (names.index("left_shoulder"), names.index("left_hip")),
                (names.index("right_shoulder"), names.index("right_hip")),
                (names.index("left_eye"), names.index("left_ear")), (names.index("right_eye"), names.index("right_ear")),
                (names.index("nose"), names.index("left_eye")), (names.index("nose"), names.index("right_eye"))]
        out = [e for e in out if e[0] != e[1]]
    else:
        out += [(names.index("neck"), names.index("root_of_tail")),
                (names.index("left_shoulder"), names.index("left_hip")),
                (names.index("right_shoulder"), names.index("right_hip")),
                (names.index("left_hip"), names.index("right_hip")),
                (names.index("left_shoulder"), names.index("right_shoulder"))]
    return sorted(set(out))
