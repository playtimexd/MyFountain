"""Generated transitions and seamless loops with NVIDIA MotionBricks (SOMA model, ONNX on CPU).

MotionBricks is pose-conditioned, not text-conditioned: given the last four poses of one motion (context) and up to four poses
it must reach (target), it generates the 0.6-1.9 s of motion in between. CutLab uses it to
  * join two SOMA clips (Kimodo output, or any SOMA BVH) with a generated transition, and
  * close a one-shot clip into a seamless loop (a transition from its end back to its own start).
The result is a new SOMA source clip; the usual retarget step puts it on any character.

Licences: the ONNX model is under the NVIDIA Open Model Agreement (commercial use allowed). The encode / decode / endpoint
correction below is a numpy port of the Apache-2.0 reference runtime (github.com/NVlabs/motionbricks, motionbricks/model/soma),
which uses NVIDIA Warp + TensorRT. The Bones sample clips in that repository (evaluation-only licence) are not used.
"""
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from ... import config

MODEL_DIR = config.MODELS_DIR / "motionbricks" / "soma"
ONNX_FILES = ("mmm_v6_soma_fp16.onnx", "mmm_v6_soma_fp32.onnx")
OUT_DIR = config.DATA / "anim" / "motionbricks"
FPS = 30.0
POSES_PER_TOKEN = 4
MIN_TOKENS, MAX_TOKENS = 6, 16
BOUNDARY = POSES_PER_TOKEN - 1
HEADING_JOINTS = ("RightLeg", "LeftLeg")
DESIRED_HEADING = math.atan2(1.0, 0.0)          # reference default heading_alignment (1, 0): last context pose faces +X

_lock = threading.Lock()
_session = None


# ------------------------------------------------------------------------------------------------ model
def model_path() -> Path | None:
    for f in ONNX_FILES:
        p = MODEL_DIR / f
        if p.is_file():
            return p
    return None


def joint_names() -> list[str]:
    return json.loads((MODEL_DIR / "soma_n1.json").read_text("utf-8"))["joint_names"]


_std: list = []


def standard_offsets() -> np.ndarray:
    """Per model joint (30,3,3): SOMA BVHs written by Kimodo use the 'standard' T pose; the ONNX graph expects global rotations
    in the 'capture' T pose (identity offsets) and converts them itself. capture = standard @ S, standard = capture @ S^T
    (soma_model_rig.npz from tools/onnx/assets in the MotionBricks repository)."""
    if not _std:
        _std.append(np.load(MODEL_DIR / "soma_model_rig.npz")["standard_offsets"].astype(np.float64))
    return _std[0]


def status() -> dict:
    p = model_path()
    ok = bool(p and (MODEL_DIR / "soma_n1.json").is_file() and (MODEL_DIR / "soma_model_rig.npz").is_file())
    return {"available": ok, "model": p.name if p else None,
            "licence": "NVIDIA Open Model Agreement (commercial use allowed)",
            "note": None if ok else (f"Put mmm_v6_soma_fp16.onnx (motionbricks/assets/onnx/soma), soma_n1.json and soma_model_rig.npz "
                                     f"(tools/onnx/assets) from github.com/NVlabs/motionbricks into {MODEL_DIR}")}


def _sess():
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            p = model_path()
            if not p:
                raise RuntimeError(status()["note"])
            so = ort.SessionOptions()
            so.log_severity_level = 3
            _session = ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])
        return _session


# ------------------------------------------------------------------------------------------------ BVH
@dataclass
class Motion:
    names: list[str]
    parents: list[int]
    offsets: np.ndarray                       # (J,3) metres
    channels: list[list[str]]
    pos: np.ndarray                           # (T,J,3) local positions, metres
    rot: np.ndarray                           # (T,J,3,3) local rotations
    fps: float
    end_sites: dict = field(default_factory=dict)   # joint -> end-site offset (metres), kept for writing

    @property
    def T(self) -> int:
        return self.pos.shape[0]


