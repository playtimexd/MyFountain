"""Stage (c): 2D pose.  ViTPose++ through `transformers` (`VitPoseForPoseEstimation`) for humans (COCO-17, expert 0)
and animals (AP-10K-17, expert 3), plus a dev-only `mock` backend that replays keypoints from a sidecar file."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .models import DATASET_INDEX


def xyxy_expand(box: np.ndarray, factor: float, w: int, h: int) -> np.ndarray:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    bw, bh = (box[2] - box[0]) * factor, (box[3] - box[1]) * factor
    return np.array([max(0, cx - bw / 2), max(0, cy - bh / 2), min(w, cx + bw / 2), min(h, cy + bh / 2)], float)


class VitPose2D:
    def __init__(self, model_dir, device: str = "cuda", fp16: bool = True, model=None, processor=None):
        import torch
        from transformers import AutoProcessor, VitPoseForPoseEstimation
        self.torch = torch
        self.device = device if (device == "cpu" or torch.cuda.is_available()) else "cpu"
        self.processor = processor or AutoProcessor.from_pretrained(str(model_dir))
        self.model = model or VitPoseForPoseEstimation.from_pretrained(str(model_dir))
        self.fp16 = fp16 and self.device != "cpu"
        if self.fp16:
            self.model = self.model.half()
        self.model.to(self.device).eval()

    def estimate(self, frames_bgr: list[np.ndarray], boxes_xyxy: np.ndarray, subject: str) -> tuple[np.ndarray, np.ndarray]:
        """One box per frame.  Returns keypoints (B,17,2) px in the frames' coordinates and scores (B,17)."""
        torch = self.torch
        rgb = [np.ascontiguousarray(f[:, :, ::-1]) for f in frames_bgr]
        xywh = [np.array([[b[0], b[1], b[2] - b[0], b[3] - b[1]]], float) for b in boxes_xyxy]
        inputs = self.processor(images=rgb, boxes=xywh, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        if self.fp16:
            inputs["pixel_values"] = inputs["pixel_values"].half()
        n_exp = getattr(getattr(self.model.config, "backbone_config", None), "num_experts", 1) or 1
        if n_exp > 1:
            inputs["dataset_index"] = torch.full((inputs["pixel_values"].shape[0],), DATASET_INDEX[subject],
                                                 dtype=torch.long, device=self.device)
        with torch.no_grad():
            out = self.model(**inputs)
        out.heatmaps = out.heatmaps.float()
        res = self.processor.post_process_pose_estimation(out, boxes=xywh)
        kp = np.stack([r[0]["keypoints"].float().cpu().numpy() for r in res])
        sc = np.stack([r[0]["scores"].float().cpu().numpy() for r in res])
        return kp, sc


def estimate_all(pose: VitPose2D, frameset, boxes: np.ndarray, subject: str, ctx=None, batch: int = 8,
                 expand: float = 1.15) -> tuple[np.ndarray, np.ndarray]:
    kps, scs = [], []
    for i in range(0, frameset.n, batch):
        idx = list(range(i, min(i + batch, frameset.n)))
        fr = [frameset.read(j) for j in idx]
        bx = [xyxy_expand(boxes[j], expand, frameset.width, frameset.height) for j in idx]
        k, s = pose.estimate(fr, np.array(bx), subject)
        kps.append(k)
        scs.append(s)
        if ctx:
            ctx.progress(0.32 + 0.23 * (i / max(frameset.n, 1)), f"2D pose {min(i + batch, frameset.n)}/{frameset.n}")
    return np.concatenate(kps), np.concatenate(scs)


# ------------------------------------------------------------------------------------------------ mock (dev only)
def mock_sidecar(video_path: str | Path) -> Path:
    return Path(str(video_path) + ".kp.npz")


def save_mock_sidecar(video_path, kp, conf, fps, skeleton, box=None):
    np.savez(mock_sidecar(video_path), kp=kp.astype(np.float32), conf=conf.astype(np.float32), fps=float(fps),
             skeleton=skeleton, box=np.zeros(4) if box is None else np.asarray(box))


def mock_estimate(video_path, times: np.ndarray, scale: float) -> dict:
    """Replay the sidecar keypoints at the requested timestamps (nearest frame); `scale` = extracted/source size."""
    p = mock_sidecar(video_path)
    if not p.exists():
        raise FileNotFoundError("The mock backend needs a '<video>.kp.npz' sidecar next to the video (dev only).")
    z = np.load(p, allow_pickle=False)
    kp, conf, fps = z["kp"], z["conf"], float(z["fps"])
    idx = np.clip(np.round(times * fps).astype(int), 0, len(kp) - 1)
    return {"kp": kp[idx] * scale, "conf": conf[idx], "skeleton": str(z["skeleton"])}
