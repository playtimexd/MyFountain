"""Objective quality metrics for a retargeted clip, computed from `bake.npz` (see backend/blender/retarget.py).
Pure numpy so it also runs inside Blender's Python (loaded by path). World frame = Blender (Z up), metres, ground z = 0."""
from __future__ import annotations

import numpy as np

RUBRIC = [
    "Feet: do planted feet stay put on the ground (no skating), and do they leave the ground only during swing?",
    "Ground contact: no foot / paw sinks through the floor and the character does not hover.",
    "Orientation: legs bend the natural way (knees/elbows not inverted), head and spine face the travel direction.",
    "Proportions: limbs keep their length, no stretching / collapsing, body height looks right for the species.",
    "Smoothness: no popping or jitter between frames; motion reads as one continuous gait.",
    "Loop: if the clip is meant to loop, the last frame flows into the first without a visible jump.",
    "Fidelity: gait pattern (footfall order, stride, head bob) resembles the reference motion.",
]


def _qmul(a, b):
    w1, x1, y1, z1 = np.moveaxis(a, -1, 0)
    w2, x2, y2, z2 = np.moveaxis(b, -1, 0)
    return np.stack([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2], axis=-1)


def _qinv(q):
    return q * np.array([1, -1, -1, -1.0])


def _angle(q):
    return 2 * np.arccos(np.clip(np.abs(q[..., 0]), 0, 1))


def _sg(win, order=2):
    mm = win // 2
    A = np.vander(np.arange(-mm, mm + 1), order + 1, increasing=True).astype(float)
    return np.linalg.pinv(A)[0]


_NOT_BODY = __import__("re").compile(r"(^|[_.\-\s])(root|master|armature|world|origin|trajectory|interaction|center_of_mass|centerofmass|com|attach|socket|weapon|prop)$|(^|[_.\-\s])(ik|pole|target|ctrl)([_.\-\s]|$)|^ik", __import__("re").I)


def body_bones(names) -> list[int]:
    """Indices of bones that belong to the body: drops engine root bones (UE `root` stays at the origin) and IK / pole helpers."""
    idx = [i for i, n in enumerate(names or []) if not _NOT_BODY.search(str(n))]
    return idx or list(range(len(names or [])))


def _find(names, pats, side=None):
    import re
    for pat in pats:
        for i, n in enumerate(names):
            ln = n.lower()
            if side == "l" and not re.search(r"(^|[_.\-\s])(l|left)([_.\-\s]|$)|left|_l$|\.l$", ln):
                continue
            if side == "r" and not re.search(r"(^|[_.\-\s])(r|right)([_.\-\s]|$)|right|_r$|\.r$", ln):
                continue
            if re.search(pat, ln) and not re.search(r"twist|ik_|_ik|pole|roll", ln):
                return i
    return None


