"""Stage (b1): subject detection with an RT-DETR(v2) COCO detector through `transformers` (weights from `prepare`)."""
from __future__ import annotations

import numpy as np

from .tracker import Det

HUMAN_LABELS = {"person"}
# COCO animal classes that a four-legged AP-10K pose expert can handle (bird excluded: it is a biped)
QUADRUPED_LABELS = {"cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"}


def subject_of_label(label: str) -> str | None:
    if label in HUMAN_LABELS:
        return "humanoid"
    if label in QUADRUPED_LABELS:
        return "quadruped"
    return None


class Detector:
    def __init__(self, model_dir, device: str = "cuda", fp16: bool = True, model=None, processor=None):
        import torch
        from transformers import AutoImageProcessor, AutoModelForObjectDetection
        self.torch = torch
        self.device = device if (device == "cpu" or torch.cuda.is_available()) else "cpu"
        self.processor = processor or AutoImageProcessor.from_pretrained(str(model_dir))
        self.model = model or AutoModelForObjectDetection.from_pretrained(str(model_dir))
        self.fp16 = fp16 and self.device != "cpu"
        if self.fp16:
            self.model = self.model.half()
        self.model.to(self.device).eval()
        self.id2label = {int(k): v for k, v in self.model.config.id2label.items()}

    def detect(self, frames_bgr: list[np.ndarray], threshold: float = 0.35) -> list[list[Det]]:
        """frames: BGR uint8 images.  Returns, per frame, detections restricted to human / quadruped classes."""
        torch = self.torch
        rgb = [np.ascontiguousarray(f[:, :, ::-1]) for f in frames_bgr]
        inp = self.processor(images=rgb, return_tensors="pt")
        inp = {k: (v.to(self.device).half() if (self.fp16 and v.dtype == torch.float32) else v.to(self.device))
               for k, v in inp.items()}
        with torch.no_grad():
            out = self.model(**inp)
        sizes = torch.tensor([[f.shape[0], f.shape[1]] for f in frames_bgr])
        out.logits = out.logits.float()
        out.pred_boxes = out.pred_boxes.float()
        res = self.processor.post_process_object_detection(out, threshold=threshold, target_sizes=sizes)
        dets = []
        for r in res:
            ds = []
            for s, l, b in zip(r["scores"].tolist(), r["labels"].tolist(), r["boxes"].tolist()):
                name = self.id2label.get(int(l), str(l))
                if subject_of_label(name) is None:
                    continue
                ds.append(Det(box=tuple(float(x) for x in b), score=float(s), label=name))
            dets.append(ds)
        return dets


def detect_all(det: Detector, frameset, ctx=None, batch: int = 8, threshold: float = 0.35) -> list[list[Det]]:
    out: list[list[Det]] = []
    for i in range(0, frameset.n, batch):
        idx = range(i, min(i + batch, frameset.n))
        out += det.detect([frameset.read(j) for j in idx], threshold)
        if ctx:
            ctx.progress(0.12 + 0.18 * (i / max(frameset.n, 1)), f"Detecting subject {min(i + batch, frameset.n)}/{frameset.n}")
    return out
