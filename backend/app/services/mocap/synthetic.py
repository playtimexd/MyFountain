"""Analytic ground-truth walkers (human + quadruped) and a weak-perspective camera simulator.

Used by the unit tests and by the dev-only ``mock`` 2D-pose backend.  Feet are world-static during stance
(no sliding), legs are placed with analytic two-bone IK, so the ground truth is a physically consistent gait.
Convention (world == gravity-aligned camera frame): +X image right, +Y up, +Z toward the viewer.
"""
from __future__ import annotations

import numpy as np

from .skeletons import SkeletonDef, get_skeleton


def _smooth_step(u):
    return 0.5 - 0.5 * np.cos(np.pi * np.clip(u, 0, 1))


def _ik2(hip_xy, tgt_xy, l1, l2, mid_forward: bool):
    """Two-bone IK in the sagittal (x fwd, y up) plane; returns the middle joint (knee/elbow) xy."""
    t = np.asarray(tgt_xy, float) - np.asarray(hip_xy, float)
    d = np.linalg.norm(t)
    d = np.clip(d, 1e-6, (l1 + l2) * 0.999)
    td = t / max(np.linalg.norm(t), 1e-9)
    cosa = np.clip((l1 ** 2 + d ** 2 - l2 ** 2) / (2 * l1 * d), -1, 1)
    a = np.arccos(cosa)
    a = a if mid_forward else -a
    # rotate td by +a (counter-clockwise in x-fwd,y-up) -> middle joint goes forward when td points down
    c, s = np.cos(a), np.sin(a)
    r = np.array([c * td[0] - s * td[1], s * td[0] + c * td[1]])
    return np.asarray(hip_xy, float) + r * l1