def pose_checks(names, head, tail) -> dict:
    """Humanoid pose plausibility (degrees / % of height): torso bent backwards, knees bending the wrong way.
    The other metrics only look at feet and smoothness, so a contorted body could still score high without this."""
    out = {}
    names = [str(n) for n in names]
    pel = _find(names, [r"^pelvis$|^hips?$|pelvis|hips"])
    top = _find(names, [r"^neck|neck_01|neck1", r"spine_05|upperchest|chest|spine3|spine_03"])
    thl, thr = _find(names, [r"thigh|upleg|upperleg|^leftleg$|^rightleg$"], "l"), _find(names, [r"thigh|upleg|upperleg|^leftleg$|^rightleg$"], "r")
    cal, car = _find(names, [r"calf|shin|lowerleg|knee"], "l"), _find(names, [r"calf|shin|lowerleg|knee"], "r")
    ftl, ftr = _find(names, [r"foot|ankle"], "l"), _find(names, [r"foot|ankle"], "r")
    if None in (pel, top, thl, thr, cal, car, ftl, ftr):
        return out
    tol, tor = _find(names, [r"ball|toebase|toe"], "l"), _find(names, [r"ball|toebase|toe"], "r")
    if tol is None or tor is None:
        return out
    # forward = where the toes point (both feet, horizontal, smoothed over the clip so a single odd frame cannot flip it)
    fwd = (head[:, tol] - head[:, ftl]) + (head[:, tor] - head[:, ftr])
    fwd[:, 2] = 0
    k = 9
    if fwd.shape[0] > k:
        fwd = np.stack([np.convolve(np.pad(fwd[:, i], (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid") for i in range(3)], 1)
    fwd = fwd / np.linalg.norm(fwd, axis=1, keepdims=True).clip(1e-6, None)
    torso = head[:, top] - head[:, pel]
    lean_back = np.degrees(np.arctan2(-(torso * fwd).sum(1), torso[:, 2].clip(1e-6, None)))
    out["torso_back_lean_p90_deg"] = float(np.percentile(lean_back, 90))
    h = float(np.ptp(head[..., 2])) or 1.0
    hyper = []
    for th, ca, ft in ((thl, cal, ftl), (thr, car, ftr)):
        mid = (head[:, th] + head[:, ft]) / 2                 # hip-ankle line (bone tails are unreliable on FBX rigs)
        off = ((head[:, ca] - mid) * fwd).sum(1)          # >0: knee in front of the hip-ankle line (normal)
        hyper.append(-off / h * 100)
    out["knee_backward_p90_pct_height"] = float(np.percentile(np.max(np.stack(hyper), axis=0), 90))
    return out


def compute(bake: dict) -> dict:
    fps = float(bake["fps"])
    head = np.asarray(bake["head"], float)          # (T,B,3) with root travel included
    tail = np.asarray(bake["tail"], float)
    quat = np.asarray(bake["quat"], float)          # (T,B,4) local pose rotations
    T = head.shape[0]
    height = float(bake.get("height", 1.0)) or 1.0
    feet = [int(i) for i in np.asarray(bake.get("foot_bones", [])).reshape(-1)]
    rest_floor = np.asarray(bake.get("foot_rest_floor", np.zeros(len(feet))), float).reshape(-1)
    out: dict = {"frames": int(T), "fps": fps, "duration": T / fps, "height_m": height}
    if "is_loop" in bake:
        out["is_loop"] = bool(np.asarray(bake["is_loop"]).reshape(-1)[0])

    # ---- root travel
    root_xy = np.asarray(bake.get("root_path", head[:, 0, :2]), float)[:, :2]
    path = np.linalg.norm(np.diff(root_xy, axis=0), axis=1).sum() if T > 1 else 0.0
    out["root_travel_m"] = float(path)
    out["root_speed_mps"] = float(path / max(T / fps, 1e-6))
    out["root_speed_heights_per_s"] = out["root_speed_mps"] / height

    # ---- feet
    if feet:
        sole = np.stack([np.minimum(head[:, f, 2], tail[:, f, 2]) - rest_floor[k] for k, f in enumerate(feet)], axis=1)   # (T,F)
        # uneven ground (climbing, vaulting, dropping off a ledge): the retarget saves the surfaces the source's feet stood on.
        # The floor lies between terrain_lo and terrain_hi; a flat-floor clip (or an old bake) has both at 0.
        lo, hi = np.zeros(T), np.zeros(T)
        ff = np.zeros_like(sole)                                           # the surface under each foot (T,F)
        if "terrain_lo" in bake and np.asarray(bake["terrain_lo"]).shape == (T,):
            lo, hi = np.asarray(bake["terrain_lo"], float), np.asarray(bake["terrain_hi"], float)
            out["terrain_range_m"] = float(hi.max() - lo.min())
            ff = np.asarray(bake["foot_floor"], float) if np.asarray(bake.get("foot_floor", [])).shape == sole.shape else lo[:, None] + ff
        contact = sole - ff < 0.02 * height
        # "planted" also needs the source to say so (the contacts the retarget saved and foot-locked): real mocap swings the
        # toe only 2-4 cm over the floor and a running foot skims it at full speed, so height alone counted those as slides
        planted = contact.copy()
        # prefer 'settled' (low AND standing still in the source): height-only contacts start while a landing foot is still
        # decelerating from swing speed, which read as sliding on clean mocap (GASP walks: 38%/s of pure landing motion)
        src_c = bake.get("settled") if "settled" in bake and np.asarray(bake["settled"]).any() else bake.get("contacts")
        if src_c is not None and np.asarray(src_c).shape == contact.shape and np.asarray(src_c).any():
            planted &= np.asarray(src_c, bool)
        interior = planted.copy()                                          # ignore ~0.067 s at each end of a contact (touch-down / lift-off)
        for _ in range(max(2, int(round(0.067 * fps)))):
            interior = interior & np.roll(interior, 1, axis=0) & np.roll(interior, -1, axis=0)
        both = interior[1:] & interior[:-1]
        # slide = the slower end of the foot bone (a rolling foot keeps its heel or its ball still; a sliding foot moves both).
        # Switching to "whichever end is lower" jumped a whole foot length in one frame when the ends crossed.
        ends = np.stack([head[:, feet, :2], tail[:, feet, :2]], axis=0)  # (2,T,F,2)
        speed = (np.linalg.norm(np.diff(ends, axis=1), axis=3) * fps).min(axis=0)   # (T-1,F)
        slide = speed[both]
        out["foot_slide_mps"] = float(slide.mean()) if slide.size else 0.0
        out["foot_slide_p95_mps"] = float(np.percentile(slide, 95)) if slide.size else 0.0
        out["foot_slide_pct_height_per_s"] = 100 * out["foot_slide_mps"] / height
        # slide relative to what travelling at root speed would give: ratio of 1 = foot moves with the body (ice skating)
        out["foot_slide_ratio"] = out["foot_slide_mps"] / max(out["root_speed_mps"], 0.05)
        out["contact_fraction"] = float(contact.mean())
        pen = np.clip(ff - sole, 0, None)
        out["ground_penetration_max_pct_height"] = float(100 * pen.max() / height)
        out["ground_penetration_frames_pct"] = float(100 * (pen > 0.01 * height).any(axis=1).mean())
        floor0 = float(rest_floor.min()) if rest_floor.size else 0.0
        bb = body_bones([str(n) for n in np.asarray(bake.get("names", [])).reshape(-1)]) if "names" in bake else list(range(head.shape[1]))
        body_low = np.minimum(head[:, bb, 2], tail[:, bb, 2]).min(axis=1) - floor0     # lowest point of the whole body (T,)
        lowest = np.minimum(sole.min(axis=1), body_low) - hi                          # above the highest surface nearby
        # airborne on purpose (jumps, falls): the body's centre accelerates down at about g -> not "floating"
        com = head[:, bb, 2].mean(axis=1)
        air = np.zeros(T, bool)
        if T > 4:
            acc = np.zeros(T)
            acc[1:-1] = (com[2:] - 2 * com[1:-1] + com[:-2]) * fps * fps
            acc = np.convolve(acc, np.ones(3) / 3, mode="same")
            vz = np.gradient(com) * fps
            # free fall (~g), or moving up / down faster than any step or landing: game falls are often constant-speed
            air = (((acc < -5.0) & (acc > -16.0)) | (np.abs(vz) > 2.0)) & (lowest > 0.02 * height)
            air = air | np.roll(air, 1) | np.roll(air, -1)                                  # include take-off / landing frames
        grounded = ~air
        fl = np.clip(lowest[grounded], 0, None) if grounded.any() else np.zeros(1)
        out["airborne_frames_pct"] = float(100 * air.mean())
        out["floating_mean_pct_height"] = float(100 * fl.mean() / height)
        out["floating_max_pct_height"] = float(100 * fl.max() / height)
        # gait cycle estimate from the first foot height signal
        sig = sole[:, 0] - sole[:, 0].mean()
        if T > 8 and sig.std() > 1e-5:
            spec = np.abs(np.fft.rfft(sig * np.hanning(T)))
            fr = np.fft.rfftfreq(T, 1 / fps)
            spec[fr < 0.3] = 0
            out["cycle_hz"] = float(fr[int(spec.argmax())])
    # ---- jitter: how far local rotations stray from a smoothed copy of themselves (degrees); real motion is smooth at 30 fps
    if T > 9:
        qs = quat.copy()
        for t in range(1, T):
            flip = (qs[t] * qs[t - 1]).sum(-1) < 0
            qs[t] = np.where(flip[..., None], -qs[t], qs[t])
        win = 7
        co = _sg(win)
        pad = np.concatenate([np.repeat(qs[:1], win // 2, 0), qs, np.repeat(qs[-1:], win // 2, 0)], 0)
        sm = sum(co[i] * pad[i:i + T] for i in range(win))
        sm /= np.linalg.norm(sm, axis=-1, keepdims=True).clip(1e-9, None)
        res = np.degrees(_angle(_qmul(qs, _qinv(sm))))
        res = np.where(res > 180, 360 - res, res)[2:-2]
        out["jitter_mean_deg"] = float(res.mean())
        out["jitter_p95_deg"] = float(np.percentile(res, 95))
        out["jitter_max_deg"] = float(res.max())
    # ---- loop seam (pose difference first vs last, root horizontal removed, per-frame motion as unit).
    # Body bones only: UE helper bones (ik_foot_*, ik_hand_*, center_of_mass, interaction) sit at the origin and do not follow
    # the travel, so they made every travelling loop look broken.
    if T > 2:
        bb = body_bones([str(n) for n in np.asarray(bake["names"]).reshape(-1)]) if "names" in bake else list(range(head.shape[1]))
        hb = head[:, bb]

        def rel(t):
            return hb[t] - np.array([root_xy[t, 0], root_xy[t, 1], 0.0])
        rel0, rel1 = rel(0), rel(T - 1)
        seam = np.linalg.norm(rel0 - rel1, axis=1)
        step = np.linalg.norm(rel0 - rel(1), axis=1)
        out["loop_seam_mean_pct_height"] = float(100 * seam.mean() / height)
        out["loop_seam_max_pct_height"] = float(100 * seam.max() / height)
        out["loop_seam_vs_step"] = float(seam.mean() / max(step.mean(), 1e-6))     # ~1 = like a normal frame step
        dq = _qmul(quat[0], _qinv(quat[-1]))
        a = np.degrees(_angle(dq))
        a = np.where(a > 180, 360 - a, a)
        out["loop_seam_max_deg"] = float(a.max())
    # ---- pose plausibility (humanoids)
    try:
        if "names" in bake:
            out.update(pose_checks(np.asarray(bake["names"]).reshape(-1), head, tail))
    except Exception:  # noqa: BLE001
        pass
    # ---- bone-length stretch (should be ~0 for pure-rotation animation)
    L = np.linalg.norm(tail - head, axis=2)
    L0 = np.median(L, axis=0).clip(1e-6, None)
    out["bone_stretch_max_pct"] = float(100 * np.abs(L / L0 - 1).max())
    out["score"] = score(out)
    out["issues"] = issues(out)
    return out


def issues(m: dict) -> list[dict]:
    """Rule-based findings from the numbers alone (no model involved)."""
    res = []

    def add(sev, code, msg, hint):
        res.append({"severity": sev, "code": code, "message": msg, "suggestion": hint})

    s = m.get("foot_slide_pct_height_per_s")
    if s is not None:
        if s > 25:
            add("high", "foot_sliding", f"Planted feet slide {s:.0f}% of body height per second.", "Enable foot lock, or lower the scale / use in-place with a matching stride.")
        elif s > 10:
            add("medium", "foot_sliding", f"Some foot sliding ({s:.0f}% of height per second).", "Enable foot lock or raise smoothing.")
    p = m.get("ground_penetration_max_pct_height")
    if p is not None and p > 3:
        add("high" if p > 8 else "medium", "ground_penetration", f"Feet sink up to {p:.1f}% of body height into the floor.", "Enable ground fix; check that the rig origin is at the feet.")
    f = m.get("floating_mean_pct_height")
    if f is not None and f > 3:
        add("medium", "floating", f"Character hovers {f:.1f}% of its height above the floor on average.", "Enable ground fix, or check the scale option.")
    j = m.get("jitter_p95_deg")
    if j is not None and j > 5:
        add("high" if j > 12 else "medium", "jitter", f"Rotation jitter p95 = {j:.1f} deg.", "Increase smoothing.")
    bl = m.get("torso_back_lean_p90_deg")
    if bl is not None and bl > 25:
        add("high", "pose_back", f"The torso bends backwards by up to {bl:.0f} degrees.",
            "The source motion is probably wrong (e.g. motion capture from a side view). Use a front / three-quarter video or 'Describe the motion'.")
    kb = m.get("knee_backward_p90_pct_height")
    if kb is not None and kb > 2:
        add("high", "pose_knee", "Knees bend the wrong way.", "Check the bone map (thigh / calf) or use a cleaner source.")
    sv = m.get("loop_seam_vs_step") if m.get("is_loop", True) else None
    if sv is not None and sv > 3:
        add("medium", "loop_seam", f"The last frame differs from the first by {sv:.1f}x a normal frame step.", "Enable loop closing or trim the clip to a whole number of cycles.")
    st = m.get("bone_stretch_max_pct")
    if st is not None and st > 2:
        add("medium", "stretch", f"Bones change length by up to {st:.1f}%.", "Check that the rig has no scale animation / non-uniform scale.")
    return res


def score(m: dict) -> float:
    """0..100 heuristic quality score (higher = better)."""
    sc = 100.0
    sc -= min(35, max(0, m.get("foot_slide_pct_height_per_s", 0) - 3) * 0.9)
    sc -= min(20, m.get("ground_penetration_max_pct_height", 0) * 2)
    sc -= min(15, m.get("floating_mean_pct_height", 0) * 2)
    sc -= min(15, max(0, m.get("jitter_p95_deg", 0) - 1) * 3)
    sc -= min(25, max(0, m.get("torso_back_lean_p90_deg", 0) - 25) * 0.8)     # torso bent backwards (contorted spine)
    sc -= min(20, max(0, m.get("knee_backward_p90_pct_height", 0) - 2) * 3)    # knees bending the wrong way
    if m.get("is_loop", True):                     # a one-shot action (attack, jump, roll) does not need matching ends
        sc -= min(10, max(0, m.get("loop_seam_vs_step", 0) - 1) * 2)
    sc -= min(5, m.get("bone_stretch_max_pct", 0))
    return round(max(0.0, sc), 1)


def compute_from_npz(path) -> dict:
    d = np.load(path, allow_pickle=False)
    return compute({k: d[k] for k in d.files})
