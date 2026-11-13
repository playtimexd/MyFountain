"""fix_lr_swaps: undo injected left/right label swaps without touching natural paw crossings."""
import sys
import numpy as np
sys.path.insert(0, r"D:\CutLab\backend")
from app.services.mocap.pipeline import fix_lr_swaps  # noqa: E402

names = ["left_eye", "right_eye", "nose", "neck", "root_of_tail", "left_shoulder", "left_elbow", "left_front_paw",
         "right_shoulder", "right_elbow", "right_front_paw", "left_hip", "left_knee", "left_back_paw",
         "right_hip", "right_knee", "right_back_paw"]
ix = {n: i for i, n in enumerate(names)}
rng = np.random.default_rng(0)
T, fps = 120, 30.0
t = np.arange(T) / fps
kp = np.zeros((T, 17, 2))
base_x = 200 + 60 * t                                     # walking to the right in image space
for side, ph in (("left", 0.0), ("right", np.pi)):        # legs half a cycle apart -> paws cross every half cycle
    for part, dx, dy, amp in (("shoulder", 80, 0, 0), ("elbow", 82, 40, 12), ("front_paw", 85, 80, 30),
                              ("hip", -80, 0, 0), ("knee", -78, 40, 12), ("back_paw", -75, 80, 30)):
        swing = amp * np.sin(2 * np.pi * t / 1.2 + ph + (np.pi if "back" in part or part in ("hip", "knee") else 0))
        kp[:, ix[f"{side}_{part}"]] = np.stack([base_x + dx + swing + (3 if side == "left" else -3), 300 + dy + 0 * t], 1)
for n, (dx, dy) in {"nose": (130, -10), "neck": (90, -20), "root_of_tail": (-100, -10), "left_eye": (120, -25), "right_eye": (118, -24)}.items():
    kp[:, ix[n]] = np.stack([base_x + dx, 300 + dy + 0 * t], 1)
kp += rng.normal(0, 2.0, kp.shape)
truth = kp.copy()
conf = np.full((T, 17), 0.8)
boxes = np.stack([base_x - 150, np.full(T, 250.0), base_x + 170, np.full(T, 400.0)], 1)

# natural crossings only -> nothing should change
k0, _, n0 = fix_lr_swaps(kp, conf, names, boxes)
print(f"[{'ok' if n0 == 0 and np.allclose(k0, kp) else 'FAIL'}] no false corrections on clean data (fixed={n0})")

# inject swaps on 10% of frames, per chain
bad = kp.copy()
chains = [("shoulder", "elbow", "front_paw"), ("hip", "knee", "back_paw")]
injected = 0
for ch in chains:
    for f in rng.choice(np.arange(5, T - 5), size=T // 10, replace=False):
        L = [ix[f"left_{w}"] for w in ch]; R = [ix[f"right_{w}"] for w in ch]
        bad[f, L], bad[f, R] = bad[f, R].copy(), bad[f, L].copy(); injected += 1
fixed, _, n = fix_lr_swaps(bad, conf, names, boxes)
err = np.abs(fixed - truth).max()
# a swap left in place is only acceptable where both legs nearly coincide (crossing point: ~6 px apart + noise)
print(f"[{'ok' if err < 10 else 'FAIL'}] injected {injected} chain swaps -> frames fixed={n}, max error vs truth={err:.3f}px "
      f"(only ambiguous crossing frames may stay)")