def walk_sequence(subject: str, n_frames: int = 120, fps: float = 30.0, stride_hz: float | None = None,
                  speed: float | None = None, size: float = 1.0, heading_deg: float = 0.0, seed: int = 0,
                  proportion_jitter: float = 0.0):
    """Generate a walking gait. Returns dict(joints3d (T,17,3) world/camera frame, pelvis (T,3), names, skeleton,
    contacts (T,F) bool, feet idx list, heading rad, fps)."""
    rng = np.random.default_rng(seed)
    sk = get_skeleton("coco17" if subject == "humanoid" else "ap10k17")
    rest = sk.rest * size
    if proportion_jitter > 0:                                   # make the truth deviate from the canonical proportions
        grp = sk.length_groups()
        fac = {g: float(np.exp(rng.normal(0, proportion_jitter))) for g in set(grp.tolist())}
        new = rest.copy()
        for j in sk.order():
            p_ = sk.parent[j]
            if p_ >= 0:
                new[j] = new[p_] + (rest[j] - rest[p_]) * fac[int(grp[j])]
        # keep the standing foot height (so the ground is still y=0): shift down by the lowest foot offset
        rest = new
        rest[:, 1] -= min(rest[sk.idx(n)][1] - sk.rest[sk.idx(n)][1] * size for n in sk.feet)
    T = n_frames
    t = np.arange(T) / fps
    if subject == "humanoid":
        f = stride_hz or 0.9           # stride = full cycle (L+R) ~ 0.9 Hz
        v = speed or 1.25 * size
        beta = 0.6
        phases = {"left_ankle": 0.0, "right_ankle": 0.5}
        arm_phase = {"left_wrist": 0.5, "right_wrist": 0.0}
        leg_mid_forward = True
        arm_mid_forward = False
    else:
        f = stride_hz or 0.8
        v = speed or 0.9 * size
        beta = 0.7
        phases = {"left_back_paw": 0.0, "left_front_paw": 0.25, "right_back_paw": 0.5, "right_front_paw": 0.75}
        arm_phase = {}
        leg_mid_forward = True
        arm_mid_forward = False
    L = v / f                          # stride length
    X = np.zeros((T, sk.n, 3))
    pel0 = rest[sk.idx("pelvis")]
    contacts = np.zeros((T, len(sk.feet)), bool)
    # body bob / sway
    bob = 0.012 * size * np.cos(4 * np.pi * f * t)
    sway = 0.0
    px = v * t
    # the animal walks along +X (body forward); the heading is applied afterwards as a yaw
    for i in range(T):
        pel = pel0 + np.array([px[i], bob[i], 0.0])
        for j in range(sk.n):
            X[i, j] = rest[j] - pel0 + pel          # start from the rest pose, then replace limbs
        # slight torso pitch oscillation (spine bends a little)
        # limbs via IK
        for gi, (a, b, c, sign, _mx) in enumerate(sk.hinges):
            ia, ib, ic = sk.idx(a), sk.idx(b), sk.idx(c)
            l1 = np.linalg.norm(rest[ib] - rest[ia])
            l2 = np.linalg.norm(rest[ic] - rest[ib])
            hip = X[i, ia]                                   # attached to the (rigid) torso
            ground_h = rest[ic][1] if False else rest[ic][1]  # foot height above ground at rest
            if c in phases:                                   # leg-like (foot on the ground)
                ph = (f * t[i] + phases[c]) % 1.0
                if ph < beta:                                 # stance: foot static in the world
                    xrel = (beta * L / 2) * (1 - 2 * ph / beta)
                    lift = 0.0
                    contacts[i, sk.feet.index(c)] = True
                else:                                         # swing
                    u = (ph - beta) / (1 - beta)
                    xrel = (beta * L / 2) * (-1 + 2 * _smooth_step(u))
                    lift = 0.12 * size * np.sin(np.pi * u) ** 1.0
                # x relative to the *pelvis* x of this limb's own rest offset (hips/shoulders sit at fixed x)
                tx = hip[0] + xrel + (rest[ic][0] - rest[ia][0])
                ty = rest[ic][1] + lift
                tgt = np.array([tx, ty])
                mid = _ik2(np.array([hip[0], hip[1]]), tgt, l1, l2, leg_mid_forward if sign < 0 else not (sign > 0))
                X[i, ic] = (tgt[0], tgt[1], hip[2] + (rest[ic][2] - rest[ia][2]))
                X[i, ib] = (mid[0], mid[1], hip[2] + (rest[ib][2] - rest[ia][2]))
            elif c in arm_phase:                              # human arms swing opposite to legs
                ph = (f * t[i] + arm_phase[c]) % 1.0
                swing = 0.22 * size * np.sin(2 * np.pi * ph + np.pi / 2 * 0) * (1 if True else -1)
                tgt = np.array([hip[0] + swing, hip[1] - (l1 + l2) * 0.93])
                mid = _ik2(np.array([hip[0], hip[1]]), tgt, l1, l2, arm_mid_forward)
                X[i, ic] = (tgt[0], tgt[1], hip[2] + (rest[ic][2] - rest[ia][2]))
                X[i, ib] = (mid[0], mid[1], hip[2] + (rest[ib][2] - rest[ia][2]))
            else:                                             # quadruped front limb handled above (c in phases)
                pass
    # head nod (tiny)
    nod = 0.01 * size * np.sin(2 * np.pi * f * 2 * t)
    for n in ("nose", "left_eye", "right_eye", "left_ear", "right_ear"):
        if n in sk.all_names:
            X[:, sk.idx(n), 1] += nod
    # lift the whole body so the lowest foot touches the ground at y=0 (rest foot height is already right)
    # yaw by heading (rotation about Y) around the world origin
    psi = np.deg2rad(heading_deg)
    c, s = np.cos(psi), np.sin(psi)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    Xw = X @ R.T
    joints = Xw[:, sk.out_idx]
    pelvis = Xw[:, sk.idx("pelvis")]
    return dict(joints3d=joints, pelvis=pelvis, names=list(sk.out_names), skeleton=sk.name, contacts=contacts,
                feet=[sk.out_names.index(n) for n in sk.feet], fps=fps, heading=psi, stride_hz=f, speed=v,
                size=size, full=Xw)


