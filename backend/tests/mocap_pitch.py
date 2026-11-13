"""Camera pitch (looking down) support in fit3d: compare assume-level vs true pitch vs auto-selected pitch."""
import sys
import numpy as np
sys.path.insert(0, r"D:\CutLab\backend")
from app.services.mocap import metrics as M            # noqa: E402
from app.services.mocap import synthetic as S          # noqa: E402
from app.services.mocap.fit3d import FitConfig, fit_motion   # noqa: E402
from app.services.mocap.pipeline import pick_camera_pitch     # noqa: E402
from app.services.mocap.skeletons import get_skeleton  # noqa: E402

FPS = 30.0


def evaluate(res, gt, pitch_used, size):
    # the fit is expressed in the world frame implied by the pitch it used -> compare against GT in that frame
    gtj = S.camera_project(gt["joints3d"], scale=1.0, pitch_deg=gt["true_pitch"] - pitch_used)[1]
    pj = res.joints3d
    pa = M.pa_mpjpe_seq(pj - pj.mean(1, keepdims=True), gtj - gtj.mean(1, keepdims=True)) / size
    # height realism in the world frame: per-frame height range of the hips (true motion bobs only a few cm)
    names = get_skeleton(gt["skeleton"]).out_names
    hips = pj[:, [names.index(n) for n in names if n.endswith("_hip")]].mean(1)
    bob = float(np.ptp(hips[:, 1])) / size
    slide = M.foot_sliding(pj, gt["feet"], gt["contacts"], FPS)
    return pa, bob, slide


rows = []
for pitch in (0.0, 20.0, 35.0):
    for seed in (0, 1):
        gt = S.walk_sequence("quadruped", n_frames=120, fps=FPS, heading_deg=-80 + 25 * seed, seed=seed)
        gt["true_pitch"] = pitch
        size = get_skeleton(gt["skeleton"]).size_ref
        kp, conf, _ = S.simulate_2d(gt["joints3d"], scale=420.0, pitch_deg=pitch, noise_px=4.0, drop_rate=0.08, seed=seed, skeleton=gt["skeleton"])
        out = {}
        for tag, p in (("level", 0.0), ("true", pitch)):
            r = fit_motion(kp, conf, gt["skeleton"], FPS, cfg=FitConfig(), image_size=(1280, 800), ground_pitch_deg=p)
            out[tag] = evaluate(r, gt, p, size)
        auto_p, scores = pick_camera_pitch(kp, conf, gt["skeleton"], FPS, image_size=(1280, 800))
        r = fit_motion(kp, conf, gt["skeleton"], FPS, cfg=FitConfig(), image_size=(1280, 800), ground_pitch_deg=auto_p)
        out["auto"] = evaluate(r, gt, auto_p, size)
        print(f"pitch={pitch:4.0f} seed={seed} auto-picked={auto_p:4.0f} | " + " | ".join(
            f"{k}: PA={v[0]*100:4.1f}% hipBob={v[1]*100:4.1f}% slide={v[2]:.2f}m/s" for k, v in out.items()), flush=True)
        rows.append((pitch, auto_p, out))

ok = all(abs(a - p) <= 15 for p, a, _ in rows) and all(o["auto"][0] <= o["level"][0] + 0.01 for p, a, o in rows)
better = [o["level"][0] - o["auto"][0] for p, a, o in rows if p >= 20]
print("SUMMARY", "PASS" if ok else "FAIL", f"| mean PA gain at pitch>=20: {np.mean(better)*100:.1f} pts")
