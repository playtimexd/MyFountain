"""Synthetic ground-truth evaluation of the mocap 2D->3D fitter (no weights, no network).

Usage:  backend\\.venv\\Scripts\\python.exe backend\\tests\\mocap_synthetic.py [--quick] [--seeds 3]
Builds an analytic quadruped (bear-like) and human walk, projects it with a random weak-perspective camera + noise +
dropped joints, runs fit3d and reports MPJPE / PA-MPJPE / bone-length error / foot sliding / gait phase vs the truth.
Exit code != 0 when a plausibility threshold is violated.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mocap import metrics as M                      # noqa: E402
from app.services.mocap import synthetic as S                    # noqa: E402
from app.services.mocap.fit3d import FitConfig, fit_motion, canonicalize   # noqa: E402
from app.services.mocap.skeletons import get_skeleton, draw_edges  # noqa: E402

# plausibility thresholds (relative to body size = human height / quadruped shoulder height)
LIMITS = {"humanoid": dict(pa_seq=0.085, mpjpe=0.12, bone=0.10, slide_ratio=3.0, slide_abs=0.30, phase=0.6),
          "quadruped": dict(pa_seq=0.085, mpjpe=0.12, bone=0.10, slide_ratio=3.0, slide_abs=0.30, phase=0.6)}


def run_case(subject: str, seed: int, heading_deg: float, pitch_deg: float, noise_px: float, drop: float,
             swap: float = 0.0, frames: int = 150, fps: float = 30.0, verbose=True, cfg: FitConfig | None = None,
             jitter: float = 0.0):
    rng = np.random.default_rng(seed)
    scale = float(rng.uniform(260, 520))
    gt = S.walk_sequence(subject, n_frames=frames, fps=fps, heading_deg=heading_deg, seed=seed, proportion_jitter=jitter)
    sk = get_skeleton(gt["skeleton"])
    kp, conf, kp_clean = S.simulate_2d(gt["joints3d"], scale=scale, pitch_deg=pitch_deg, noise_px=noise_px,
                                       drop_rate=drop, lr_swap_rate=swap, seed=seed, skeleton=gt["skeleton"])
    t0 = time.time()
    res = fit_motion(kp, conf, gt["skeleton"], fps, cfg=cfg or FitConfig(), image_size=(1280, 800))
    dt = time.time() - t0
    size = sk.size_ref
    edges = [e for e in draw_edges(gt["skeleton"])]
    # world frames are identical (camera frame) except for the pitch; compare in the pitch-rotated GT frame
    gtj = S.camera_project(gt["joints3d"], scale=1.0, pitch_deg=pitch_deg)[1]
    pj = res.joints3d
    e_mp = M.mpjpe(pj, gtj, None) / size
    e_pa_seq = M.pa_mpjpe_seq(pj - pj.mean(1, keepdims=True), gtj - gtj.mean(1, keepdims=True)) / size
    e_pa_fr = M.pa_mpjpe_frame(pj, gtj) / size
    bl = M.bone_length_error(pj, gtj, edges)
    feet = gt["feet"]
    # foot sliding: use GT contact flags for both
    slide_gt = M.foot_sliding(gtj, feet, gt["contacts"], fps)
    slide_fit = M.foot_sliding(pj, feet, gt["contacts"], fps)
    # gait phase: fore/aft position (along the body axis) of a diagonal pair of feet relative to the pelvis
    def fwd_axis(heading):
        return np.array([np.cos(heading), -np.sin(heading)])
    fg = fwd_axis(gt["heading"])
    fp = np.stack([np.cos(res.heading), -np.sin(res.heading)], -1)        # (T,2)
    def along(j3d, pel, f):
        rel = (j3d - pel[:, None])[..., [0, 2]]
        f = f[None, None, :] if f.ndim == 1 else f[:, None, :]
        return (rel * f).sum(-1)
    ag = along(gtj, gtj.mean(1), fg)
    ap = along(pj, pj.mean(1), fp)
    names = sk.out_names
    if subject == "quadruped":
        pairs = [("left_back_paw", "right_back_paw"), ("left_front_paw", "right_front_paw"), ("left_back_paw", "right_front_paw")]
    else:
        pairs = [("left_ankle", "right_ankle")]
    phase = []
    for a, b in pairs:
        ia, ib = names.index(a), names.index(b)
        sg = ag[:, ia] - ag[:, ib]
        sp = ap[:, ia] - ap[:, ib]
        sg, sp = sg - sg.mean(), sp - sp.mean()
        phase.append(float((sg * sp).sum() / (np.linalg.norm(sg) * np.linalg.norm(sp) + 1e-9)))
    out = dict(subject=subject, seed=seed, heading=heading_deg, pitch=pitch_deg, noise=noise_px, drop=drop, swap=swap,
               mpjpe=e_mp, pa_seq=e_pa_seq, pa_frame=e_pa_fr, bone_mean=bl["mean_rel_err"], bone_cv=bl["mean_cv_over_time"],
               slide_gt=slide_gt, slide_fit=slide_fit, phase_min=min(phase), phase=phase, reproj_px=res.reproj_px,
               secs=dt, heading_err_deg=float(np.degrees(np.abs(np.angle(np.exp(1j * (res.heading.mean() - gt["heading"])))))))
    if verbose:
        print(f"[{subject:9s} seed={seed} head={heading_deg:5.0f} pitch={pitch_deg:3.0f} noise={noise_px:3.1f} drop={drop:.2f}] "
              f"MPJPE={e_mp*100:5.1f}%  PA-seq={e_pa_seq*100:5.1f}%  PA-frame={e_pa_fr*100:5.1f}%  bone={bl['mean_rel_err']*100:4.1f}% "
              f"slide(gt/fit)={slide_gt:.2f}/{slide_fit:.2f} m/s  phase={min(phase):+.2f}  reproj={res.reproj_px:4.1f}px  "
              f"dpsi={out['heading_err_deg']:.0f}deg  {dt:.1f}s", flush=True)
    return out, res, gt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--subject", default="both")
    ap.add_argument("--hard", action="store_true", help="oblique views, 6px noise, 15% drops, L/R swaps, +-12% proportion mismatch")
    a = ap.parse_args()
    cases = []
    subs = ["quadruped", "humanoid"] if a.subject == "both" else [a.subject]
    rng = np.random.default_rng(123)
    for subj in subs:
        for s in range(a.seeds if not a.quick else 1):
            head = float(rng.choice([0, 180, 20, 160, -25, 200]))        # near-side views (facing right or left)
            pitch = float(rng.uniform(0, 6))
            if a.hard:
                head = float(rng.choice([0, 180, 35, 145, -35, 215]))
                cases.append((subj, s, head, float(rng.uniform(0, 9)), 6.0, 0.15, 0.03, 150, 30.0, True, None, 0.12))
            else:
                cases.append((subj, s, head, pitch, 3.0, 0.08, 0.0))
    results = []
    for c in cases:
        out, *_ = run_case(*c)
        results.append(out)
    bad = []
    for r in results:
        L = dict(LIMITS[r["subject"]])
        if a.hard:                                   # worst-case-ish conditions: looser but still "plausible"
            L.update(pa_seq=0.12, mpjpe=0.16, bone=0.12, slide_abs=0.55)
        checks = [("PA-seq", r["pa_seq"] <= L["pa_seq"]), ("MPJPE", r["mpjpe"] <= L["mpjpe"]),
                  ("bone", r["bone_mean"] <= L["bone"]), ("phase", r["phase_min"] >= L["phase"]),
                  ("slide", r["slide_fit"] <= L["slide_abs"])]
        for n, ok in checks:
            if not ok:
                bad.append(f"{r['subject']} seed={r['seed']}: {n}")
    print("\nSUMMARY", "PASS" if not bad else f"FAIL: {bad}")
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