def camera_project(joints3d: np.ndarray, *, scale: float = 400.0, pitch_deg: float = 0.0,
                   center=(640.0, 400.0), offset=(0.0, 0.0)):
    """Weak-perspective projection. world +Y up / +Z toward viewer -> image px (x right, y down).
    The object is first rotated by the camera pitch (about X)."""
    p = np.deg2rad(pitch_deg)
    c, s = np.cos(p), np.sin(p)
    Rx = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    Xc = joints3d @ Rx.T
    x = center[0] + offset[0] + scale * Xc[..., 0]
    y = center[1] + offset[1] - scale * Xc[..., 1]
    return np.stack([x, y], -1), Xc


def simulate_2d(joints3d: np.ndarray, *, scale: float = 400.0, pitch_deg: float = 0.0, noise_px: float = 3.0,
                drop_rate: float = 0.08, far_side_drop: float = 0.15, lr_swap_rate: float = 0.0,
                outlier_rate: float = 0.01, seed: int = 0, image_size=(1280, 800), skeleton: str = "ap10k17"):
    """Simulated detector output: noisy 2D keypoints (T,J,2), confidences (T,J) with dropped/low-conf joints."""
    rng = np.random.default_rng(seed)
    T, J, _ = joints3d.shape
    W, H = image_size
    # place the subject so that it crosses the frame
    kp0, _ = camera_project(joints3d, scale=scale, pitch_deg=pitch_deg, center=(0, 0))
    mid = 0.5 * (kp0.reshape(-1, 2).min(0) + kp0.reshape(-1, 2).max(0))
    kp, Xc = camera_project(joints3d, scale=scale, pitch_deg=pitch_deg,
                            center=(W / 2 - mid[0], H / 2 - mid[1]))
    sk = get_skeleton(skeleton)
    kp_noisy = kp + rng.normal(0, noise_px, kp.shape)
    conf = np.clip(rng.normal(0.85, 0.08, (T, J)), 0.3, 1.0)
    # random drop-outs (conf ~ 0.02) and far-side (occluded) limbs get lower confidence/more drops
    drop = rng.random((T, J)) < drop_rate
    zfar = Xc[..., 2] < np.median(Xc[..., 2])
    drop |= (rng.random((T, J)) < far_side_drop) & zfar
    conf[drop] = rng.uniform(0.0, 0.12, drop.sum())
    kp_noisy[drop] += rng.normal(0, 40, (drop.sum(), 2))          # garbage position where conf is low
    out = rng.random((T, J)) < outlier_rate
    kp_noisy[out] += rng.normal(0, 60, (out.sum(), 2))            # confident outliers
    if lr_swap_rate > 0:
        names = sk.out_names
        pair = [(names.index(n), names.index(n.replace("left", "right"))) for n in names if n.startswith("left")]
        for t in range(T):
            if rng.random() < lr_swap_rate:
                for a, b in pair:
                    kp_noisy[t, [a, b]] = kp_noisy[t, [b, a]]
    return kp_noisy.astype(np.float32), conf.astype(np.float32), kp.astype(np.float32)


def render_stick_video(path, kp_clean: np.ndarray, skeleton: str, fps: float, size=(1280, 720)) -> None:
    """Write a simple video (stick figure on a flat background) from clean 2D keypoints (T,J,2) - test helper."""
    import subprocess

    import cv2

    from .skeletons import draw_edges
    edges = draw_edges(skeleton)
    W, H = size
    cmd = ["ffmpeg", "-hide_banner", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    ground = int(kp_clean[:, :, 1].max()) + 8
    for t in range(len(kp_clean)):
        img = np.full((H, W, 3), (70, 90, 70), np.uint8)
        cv2.line(img, (0, ground), (W, ground), (60, 70, 60), 2)
        for a, b in edges:
            cv2.line(img, tuple(int(c) for c in kp_clean[t, a]), tuple(int(c) for c in kp_clean[t, b]), (200, 200, 220), 6)
        for j in range(kp_clean.shape[1]):
            cv2.circle(img, tuple(int(c) for c in kp_clean[t, j]), 7, (60, 120, 255), -1)
        p.stdin.write(img.tobytes())
    p.stdin.close()
    p.wait()
