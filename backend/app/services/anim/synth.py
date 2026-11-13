"""Synthetic test motions (no model weights needed): a 4-beat quadruped walk (AP-10K-style joints) and a biped walk (SMPL-24).
Coordinates follow the motion.npz contract: metres, Y up, subject walks along +X, animal's left = -Z."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import skeletons as sk


def _smooth(u):
    return u * u * (3 - 2 * u)


def _ik2(H, F, l1, l2, bend):
    """Two-bone IK: returns middle joint for hip H, foot F (arrays (T,3)); `bend` = preferred bend direction (3,)."""
    v = F - H
    d = np.linalg.norm(v, axis=1, keepdims=True).clip(1e-6, None)
    d = np.minimum(d, (l1 + l2) * 0.999)
    u = v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-6, None)
    b = np.asarray(bend, float)[None]
    n = b - (b * u).sum(1, keepdims=True) * u
    n /= np.linalg.norm(n, axis=1, keepdims=True).clip(1e-6, None)
    a = (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d)
    a = a.clip(-1, 1)
    return H + l1 * (a * u + np.sqrt(1 - a * a) * n)


def _foot(t, T, phase, duty, stride, hx, z, lift, v):
    """World foot trajectory (x,y,z) for a foot whose hip is at body offset hx."""
    p = (t / T + phase) % 1.0
    a = stride * duty / 2
    x = np.where(p < duty, a - stride * p, -a + 2 * a * _smooth(((p - duty) / (1 - duty)).clip(0, 1)))
    y = np.where(p < duty, 0.0, lift * np.sin(np.pi * ((p - duty) / (1 - duty)).clip(0, 1)))
    return np.stack([v * t + hx + x, y, np.full_like(t, z)], axis=1)


def quadruped_walk(fps: float = 30.0, cycles: int = 3, period: float = 1.2, noise: float = 0.0, seed: int = 0) -> dict:
    T = period
    n = int(round(cycles * T * fps))
    t = np.arange(n) / fps
    stride, duty, lift = 0.62, 0.72, 0.09
    v = stride / T
    w = 2 * np.pi * t / T
    body_x = v * t
    sway = 0.018 * np.sin(w)                       # lateral spine undulation
    bob = 0.012 * np.cos(2 * w)
    root = np.stack([body_x - 0.38, 0.60 + bob, sway], axis=1)                       # root_of_tail
    neck = np.stack([body_x + 0.38, 0.66 + bob * 0.7, -sway * 1.2], axis=1)
    fwd = neck - root
    fwd /= np.linalg.norm(fwd, axis=1, keepdims=True)
    up = np.array([0, 1.0, 0])
    lat = np.cross(up, fwd)                         # points to the animal's LEFT?  up x fwd = (0,1,0)x(1,0,0) = (0,0,-1)  -> left = -Z  OK
    lat /= np.linalg.norm(lat, axis=1, keepdims=True)
    nod = 0.025 * np.sin(2 * w + 0.6)
    nose = neck + fwd * 0.26 + np.stack([np.zeros(n), -0.10 + nod, np.zeros(n)], 1)
    eyeL = nose - fwd * 0.12 + up * 0.07 + lat * 0.045
    eyeR = nose - fwd * 0.12 + up * 0.07 - lat * 0.045
    # legs: (name_prefix, phase, hip x-offset from body_x, lateral sign (+1 left), front?)
    legs = {}
    spec = {"left_hind": (0.00, -0.30, +1, False), "left_front": (0.25, 0.30, +1, True),
            "right_hind": (0.50, -0.30, -1, False), "right_front": (0.75, 0.30, -1, True)}
    for key, (ph, hx, side, front) in spec.items():
        zc = 0.13 * (-side)                          # left = -Z
        paw = _foot(t, T, ph, duty, stride, hx, zc, lift, v)
        base = root if not front else neck
        hip = base + lat * (0.12 * side) + (fwd * (0.0 if not front else -0.05)) - up * 0.06
        knee = _ik2(hip, paw + np.array([0, 0.015, 0]), 0.34, 0.33, (-1.0, 0, 0) if front else (1.0, 0, 0))
        legs[key] = (hip, knee, paw)
    names = sk.AP10K17
    J = {"left_eye": eyeL, "right_eye": eyeR, "nose": nose, "neck": neck, "root_of_tail": root,
         "left_shoulder": legs["left_front"][0], "left_elbow": legs["left_front"][1], "left_front_paw": legs["left_front"][2],
         "right_shoulder": legs["right_front"][0], "right_elbow": legs["right_front"][1], "right_front_paw": legs["right_front"][2],
         "left_hip": legs["left_hind"][0], "left_knee": legs["left_hind"][1], "left_back_paw": legs["left_hind"][2],
         "right_hip": legs["right_hind"][0], "right_knee": legs["right_hind"][1], "right_back_paw": legs["right_hind"][2]}
    P = np.stack([J[k] for k in names], axis=1)
    if noise:
        P = P + np.random.default_rng(seed).normal(0, noise, P.shape)
    return _pack(P, names, "ap10k17", fps, root, {"generator": "quadruped_walk", "cycles": cycles, "period": period, "stride_m": stride})


def biped_walk(fps: float = 30.0, cycles: int = 3, period: float = 1.0, noise: float = 0.0, seed: int = 0) -> dict:
    T = period
    n = int(round(cycles * T * fps))
    t = np.arange(n) / fps
    stride, duty, lift = 1.1, 0.62, 0.10
    v = stride / T
    w = 2 * np.pi * t / T
    bob = 0.02 * np.cos(2 * w)
    up = np.array([0, 1.0, 0])
    pelvis = np.stack([v * t, 0.93 + bob, 0.02 * np.sin(w)], 1)
    yaw = 0.08 * np.sin(w)
    fwd = np.stack([np.cos(yaw), np.zeros(n), np.sin(yaw)], 1)
    lat = np.cross(up, fwd)
    J = {"pelvis": pelvis}
    J["spine1"] = pelvis + up * 0.12
    J["spine2"] = pelvis + up * 0.26 + fwd * 0.01
    J["spine3"] = pelvis + up * 0.40 + fwd * 0.015
    J["neck"] = pelvis + up * 0.56
    J["head"] = pelvis + up * 0.68 + fwd * 0.02
    for s, side in (("left", 1), ("right", -1)):
        ph = 0.0 if side == 1 else 0.5
        hip = pelvis + lat * (0.09 * side)
        ank = _foot(t, T, ph, duty, stride, 0.0, -0.09 * side, lift, v) + np.array([0, 0.07, 0])
        knee = _ik2(hip, ank, 0.50, 0.48, (1.0, 0, 0))
        toe = ank + np.array([0.13, -0.07, 0]) * 1.0
        toe[:, 1] = np.maximum(toe[:, 1], 0.0)
        J[f"{s}_hip"], J[f"{s}_knee"], J[f"{s}_ankle"], J[f"{s}_foot"] = hip, knee, ank, toe
        col = J["spine3"] + up * 0.10 + lat * (0.05 * side)
        sh = J["spine3"] + up * 0.12 + lat * (0.18 * side)
        swing = -0.45 * np.sin(w + (0 if side == 1 else np.pi)) * (1 if side == 1 else 1)
        el = sh + np.stack([0.28 * np.sin(swing), -0.28 * np.cos(swing), np.zeros(n)], 1) + lat * (0.02 * side)
        sw2 = swing * 1.3 - 0.25
        wr = el + np.stack([0.26 * np.sin(sw2), -0.26 * np.cos(sw2), np.zeros(n)], 1)
        J[f"{s}_collar"], J[f"{s}_shoulder"], J[f"{s}_elbow"], J[f"{s}_wrist"] = col, sh, el, wr
        J[f"{s}_hand"] = wr + (wr - el) * 0.35
    names = sk.SMPL24
    P = np.stack([J[k] for k in names], axis=1)
    if noise:
        P = P + np.random.default_rng(seed).normal(0, noise, P.shape)
    return _pack(P, names, "smpl24", fps, pelvis, {"generator": "biped_walk", "cycles": cycles, "period": period, "stride_m": stride})


def _pack(P, names, skeleton, fps, root, meta) -> dict:
    return {"fps": np.float64(fps), "skeleton": np.array(skeleton), "joint_names": np.array(names),
            "joints3d": P.astype(np.float32), "conf": np.ones(P.shape[:2], np.float32), "root": root.astype(np.float32),
            "meta": np.array(json.dumps({**meta, "synthetic": True, "camera": "none"}))}


def write_demo(kind: str, path: str | Path, **kw) -> Path:
    fn = {"quadruped_walk": quadruped_walk, "biped_walk": biped_walk}[kind]
    d = fn(**kw)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **d)
    return path