def read_bvh(path: str | Path) -> Motion:
    lines = Path(path).read_text("utf-8", errors="replace").splitlines()
    names, parents, offsets, channels, stack, end_sites = [], [], [], [], [], {}
    in_end = False
    i = 0
    while i < len(lines):
        t = lines[i].split()
        i += 1
        if not t:
            continue
        if t[0] == "MOTION":
            break
        if t[0] in ("ROOT", "JOINT"):
            names.append(t[1].rsplit(":", 1)[-1])
            parents.append(stack[-1] if stack else -1)
            stack.append(len(names) - 1)
            offsets.append([0.0, 0.0, 0.0])
            channels.append([])
        elif t[:2] == ["End", "Site"]:
            in_end = True
        elif t[0] == "OFFSET":
            v = [float(x) * 0.01 for x in t[1:4]]
            if in_end:
                end_sites[stack[-1]] = v
            else:
                offsets[stack[-1]] = v
        elif t[0] == "CHANNELS":
            channels[stack[-1]] = t[2:]
        elif t[0] == "}":
            if in_end:
                in_end = False
            elif stack:
                stack.pop()
    nf = int(lines[i].split(":")[1])
    ft = float(lines[i + 1].split(":")[1])
    data = np.array([[float(x) for x in l.split()] for l in lines[i + 2:i + 2 + nf] if l.strip()], dtype=np.float64)
    J = len(names)
    off = np.array(offsets)
    pos = np.broadcast_to(off, (data.shape[0], J, 3)).copy()
    rot = np.broadcast_to(np.eye(3), (data.shape[0], J, 3, 3)).copy()
    col = 0
    for j, ch in enumerate(channels):
        rax, rcols = "", []
        for c in ch:
            c = c.lower()
            if c.endswith("position"):
                pos[:, j, "xyz".index(c[0])] = data[:, col] * 0.01
            elif c.endswith("rotation"):
                rax += c[0].upper()
                rcols.append(col)
            col += 1
        if rax:
            rot[:, j] = Rotation.from_euler(rax, data[:, rcols], degrees=True).as_matrix()
    return Motion(names, parents, off, channels, pos, rot, 1.0 / ft if ft > 0 else FPS, end_sites)


def write_bvh(m: Motion, path: str | Path) -> Path:
    kids: dict[int, list[int]] = {}
    for j, p in enumerate(m.parents):
        kids.setdefault(p, []).append(j)
    out: list[str] = ["HIERARCHY"]

    def emit(j: int, depth: int) -> None:
        ind = "  " * depth
        out.append(f"{ind}{'ROOT' if m.parents[j] < 0 else 'JOINT'} {m.names[j]}")
        out.append(f"{ind}{{")
        o = m.offsets[j] * 100
        out.append(f"{ind}  OFFSET {o[0]:.6f} {o[1]:.6f} {o[2]:.6f}")
        out.append(f"{ind}  CHANNELS {len(m.channels[j])} {' '.join(m.channels[j])}")
        for k in kids.get(j, []):
            emit(k, depth + 1)
        if j in m.end_sites:
            e = np.array(m.end_sites[j]) * 100
            out.extend([f"{ind}  End Site", f"{ind}  {{", f"{ind}    OFFSET {e[0]:.6f} {e[1]:.6f} {e[2]:.6f}", f"{ind}  }}"])
        out.append(f"{ind}}}")
    for r in kids.get(-1, []):
        emit(r, 0)
    cols = []
    for j, ch in enumerate(m.channels):
        rax = "".join(c[0].upper() for c in ch if c.lower().endswith("rotation"))
        eul = Rotation.from_matrix(m.rot[:, j]).as_euler(rax, degrees=True) if rax else None
        ri = 0
        for c in ch:
            cl = c.lower()
            if cl.endswith("position"):
                cols.append(m.pos[:, j, "xyz".index(cl[0])] * 100)
            else:
                cols.append(eul[:, ri])
                ri += 1
    data = np.stack(cols, 1) if cols else np.zeros((m.T, 0))
    out += ["MOTION", f"Frames: {m.T}", f"Frame Time: {1.0 / m.fps:.10f}"]
    out += [" ".join(f"{v:.6f}" for v in row) for row in data]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(out) + "\n", "utf-8")
    return Path(path)


