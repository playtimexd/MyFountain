"""Stage (b2): IoU tracker + main-subject selection. Pure numpy (testable without weights)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Det:
    box: tuple[float, float, float, float]   # x1,y1,x2,y2 px
    score: float
    label: str = ""


@dataclass
class Track:
    id: int
    frames: dict[int, Det] = field(default_factory=dict)
    last: int = -1

    def mean_score(self) -> float:
        return float(np.mean([d.score for d in self.frames.values()])) if self.frames else 0.0


def iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def iou_track(dets: list[list[Det]], iou_thr: float = 0.25, max_gap: int = 12) -> list[Track]:
    """Greedy IoU association with constant-velocity prediction of the track box."""
    tracks: list[Track] = []
    vel: dict[int, np.ndarray] = {}
    for t, ds in enumerate(dets):
        cand = [tr for tr in tracks if t - tr.last <= max_gap]
        pairs = []
        for tr in cand:
            lb = np.array(tr.frames[tr.last].box, float)
            pb = lb + vel.get(tr.id, np.zeros(4)) * (t - tr.last)
            for di, d in enumerate(ds):
                pairs.append((iou(pb, d.box), tr, di))
        pairs.sort(key=lambda p: -p[0])
        used_t, used_d = set(), set()
        for s, tr, di in pairs:
            if s < iou_thr or tr.id in used_t or di in used_d:
                continue
            prev = tr.frames[tr.last]
            nb = np.array(ds[di].box, float)
            vel[tr.id] = (nb - np.array(prev.box, float)) / max(t - tr.last, 1)
            tr.frames[t] = ds[di]
            tr.last = t
            used_t.add(tr.id)
            used_d.add(di)
        for di, d in enumerate(ds):
            if di in used_d:
                continue
            tr = Track(id=len(tracks), frames={t: d}, last=t)
            tracks.append(tr)
    return tracks


def pick_main_track(tracks: list[Track], first_box=None, first_frame: int = 0) -> Track | None:
    """Largest/most confident track; or, if the user gave a box, the track that overlaps it best."""
    if not tracks:
        return None
    if first_box is not None:
        fb = (first_box[0], first_box[1], first_box[2], first_box[3])
        best, best_s = None, 0.0
        for tr in tracks:
            fr = min(tr.frames, key=lambda k: abs(k - first_frame))
            s = iou(fb, tr.frames[fr].box)
            if s > best_s:
                best, best_s = tr, s
        if best is not None and best_s > 0.05:
            return best

    def score(tr: Track) -> float:
        return sum(d.score * np.sqrt(max((d.box[2] - d.box[0]) * (d.box[3] - d.box[1]), 1.0)) for d in tr.frames.values())
    return max(tracks, key=score)


def track_boxes(tr: Track, n: int, smooth: float = 0.6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Dense (n,4) boxes (interpolated over gaps, held at the ends, EMA-smoothed), the detection mask (n,) and the
    detection scores (n,)."""
    idx = np.array(sorted(tr.frames))
    B = np.array([tr.frames[i].box for i in idx], float)
    sc = np.array([tr.frames[i].score for i in idx], float)
    t = np.arange(n)
    box = np.stack([np.interp(t, idx, B[:, c]) for c in range(4)], 1)
    score = np.interp(t, idx, sc)
    mask = np.zeros(n, bool)
    mask[idx] = True
    # centred smoothing (forward+backward EMA) keeps the crop stable without lag
    def ema(x, a):
        y = x.copy()
        for i in range(1, len(x)):
            y[i] = a * y[i - 1] + (1 - a) * x[i]
        return y
    f = ema(box, smooth)
    b = ema(box[::-1], smooth)[::-1]
    box = 0.5 * (f + b)
    return box, mask, score
