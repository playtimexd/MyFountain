"""GPU optimisation-based 2D -> 3D skeleton fitting (our own, no external weights).

Model
-----
* A canonical skeleton tree (``skeletons.py``) with per-bone *unit directions* d[t,j] (free, per frame, in the body
  frame) and per-sequence bone lengths  L_j = L0_j * exp(delta_g(j))  (left/right tied, strong prior to the canonical
  proportions or to ``bone_lengths`` supplied from a target rig).  Positions come from forward kinematics, so bone
  lengths are exact by construction.
* Body frame = yaw rotation psi[t] about the vertical axis (heading w.r.t. the camera) + root translation.
* Camera: fixed, weak perspective (orthographic + scale), gravity-aligned (pitch assumed 0).  World frame = camera
  frame: +X image right, +Y up, +Z toward the viewer.  The root depth is not observable; it is a free per-frame
  variable that is only constrained by foot-sliding / smoothness.
* Losses: confidence weighted robust reprojection, acceleration/velocity smoothness, ground contact (foot height and
  no sliding while the 2D foot is quasi-static), joint-angle limits (hinge bend range + bend direction, abduction),
  a depth prior (limbs stay close to the sagittal plane, torso bones stay close to the rest pose) and bone-length
  priors.  Missing / low-confidence joints get weight 0 and are filled in by the temporal + pose priors.
* Multi-start over the initial heading (8 values) is solved as one batched problem; the best start is refined.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import numpy as np

from .skeletons import SkeletonDef, get_skeleton

if TYPE_CHECKING:       # torch is imported lazily (it adds >10 s to engine start-up)
    import torch


@dataclass
class FitConfig:
    conf_thresh: float = 0.15
    n_starts: int = 8
    iters_stage1: int = 120
    iters_stage2: int = 450
    iters_refine: int = 150
    lr: float = 0.03
    sigma_rel: float = 0.02          # 2D noise, as a fraction of body size
    robust_c: float = 3.0            # Geman-McClure scale (in sigmas)
    w_rep: float = 1.0
    w_acc: float = 0.6
    w_vel: float = 0.02
    w_contact_h: float = 1.0
    w_slide: float = 0.6
    w_limits: float = 5.0
    w_rest: float = 1.0
    w_sag: float = 0.15
    w_len: float = 1.0
    w_psi: float = 5.0
    w_rz: float = 0.5
    acc_scale: float = 18.0          # m/s^2 per unit of body size considered "normal" limb acceleration
    contact_speed: float = 0.35      # m/s (relative to body size) below which a foot counts as planted
    seed: int = 0


@dataclass
class FitResult:
    skeleton: str
    joints3d: np.ndarray            # (T,J,3) world (camera frame), Y up, metres (canonical size)
    pelvis: np.ndarray              # (T,3)  mid-hip virtual root
    heading: np.ndarray             # (T,)   yaw of the body forward axis (rad), 0 = facing image right
    contacts: np.ndarray            # (T,F)  foot contact probability (from the fitted motion)
    feet: list[int]                 # output joint indices of the feet
    kp_proj: np.ndarray             # (T,J,2) reprojection of the fit (px)
    scale_px_per_m: float
    bone_lengths: dict[str, float]
    pivot: tuple[float, float]      # image centre used for the projection (px)
    losses: dict[str, float] = field(default_factory=dict)
    reproj_px: float = 0.0          # mean confident reprojection error (px)
    local: np.ndarray | None = None  # (T,J,3) pelvis-relative, heading-free (body frame)
    interpolated: np.ndarray | None = None  # (T,J) True where the 2D keypoint was missing
    full: np.ndarray | None = None  # (T,N,3) all internal joints incl. virtual ones (pelvis, neck)
    all_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------- helpers
def interp_missing(kp: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Linear interpolation (constant extrapolation) of invalid 2D keypoints along time, per joint."""
    out = kp.copy()
    T, J, _ = kp.shape
    t = np.arange(T)
    for j in range(J):
        v = valid[:, j]
        if v.sum() == 0:
            continue
        if v.sum() == T:
            continue
        for c in range(2):
            out[:, j, c] = np.interp(t, t[v], kp[v, j, c])
    # joints never seen: fall back to the per-frame centroid of the others
    never = valid.sum(0) == 0
    if never.any():
        cen = np.array([out[t_][~never].mean(0) for t_ in range(T)])
        out[:, never] = cen[:, None, :]
    return out