def resample(m: Motion, fps: float = FPS) -> Motion:
    if abs(m.fps - fps) < 1e-3 or m.T < 2:
        return m
    t0 = np.arange(m.T) / m.fps
    t1 = np.arange(0, t0[-1] + 1e-9, 1.0 / fps)
    pos = np.stack([np.stack([np.interp(t1, t0, m.pos[:, j, k]) for k in range(3)], -1) for j in range(len(m.names))], 1)
    rot = np.stack([Slerp(t0, Rotation.from_matrix(m.rot[:, j]))(t1).as_matrix() for j in range(len(m.names))], 1)
    return Motion(m.names, m.parents, m.offsets, m.channels, pos, rot, fps, m.end_sites)


def fk(m: Motion, pos: np.ndarray | None = None, rot: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    pos = m.pos if pos is None else pos
    rot = m.rot if rot is None else rot
    T, J = pos.shape[:2]
    wp_, wr = np.zeros((T, J, 3)), np.zeros((T, J, 3, 3))
    for j, p in enumerate(m.parents):
        if p < 0:
            wp_[:, j], wr[:, j] = pos[:, j], rot[:, j]
        else:
            wr[:, j] = wr[:, p] @ rot[:, j]
            wp_[:, j] = wp_[:, p] + np.einsum("tij,tj->ti", wr[:, p], pos[:, j])
    return wp_, wr


# ------------------------------------------------------------------------------------------------ model space helpers
def _ry(a) -> np.ndarray:
    a = np.asarray(a, float)
    c, s = np.cos(a), np.sin(a)
    R = np.zeros(a.shape + (3, 3))
    R[..., 0, 0], R[..., 0, 2], R[..., 1, 1], R[..., 2, 0], R[..., 2, 2] = c, s, 1.0, -s, c
    return R


def heading(right: np.ndarray, left: np.ndarray) -> np.ndarray:
    """Heading from the hip line (training's compute_heading_method='hips_pos'): forward = up x (right - left)."""
    ax, az = right[..., 0] - left[..., 0], right[..., 2] - left[..., 2]
    n = np.sqrt(ax * ax + az * az)
    fx, fz = az / np.maximum(n, 1e-9), -ax / np.maximum(n, 1e-9)
    return np.where(n > 1e-6, np.arctan2(fx, fz), 0.0)


def _check_soma(m: Motion) -> dict[str, int]:
    idx = {n: i for i, n in enumerate(m.names)}
    miss = [n for n in joint_names() if n not in idx]
    if miss:
        raise RuntimeError("MotionBricks needs a SOMA skeleton (Kimodo output); missing joints: " + ", ".join(miss[:8]))
    return idx


def place(m: Motion, world_pos: np.ndarray, world_rot: np.ndarray, dest_xz, dest_heading: float, frame: int = 0):
    """Rigidly move a motion (world arrays) on the ground so `frame`'s hips sit at dest_xz and face dest_heading."""
    idx = _check_soma(m)
    h0 = float(heading(world_pos[frame, idx["RightLeg"]], world_pos[frame, idx["LeftLeg"]]))
    Ry = _ry(dest_heading - h0)
    hip = world_pos[frame, idx["Hips"]]
    p = np.einsum("ij,tkj->tki", Ry, world_pos - np.array([hip[0], 0.0, hip[2]])) + np.array([dest_xz[0], 0.0, dest_xz[1]])
    r = np.einsum("ij,tkjl->tkil", Ry, world_rot)
    return p, r


# ------------------------------------------------------------------------------------------------ inference
def _encode(ctx_p, ctx_r, tgt_p, tgt_r, n_target: int, idx: dict[str, int], model_joints: list[int], tokens: tuple[int, ...]):
    """8 poses (4 context + 4 target, world space) -> ONNX inputs + the alignment transform (numpy port of the Warp kernels)."""
    P = np.concatenate([ctx_p, tgt_p], 0)            # (8,J,3)
    R = np.concatenate([ctx_r, tgt_r], 0)            # (8,J,3,3)
    rl, ll, hips = idx["RightLeg"], idx["LeftLeg"], idx["Hips"]
    last = POSES_PER_TOKEN - 1
    cur = float(heading(P[last, rl], P[last, ll]))
    ta = np.array([P[last, hips, 0], 0.0, P[last, hips, 2]])
    qa = _ry(cur - DESIRED_HEADING)
    qa_inv = qa.T
    A = np.einsum("ij,fkj->fki", qa_inv, P - ta)      # aligned positions
    AR = np.einsum("ij,fkjl->fkil", qa_inv, R)        # aligned rotations
    h = heading(A[:, rl], A[:, ll])
    nxt = [f if f == last else min(f + 1, 7) for f in range(8)]
    dh = np.arctan2(np.sin(h[nxt] - h), np.cos(h[nxt] - h))
    hp = A[:, hips]
    g = np.stack([hp[:, 0], hp[:, 1], hp[:, 2], np.cos(h), np.sin(h)], 1)
    lr = np.stack([dh * FPS, (hp[nxt, 0] - hp[:, 0]) * FPS, (hp[nxt, 2] - hp[:, 2]) * FPS, hp[:, 1]], 1)
    root_ground = np.stack([hp[:, 0], np.zeros(8), hp[:, 2]], 1)
    pos_feat = (A[:, model_joints[1:]] - root_ground[:, None]).reshape(8, -1)
    rr = AR[:, model_joints] @ standard_offsets()[None]  # (8,30,3,3) in the capture T pose
    rot_feat = np.concatenate([rr[..., :, 0], rr[..., :, 1]], -1).reshape(8, -1)
    lp = np.concatenate([pos_feat, rot_feat], 1)
    given = np.array([1 if i < POSES_PER_TOKEN + n_target else 0 for i in range(8)], np.int32)
    has_lr = given.copy()
    has_lr[last] = 0
    has_lr[POSES_PER_TOKEN + n_target - 1] = 0
    allowed = np.array([1 if MIN_TOKENS + i in tokens else 0 for i in range(MAX_TOKENS - MIN_TOKENS + 1)], np.int32)
    feeds = {"global_root_values": g[None].astype(np.float32), "has_global_root_values": given[None],
             "local_root_values": lr[None].astype(np.float32), "has_local_root_values": has_lr[None],
             "local_poses": lp[None].astype(np.float32), "has_local_poses": given[None].copy(),
             "allowed_pred_num_tokens": allowed[None]}
    return feeds, ta, qa


def _rot6d(x: np.ndarray) -> np.ndarray:
    xa = x[..., 0:3] / np.maximum(np.linalg.norm(x[..., 0:3], axis=-1, keepdims=True), 1e-9)
    za = np.cross(xa, x[..., 3:6])
    za /= np.maximum(np.linalg.norm(za, axis=-1, keepdims=True), 1e-9)
    ya = np.cross(za, xa)
    return np.stack([xa, ya, za], -1)


def generate_between(m: Motion, ctx_p, ctx_r, tgt_p, tgt_r, n_target: int, tokens: tuple[int, ...] = tuple(range(8, 15))):
    """Context (4 world poses) + target (n_target world poses, padded to 4) -> generated local poses (F,J,3)/(F,J,3,3),
    endpoint-corrected to start at the last context pose and end at the first target pose."""
    idx = _check_soma(m)
    mj = [idx[n] for n in joint_names()]
    feeds, ta, qa = _encode(ctx_p, ctx_r, tgt_p, tgt_r, n_target, idx, mj, tokens)
    out = dict(zip([o.name for o in _sess().get_outputs()], _sess().run(None, feeds)))
    n_tok = int(out["pred_num_tokens"][0])
    F = max(0, n_tok * POSES_PER_TOKEN - 2 * BOUNDARY)
    feat = out["pred_global_motions"][0, BOUNDARY:BOUNDARY + F].astype(np.float64)
    nm = len(mj)
    rot_off = 5 + (nm - 1) * 3
    G = _rot6d(feat[:, rot_off:rot_off + 6 * nm].reshape(F, nm, 6))      # (F,30,3,3) aligned model space
    Gw = np.einsum("ij,fkjl->fkil", qa, G) @ np.transpose(standard_offsets(), (0, 2, 1))[None]   # world, standard T pose
    hips_w = np.einsum("ij,fj->fi", qa, feat[:, 0:3]) + ta
    # local transforms on the full skeleton: model joints from the prediction, the rest from the reference (rest) pose
    J = len(m.names)
    pos = np.broadcast_to(m.offsets, (F, J, 3)).copy()
    rot = np.broadcast_to(np.eye(3), (F, J, 3, 3)).copy()
    world_of = {j: k for k, j in enumerate(mj)}
    hips = idx["Hips"]
    for j in range(J):
        if j == hips:
            par = m.parents[j]
            if par >= 0:                                  # e.g. Kimodo's static 'Root' above Hips (identity is assumed)
                pass
            pos[:, j], rot[:, j] = hips_w, Gw[:, world_of[j]]
        elif j in world_of and m.parents[j] in world_of:
            rot[:, j] = np.einsum("fji,fjk->fik", Gw[:, world_of[m.parents[j]]], Gw[:, world_of[j]])
    return pos, rot, n_tok


def _endpoint_correct(m: Motion, pos, rot, ctx_last_pos, ctx_last_rot, tgt0_pos, tgt0_rot, hips: int):
    """Reference _post_process_generated_poses: start delta fades out while the end delta fades in (local space; Hips in world)."""
    F = pos.shape[0]
    if F < 2:
        return pos, rot
    w = np.arange(F) / (F - 1)
    J = pos.shape[1]
    for j in range(J):
        if j == hips:
            dps, dpe = ctx_last_pos[j] - pos[0, j], tgt0_pos[j] - pos[-1, j]
            dqs = Rotation.from_matrix(rot[0, j].T @ ctx_last_rot[j])
            dqe = Rotation.from_matrix(rot[-1, j].T @ tgt0_rot[j])
            pos[:, j] = pos[:, j] + dps[None] * (1 - w)[:, None] + dpe[None] * w[:, None]
        else:
            dqs = Rotation.from_matrix(rot[0, j].T @ ctx_last_rot[j])
            dqe = Rotation.from_matrix(rot[-1, j].T @ tgt0_rot[j])
        s = Rotation.from_rotvec(dqs.as_rotvec()[None] * (1 - w)[:, None]).as_matrix()
        e = Rotation.from_rotvec(dqe.as_rotvec()[None] * w[:, None]).as_matrix()
        rot[:, j] = rot[:, j] @ s @ e
    return pos, rot


def _to_local(m: Motion, wpos: np.ndarray, wrot: np.ndarray, hips: int):
    """World arrays -> local arrays (positions stay offsets except Hips, which carries the world placement)."""
    T, J = wpos.shape[:2]
    pos = np.broadcast_to(m.offsets, (T, J, 3)).copy()
    rot = np.zeros((T, J, 3, 3))
    for j, p in enumerate(m.parents):
        if p < 0 or j == hips:
            rot[:, j] = wrot[:, j] if j == hips else np.eye(3)
            if j == hips:
                pos[:, j] = wpos[:, j]
        else:
            rot[:, j] = np.einsum("tji,tjk->tik", wrot[:, p], wrot[:, j])
    return pos, rot


def _ground_speed(wp_: np.ndarray, hips: int, frames: slice) -> float:
    xz = wp_[frames, hips][:, [0, 2]]
    return float(np.linalg.norm(np.diff(xz, axis=0), axis=1).mean() * FPS) if len(xz) > 1 else 0.0


def join(a_path: str, b_path: str, out_path: str | Path, *, distance: float | None = None, turn_deg: float = 0.0,
         tokens: tuple[int, ...] = tuple(range(8, 15)), loop: bool = False, a_end: float | None = None,
         b_start: float | None = None) -> dict:
    """A + generated transition + B (or, with loop=True, A + the transition back to A's own start) -> SOMA BVH.

    distance: how far B starts from where A ends (metres along A's final heading); default from A's end and B's start speed.
    turn_deg: B's heading relative to A's final heading. a_end / b_start: trim A after / B before these seconds."""
    t0 = time.time()
    A = resample(read_bvh(a_path))
    B = A if (loop or b_path == a_path) else resample(read_bvh(b_path))
    if A.names != B.names:
        raise RuntimeError("Both motions must use the same SOMA skeleton.")
    idx = _check_soma(A)
    hips = idx["Hips"]
    if a_end is not None:
        A = Motion(A.names, A.parents, A.offsets, A.channels, A.pos[: max(4, int(round(a_end * FPS)) + 1)],
                   A.rot[: max(4, int(round(a_end * FPS)) + 1)], FPS, A.end_sites)
    if b_start is not None and not loop:
        k = min(B.T - 1, int(round(b_start * FPS)))
        B = Motion(B.names, B.parents, B.offsets, B.channels, B.pos[k:], B.rot[k:], FPS, B.end_sites)
    if A.T < 4 or B.T < 1:
        raise RuntimeError("Each motion needs at least 4 frames (0.13 s).")
    Ap, Ar = fk(A)
    Bp, Br = fk(B)
    # parents above Hips (Kimodo 'Root') are folded into the world arrays; the output writes them as identity
    hA = float(heading(Ap[-1, idx["RightLeg"]], Ap[-1, idx["LeftLeg"]]))
    if distance is None:
        v = 0.5 * (_ground_speed(Ap, hips, slice(-10, None)) + _ground_speed(Bp, hips, slice(0, 10)))
        distance = float(np.clip(v * 1.3, 0.0, 4.0))
    fwd = np.array([math.sin(hA), math.cos(hA)])
    dest = np.array([Ap[-1, hips, 0], Ap[-1, hips, 2]]) + distance * fwd
    if loop:
        # back to the clip's own start: same facing as frame 0 (a clip that turns around must turn back), so every cycle lines up
        h0 = float(heading(Bp[0, idx["RightLeg"]], Bp[0, idx["LeftLeg"]]))
        Bp, Br = place(B, Bp, Br, dest, h0 + math.radians(turn_deg))
    else:
        Bp, Br = place(B, Bp, Br, dest, hA + math.radians(turn_deg))
    nt = min(4, B.T)
    tgt_p = np.concatenate([Bp[:nt], np.repeat(Bp[nt - 1:nt], 4 - nt, 0)], 0)
    tgt_r = np.concatenate([Br[:nt], np.repeat(Br[nt - 1:nt], 4 - nt, 0)], 0)
    gpos, grot, n_tok = generate_between(A, Ap[-4:], Ar[-4:], tgt_p, tgt_r, nt, tokens)
    Al_p, Al_r = _to_local(A, Ap, Ar, hips)
    Bl_p, Bl_r = _to_local(B, Bp, Br, hips)
    gpos, grot = _endpoint_correct(A, gpos, grot, Al_p[-1], Al_r[-1], Bl_p[0], Bl_r[0], hips)
    # drop the duplicated endpoints: the transition starts at A's last pose and ends at B's first pose
    gpos, grot = gpos[1:-1], grot[1:-1]
    parts_p = [Al_p, gpos] + ([] if loop else [Bl_p])
    parts_r = [Al_r, grot] + ([] if loop else [Bl_r])
    P, R = np.concatenate(parts_p, 0), np.concatenate(parts_r, 0)
    for j, p in enumerate(A.parents):                    # anything above Hips: identity / origin
        if j != hips and (p < 0 or j in _ancestors(A, hips)):
            R[:, j] = np.eye(3)
            P[:, j] = 0.0 if p < 0 else A.offsets[j]
    out = Motion(A.names, A.parents, A.offsets, A.channels, P, R, FPS, A.end_sites)
    write_bvh(out, out_path)
    return {"path": str(out_path), "frames": out.T, "fps": FPS, "a_frames": A.T, "transition_frames": int(gpos.shape[0]),
            "b_frames": 0 if loop else B.T, "tokens": n_tok, "distance_m": round(distance, 3), "turn_deg": turn_deg, "loop": loop,
            "seconds": round(time.time() - t0, 2)}


def _ancestors(m: Motion, j: int) -> set[int]:
    out, p = set(), m.parents[j]
    while p >= 0:
        out.add(p)
        p = m.parents[p]
    return out
