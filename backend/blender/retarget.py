"""Direction-based retargeting:  motion.npz (+ bone map)  ->  baked pose track on the target armature  (bake.npz).

args: {
  "rig_blend": path, "motion": motion.npz, "bone_map": {bone: joint}, "out_bake": bake.npz, "result": path,
  "options": {in_place, loop, scale, foot_lock, ground_fix, fps, smooth(0..1), start, end, secondary}
}
Method (all vectorised over frames, pure numpy; Blender is only used to read the rig and to verify):
  1. clean the source (gap fill, resample, Savitzky-Golay), map Y-up -> Z-up, align the heading with the rig's forward axis
  2. every mapped bone B whose nearest mapped descendant is C gets the world rotation that takes its rest direction
     (head_B -> head_C) to the source direction (joint_B -> joint_C) by the shortest arc.  Bones between B and C are blended,
     the root group follows the full body frame (spine axis + hip line), end bones follow their parent / the face points.
  3. root motion: horizontal travel scaled by (rig leg height / source leg height), vertical follows the source pelvis height.
  4. clean-up: foot contact detection from the source, floor fix, two-bone IK foot lock on every leg, flat feet, loop closing.
Everything is expressed as pose-bone rotations (+ location of the root bones) so the result is a normal bone animation.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402

import common  # noqa: E402

sk = common.load_local_module("skeletons")
metrics = common.load_local_module("metrics")
EPS = 1e-9


# ===================================================================================================== math helpers
def nrm(v, axis=-1):
    return v / np.maximum(np.linalg.norm(v, axis=axis, keepdims=True), EPS)


def q_mul(a, b):
    w1, x1, y1, z1 = np.moveaxis(a, -1, 0)
    w2, x2, y2, z2 = np.moveaxis(b, -1, 0)
    return np.stack([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2], axis=-1)


def q_from_mat(R):
    m = R
    t = m[..., 0, 0] + m[..., 1, 1] + m[..., 2, 2]
    w = np.sqrt(np.maximum(1 + t, 0)) / 2
    x = np.sqrt(np.maximum(1 + m[..., 0, 0] - m[..., 1, 1] - m[..., 2, 2], 0)) / 2
    y = np.sqrt(np.maximum(1 - m[..., 0, 0] + m[..., 1, 1] - m[..., 2, 2], 0)) / 2
    z = np.sqrt(np.maximum(1 - m[..., 0, 0] - m[..., 1, 1] + m[..., 2, 2], 0)) / 2
    x = np.copysign(x, m[..., 2, 1] - m[..., 1, 2])
    y = np.copysign(y, m[..., 0, 2] - m[..., 2, 0])
    z = np.copysign(z, m[..., 1, 0] - m[..., 0, 1])
    q = np.stack([w, x, y, z], axis=-1)
    # when w ~ 0 the sign trick above is unreliable: fall back to the largest component formulation
    small = w < 1e-3
    if np.any(small):
        qs = q[small]
        ms = m[small]
        idx = np.argmax(np.stack([x[small], y[small], z[small]], axis=-1), axis=-1)
        for k in range(qs.shape[0]):
            i = idx[k]
            r = ms[k]
            j, kk = (i + 1) % 3, (i + 2) % 3
            s = math.sqrt(max(r[i, i] - r[j, j] - r[kk, kk] + 1, 1e-12)) * 2
            v = np.zeros(4)
            v[1 + i] = 0.25 * s
            v[1 + j] = (r[j, i] + r[i, j]) / s
            v[1 + kk] = (r[kk, i] + r[i, kk]) / s
            v[0] = (r[kk, j] - r[j, kk]) / s
            qs[k] = v
        q[small] = qs
    return nrm(q)


def mat_from_q(q):
    q = nrm(q)
    w, x, y, z = np.moveaxis(q, -1, 0)
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], -2)


def q_slerp(a, b, t):
    t = np.asarray(t, float)
    while t.ndim < a.ndim:
        t = t[..., None]
    d = (a * b).sum(-1, keepdims=True)
    b = np.where(d < 0, -b, b)
    d = np.abs(d).clip(0, 1)
    th = np.arccos(d)
    s = np.sin(th)
    small = s < 1e-6
    wa = np.where(small, 1 - t, np.sin((1 - t) * th) / np.where(small, 1, s))
    wb = np.where(small, t, np.sin(t * th) / np.where(small, 1, s))
    return nrm(wa * a + wb * b)


def swing(a, b):
    """Shortest-arc rotation matrices taking unit vector(s) a to unit vector(s) b.  a:(3,) or (T,3), b:(T,3)."""
    a = np.broadcast_to(nrm(a), b.shape)
    b = nrm(b)
    c = np.cross(a, b)
    d = (a * b).sum(-1)
    s = np.linalg.norm(c, axis=-1)
    anti = (d < -0.999999)
    k = c / np.maximum(s, EPS)[..., None]
    # antiparallel: any perpendicular axis
    perp = nrm(np.cross(a, np.where(np.abs(a[..., :1]) < 0.9, np.array([1.0, 0, 0]), np.array([0, 1.0, 0]))))
    k = np.where(anti[..., None], perp, k)
    ang = np.arctan2(s, d)
    K = np.zeros(b.shape[:-1] + (3, 3))
    K[..., 0, 1], K[..., 0, 2] = -k[..., 2], k[..., 1]
    K[..., 1, 0], K[..., 1, 2] = k[..., 2], -k[..., 0]
    K[..., 2, 0], K[..., 2, 1] = -k[..., 1], k[..., 0]
    I = np.eye(3)
    return I + np.sin(ang)[..., None, None] * K + (1 - np.cos(ang))[..., None, None] * (K @ K)


def rz(theta):
    c, s = np.cos(theta), np.sin(theta)
    R = np.zeros(np.shape(theta) + (3, 3))
    R[..., 0, 0], R[..., 0, 1], R[..., 1, 0], R[..., 1, 1], R[..., 2, 2] = c, -s, s, c, 1
    return R


def sg_coeffs(win, order=2):
    m = win // 2
    A = np.vander(np.arange(-m, m + 1), order + 1, increasing=True).astype(float)
    return np.linalg.pinv(A)[0]


def sg_smooth(x, win, order=2, axis=0):
    if win < 3 or x.shape[axis] < win:
        return x
    win = win if win % 2 else win + 1
    c = sg_coeffs(win, min(order, win - 1))
    m = win // 2
    xm = np.moveaxis(x, axis, 0)
    pad = np.concatenate([np.repeat(xm[:1], m, 0), xm, np.repeat(xm[-1:], m, 0)], 0)
    out = sum(c[i] * pad[i:i + xm.shape[0]] for i in range(win))
    return np.moveaxis(out, 0, axis)


def fix_quat_continuity(q):
    q = q.copy()
    for t in range(1, q.shape[0]):
        flip = (q[t] * q[t - 1]).sum(-1) < 0
        q[t] = np.where(flip[..., None], -q[t], q[t])
    return q


def stabilize_lateral(lat_s, a_s, lat_t, a_t, up, notes, win=7):
    """Real footage (esp. side views) swaps left/right labels on some frames. The body frame is built from the
    hip/shoulder line, so one swapped frame rolls the whole character 180 deg. For horizontal-spine rigs we know the
    anatomical left (up x forward): force the source line onto the same side as the rig's rest line, then smooth it."""
    fwd = a_s.copy()
    fwd[:, 2] = 0.0
    ok = np.linalg.norm(fwd, axis=1) > 1e-6
    left_ref = np.cross(np.broadcast_to(up, fwd.shape), fwd)
    rest_fwd = np.array([a_t[0], a_t[1], 0.0])
    rest_side = np.sign(float(np.dot(lat_t, np.cross(up, rest_fwd)))) or 1.0
    side = np.sign((lat_s * left_ref).sum(1))
    side[(side == 0) | ~ok] = rest_side
    flipped = int((side != rest_side).sum())
    lat = lat_s * (side * rest_side)[:, None]
    if flipped:
        notes.append(f"Corrected {flipped} frame(s) with swapped left/right labels on the hip/shoulder line.")
    # temporal smoothing of the (unit) lateral direction, magnitude kept
    mag = np.linalg.norm(lat, axis=1, keepdims=True)
    u = lat / np.maximum(mag, EPS)
    if u.shape[0] >= 3:
        k = max(1, min(win, u.shape[0]) // 2)
        pad = np.concatenate([np.repeat(u[:1], k, 0), u, np.repeat(u[-1:], k, 0)], 0)
        u = np.stack([pad[i:i + 2 * k + 1].mean(0) for i in range(u.shape[0])], 0)
        u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), EPS)
    return u * mag


# ===================================================================================================== source handling

