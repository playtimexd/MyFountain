"""Runs INSIDE the SAM 3D Body environment (cwd = repo root).  UNTESTED (gated weights not available when written).
usage: python sam3d_worker.py in.json out.npz
Follows the README: `setup_sam_3d_body(...)` from notebook/utils.py, then `estimator.process_one_image(rgb, bboxes=...)`
(passing our own subject box skips the built-in detector)."""
import json
import sys

import cv2
import numpy as np


def main(spec_path, out_path):
    spec = json.load(open(spec_path, encoding="utf-8"))
    sys.path.insert(0, ".")
    from notebook.utils import setup_sam_3d_body            # repo-local helper (README)

    estimator = setup_sam_3d_body(hf_repo_id="facebook/sam-3d-body-dinov3", detector_name=None, segmentor_name=None,
                                  fov_name=None)
    n = spec["n"]
    kp3d = np.zeros((n, 70, 3), np.float32)
    kp2d = np.zeros((n, 70, 2), np.float32)
    cam_t = np.zeros((n, 3), np.float32)
    valid = np.zeros(n, bool)
    for i in range(n):
        img = cv2.imread(f"{spec['frames_dir']}/{i + 1:06d}.jpg")
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        box = np.array([spec["boxes"][i]], dtype=np.float32)
        outs = estimator.process_one_image(rgb, bboxes=box)
        if not outs:
            continue
        o = outs[0]
        kp3d[i] = np.asarray(o["pred_keypoints_3d"])[:70]
        kp2d[i] = np.asarray(o["pred_keypoints_2d"])[:70]
        cam_t[i] = np.asarray(o["pred_cam_t"]).reshape(-1)[:3]
        valid[i] = True
    np.savez(out_path, kp3d=kp3d, kp2d=kp2d, cam_t=cam_t, valid=valid)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