def _gauss1d(x: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or len(x) < 3:
        return x
    r = int(max(1, math.ceil(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(x, [(r, r)] + [(0, 0)] * (x.ndim - 1), mode="edge")
    out = np.zeros_like(x, dtype=np.float64)
    for i, w in enumerate(k):
        out += w * pad[i:i + len(x)]
    return out


def estimate_scale(kpi: np.ndarray, sk: SkeletonDef, size: float) -> float:
    """Initial pixels-per-metre from the longest torso/limb 2D extents (90th percentile over time)."""
    names = sk.out_names
    pairs = []
    if sk.name == "coco17":
        pairs = [("left_shoulder", "left_hip", 0.52 * size), ("right_shoulder", "right_hip", 0.52 * size),
                 ("left_hip", "left_knee", 0.43 * size), ("right_hip", "right_knee", 0.43 * size),
                 ("left_knee", "left_ankle", 0.43 * size), ("right_knee", "right_ankle", 0.43 * size)]
    else:
        pairs = [("neck", "left_hip", 1.1 * size), ("neck", "right_hip", 1.1 * size),
                 ("left_hip", "left_knee", 0.37 * size), ("right_hip", "right_knee", 0.37 * size),
                 ("left_shoulder", "left_elbow", 0.36 * size), ("right_shoulder", "right_elbow", 0.36 * size)]
    est = []
    for a, b, ln in pairs:
        d = np.linalg.norm(kpi[:, names.index(a)] - kpi[:, names.index(b)], axis=-1)
        est.append(np.percentile(d, 90) / ln)
    return float(max(np.median(est), 1e-3))


def contact_probability(obs_m: np.ndarray, conf: np.ndarray, feet_out_idx: list[int], fps: float, size: float,
                        cfg: FitConfig) -> np.ndarray:
    """P(foot planted) from the 2D track only. obs_m: (T,J,2) in image-metres (y up)."""
    T = obs_m.shape[0]
    F = len(feet_out_idx)
    P = np.zeros((T, F))
    foot = obs_m[:, feet_out_idx]                                   # (T,F,2)
    foot_s = _gauss1d(foot, 1.0)
    vel = np.zeros_like(foot_s)
    vel[1:-1] = (foot_s[2:] - foot_s[:-2]) * fps / 2
    vel[0], vel[-1] = vel[1], vel[-2]
    speed = np.linalg.norm(vel, axis=-1)                            # m/s (image-plane)
    v0 = cfg.contact_speed * size
    ps = np.exp(-0.5 * (speed / v0) ** 2)
    ymin = foot_s[..., 1].min(1)                                    # lowest foot per frame (y up)
    win = max(3, int(round(0.6 * fps)))
    ymin_w = np.array([ymin[max(0, t - win):t + win + 1].min() for t in range(T)])
    low = np.exp(-0.5 * ((foot_s[..., 1] - ymin_w[:, None]) / (0.07 * size)) ** 2)
    P = ps * low
    P[conf[:, feet_out_idx] < cfg.conf_thresh] = 0.0
    return P


def _rot_y(x: torch.Tensor, psi: torch.Tensor) -> torch.Tensor:
    """Rotate (...,3) vectors about +Y by psi (broadcast over leading dims): x'=c x + s z, z'=-s x + c z."""
    import torch
    c, s = torch.cos(psi)[..., None], torch.sin(psi)[..., None]
    X, Y, Z = x[..., 0:1], x[..., 1:2], x[..., 2:3]
    return torch.cat([c * X + s * Z, Y, -s * X + c * Z], -1)


def _fk(dirs: torch.Tensor, lengths: torch.Tensor, parent: list[int], order: list[int]) -> torch.Tensor:
    import torch
    B, T, N, _ = dirs.shape
    pos: list = [None] * N
    for j in order:
        if parent[j] < 0:
            pos[j] = torch.zeros(B, T, 3, device=dirs.device, dtype=dirs.dtype)
        else:
            pos[j] = pos[parent[j]] + dirs[:, :, j] * lengths[:, None, j, None]
    return torch.stack(pos, 2)


# ---------------------------------------------------------------------------------------------------- main fit
def fit_motion(kp: np.ndarray, conf: np.ndarray, skeleton: str, fps: float, *, cfg: FitConfig | None = None,
               size: float = 1.0, bone_lengths: dict[str, float] | None = None, image_size=(1280, 720),
               device: str | None = None, progress: Callable[[float, str], None] | None = None,
               ground_pitch_deg: float = 0.0) -> FitResult:
    """Fit a 3D skeleton to 2D keypoints.  kp (T,J,2) px, conf (T,J) in [0,1]."""
    import torch
    cfg = cfg or FitConfig()
    sk = get_skeleton(skeleton)
    # camera pitch (looking down by `ground_pitch_deg`): world stays gravity-aligned (Y up, ground y=0) and the
    # projection is  x_img = s*X,  y_img = s*(Y*cos - Z*sin) + gy   (points nearer the camera (+Z) appear lower)
    th = math.radians(float(ground_pitch_deg or 0.0))
    cth, sth = math.cos(th), math.sin(th)
    kp = np.asarray(kp, np.float64)
    conf = np.clip(np.asarray(conf, np.float64), 0, 1)
    T, J, _ = kp.shape
    if T < 6:
        raise ValueError("Too few frames for 3D fitting (need at least 6).")
    if J != 17:
        raise ValueError(f"Expected 17 keypoints for {skeleton}, got {J}.")
    # many tiny kernels: the CPU is faster than the GPU for short clips; the GPU wins on long ones
    dev = torch.device(device or ("cuda" if (torch.cuda.is_available() and T > 700) else "cpu"))
    torch.manual_seed(cfg.seed)
    dt = torch.float32
    N = sk.n
    body = sk.size_ref * size
    rep = lambda p, m="": progress(p, m) if progress else None   # noqa: E731

    valid = conf >= cfg.conf_thresh
    kpi = interp_missing(kp, valid)
    pivot = (image_size[0] / 2.0, image_size[1] / 2.0)
    s0 = estimate_scale(kpi, sk, size)
    obs = np.stack([(kp[..., 0] - pivot[0]) / s0, -(kp[..., 1] - pivot[1]) / s0], -1)     # image-metres, y up
    obs_i = np.stack([(kpi[..., 0] - pivot[0]) / s0, -(kpi[..., 1] - pivot[1]) / s0], -1)
    w_obs = np.where(valid, conf, 0.0)
    feet_out = [sk.out_names.index(n) for n in sk.feet]
    P_contact = contact_probability(obs_i, conf, feet_out, fps, size, cfg)

    # init values
    rest_d, rest_len = sk.rest_dirs_lengths()
    rest_len = rest_len * size
    order = sk.order()
    groups = sk.length_groups()
    # canonical bone length override (rig proportions)
    base_len = rest_len.copy()
    fixed_len = False
    if bone_lengths:
        for n, v in bone_lengths.items():
            if n in sk.all_names and v and v > 0:
                j = sk.idx(n)
                base_len[j] = float(v)
        fixed_len = True
    B = cfg.n_starts
    psi0 = torch.tensor([2 * math.pi * k / B for k in range(B)], dtype=dt, device=dev)
    pel2d = obs_i[:, [sk.out_names.index(n) for n in (("left_hip", "right_hip"))]].mean(1) if sk.name == "coco17" \
        else obs_i[:, [sk.out_names.index("left_hip"), sk.out_names.index("right_hip")]].mean(1)

    def tt(a):
        return torch.as_tensor(np.asarray(a), dtype=dt, device=dev)

    obs_t, w_t = tt(obs), tt(w_obs)
    P_t = tt(P_contact)
    rest_d_t, base_len_t = tt(rest_d), tt(base_len)
    groups_t = torch.as_tensor(groups, device=dev)
    parent = sk.parent
    out_idx = torch.as_tensor(sk.out_idx, device=dev)
    feet_all = torch.as_tensor([sk.idx(n) for n in sk.feet], device=dev)
    foot_h = tt([sk.rest[sk.idx(n)][1] * size for n in sk.feet])
    rw = tt([sk.rest_weight.get(n, 0.0) for n in sk.all_names])
    limb_mask = tt([1.0 if sk.bone_class.get(n) == "limb" else 0.0 for n in sk.all_names])
    hinge_idx = [(sk.idx(c), sk.idx(b), s, math.radians(m)) for a, b, c, s, m in sk.hinges]   # child of 2nd bone, 1st bone, sign
    hinge_b = [(sk.idx(b), sk.idx(c), s, math.radians(m)) for a, b, c, s, m in sk.hinges]     # (bone1 joint, bone2 joint)
    sigma = cfg.sigma_rel * body
    rc = cfg.robust_c

    # parameters ---------------------------------------------------------------------------------------
    raw = rest_d_t[None, None].repeat(B, T, 1, 1).clone()
    raw += 0.02 * torch.randn_like(raw)
    raw[:, :, 0] = torch.tensor([1.0, 0.0, 0.0], device=dev)              # root has no bone
    raw = raw.requires_grad_(True)
    delta = torch.zeros(B, N, device=dev, dtype=dt, requires_grad=True)
    log_s = torch.zeros(B, device=dev, dtype=dt, requires_grad=True)
    root_uv = tt(pel2d)[None].repeat(B, 1, 1).clone().requires_grad_(True)
    dpsi = torch.zeros(B, T, device=dev, dtype=dt, requires_grad=True)
    psi_g = psi0.clone().requires_grad_(True)
    rz = torch.zeros(B, T, device=dev, dtype=dt, requires_grad=True)
    foot_obs_y = obs_i[:, feet_out, 1]
    gy0 = float(np.percentile(foot_obs_y, 4)) - float(foot_h.mean())
    gy = torch.full((B,), gy0, device=dev, dtype=dt).requires_grad_(True)

    def forward(raw_, delta_, log_s_, root_uv_, dpsi_, psi_g_, rz_, gy_):
        d = raw_ / raw_.norm(dim=-1, keepdim=True).clamp_min(1e-8)
        lengths = base_len_t[None] * torch.exp(delta_[:, groups_t])
        Pl = _fk(d, lengths, parent, order)                            # (B,T,N,3) body frame
        psi = psi_g_[:, None] + dpsi_
        R = _rot_y(Pl, psi[..., None].expand(-1, -1, N))               # (B,T,N,3)
        s_rel = torch.exp(log_s_)[:, None, None]
        root_w = torch.cat([root_uv_[..., 0:1] / s_rel,
                            ((root_uv_[..., 1:2] - gy_[:, None, None]) / s_rel + rz_[..., None] * sth) / cth,
                            rz_[..., None]], -1)                          # (B,T,3); world height above the ground
        Xw = R + root_w[:, :, None]
        return d, lengths, Pl, psi, Xw, s_rel

    def losses(stage: int, it_frac: float):
        gyv = torch.stack([torch.zeros_like(gy), gy], -1)[:, None, None, :]
        d, lengths, Pl, psi, Xw, s_rel = forward(raw, delta, log_s, root_uv, dpsi, psi_g, rz, gy)
        Xo = Xw[:, :, out_idx]
        cam2 = Xo[..., :2] if th == 0.0 else torch.stack([Xo[..., 0], Xo[..., 1] * cth - Xo[..., 2] * sth], -1)
        pred = torch.exp(log_s)[:, None, None, None] * cam2 + gyv
        r = (pred - obs_t[None]) / sigma
        r2 = (r ** 2).sum(-1)
        cc = 25.0 if stage == 1 else rc * (1.0 + 7.0 * (1.0 - it_frac) ** 2)      # graduated robust scale
        rho = cc * cc * r2 / (r2 + cc * cc)
        L = {}
        L["rep"] = (w_t[None] * rho).sum((1, 2)) / (w_t.sum() + 1e-6)
        # smoothness (dimensionless): acceleration in units of acc_scale, velocity small
        acc = (Xw[:, 2:] - 2 * Xw[:, 1:-1] + Xw[:, :-2]) * (fps ** 2) / (cfg.acc_scale * size)
        L["acc"] = (acc ** 2).mean((1, 2, 3))
        vel = (Xw[:, 1:] - Xw[:, :-1]) * fps / (2.0 * size)
        L["vel"] = (vel ** 2).mean((1, 2, 3))
        # heading smoothness
        dps = (psi[:, 1:] - psi[:, :-1]) * fps
        L["psi"] = (dps ** 2).mean(1) * 0.05 + ((psi[:, 2:] - 2 * psi[:, 1:-1] + psi[:, :-2]) * fps * fps) .pow(2).mean(1) * 0.001
        # root depth: smooth and loosely following the heading direction
        rzv = (rz[:, 1:] - rz[:, :-1]) * fps
        L["rz"] = (rzv ** 2).mean(1)
        if th != 0.0:
            # with a tilted camera the root can slide along the (slanted) viewing ray without changing the image,
            # which trades depth for height: pin the pelvis height to stay near-constant over the clip.
            ry = Xw[:, :, 0, 1]
            L["root_h"] = (((ry - ry.mean(1, keepdim=True)) / (0.04 * size)) ** 2).mean(1)
        # ground contact
        Yf = Xw[:, :, feet_all, 1]                                     # (B,T,F)
        hh = foot_h[None, None]
        L["contact_h"] = (P_t[None] * ((Yf - hh) / (0.04 * size)) ** 2).sum((1, 2)) / (P_t.sum() + 1e-6) \
            + (torch.relu(0.7 * hh - Yf) ** 2 / (0.03 * size) ** 2).mean((1, 2))
        vf = (Xw[:, 1:, feet_all][..., [0, 2]] - Xw[:, :-1, feet_all][..., [0, 2]]) * fps     # (B,T-1,F,2) m/s
        pm = torch.minimum(P_t[1:], P_t[:-1])[None]
        L["slide"] = (pm * (vf ** 2).sum(-1) / (0.12 * size) ** 2).sum((1, 2)) / (pm.sum() + 1e-6)
        # joint limits ------------------------------------------------------------------------------
        lim = torch.zeros(B, device=dev, dtype=dt)
        for (j1, j2, sgn, mx) in hinge_b:
            d1, d2 = d[:, :, j1], d[:, :, j2]
            cosb = (d1 * d2).sum(-1).clamp(-1 + 1e-6, 1 - 1e-6)
            bend = torch.acos(cosb)
            lim = lim + (torch.relu(bend - mx) ** 2).mean(1) * 4.0
            cz = torch.cross(d1, d2, dim=-1)[..., 2]
            lim = lim + (torch.relu(-sgn * cz - 0.03) ** 2).mean(1) * 10.0
        ab = torch.relu(d[..., 2].abs() - sk.abduct_limit) * limb_mask[None, None]
        lim = lim + (ab ** 2).mean((1, 2)) * 10.0
        L["limits"] = lim
        # depth prior: limbs near the sagittal plane; torso/head near the rest direction
        L["sag"] = ((d[..., 2] * limb_mask[None, None]) ** 2).mean((1, 2))
        L["rest"] = ((1 - (d * rest_d_t[None, None]).sum(-1)) * rw[None, None]).mean((1, 2))
        # bone length prior
        L["len"] = ((delta / 0.12) ** 2).mean(1) if not fixed_len else ((delta / 0.01) ** 2).mean(1)
        return L, Xw

    W = {"rep": cfg.w_rep, "acc": cfg.w_acc, "vel": cfg.w_vel, "psi": cfg.w_psi, "rz": cfg.w_rz,
         "contact_h": cfg.w_contact_h, "slide": cfg.w_slide, "limits": cfg.w_limits, "sag": cfg.w_sag,
         "rest": cfg.w_rest, "len": cfg.w_len}
    if th != 0.0:
        W["root_h"] = 0.5

    def total(L, stage, frac):
        wscale = {"limits": min(1.0, 0.2 + frac), "contact_h": min(1.0, 0.3 + frac), "slide": min(1.0, 0.3 + frac)}
        if stage == 1:
            use = ("rep", "psi", "vel")
        else:
            use = tuple(W.keys())
        tot = 0.0
        for k in use:
            tot = tot + W[k] * wscale.get(k, 1.0) * L[k]
        return tot

    def run(params, iters, stage, lr, tag):
        opt = torch.optim.Adam(params, lr=lr)
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, iters, eta_min=lr * 0.05)
        for it in range(iters):
            opt.zero_grad()
            L, _ = losses(stage, it / max(iters - 1, 1))
            tot = total(L, stage, it / max(iters - 1, 1))
            tot.sum().backward()
            opt.step()
            sched.step()
            if it % 25 == 0:
                rep(0.0 + (it / iters), tag)
        return {k: v.detach() for k, v in L.items()}

    rep(0.0, "Fitting: coarse alignment")
    run([log_s, root_uv, dpsi, psi_g, gy], cfg.iters_stage1, 1, cfg.lr, "coarse alignment")
    rep(0.2, "Fitting: full pose")
    L2 = run([raw, delta, log_s, root_uv, dpsi, psi_g, rz, gy], cfg.iters_stage2, 2, cfg.lr * 0.7, "full pose")
    with torch.no_grad():
        Lf, _ = losses(2, 1.0)
        tot = sum(W[k] * Lf[k] for k in W)
        best = int(torch.argmin(tot))
    rep(0.8, "Fitting: refining best start")
    # refine only the best start
    sel = lambda t_: t_.detach()[best:best + 1].clone().requires_grad_(True)   # noqa: E731
    B = 1
    raw, delta, log_s, root_uv, dpsi, psi_g, rz, gy = (sel(raw), sel(delta), sel(log_s), sel(root_uv), sel(dpsi), sel(psi_g), sel(rz), sel(gy))
    Lr = run([raw, delta, log_s, root_uv, dpsi, psi_g, rz, gy], cfg.iters_refine, 2, cfg.lr * 0.3, "refining")
    with torch.no_grad():
        d, lengths, Pl, psi, Xw, s_rel = forward(raw, delta, log_s, root_uv, dpsi, psi_g, rz, gy)
        Lf, _ = losses(2, 1.0)
        total_loss = float(sum(W[k] * Lf[k] for k in W)[0])
        gy_np = float(gy.detach()[0])
        X = Xw[0].cpu().numpy().astype(np.float64)                         # (T,N,3)
        Pl0 = Pl[0].cpu().numpy().astype(np.float64)
        psi_np = psi[0].cpu().numpy().astype(np.float64)
        scale = float(torch.exp(log_s.detach())[0]) * s0
        len_np = lengths[0].cpu().numpy()
    joints = X[:, sk.out_idx]
    pelvis = X[:, 0]
    # reprojection in px
    y_cam = joints[..., 1] * cth - joints[..., 2] * sth
    proj = np.stack([pivot[0] + scale * joints[..., 0], pivot[1] - s0 * (float(torch.exp(log_s.detach())[0]) * y_cam + gy_np)], -1)
    err = np.linalg.norm(proj - kp, axis=-1)
    reproj_px = float(err[valid].mean()) if valid.any() else float("nan")
    # contacts from the fitted motion
    Yf = X[:, [sk.idx(n) for n in sk.feet], 1] - np.array([sk.rest[sk.idx(n)][1] * size for n in sk.feet])[None]
    vfx = np.zeros_like(Yf)
    fe = X[:, [sk.idx(n) for n in sk.feet]]
    vfx[1:-1] = np.linalg.norm((fe[2:] - fe[:-2])[..., [0, 2]], axis=-1) * fps / 2
    vfx[0], vfx[-1] = vfx[1], vfx[-2]
    contacts = (np.exp(-0.5 * (Yf / (0.04 * size)) ** 2) * np.exp(-0.5 * (vfx / (0.25 * size)) ** 2))
    local = Pl0[:, sk.out_idx] - Pl0[:, 0:1]
    res = FitResult(skeleton=sk.name, joints3d=joints, pelvis=pelvis, heading=psi_np, contacts=contacts,
                    feet=feet_out, kp_proj=proj, scale_px_per_m=scale,
                    bone_lengths={sk.all_names[j]: float(len_np[j]) for j in range(1, N)},
                    pivot=pivot, losses={k: float(v[0]) for k, v in Lf.items()}, reproj_px=reproj_px, local=local,
                    interpolated=~valid, full=X, all_names=list(sk.all_names))
    res.total_loss = total_loss
    res.camera_pitch_deg = float(ground_pitch_deg or 0.0)
    rep(1.0, "Fitting: done")
    return res


# ---------------------------------------------------------------------------------------------------- frames
def canonicalize(res: FitResult) -> dict:
    """Rotate the fit about +Y so that the mean heading faces +Z (glTF convention: +Y up, forward +Z, the subject's
    left = +X).  The path starts at x = z = 0.  Returns dict(joints, pelvis, full, local, yaw)."""
    f = np.array([np.cos(res.heading).mean(), np.sin(res.heading).mean()])     # forward = (cos psi, -sin psi) in (x,z)
    mean_psi = math.atan2(f[1], f[0])
    fx, fz = math.cos(mean_psi), -math.sin(mean_psi)
    phi = math.atan2(-fx, fz)
    c, s = math.cos(phi), math.sin(phi)
    Ry = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    j = res.joints3d @ Ry.T
    p = res.pelvis @ Ry.T
    full = res.full @ Ry.T if res.full is not None else None
    # body frame (x fwd, y up, z right) -> facing +Z with left = +X:  x' = -z, y' = y, z' = x
    loc = res.local
    loc_c = np.stack([-loc[..., 2], loc[..., 1], loc[..., 0]], -1)
    shift = np.array([p[0, 0], 0.0, p[0, 2]])
    return {"joints": j - shift, "pelvis": p - shift, "full": None if full is None else full - shift,
            "local": loc_c, "yaw": phi, "rotation": Ry}