def recover_travel(P, jn, fps, notes, mode="auto"):
    """Camera-tracked or in-place source (Y-up, metres): planted feet appear to slide backwards while the body stays put.
    Real feet do not slide, so the body must travel forward at the speed the planted feet slide back. Adds that travel to
    every joint. 'auto' only acts when planted feet clearly slide but the body hardly travels."""
    import re
    if mode in (False, "off", "no"):
        return P
    T = P.shape[0]
    feet = [i for i, n in enumerate(jn) if re.search(r"paw|ankle|foot|heel|toe|hoof", n.lower()) and not re.search(r"end|tip", n.lower())]
    if T < 12 or len(feet) < 2:
        return P
    # body joints only: UE helper bones (ik_*_root at the origin, center_of_mass / interaction at a warp target metres away)
    # stand still and pulled the body's travel towards zero
    d0 = np.linalg.norm(P[0] - np.median(P[0], axis=0), axis=1)
    body_j = [i for i in range(P.shape[1]) if d0[i] <= 4.0 * max(float(np.median(d0)), 1e-9)
              and not re.search(r"^ik_|center_of_mass|interaction|^root$", jn[i].lower())] or list(range(P.shape[1]))
    feet = [i for i in feet if i in body_j and not jn[i].lower().startswith("ik_")] or feet
    # body height of the first frame - not the clip's height range (a 2.5 m climb read as a 3.5 m body)
    h = float(np.ptp(P[0, body_j, 1])) or 1.0
    y = P[:, feet, 1]
    xz = P[:, feet][:, :, [0, 2]]
    vxz = np.zeros_like(xz)
    vxz[1:-1] = (xz[2:] - xz[:-2]) * fps / 2
    vxz[0], vxz[-1] = vxz[1], vxz[-2]
    vy = np.zeros_like(y)
    vy[1:-1] = (y[2:] - y[:-2]) * fps / 2
    low = np.percentile(y, 10, axis=0)
    contact = (y < low + 0.04 * h) & (np.abs(vy) < 0.6 * h)                     # (T,F) planted
    c = np.full((T, 2), np.nan)
    for t in range(T):
        if contact[t].any():
            c[t] = -np.median(vxz[t, contact[t]], axis=0)
    ok = ~np.isnan(c[:, 0])
    if ok.sum() < 0.3 * T:
        return P
    slide = float(np.median(np.linalg.norm(c[ok], axis=1)))
    pel = next((i for i in body_j if re.search(r"pelvis|hips?$", jn[i].lower())), None)
    body = P[:, pel, [0, 2]] if pel is not None else P[:, body_j][:, :, [0, 2]].mean(axis=1)
    # speed over 1 s windows: a pivot (back, then forward again) ends where it started, which read as "the body stays put"
    # (GASP / Lyra pivots, a GASP climb start: their real root motion was replaced); video jitter does not add up over 1 s
    w = int(min(round(fps), T - 1))
    body_speed = float(np.mean(np.linalg.norm(body[w:] - body[:-w], axis=1))) / (w / fps)
    if mode == "auto" and not (slide > 0.15 * h and body_speed < 0.5 * slide):
        return P
    tt = np.arange(T)
    for k in range(2):
        c[:, k] = np.interp(tt, tt[ok], c[ok, k])
    win = max(3, int(round(fps * 0.4)) | 1)
    kern = np.ones(win) / win
    c = np.stack([np.convolve(np.pad(c[:, k], (win // 2, win // 2), mode="edge"), kern, mode="valid") for k in range(2)], 1)
    off = np.concatenate([np.zeros((1, 2)), np.cumsum((c[1:] + c[:-1]) / 2, axis=0) / fps], 0)
    P = P.copy()
    P[..., 0] += off[:, None, 0]
    P[..., 2] += off[:, None, 1]
    notes.append(f"Recovered forward travel from the planted feet ({np.linalg.norm(off[-1]):.2f} m over the clip): the source was "
                 f"camera-tracked or in place (feet slid {slide / h * 100:.0f}% of height/s while the body stayed put).")
    return P


def load_motion(path, opts):
    d = np.load(path, allow_pickle=False)
    fps = float(d["fps"])
    skel = str(d["skeleton"]) if "skeleton" in d.files else "custom"
    jn = [str(x) for x in d["joint_names"]]
    P = np.asarray(d["joints3d"], float)
    conf = np.asarray(d["conf"], float) if "conf" in d.files else np.ones(P.shape[:2])
    root = np.asarray(d["root"], float) if "root" in d.files else None
    parents = [int(x) for x in d["parents"]] if "parents" in d.files else None
    meta = {}
    if "meta" in d.files:
        try:
            meta = json.loads(str(d["meta"]))
        except Exception:  # noqa: BLE001
            meta = {}
    jn, P, conf = sk.augment(skel, jn, P, conf)
    if parents is None or len(parents) != len(jn):
        parents = sk.builtin_parents(skel, jn)
    notes = []
    if parents is None:
        parents = sk.infer_parents(jn, P)
        notes.append("Source hierarchy inferred from joint distances.")
    ri = next((i for i, p in enumerate(parents) if p < 0), 0)
    if root is not None and np.ptp(P[:, ri], axis=0).max() < 0.02 and np.ptp(root, axis=0).max() > 0.1:
        P = P + root[:, None, :]
        notes.append("joints3d were root-relative; added `root` travel.")
    # range
    a, b = int(opts.get("start") or 0), opts.get("end")
    b = int(b) if b else P.shape[0]
    P, conf = P[a:b], conf[a:b]
    # gap filling of low-confidence samples
    T = P.shape[0]
    thr = 0.15
    tt = np.arange(T)
    for j in range(P.shape[1]):
        bad = conf[:, j] < thr
        if bad.any() and (~bad).sum() >= 2:
            for k in range(3):
                P[bad, j, k] = np.interp(tt[bad], tt[~bad], P[~bad, j, k])
            notes.append(f"filled {int(bad.sum())} low-confidence frames of '{jn[j]}'") if bad.mean() > 0.1 else None
    # travel recovery (camera-tracked / in-place sources), before resampling so it works on the source frame rate
    P = recover_travel(P, jn, fps, notes, opts.get("recover_travel", "auto"))
    # resample (speed > 1 plays the motion faster: the source is read as if it was shot at fps * speed)
    speed = max(0.1, min(5.0, float(opts.get("speed") or 1.0)))
    tfps = float(opts.get("fps") or fps)
    sfps = fps * speed
    if abs(speed - 1.0) > 1e-3:
        notes.append(f"time scaled x{speed:g} ({'faster' if speed > 1 else 'slower'})")
    if abs(tfps - sfps) > 1e-3 and T > 1:
        n = max(2, int(round((T - 1) / sfps * tfps)) + 1)
        tn = np.linspace(0, T - 1, n)
        P = np.stack([np.stack([np.interp(tn, tt, P[:, j, k]) for k in range(3)], -1) for j in range(P.shape[1])], 1)
    fps = tfps
    # looping clips: keep the best-matching stretch of whole cycles plus the frames the loop cross-fade blends away
    lw = opts.get("loop_window", "auto")
    if opts.get("loop") and lw in ("auto", "require"):
        w = find_loop_window(P, ri, fps)
        if w is None and lw == "require":            # caller only wants a loop if the motion really repeats
            opts["loop"] = False
            notes.append("loop: no repeating cycle found, kept as a one-shot clip")
        elif w is None:
            notes.append("loop: no repeating cycle found, the ends are cross-faded as they are")
        else:
            s, e, k, cost = w
            P = P[s:e + k]
            notes.append(f"loop: kept frames {s}-{e} ({(e - s) / fps:.2f} s) whose start and end poses match "
                         f"(mean joint error {cost * 100:.1f}% of height), plus {k} frames for the cross-fade")
    return {"fps": fps, "skeleton": skel, "names": jn, "P": P, "parents": parents, "notes": notes, "meta": meta}


def find_loop_window(P, ri, fps, min_len_s=0.6, fade_s=0.2, max_cost=0.06):
    """Best loop for a cyclic motion: frames s..e where the poses around s and around e match (root travel removed), so that
    cross-fading frames e..e+k into s..s+k (solve()'s loop closing) blends near-identical poses. Prefers longer windows (more
    cycles) when they match almost as well. Returns (s, e, k, cost) with cost = mean joint error / body height, or None."""
    T = P.shape[0]
    k = max(3, int(round(fade_s * fps)))          # = the cross-fade length solve() uses
    mn = max(2, int(round(min_len_s * fps)))
    if T < mn + k + 2:
        return None
    # axis-agnostic (sources can be Y-up and their root joint can be static): compare poses about each frame's joint centroid
    rel = P - P.mean(axis=1, keepdims=True)
    size = max(float(np.median(np.ptp(P, axis=1).max(-1))), 0.1)     # body extent ~ height
    if T > 1800:                                  # 60 s at 30 fps; longer takes are not loop material
        return None
    d = np.empty((T, T))
    for t in range(T):
        d[t] = np.linalg.norm(rel - rel[t], axis=-1).mean(-1) / size
    box = np.ones(k + 1) / (k + 1)
    best = []                                     # (cost, L, s)
    for L in range(mn, T - k):
        diag = np.convolve(np.diagonal(d, offset=L), box, "valid")   # starts s with s + L + k < T
        if diag.size == 0:
            break
        s = int(diag.argmin())
        best.append((float(diag[s]), L, s))
    if not best:
        return None
    cmin = min(b[0] for b in best)
    if cmin > max_cost:
        return None
    ok = [b for b in best if b[0] <= max(1.25 * cmin, cmin + 0.004)]
    cost, L, s = max(ok, key=lambda b: (b[1], -b[0]))
    return s, s + L, k, cost


# ===================================================================================================== direct transfer
# Same skeleton family (UE5 Manny / Quinn animations from GASP, Lyra, ALS ...): the bones of the source file are sampled
# directly and each same-named rig bone gets the source bone's rotation relative to its rest pose. Positions-only retargeting
# loses twist and drifts the feet on fast motions; this keeps the animation as authored. Filled by main() before solve().
DIRECT: dict = {}


def _rot(m) -> np.ndarray:
    r = np.array(m, float)[:3, :3]
    return r / np.maximum(np.linalg.norm(r, axis=0, keepdims=True), EPS)


def sample_direct(path: str, rig_names: list[str], start: int = 0, end: int | None = None) -> dict | None:
    """Import the source file next to the rig and read world rotations of the bones whose names the rig shares."""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":                                   # common.import_model() resets the scene - the rig must stay
        bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=False, ignore_leaf_bones=False, use_anim=True)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        return None
    new = [o for o in bpy.data.objects if o not in before]
    try:
        arms = [o for o in new if o.type == "ARMATURE"]
        src = next((a for a in arms if a.animation_data and a.animation_data.action), arms[0] if arms else None)
        if src is None:
            return None
        act = src.animation_data.action if src.animation_data and src.animation_data.action else None
        if act is None:
            acts = [a for a in bpy.data.actions if a.users]
            act = max(acts, key=lambda a: a.frame_range[1] - a.frame_range[0]) if acts else None
            if act is None:
                return None
            src.animation_data_create()
            src.animation_data.action = act
        common_names = [n for n in rig_names if n in src.data.bones]
        if len(common_names) < 10:
            return None
        f0, f1 = int(round(act.frame_range[0])), int(round(act.frame_range[1]))
        frames = list(range(f0, f1 + 1))[start:end]
        sc = bpy.context.scene
        sc.frame_set(frames[0])
        mw0 = np.array(src.matrix_world)
        R0 = np.stack([_rot(mw0 @ np.array(src.data.bones[n].matrix_local)) for n in common_names])
        H0 = {n: (mw0 @ np.append(np.array(src.data.bones[n].head_local), 1))[:3] for n in common_names}
        R = np.zeros((len(frames), len(common_names), 3, 3))
        for t, f in enumerate(frames):
            sc.frame_set(f)
            mw = np.array(src.matrix_world)
            for k, n in enumerate(common_names):
                R[t, k] = _rot(mw @ np.array(src.pose.bones[n].matrix))
        return {"names": common_names, "R": R, "R0": R0, "H0": H0}
    finally:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)


# ===================================================================================================== solver
class Rig:
    def __init__(self, arm):
        rd = common.rest_data(arm)
        self.names = rd["names"]
        self.parent = rd["parent"]
        self.rest = rd["rest"]                 # (B,4,4) armature-space rest matrices
        self.R = rd["rest"][:, :3, :3]
        self.head = rd["head"]
        self.tail = rd["tail"]
        self.B = len(self.names)
        self.idx = {n: i for i, n in enumerate(self.names)}
        self.children = [[] for _ in range(self.B)]
        for i, p in enumerate(self.parent):
            if p >= 0:
                self.children[p].append(i)
        self.length = np.linalg.norm(self.tail - self.head, axis=1)
        self.parsed = [sk.parse_name(n) for n in self.names]

    def anc(self, i):
        out = []
        p = self.parent[i]
        while p >= 0:
            out.append(p)
            p = self.parent[p]
        return out

    def first_mapped_desc(self, i, mapped):
        out, stack = [], list(self.children[i])
        while stack:
            c = stack.pop()
            if c in mapped:
                out.append(c)
            else:
                stack.extend(self.children[c])
        return out


def heading_angle(fwd_xy):
    return math.atan2(float(np.mean(fwd_xy[:, 1] / np.maximum(np.linalg.norm(fwd_xy, axis=1), EPS))),
                      float(np.mean(fwd_xy[:, 0] / np.maximum(np.linalg.norm(fwd_xy, axis=1), EPS))))


def frame_from(axis, lat):
    """Orthonormal frames (columns x=axis, y=lateral, z=axis x lateral). axis:(...,3) lat:(...,3)"""
    x = nrm(axis)
    y = lat - (lat * x).sum(-1, keepdims=True) * x
    y = nrm(y)
    z = np.cross(x, y)
    return np.stack([x, y, z], axis=-1)


def soft_mask(c, ramp):
    """1 inside contact, smooth cosine-like ramps of `ramp` frames around it."""
    c = np.asarray(c, float)
    T = len(c)
    if T <= 2 * ramp + 1:
        return c
    k = np.hanning(2 * ramp + 3)[1:-1]
    k /= k.sum()
    sm = np.convolve(np.pad(c, ramp, mode="edge"), k, mode="valid")
    return np.clip(np.maximum(c, sm), 0, 1)


def _erode(w, r):
    """Running minimum over +-r frames."""
    if r < 1 or len(w) <= 2 * r:
        return w
    return np.lib.stride_tricks.sliding_window_view(np.pad(w, r, mode="edge"), 2 * r + 1).min(axis=1)


def _hann_smooth(w, r):
    if r < 1 or len(w) <= 2 * r + 1:
        return w
    k = np.hanning(2 * r + 3)[1:-1]
    return np.convolve(np.pad(w, r, mode="edge"), k / k.sum(), mode="valid")


def lock_weight(c, ramp, E0, tgt, leg, far=0.6):
    """Per-frame foot-lock weight (0..1).  The contact mask with soft edges, faded out where the lock would move the ankle
    far from where the source puts it: a foot still sweeping in before it plants (a mantle's foot reaching the ledge was
    pulled a full leg length in one frame) or already peeling off.  Ordinary drift of a planted source foot (in-place
    sources with recovered travel slide 1-2 m/s) stays locked: full below far/2 of the leg, none beyond `far`.  Sources
    that do not travel at all pass a larger `far` - there the lock stands in for the missing root motion.  The gate is
    eroded then smoothed over the same window, so it never exceeds its raw value and has no steps."""
    dev = np.linalg.norm(tgt[:, :2] - E0[:, :2], axis=1) / leg     # sideways only: pulling a foot up out of a ledge is the lock's job
    g = np.clip((far - dev) / (0.5 * far), 0.0, 1.0)
    r = max(1, ramp // 2)
    # only the gate is eroded / smoothed: easing the contact mask itself left a landing foot in the ledge for 2 frames
    return np.clip(soft_mask(c, ramp) * _hann_smooth(_erode(g, r), r), 0.0, 1.0)


def soft_reach(dist, l1, l2, src, soft=0.04):
    """Two-bone IK distance with a soft limit: exact up to the source's own hip-ankle distance `src` (or (1 - soft) of the
    full length if that is less), then easing towards full length.  A hard clamp straightens the knee within a frame or two
    whenever a pinned foot is just out of reach (the stance leg of a run is at 93-99 % extension) - at contact start and end
    that was most of the lock's knee pop.  Starting the ease at the source's extension keeps straight standing legs straight."""
    full = l1 + l2
    d0 = np.clip(np.maximum(src, (1 - soft) * full), 0.0, full * 0.9999)
    s = np.maximum(full - d0, 1e-4 * full)
    out = np.where(dist > d0, full - s * np.exp(-(dist - d0) / s), dist)
    return np.clip(out, abs(l1 - l2) + 1e-4, full * 0.9999)


def _deblip(c):
    T = len(c)
    for _ in range(2):                                  # remove 1-frame blips and holes
        c2 = c.copy()
        for t in range(1, T - 1):
            if c[t - 1] == c[t + 1] and c[t] != c[t - 1]:
                c2[t] = c[t - 1]
        c = c2
    return c


def support_levels(H, XY, fps, leg_len, Zp=None, XYp=None):
    """The surfaces the feet stand on -> dict(lo, hi (T,), ground (shift), drop (T,) bool, floor (T,F)).
    H (T,F) foot heights above the clip's lowest foot position, XY (T,F,2), Zp (T,) pelvis height.
    A foot that stands still (3-D) for >= 0.067 s is on a surface at its height there.  Flat-floor clips (and in-place
    loops, whose planted feet move) give all zeros.  Clips that climb or drop (UE traversal, cliff jumps) used to be
    measured against one global floor, which halved their scale and pinned feet standing on a ledge to the ground.
      lo/hi:  the floor of the whole body lies between them (between two stands, or bounding a fall at the clip's ends)
      floor:  the surface under each foot - its own previous / next stand, the upper one once the foot is above it
      ground: shift so that 0 is the lowest surface stood on (not the lowest point reached: a clip ending mid-fall)
      drop:   airborne frames at the clip's ends (falling in, or off a ledge with no landing) - no contacts there"""
    T, F = H.shape
    res = {"lo": np.zeros(T), "hi": np.zeros(T), "ground": 0.0, "drop": np.zeros(T, bool), "floor": np.zeros((T, F))}
    if T < 6:
        return res
    if Zp is not None and XYp is not None and np.ptp(XYp, axis=0).max() < 0.4 * leg_len and np.ptp(Zp) < 0.5 * leg_len:
        return res                  # in-place clip: feet hanging still in a fall / jump loop are not standing on anything
    d = np.concatenate([np.diff(XY, axis=0), np.diff(H, axis=0)[..., None]], axis=2)       # (T-1,F,3)
    v = np.linalg.norm(d, axis=2) * fps
    v = np.concatenate([v[:1], v], axis=0)
    # no smoothing: a sprinting foot is planted for only ~3 frames and still drifts ~0.4-0.8 m/s
    still = v < 1.0 * leg_len
    if Zp is not None and T > 4:
        # ballistic flight (the pelvis falls at ~g): a foot slowing at the top of a jump is not standing on anything
        acc = np.zeros(T)
        acc[1:-1] = (Zp[2:] - 2 * Zp[1:-1] + Zp[:-2]) * fps * fps
        acc = np.convolve(acc, np.ones(3) / 3, mode="same")
        still &= ~(acc < -5.0)[:, None]                      # not widened: a sprint stance is 3 frames between flights
    min_run = max(2, int(round(0.067 * fps)))                # sprint plants last 2-3 frames at 30 fps
    snap = 0.2 * leg_len                                     # ~17 cm: a stair step; tiptoes and raised heels are less
    tol = 0.1 * leg_len                                      # a foot this close to a surface is on it
    raw = []                                                 # (foot, t0, t1, level)
    for f in range(F):
        t = 0
        while t < T:
            if still[t, f]:
                t2 = t
                while t2 + 1 < T and still[t2 + 1, f]:
                    t2 += 1
                if t2 - t + 1 >= min_run:
                    lvl = float(np.median(H[t:t2 + 1, f]))
                    # a foot held up while the other one stands lower the whole time is a pose, not a surface
                    others = [g for g in range(F) if g != f]
                    held = others and all(bool((still[t:t2 + 1, g] & (H[t:t2 + 1, g] < lvl - snap)).all()) for g in others)
                    if not held:
                        raw.append((f, t, t2, lvl))
                t = t2 + 1
            else:
                t += 1
    if not raw:
        return res
    ground = min(r[3] for r in raw)
    if ground > snap:
        # move the ground up only for a clip that falls in or off at its ends (cliff jump); feet below the stands in the
        # middle of the clip (in-place loops, odd stands) keep the old floor
        first, last = min(r[1] for r in raw), max(r[2] for r in raw)
        below = np.where(H.min(axis=1) < ground - snap)[0]
        ground = ground if len(below) and ((below < first) | (below > last)).all() else 0.0
    else:
        ground = 0.0
    H = H - ground
    runs = [(f, t0, t1, 0.0 if abs(l - ground) < snap else l - ground) for f, t0, t1, l in raw]
    # one surface, one level: stands closer than a step (a landing 8 cm off the take-off) are the same surface
    lv = sorted({r[3] for r in runs})
    groups = []
    for l in lv:
        if groups and l - groups[-1][-1] < snap:
            groups[-1].append(l)
        else:
            groups.append([l])
    level_of = {l: (0.0 if 0.0 in g else float(np.median(g))) for g in groups for l in g}
    runs = [(f, t0, t1, level_of[l]) for f, t0, t1, l in runs]
    res["ground"] = ground
    lo, hi = res["lo"], res["hi"]
    act_lo = np.full(T, np.inf)
    act_hi = np.full(T, -np.inf)
    for _, t0, t1, lvl in runs:
        act_lo[t0:t1 + 1] = np.minimum(act_lo[t0:t1 + 1], lvl)
        act_hi[t0:t1 + 1] = np.maximum(act_hi[t0:t1 + 1], lvl)
    idx = np.where(np.isfinite(act_lo))[0]
    for t in range(T):
        if np.isfinite(act_lo[t]):
            lo[t], hi[t] = act_lo[t], act_hi[t]
            continue
        k = np.searchsorted(idx, t)
        cand = [i for i in (idx[k - 1] if k > 0 else None, idx[k] if k < len(idx) else None) if i is not None]
        lo[t] = min(act_lo[i] for i in cand)
        hi[t] = max(act_hi[i] for i in cand)
    drop = res["drop"] = _air_ends(H, lo, hi, int(idx[0]), int(idx[-1]), tol)
    # per foot: between its own stands
    for f in range(F):
        fr = sorted((t0, t1, l) for g, t0, t1, l in runs if g == f)
        fl = res["floor"][:, f]
        if not fr:
            fl[:] = lo
            continue
        for t in range(T):
            prev = next((r for r in reversed(fr) if r[0] <= t), None)
            if prev is not None and t <= prev[1]:
                fl[t] = prev[2]
                continue
            nxt = next((r for r in fr if r[0] > t), None)
            levels = [r[2] for r in (prev, nxt) if r is not None]
            # an airborne end (landing in a pit, falling in) counts as a stand at its lowest point
            if prev is None and drop[:t].any():
                levels.append(float(lo[np.where(drop[:t])[0][-1]]))
            if nxt is None and drop[t + 1:].any():
                levels.append(float(lo[t + 1 + np.where(drop[t + 1:])[0][0]]))
            top = max(levels)
            # between stands: on the upper surface only once really up there (the metrics would count a foot a few cm
            # under it as sinking in); otherwise the lower one
            fl[t] = top if H[t, f] >= top - 0.25 * tol else min(min(levels), lo[t])
        fl[drop] = lo[drop]
    return res


def _air_ends(H, lo, hi, first, last, snap):
    """Frames before the first / after the last stand: a clip that starts mid-fall or ends mid-jump has no surface there.
    A drop below the edge level widens lo down to the lowest foot; feet high above it (falling in from above) widen hi up
    to the lower foot of each frame.  Returns the mask of those airborne frames (no contacts there)."""
    T = H.shape[0]
    low_foot = H.min(axis=1)
    drop = np.zeros(T, bool)
    for a, b in ((0, first), (last + 1, T)):
        if b <= a:
            continue
        edge = lo[first] if a == 0 else lo[last]
        # only the part beyond the last (trailing) / before the first (leading) time a foot is back at the edge level:
        # running on after a vault keeps touching it
        near = np.where(np.abs(low_foot[a:b] - edge) < 1.5 * snap)[0]
        if len(near):
            a, b = (a, a + int(near[0])) if a == 0 else (a + int(near[-1]) + 1, b)
        if b <= a:
            continue
        seg = low_foot[a:b]
        if float(seg.min()) < edge - 3 * snap:
            lo[a:b] = np.minimum(lo[a:b], float(seg.min()))
            drop[a:b] = True
        if float(seg.max()) > edge + 3 * snap:
            hi[a:b] = np.maximum(hi[a:b], seg)
            drop[a:b] = True
    return drop


def detect_contacts(h, leg_len, T, xy=None, fps=30.0, travels=False):
    """Planted frames of one foot -> (contact, settled).
    contact: the foot is low (height only) - what foot lock pins.
    settled: low AND not moving horizontally. A running foot skims the floor at full speed for a few frames before it lands;
    those frames must not decide where the foot is planted or how far the pelvis drops (that dragged jogs through the floor)."""
    rng = max(h.max() - h.min(), 1e-6)
    if rng < 0.06 * leg_len:
        return np.ones(T, bool), np.ones(T, bool)
    c = _deblip((h < h.min() + 0.25 * rng) & (h < h.min() + 0.2 * leg_len))
    settled = c
    if xy is not None and T > 6:
        v = np.r_[0.0, np.linalg.norm(np.diff(xy, axis=0), axis=1) * fps]
        v[0] = v[1]
        v = np.convolve(np.pad(v, 1, mode="edge"), np.ones(3) / 3, mode="valid")
        thr = float(np.clip(0.25 * np.percentile(v, 90), 0.4 * leg_len, 1.0 * leg_len))
        cv = _deblip(c & (v < thr)) & c
        still = cv.sum() / max(c.sum(), 1)
        if travels:
            # the body really travels (direct transfer of an animation file).  A foot that never stands still anywhere comes
            # from an in-place source with reconstructed travel (ALS / Lyra runs drift 0.2-1.2x body speed while planted):
            # height-only, the lock stands in.  A foot that does stand still has real root motion; then a low stretch that
            # never stops is a toe skimming the floor (GASP runs: 6 m/s) - planting it froze that foot while the body ran on.
            return c, (c.copy() if still < 0.05 else cv)
        # an in-place source whose travel could not be recovered has planted feet that move backwards: keep height-only
        if still >= 0.3:
            settled = cv.copy()
            t = 0
            while t < T:                                # a low stretch that never stands still (rolling, sliding): all of it
                if c[t]:
                    t2 = t
                    while t2 + 1 < T and c[t2 + 1]:
                        t2 += 1
                    if not cv[t:t2 + 1].any():
                        settled[t:t2 + 1] = True
                    t = t2 + 1
                else:
                    t += 1
    return c, settled


def plant_xy(xy, c, settled):
    """Per contact stretch of `c`: the foot's planted xy = median over the frames where it is settled (all frames if none)."""
    plant = np.asarray(xy, float).copy()
    T = len(c)
    t = 0
    while t < T:
        if c[t]:
            t2 = t
            while t2 + 1 < T and c[t2 + 1]:
                t2 += 1
            seg = np.arange(t, t2 + 1)
            st = seg[settled[seg]]
            plant[t:t2 + 1] = np.median(xy[st if len(st) else seg], axis=0)
            t = t2 + 1
        else:
            t += 1
    return plant


def solve(arm, motion, bone_map, opts, ctx_notes):
    rig = Rig(arm)
    B = rig.B
    jn, P, jpar = motion["names"], motion["P"].copy(), motion["parents"]
    fps = motion["fps"]
    T = P.shape[0]
    J = len(jn)
    jidx = {n: i for i, n in enumerate(jn)}
    sroles = sk.source_roles(jn)
    quad_src = motion["skeleton"] in sk.QUADRUPED_SKELETONS or any("paw" in n for n in jn)

    # ---- validated mapping  bone index -> joint index
    mapped, used = {}, {}
    for b, j in bone_map.items():
        if b not in rig.idx or j not in jidx:
            ctx_notes.append(f"bone-map entry {b}->{j} ignored (unknown name)")
            continue
        if jidx[j] in used:
            ctx_notes.append(f"joint '{j}' mapped twice ({rig.names[used[jidx[j]]]}, {b}); keeping the first")
            continue
        mapped[rig.idx[b]] = jidx[j]
        used[jidx[j]] = rig.idx[b]
    if not mapped:
        raise RuntimeError("The bone map is empty or does not match this rig / motion.")

    # ---- smoothing
    smooth = float(opts.get("smooth", 0.5))
    if smooth > 0:
        P = sg_smooth(P, max(int(round((0.05 + 0.2 * smooth) * fps)) | 1, 3), 2, 0)

    def jfind(role, side=None, limb=None):
        for n in jn:
            r = sroles[n]
            if r["role"] == role and (side is None or r["side"] == side) and (limb is None or r["limb"] == limb):
                return jidx[n]
        return None

    top = sorted([b for b in mapped if not any(a in mapped for a in rig.anc(b))], key=lambda b: len(rig.anc(b)))
    pelvis_b = top[0]

    # ---- span targets
    span = {}
    for b in mapped:
        cands = [c for c in rig.first_mapped_desc(b, mapped) if mapped[c] != mapped[b]]
        if cands:
            axis = nrm(rig.tail[b] - rig.head[b])
            span[b] = max(cands, key=lambda c: float(np.dot(nrm(rig.head[c] - rig.head[b]), axis)))
    if pelvis_b not in span:
        raise RuntimeError("The root bone has no mapped descendant: map at least pelvis + head/legs.")
    root_j = mapped[pelvis_b]
    tgt_joint = mapped[span[pelvis_b]]

    tip_h = {j: np.stack([P[..., 0], -P[..., 2], P[..., 1]], axis=-1)[:, j, 2].copy() for j in mapped.values()}   # un-adjusted heights (Z up)

    # ---- paw-tip joints (AP-10K 'paw' = tip of the foot): estimate the ankle on the knee->tip line
    for b, j in mapped.items():
        r = sroles[jn[j]]
        if r["role"] == "end" and "paw" in jn[j] and rig.parent[b] in mapped:
            m_b = rig.parent[b]
            v = rig.head[m_b] - rig.tail[b]
            frac = float(np.clip(np.dot(rig.head[b] - rig.tail[b], v) / max(np.dot(v, v), EPS), 0.0, 0.6))
            P[:, j] = P[:, j] + (P[:, mapped[m_b]] - P[:, j]) * frac
    Pz = np.stack([P[..., 0], -P[..., 2], P[..., 1]], axis=-1)           # Y-up -> Z-up

    # ---- yaw alignment (source heading -> rig forward)
    hipL, hipR = jfind("upper", "L", "hind"), jfind("upper", "R", "hind")
    shL, shR = jfind("upper", "L", "front"), jfind("upper", "R", "front")
    lat_pair = None
    for a, b in ((hipL, hipR), (shL, shR)):
        if a is not None and b is not None and a in used and b in used:
            lat_pair = (a, b)
            break
    up = np.array([0, 0, 1.0])
    s_axis = Pz[:, tgt_joint] - Pz[:, root_j]
    horizontal_spine = np.linalg.norm(s_axis[:, :2], axis=1).mean() > 0.5 * np.abs(s_axis[:, 2]).mean()
    a_t = rig.head[span[pelvis_b]] - rig.head[pelvis_b]
    if horizontal_spine:
        fwd_s, fwd_t = s_axis[:, :2], a_t[:2]
    elif lat_pair:
        lat_s0 = Pz[:, lat_pair[0]] - Pz[:, lat_pair[1]]
        lat_t0 = rig.head[used[lat_pair[0]]] - rig.head[used[lat_pair[1]]]
        fwd_s, fwd_t = np.cross(lat_s0, up)[:, :2], np.cross(lat_t0, up)[:2]
    else:
        fwd_s, fwd_t = s_axis[:, :2], np.array([0, -1.0])
    yaw = math.atan2(fwd_t[1], fwd_t[0]) - heading_angle(fwd_s)
    direct = None
    if DIRECT.get("R") is not None:
        if DIRECT["R"].shape[0] != T:
            ctx_notes.append(f"direct transfer skipped: {DIRECT['R'].shape[0]} sampled frames vs {T} motion frames")
        else:
            # heading from the REST poses (hip line), so rotations and positions share one frame
            h0 = DIRECT["H0"]
            ln = next(((a, b) for a, b in (("thigh_l", "thigh_r"), ("upperarm_l", "upperarm_r")) if a in h0 and b in h0 and a in rig.idx and b in rig.idx), None)
            if ln:
                ls = np.array(h0[ln[0]]) - np.array(h0[ln[1]])
                lt = rig.head[rig.idx[ln[0]]] - rig.head[rig.idx[ln[1]]]
                yaw = math.atan2(lt[1], lt[0]) - math.atan2(ls[1], ls[0])
                direct = DIRECT
                ctx_notes.append(f"direct transfer: {len(direct['names'])} same-named bones copied from the source file")
            else:
                ctx_notes.append("direct transfer skipped: no hip line to align the rest poses")
    Pw = Pz @ rz(yaw).T
    fwd_rest = nrm(fwd_t)

    # ---- body frame (spine axis + hip line)
    G = Fs = Ft = None
    if lat_pair:
        a_s = Pw[:, tgt_joint] - Pw[:, root_j]
        lat_s = Pw[:, lat_pair[0]] - Pw[:, lat_pair[1]]
        lat_t = rig.head[used[lat_pair[0]]] - rig.head[used[lat_pair[1]]]
        if horizontal_spine:
            lat_s = stabilize_lateral(lat_s, a_s, lat_t, a_t, up, ctx_notes)
        if np.linalg.norm(np.cross(nrm(a_s), nrm(lat_s)), axis=1).min() > 0.2 and np.linalg.norm(np.cross(nrm(a_t), nrm(lat_t))) > 0.2:
            Fs, Ft = frame_from(a_s, lat_s), frame_from(a_t, lat_t)
            G = Fs @ Ft.T
        else:
            ctx_notes.append("Hip line is (nearly) parallel to the spine: using swing-only root rotation.")

    free_kids = {}
    for b, j in mapped.items():
        if b not in span:
            ks = [c for c in range(J) if jpar[c] == j and c not in used]
            if ks:
                free_kids[b] = ks

    def build_W(overrides=None):
        overrides = overrides or {}
        Wm = [None] * B
        for b in span:                                              # direction-driven bones
            if b == pelvis_b and G is not None:
                Wm[b] = G @ rig.R[b]
            else:
                d = overrides.get(b)
                if d is None:
                    d = Pw[:, mapped[span[b]]] - Pw[:, mapped[b]]
                Wm[b] = swing(rig.head[span[b]] - rig.head[b], d) @ rig.R[b]
        if G is not None:                                           # end bones driven by face points etc. (residual in body frame)
            for b, ks in free_kids.items():
                j = mapped[b]
                dvec = np.mean([Pw[:, c] - Pw[:, j] for c in ks], axis=0)
                ub = np.einsum("tji,tj->ti", Fs, nrm(dvec))
                uref = nrm(ub.mean(axis=0))
                Wm[b] = G @ swing(Ft @ uref, ub @ Ft.T) @ rig.R[b]
        for b in range(B):                                          # parents first: blends + inheritance
            if Wm[b] is not None:
                continue
            p = rig.parent[b]
            inherit = (Wm[p] @ rig.R[p].T @ rig.R[b]) if p >= 0 else np.broadcast_to(rig.R[b], (T, 3, 3)).copy()
            if b in mapped:
                Wm[b] = inherit
                continue
            A = next((a for a in rig.anc(b) if a in mapped), None)
            cands = [c for c in rig.first_mapped_desc(b, mapped) if Wm[c] is not None and c != b]
            if A is not None and cands and Wm[A] is not None:
                axis = nrm(rig.tail[b] - rig.head[b])
                D = max(cands, key=lambda c: float(np.dot(nrm(rig.head[c] - rig.head[b]), axis)))
                frac = float(np.clip(np.linalg.norm(rig.head[b] - rig.head[A]) / max(np.linalg.norm(rig.head[D] - rig.head[A]), EPS), 0, 1))
                Wm[b] = mat_from_q(q_slerp(q_from_mat(Wm[A]), q_from_mat(Wm[D]), frac))
            else:
                Wm[b] = inherit
        if direct is not None:
            K = rz(yaw)
            dmap = {n: k for k, n in enumerate(direct["names"])}
            for b in range(B):                                       # parents first (topological order)
                k = dmap.get(rig.names[b])
                if k is not None and b not in overrides:           # foot-lock IK keeps its thigh / shin directions
                    delta = direct["R"][:, k] @ direct["R0"][k].T       # source world rotation since its rest pose
                    Wm[b] = K @ delta @ K.T @ rig.R[b]
                elif k is None and rig.parent[b] >= 0:              # bones the source lacks follow their parent
                    p = rig.parent[b]
                    Wm[b] = Wm[p] @ rig.R[p].T @ rig.R[b]
        return Wm

    # ---- leg chains (hip, knee, foot) from the map
    legs = []
    for b, j in mapped.items():
        r = sroles[jn[j]]
        if not (r["role"] == "end" and (quad_src or r["limb"] == "hind")):
            continue
        am = [a for a in rig.anc(b) if a in mapped]
        if len(am) >= 2 and span.get(am[1]) == am[0] and span.get(am[0]) == b:
            legs.append({"U": am[1], "M": am[0], "F": b, "joint": j})
    foot_idx = [l["F"] for l in legs]
    rest_floor = np.array([min(rig.head[f][2], rig.tail[f][2]) for f in foot_idx]) if legs else np.zeros(0)

    # ---- root placement
    if legs:
        fj = [l["joint"] for l in legs]
        g_s = float(np.percentile(np.stack([tip_h[j] for j in fj], axis=1).min(axis=1), 2))
        leg_len_s = float(np.median([np.linalg.norm(Pw[:, mapped[l["U"]]] - Pw[:, mapped[l["M"]]], axis=1).mean()
                                     + np.linalg.norm(Pw[:, mapped[l["M"]]] - Pw[:, l["joint"]], axis=1).mean() for l in legs]))
        leg_len_t = float(np.median([np.linalg.norm(rig.head[l["M"]] - rig.head[l["U"]]) + np.linalg.norm(rig.head[l["F"]] - rig.head[l["M"]]) for l in legs]))
        foot_h = np.stack([tip_h[j] - g_s for j in fj], axis=1)
        if opts.get("terrain"):
            sup = support_levels(foot_h, np.stack([Pw[:, j, :2] for j in fj], axis=1), fps, leg_len_s, Pw[:, root_j, 2], Pw[:, root_j, :2])
        else:
            sup = {"lo": np.zeros(T), "hi": np.zeros(T), "ground": 0.0, "drop": np.zeros(T, bool), "floor": np.zeros((T, len(fj)))}
        terr_lo, terr_hi, g_up, terr_drop, terr_floor = sup["lo"], sup["hi"], sup["ground"], sup["drop"], sup["floor"]
        if g_up:
            g_s += g_up
            ctx_notes.append(f"ground = the lowest surface the feet stand on ({g_up:.2f} m above the lowest foot position)")
    else:
        g_s = float(Pw[:, root_j, 2].min() - 0.5)
        leg_len_s = leg_len_t = 1.0
        terr_lo = terr_hi = np.zeros(T)
        terr_drop = np.zeros(T, bool)
        terr_floor = np.zeros((T, 0))
    if terr_hi.any() or terr_lo.any():
        ctx_notes.append(f"uneven ground: the feet stand on surfaces from {terr_lo.min():.2f} to {terr_hi.max():.2f} m (source units)")
    h_s = Pw[:, root_j, 2] - g_s
    # pelvis above the lower foot of the same frame: the standing height whatever the ground does.  Measured from the clip's
    # lowest ground instead, a climb or a cliff jump (most frames far above / below it) scaled the character by 0.08-17x.
    h_rel = h_s
    if legs and opts.get("terrain"):
        low_foot = np.stack([tip_h[j] for j in fj], axis=1).min(axis=1)
        if abs(float(np.median(low_foot - g_s))) > 0.15 * leg_len_s or terr_hi.any() or terr_lo.any():   # flat clips: old reference
            h_rel = Pw[:, root_j, 2] - low_foot
            ctx_notes.append("ground level changes: standing height measured from the feet of each frame")
    pelvis_h_t = float(rig.head[pelvis_b][2])
    s_h = pelvis_h_t / max(float(np.median(h_rel)), 0.05)                      # fallback: keep the rest standing height
    s_leg = leg_len_t / max(leg_len_s, 0.05)
    s_t = s_leg if abs(s_leg - s_h) / s_h < 0.6 else s_h                      # horizontal travel + bobbing (stride proportion)
    user_scale = float(opts.get("scale", 1.0))
    mean_h = pelvis_h_t
    if legs:
        # keep the source's leg bend: (hip - ankle) / leg length stays the same, so strides fit the rig's legs
        rho = float(np.median([np.median(Pw[:, mapped[l["U"]], 2] - Pw[:, l["joint"], 2]) / max(leg_len_s, 1e-3) for l in legs]))
        rho = float(np.clip(rho, 0.5, 0.98))
        z_u = float(np.mean([rig.head[l["U"]][2] for l in legs]))
        z_f = float(np.mean([rig.head[l["F"]][2] for l in legs]))
        mean_h = z_f + rho * leg_len_t + (pelvis_h_t - z_u)
        mean_h = min(mean_h, pelvis_h_t * 1.02)
    ctx_notes.append(f"scale: travel x{s_t * user_scale:.3f} (leg ratio {s_leg:.3f}); pelvis height {mean_h:.3f} m (rest {pelvis_h_t:.3f})")
    D = s_t * user_scale * (Pw[:, root_j, :2] - Pw[0, root_j, :2])
    H = np.zeros((T, 3))
    H[:, :2] = rig.head[pelvis_b][:2] + D
    H[:, 2] = mean_h + s_t * (h_s - np.median(h_rel))
    lv_scale = s_t * user_scale if direct is not None else s_t                # the same scale the pelvis height uses
    terr_lo_t, terr_hi_t = lv_scale * terr_lo, lv_scale * terr_hi              # surfaces in rig metres above its floor
    if direct is not None:
        # the bones are copied, so the pelvis must sit where the source's does (scaled by leg length); the median-leg-bend
        # estimate above sinks runs and jumps (deep knee bends + flight phases) through the floor
        # h_s is measured from the source's lowest ANKLE height (g_s), so the rig's own ankle height is added back
        H[:, 2] = s_t * user_scale * h_s + (float(np.mean([rig.head[f][2] for f in foot_idx])) if foot_idx else 0.0)

    anc_p = rig.anc(pelvis_b)
    travel_bone = anc_p[-1] if anc_p and all(a not in mapped for a in anc_p) else None

    def fk(Wm, Hdrive, Dtravel):
        Mp_R, Mp_t = [None] * B, [None] * B
        Q, L = np.zeros((T, B, 4)), np.zeros((T, B, 3))
        hd, tl = np.zeros((T, B, 3)), np.zeros((T, B, 3))
        for b in range(B):
            p = rig.parent[b]
            Rb = rig.R[b]
            if p < 0:
                dR, dt = np.broadcast_to(np.eye(3), (T, 3, 3)), np.zeros((T, 3))
            else:
                dR = Mp_R[p] @ rig.R[p].T
                dt = Mp_t[p] - np.einsum("tij,j->ti", dR, rig.head[p])
            loc = np.zeros((T, 3))
            target = None
            if b == travel_bone:
                target = np.broadcast_to(rig.head[b], (T, 3)).copy()
                target[:, :2] += Dtravel
            elif b == pelvis_b:
                target = Hdrive
            if target is not None:
                loc = np.einsum("ji,tj->ti", Rb, np.einsum("tji,tj->ti", dR, target - dt) - rig.head[b])
            Q[:, b] = q_from_mat(Rb.T @ np.swapaxes(dR, -1, -2) @ Wm[b])
            L[:, b] = loc
            Mp_R[b] = Wm[b]
            Mp_t[b] = np.einsum("tij,tj->ti", dR, rig.head[b] + loc @ Rb.T) + dt
            hd[:, b] = Mp_t[b]
            tl[:, b] = Mp_t[b] + np.einsum("tij,j->ti", Wm[b], np.array([0, rig.length[b], 0]))
        return Q, L, hd, tl

    ground_fix = bool(opts.get("ground_fix", True))
    foot_lock = bool(opts.get("foot_lock", True))
    flags = []
    contacts = np.zeros((T, len(legs)), bool)
    settled = np.zeros((T, len(legs)), bool)
    lvl_t = np.zeros((T, len(legs)))                     # rig height of the surface each foot is on (0 = the floor)
    foot_floor = np.zeros((T, len(legs)))
    Wm = build_W()
    W_src = Wm                                           # world rotations before any foot clean-up
    Hd = H.copy()
    # the pelvis covers more than a leg length.  Animation files only: poses estimated from video jitter so much that their
    # planted feet rarely read as still, and the lock (at full weight) is what keeps them planted
    # direct transfers only: a solved walk (Epic mocap on Manny) sank 6 % into the floor once its plant spots moved
    src_travels = direct is not None and bool(opts.get("terrain")) and bool(np.ptp(Pw[:, root_j, :2], axis=0).max() > 1.0 * leg_len_s)
    for k, l in enumerate(legs):
        # always detected: the metrics need them even when no clean-up runs (direct transfers of clean mocap)
        # contact_gate False = the old height-only behaviour (kept for A/B checks)
        # heights are measured above the surface the foot is nearest to, so a foot standing on a ledge counts as planted
        floor_k = terr_floor[:, k]                              # the surface under this foot (source units)
        lvl_s, h_above = floor_k, tip_h[l["joint"]] - g_s - floor_k
        if terr_drop.any():                                     # falling: kept out of the contact thresholds
            h_above = np.where(terr_drop, float(h_above[~terr_drop].max()) if (~terr_drop).any() else 1.0, h_above)
        contacts[:, k], settled[:, k] = detect_contacts(h_above, leg_len_s, T,
                                                        Pw[:, l["joint"], :2] if opts.get("contact_gate", True) else None, fps,
                                                        travels=src_travels)
        contacts[terr_drop, k] = settled[terr_drop, k] = False      # falling: nothing to stand on
        # one level per contact (no step in the middle of a stand)
        lv = lvl_s.copy()
        t = 0
        while t < T:
            if contacts[t, k]:
                t2 = t
                while t2 + 1 < T and contacts[t2 + 1, k]:
                    t2 += 1
                lv[t:t2 + 1] = np.median(lvl_s[t:t2 + 1])
                t = t2 + 1
            else:
                t += 1
        lvl_t[:, k] = lv_scale * lv
        foot_floor[:, k] = lv_scale * np.where(contacts[:, k], lv, floor_k)
    if legs and (ground_fix or foot_lock):
        if ground_fix:
            Q, L, hd, tl = fk(Wm, Hd, D)
            sole = np.stack([np.minimum(hd[:, f, 2], tl[:, f, 2]) - rest_floor[k] for k, f in enumerate(foot_idx)], axis=1)
            # largest pelvis lift for which every planted leg can still reach its (planted xy, rest ankle height) target
            dmax = np.full((T, len(legs)), np.inf)
            for k, l in enumerate(legs):
                c = settled[:, k]                         # only frames where the source foot really stands still
                A, E0 = hd[:, l["U"]], hd[:, l["F"]]
                plant = plant_xy(E0[:, :2], contacts[:, k], c)
                full = np.linalg.norm(rig.head[l["M"]] - rig.head[l["U"]]) + np.linalg.norm(rig.head[l["F"]] - rig.head[l["M"]])
                # keep the source's per-frame leg extension (hip-ankle distance / leg length)
                rho_t = np.linalg.norm(Pw[:, mapped[l["U"]]] - Pw[:, l["joint"]], axis=1) / max(leg_len_s, 1e-3)
                dist = np.clip(rho_t, 0.3, 0.93) * full
                hh2 = ((plant - A[:, :2]) ** 2).sum(1)
                vz = rig.head[l["F"]][2] + lvl_t[:, k] - A[:, 2]
                dm = vz + np.sqrt(np.maximum(dist ** 2 - hh2, 0.0))
                dmax[:, k] = np.where(c, dm, np.inf)
            need = np.where(np.isfinite(dmax), dmax, np.nan)
            cnt = np.isfinite(dmax).sum(axis=1)
            need = np.where(cnt > 0, np.nansum(np.where(np.isfinite(dmax), dmax, 0), axis=1) / np.maximum(cnt, 1), np.inf)
            ok = np.isfinite(need)
            if ok.any():
                need = np.interp(np.arange(T), np.where(ok)[0], need[ok])
            else:
                need = np.zeros(T)
            # a planted spot the leg cannot reach sideways (the body has run past it) asks for the pelvis at ankle height;
            # never crouch more than a fifth of the leg for it - the foot lock lets that foot go a little instead
            leg_full = float(np.mean([np.linalg.norm(rig.head[l["M"]] - rig.head[l["U"]]) + np.linalg.norm(rig.head[l["F"]] - rig.head[l["M"]]) for l in legs]))
            need = np.maximum(need, -0.2 * leg_full)
            dz = sg_smooth(need[:, None], max(3, int(round(0.12 * fps)) | 1), 2, 0)[:, 0]
            Hd[:, 2] += dz
            flags.append("ground_fix")
        if foot_lock:
            Q, L, hd, tl = fk(Wm, Hd, D)
            ov = {}
            ramp = max(2, int(round(0.08 * fps)))
            for k, l in enumerate(legs):
                U_, M_, F_ = l["U"], l["M"], l["F"]
                # direct transfers: lock only where the source foot really stands still - locking the landing frames of clean
                # mocap snapped the foot up to 50 cm when it released
                c = settled[:, k] if direct is not None else contacts[:, k]
                A, K0, E0 = hd[:, U_], hd[:, M_], hd[:, F_]
                l1 = np.linalg.norm(rig.head[M_] - rig.head[U_])
                l2 = np.linalg.norm(rig.head[F_] - rig.head[M_])
                plant = E0.copy()
                plant[:, :2] = plant_xy(E0[:, :2], c, settled[:, k])
                plant[:, 2] = rig.head[F_][2] + lvl_t[:, k]
                # direct transfers only: there E0 is the source file's own foot.  A solved foot (joint positions, video, BVH of
                # another skeleton) can sit in the floor or off its spot, and the full-weight lock is what puts it back
                ws = lock_weight(c, ramp, E0, plant, l1 + l2, 0.6 if src_travels else 1.5) if direct is not None else soft_mask(c, ramp)
                tgt = E0 * (1 - ws[:, None]) + plant * ws[:, None]
                v = tgt - A
                dist = np.linalg.norm(v, axis=1)
                dc = soft_reach(dist, l1, l2, np.linalg.norm(E0 - A, axis=1))
                u = v / np.maximum(dist, EPS)[:, None]
                # bend-plane pole: the rest-pose bend direction carried along by the thigh's own rotation (no flipping)
                e_rest = rig.head[F_] - rig.head[U_]
                k_rest = rig.head[M_] - rig.head[U_]
                n_rest = k_rest - np.dot(k_rest, nrm(e_rest)) * nrm(e_rest)
                if np.linalg.norm(n_rest) < 0.02 * max(np.linalg.norm(e_rest), EPS):
                    n_rest = np.append(fwd_rest, 0.0) * (1.0 if sroles[jn[l["joint"]]]["limb"] != "front" else -1.0)
                pole = np.einsum("tij,j->ti", Wm[U_] @ rig.R[U_].T, nrm(n_rest))
                # where the source's own knee points, once it is bent by more than ~5 % of the leg; the rest-pose pole only
                # steers near-straight legs (soft reach bends a pinned straight leg a little - the wrong way on an in-place turn)
                kb = (K0 - A) - ((K0 - A) * nrm(E0 - A)).sum(1, keepdims=True) * nrm(E0 - A)
                pole = kb + 0.05 * (l1 + l2) * pole
                n = pole - (pole * u).sum(1, keepdims=True) * u
                bad = np.linalg.norm(n, axis=1) < 1e-5
                n = np.where(bad[:, None], nrm(np.cross(u, np.array([1.0, 0, 0]))), nrm(n))
                cosA = ((l1 * l1 + dc * dc - l2 * l2) / (2 * l1 * dc)).clip(-1, 1)
                K1 = A + l1 * (cosA[:, None] * u + np.sqrt(1 - cosA ** 2)[:, None] * n)
                E1 = A + u * dc[:, None]
                ov[U_] = nrm(nrm(K0 - A) * (1 - ws[:, None]) + nrm(K1 - A) * ws[:, None])
                ov[M_] = nrm(nrm(E0 - K0) * (1 - ws[:, None]) + nrm(E1 - K1) * ws[:, None])
            Wm = build_W(ov)
            flags.append("foot_lock")
        # flat feet while planted: level the foot at one heading per contact - the foot's own heading in the source for
        # direct transfers (the pelvis's said "forward" with an offset when the source skeleton's pelvis axes differ from
        # the rig's: ALS (UE4 mannequin) turns on the spot had both feet ~135 deg off the body), the pelvis's for solved
        # feet (too noisy).  Headings are unwrapped (a turn crossing +-180 deg took the median of [-170 .. +170]); frames
        # whose bone points up or down (lying, getting up) have no heading and are bridged.
        def heading(R_world, b):
            f = np.einsum("tij,j->ti", R_world @ rig.R[b].T, np.append(fwd_rest, 0.0))
            a = np.arctan2(f[:, 1], f[:, 0]) - math.atan2(fwd_rest[1], fwd_rest[0])
            ok = np.linalg.norm(f[:, :2], axis=1) > 0.5
            return np.interp(np.arange(T), np.flatnonzero(ok), np.unwrap(a[ok])) if ok.any() and not ok.all() else np.unwrap(a)
        yaw_pel = heading(Wm[pelvis_b], pelvis_b)
        for k, l in enumerate(legs):
            c = contacts[:, k]
            ws = 0.4 + 0.6 * soft_mask(c, max(2, int(round(0.08 * fps))))
            yaw_t = heading(W_src[l["F"]], l["F"]) if direct is not None else yaw_pel
            yk = yaw_t.copy()
            t = 0
            while t < T:                                   # a planted foot keeps one heading for the whole contact
                if c[t]:
                    t2 = t
                    while t2 + 1 < T and c[t2 + 1]:
                        t2 += 1
                    seg = yaw_t[t:t2 + 1]
                    if len(seg) > 0.8 * T and np.ptp(seg) > math.radians(20.0):
                        # the feet never lift (a shuffling turn on the spot: one contact for the whole clip) - follow the
                        # turn.  Not for ordinary contacts: a running pivot turns on the ball, and turning the foot about
                        # the ankle slid the toes
                        yk[t:t2 + 1] = sg_smooth(seg[:, None], max(3, int(round(0.2 * fps)) | 1), 2, 0)[:, 0]
                    else:
                        yk[t:t2 + 1] = np.median(seg)
                    t = t2 + 1
                else:
                    t += 1
            Wm[l["F"]] = mat_from_q(q_slerp(q_from_mat(Wm[l["F"]]), q_from_mat(rz(yk) @ rig.R[l["F"]]), ws))
        # never let a paw tip go through the floor: pitch the paw about the ankle until the tip touches
        Q, L, hd, tl = fk(Wm, Hd, D)
        for k, l in enumerate(legs):
            F_ = l["F"]
            ln = rig.length[F_]
            hz = hd[:, F_, 2]
            v = tl[:, F_] - hd[:, F_]
            floor = rest_floor[k] + terr_lo_t                # the lowest surface around this frame
            bad =(tl[:, F_, 2] < floor - 1e-4) & (hz - floor < ln * 0.999)
            if bad.any():
                zt = np.minimum(floor - hz, 0.0)
                hor = np.sqrt(np.maximum(ln * ln - zt * zt, 0.0))
                dxy = nrm(np.concatenate([v[:, :2], np.zeros((T, 1))], axis=1))
                vn = np.concatenate([dxy[:, :2] * hor[:, None], zt[:, None]], axis=1)
                Wn = swing(nrm(v), nrm(vn)) @ Wm[F_]
                Wm[F_] = np.where(bad[:, None, None], Wn, Wm[F_])
        flags.append("flat_feet")
    elif not legs:
        ctx_notes.append("No leg chains found in the bone map: foot lock / ground fix skipped.")

    # ---- loop closing: cross-fade the tail into the start (world-rotation space), then drop the cross-faded frames
    Wq = np.stack([q_from_mat(w) for w in Wm], axis=1)
    loop_k = 0
    if opts.get("loop") and T > 12:
        k = min(max(3, int(round(0.2 * fps))), T // 4)
        M_ = T - k
        w = ((np.arange(k) + 1) / (k + 1))
        Wn = Wq[:M_].copy()
        Wn[:k] = q_slerp(Wq[M_:], Wq[:k], w[:, None])
        Hn = Hd[:M_].copy()
        Hn[:k, 2] = Hd[M_:, 2] * (1 - w) + Hd[:k, 2] * w
        vel = np.diff(D, axis=0, prepend=D[:1])
        vel[0] = vel[1]
        vn = vel[:M_].copy()
        vn[:k] = vel[M_:] * (1 - w[:, None]) + vel[:k] * w[:, None]
        Dn = np.cumsum(vn, axis=0)
        Dn -= Dn[0]
        Hn[:, :2] = rig.head[pelvis_b][:2] + Dn
        Wq, Hd, D, T = Wn, Hn, Dn, M_
        Wm = [mat_from_q(Wq[:, b]) for b in range(B)]
        loop_k = k
        contacts = contacts[:T]
        settled = settled[:T]
        terr_lo_t, terr_hi_t, foot_floor = terr_lo_t[:T], terr_hi_t[:T], foot_floor[:T]
        ctx_notes.append(f"loop closed with a {k}-frame cross-fade ({T} frames kept)")
    Q, L, hd, tl = fk(Wm, Hd, D)
    _, L_ip, _, _ = fk(Wm, Hd - np.concatenate([D, np.zeros((T, 1))], axis=1), np.zeros_like(D))
    Q = fix_quat_continuity(Q)
    if smooth >= 0.8:
        Q = nrm(sg_smooth(Q, 5, 2, 0))
    travel = np.zeros((T, 3))
    travel[:, :2] = D
    out = {
        "names": np.array(rig.names), "quat": Q, "loc": L, "loc_inplace": L_ip, "head": hd, "tail": tl, "travel": travel,
        "fps": np.float64(fps), "height": np.float64(tl[..., 2].max()), "foot_bones": np.array(foot_idx, int),
        "foot_rest_floor": rest_floor, "root_path": hd[:, pelvis_b, :2], "contacts": contacts,
        "settled": settled, "terrain_lo": terr_lo_t, "terrain_hi": terr_hi_t, "foot_floor": foot_floor,
        "travel_bone": np.array(rig.names[travel_bone] if travel_bone is not None else rig.names[pelvis_b]),
    }
    info = {"scale_travel": s_t * user_scale, "scale_height": s_h, "yaw_deg": math.degrees(yaw), "pelvis_bone": rig.names[pelvis_b],
            "legs": [{"U": rig.names[l["U"]], "M": rig.names[l["M"]], "F": rig.names[l["F"]]} for l in legs],
            "travel_bone": rig.names[travel_bone] if travel_bone is not None else None, "loop": bool(opts.get("loop")), "loop_crossfade_frames": loop_k,
            "frames": int(T), "fps": fps, "cleanup": flags}
    return out, info


def main(args):
    common.progress(0.05, "opening rig")
    bpy.ops.wm.open_mainfile(filepath=args["rig_blend"])
    arm = common.pick_armature(args.get("armature"))
    opts = args.get("options", {})
    notes = []
    mw = np.array(arm.matrix_world)
    if np.abs(mw - np.eye(4)).max() > 1e-4:
        raise RuntimeError("Rig is not normalised (armature transform is not identity). Re-import the rig.")
    common.progress(0.15, "loading motion")
    motion = load_motion(args["motion"], opts)
    notes += motion["notes"]
    DIRECT.clear()
    src_file = opts.get("direct_source")
    if src_file and os.path.isfile(src_file):
        if abs(float(opts.get("speed") or 1.0) - 1.0) > 1e-3 or opts.get("fps") or (opts.get("loop") and opts.get("loop_window", "auto") != "none"):
            notes.append("direct transfer skipped: speed / fps / loop-window changes need the position solver")
        else:
            common.progress(0.2, "reading source bones")
            end = opts.get("end")
            d = sample_direct(src_file, Rig(arm).names, int(opts.get("start") or 0), int(end) if end else None)
            if d:
                DIRECT.update(d)
            else:
                notes.append("direct transfer skipped: the source shares too few bone names with the rig")
    common.progress(0.3, "solving")
    out, info = solve(arm, motion, args["bone_map"], opts, notes)
    bb = common.mesh_bbox_world()
    out["height"] = np.float64(float(bb[1][2] - bb[0][2]) if bb else out["height"])
    out["is_loop"] = np.array([bool(opts.get("loop"))])          # metrics: loop seam only matters for looping clips
    m = metrics.compute({k: out[k] for k in out})
    common.progress(0.8, "writing")
    os.makedirs(os.path.dirname(args["out_bake"]), exist_ok=True)
    out["meta"] = np.array(json.dumps({"options": opts, "info": info}, default=str))
    np.savez_compressed(args["out_bake"], **out)
    # verification against Blender's own pose evaluation (first, middle, last frame)
    check = {}
    if args.get("verify", True):
        bake = common.load_bake(args["out_bake"])
        errs = []
        T = bake["quat"].shape[0]
        for fi in sorted({0, T // 2, T - 1}):
            common.set_pose_frame(arm, bake, fi)
            bpy.context.view_layer.update()
            for bi, name in enumerate(bake["names"]):
                pb = arm.pose.bones[name]
                errs.append(float(np.linalg.norm(np.array(pb.head) - bake["head"][fi, bi])))
        check = {"fk_max_err_m": max(errs), "fk_mean_err_m": float(np.mean(errs))}
        notes.append(f"FK check vs Blender: max error {max(errs) * 1000:.2f} mm")
    common.progress(0.98, "done")
    return {"info": info, "metrics": m, "notes": notes, "check": check, "frames": int(out["quat"].shape[0]), "fps": float(out["fps"]), "bake": args["out_bake"]}


common.run(main)
