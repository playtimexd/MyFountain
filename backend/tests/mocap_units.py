"""Unit tests for the mocap building blocks that need neither weights nor a running engine.
Usage:  backend\\.venv\\Scripts\\python.exe backend\\tests\\mocap_units.py
Covers: IoU tracker + subject selection, motion.npz round trip, GEM-X / SAM 3D Body converters (synthetic arrays only -
the real models are untested), rig proportions, gait/loop detection, keypoint cleaning, backend selection, and the
transformers plumbing of the detector + ViTPose++ wrappers using RANDOMLY initialised tiny models (no downloads).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mocap import (backends, detect, gem, metrics, models, motion_io, pipeline, pose2d, postprocess, rig,  # noqa: E402
                                sam3d, synthetic as S, tracker)
from app.services.mocap.fit3d import FitConfig, canonicalize, fit_motion                                             # noqa: E402
from app.services.mocap.skeletons import get_skeleton                                                                 # noqa: E402

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"   [{extra}]" if extra else ""), flush=True)


def t_tracker():
    D = tracker.Det
    dets = []
    for t in range(40):
        a = D((100 + 5 * t, 100, 300 + 5 * t, 400), 0.9, "bear")                  # big subject moving right
        b = D((600 - 4 * t, 250, 700 - 4 * t, 400), 0.95, "person")               # small subject moving left
        frame = [a, b] if t not in (10, 11, 12) else [b]                          # big one lost for 3 frames
        dets.append(frame)
    tr = tracker.iou_track(dets)
    check("tracker: two subjects -> 2 tracks (gap bridged)", len(tr) == 2, str([len(t.frames) for t in tr]))
    main = tracker.pick_main_track(tr)
    check("tracker: largest/most confident chosen", main.frames[0].label == "bear")
    sel = tracker.pick_main_track(tr, first_box=(600, 250, 700, 400))
    check("tracker: user box selects the other subject", sel.frames[0].label == "person")
    boxes, mask, score = tracker.track_boxes(main, 40)
    check("tracker: dense boxes + mask", boxes.shape == (40, 4) and mask.sum() == 37 and not mask[11])
    check("tracker: interpolated box inside the gap", 100 + 5 * 10 - 10 < boxes[11, 0] < 100 + 5 * 12 + 10, f"{boxes[11, 0]:.0f}")


def t_motion_io():
    T, J = 20, 17
    with tempfile.TemporaryDirectory() as d:
        p = motion_io.save_motion(Path(d) / "m.npz", fps=25, skeleton="ap10k17", joint_names=[f"j{i}" for i in range(J)],
                                  joints3d=np.random.rand(T, J, 3), conf=np.random.rand(T, J), root=np.random.rand(T, 3),
                                  meta={"a": 1, "arr": np.arange(3)}, extras={"contacts": np.zeros((T, 4))})
        m = motion_io.load_motion(p)
    check("motion.npz round trip", m["skeleton"] == "ap10k17" and m["meta"]["arr"] == [0, 1, 2] and m["fps"] == 25.0 and not motion_io.validate_motion(m))
    m["root"] = m["root"][:5]
    check("motion.npz validator catches bad shapes", "root shape" in motion_io.validate_motion(m))


def t_gem():
    # Z-up world, a person walking along +X, 77 joints
    T, J = 60, 77
    rng = np.random.default_rng(0)
    j = rng.normal(0, 0.15, (T, J, 3))
    j[..., 2] = np.abs(j[..., 2]) * 5 + 0.0          # height 0..1.8 (z)
    j[..., 2] = np.clip(j[..., 2], 0, 1.8)
    j[..., 0] += np.linspace(0, 3, T)[:, None]
    m = gem.soma77_to_motion(j, 30.0, up="+z")
    ext = m["joints3d"].max((0, 1)) - m["joints3d"].min((0, 1))
    check("gem converter: Z-up -> Y-up", ext[1] > 1.0 and m["skeleton"] == "soma77" and m["joints3d"].shape == (T, J, 3), str(ext.round(2)))
    check("gem converter: ground at y~0", abs(np.percentile(m["joints3d"][..., 1], 1)) < 1e-6)
    try:
        gem.soma77_to_motion(np.random.rand(T, J, 3) * [5, 0.3, 5], 30.0, up="+y")
        bad = False
    except ValueError:
        bad = True
    check("gem converter: wrong up axis detected", bad)
    check("gem status: not installed -> how-to note", not gem.status()["available"] and "gem-x" in gem.status()["note"].lower())


def t_sam3d():
    T = 90
    rng = np.random.default_rng(1)
    base = rng.normal(0, 0.2, (70, 3))
    kp = np.repeat(base[None], T, 0) + rng.normal(0, 0.02, (T, 70, 3))        # camera frame, y down
    cam_t = np.stack([np.linspace(-1, 1, T), np.full(T, 0.5), np.full(T, 4.0)], 1) + rng.normal(0, 0.02, (T, 3))
    valid = np.ones(T, bool)
    valid[30:35] = False
    m = sam3d.mhr70_to_motion(kp, cam_t, 30.0, valid=valid)
    raw = (kp + cam_t[:, None]) * np.array([1, -1, -1])
    check("sam3d converter: shapes/names", m["joints3d"].shape == (T, 70, 3) and len(m["joint_names"]) == 70 and m["skeleton"] == "mhr70")
    check("sam3d converter: smoothing reduces jitter", metrics.jitter(m["joints3d"], 30, 1.7) < 0.5 * metrics.jitter(raw, 30, 1.7))
    check("sam3d converter: Y flip (OpenCV -> Y up) + ground", np.corrcoef(m["joints3d"][0, :, 1], -base[:, 1])[0, 1] > 0.95
          and np.percentile(m["joints3d"][:, [13, 14, 15, 18, 17, 20], 1], 2) < 0.01)
    check("sam3d converter: invalid frames interpolated", np.isfinite(m["joints3d"]).all() and m["conf"][32].max() < 0.5)
    check("sam3d status: not installed -> how-to", not sam3d.status()["available"] and "sam-3d-body" in sam3d.status()["note"].lower())


def t_rig():
    sk = get_skeleton("ap10k17")
    heads = {n: (sk.rest[sk.idx(n)] * 1.3).tolist() for n in sk.all_names}
    bones = [{"name": f"B_{n}", "head": heads[n]} for n in sk.all_names]
    r = {"bones_json": bones, "bone_map_json": {n: f"B_{n}" for n in sk.all_names if n != "pelvis"}}
    ln, note = rig.bone_lengths_from_rig(r, sk)
    _, canon = sk.rest_dirs_lengths()
    k = sk.idx("left_knee")
    check("rig proportions: lengths scaled x1.3", abs(ln["left_knee"] / canon[k] - 1.3) < 0.01 and len(ln) > 8, note)
    full = pipeline.complete_lengths(sk, {"left_knee": ln["left_knee"], "right_knee": ln["right_knee"],
                                          "left_back_paw": ln["left_back_paw"], "left_elbow": ln["left_elbow"]})
    check("rig proportions: missing bones scaled consistently", abs(full["neck"] / canon[sk.idx("neck")] - 1.3) < 0.02)
    ln2, note2 = rig.bone_lengths_from_rig({"bones_json": "[]"}, sk)
    check("rig proportions: empty rig -> fallback note", ln2 == {} and "canonical" in note2)


def t_gait():
    for subj in ("quadruped", "humanoid"):
        gt = S.walk_sequence(subj, n_frames=150, fps=30.0, seed=2)
        sk = get_skeleton(gt["skeleton"])
        pel = gt["pelvis"]
        # canonical frame facing +Z: world (x fwd) -> z
        loc = gt["joints3d"] - pel[:, None]
        loc = np.stack([-loc[..., 2], loc[..., 1], loc[..., 0]], -1)
        g = postprocess.detect_gait(loc, gt["feet"], 30.0, sk.size_ref)
        true = 1 / gt["stride_hz"]
        check(f"gait [{subj}]: period", g["period_s"] and abs(g["period_s"] - true) < 0.05 * true, f"{g['period_s']:.3f} vs {true:.3f}")
        check(f"gait [{subj}]: seamless loop", g["loop_cycles"] >= 2 and g["loop_error"] < 0.05, str({k: g[k] for k in ('loop_start', 'loop_end', 'loop_cycles', 'loop_error')}))
    still = np.zeros((90, 17, 3)) + np.random.rand(17, 3) * 0.1
    g = postprocess.detect_gait(still + np.random.normal(0, 1e-4, still.shape), [1, 2], 30.0, 1.0)
    check("gait: standing still -> no confident period", g["period_s"] is None or g["confidence"] < 0.5)


def t_clean():
    T = 30
    kp = np.zeros((T, 17, 2)) + np.arange(T)[:, None, None] * 2.0 + np.arange(17)[None, :, None]
    conf = np.full((T, 17), 0.9)
    kp[12, 5] += 300                                   # a jump
    boxes = np.tile([0.0, 0.0, 400.0, 400.0], (T, 1))
    k2, c2 = pipeline.clean_keypoints(kp, conf, boxes, np.ones(T, bool))
    check("2D cleaning: outlier suppressed, others kept", c2[12, 5] <= 0.05 and c2[11, 5] == 0.9 and c2[12, 4] == 0.9)


def t_backends():
    check("backends: auto humanoid without gem -> vitpose", backends.resolve("auto", "humanoid") in ("vitpose", "mock"))
    try:
        backends.resolve("gem", "quadruped")
        bad = False
    except ValueError:
        bad = True
    check("backends: gem rejects quadruped", bad)
    try:
        backends.resolve("nope", "auto")
        bad = False
    except ValueError:
        bad = True
    check("backends: unknown id rejected", bad)
    st = models.vitpose_status()
    check("models: sizes + not installed", st["size_bytes"] > 600e6 and isinstance(st["available"], bool))
    check("models: ViTPose++ expert ids (COCO=0, AP-10K=3)", models.DATASET_INDEX == {"humanoid": 0, "quadruped": 3})
    check("detector labels", detect.subject_of_label("bear") == "quadruped" and detect.subject_of_label("person") == "humanoid"
          and detect.subject_of_label("bird") is None and detect.subject_of_label("car") is None)


def t_fit_missing():
    """A whole front leg disappears for 30 frames, plus left/right label swaps: the fit must stay plausible."""
    gt = S.walk_sequence("quadruped", n_frames=150, fps=30.0, seed=5, heading_deg=0.0)
    sk = get_skeleton("ap10k17")
    kp, conf, _ = S.simulate_2d(gt["joints3d"], scale=380, noise_px=3.0, drop_rate=0.05, seed=5, skeleton="ap10k17")
    leg = [sk.out_names.index(n) for n in ("left_shoulder", "left_elbow", "left_front_paw")]
    conf[50:80][:, leg] = 0.0
    res = fit_motion(kp, conf, "ap10k17", 30.0, cfg=FitConfig(), image_size=(1280, 800))
    pj, gj = res.joints3d, gt["joints3d"]
    pa = metrics.pa_mpjpe_seq(pj - pj.mean(1, keepdims=True), gj - gj.mean(1, keepdims=True)) / sk.size_ref
    gap = metrics.pa_mpjpe_seq(pj[50:80] - pj[50:80].mean(1, keepdims=True), gj[50:80] - gj[50:80].mean(1, keepdims=True)) / sk.size_ref
    check("fit: 30-frame missing front leg -> still plausible", pa < 0.10 and gap < 0.15, f"PA all {pa * 100:.1f}%  in-gap {gap * 100:.1f}%")
    c = canonicalize(res)
    fwd = c["local"][..., 2]
    check("canonical frame: subject faces +Z (nose ahead of tail)", fwd[:, sk.out_names.index("nose")].mean() > fwd[:, sk.out_names.index("root_of_tail")].mean())
    check("canonical frame: left side is +X", c["joints"][:, sk.out_names.index("left_shoulder"), 0].mean() > c["joints"][:, sk.out_names.index("right_shoulder"), 0].mean())


def t_plumbing():
    import torch
    torch.manual_seed(0)
    np.random.seed(0)
    from transformers import (RTDetrImageProcessor, RTDetrV2Config, RTDetrV2ForObjectDetection, VitPoseBackboneConfig,
                              VitPoseConfig, VitPoseForPoseEstimation, VitPoseImageProcessor)
    from app.services.mocap.pose2d import VitPose2D
    bc = VitPoseBackboneConfig(image_size=[256, 192], patch_size=[16, 16], hidden_size=64, num_hidden_layers=2,
                               num_attention_heads=4, num_experts=6, part_features=16, out_indices=[2])
    vp = VitPose2D("-", device="cpu", model=VitPoseForPoseEstimation(VitPoseConfig(backbone_config=bc, num_labels=17)).eval(),
                   processor=VitPoseImageProcessor())
    imgs = [np.random.randint(0, 255, (480, 640, 3), np.uint8) for _ in range(3)]
    boxes = np.array([[100, 50, 300, 400], [120, 60, 320, 420], [90, 40, 310, 430]], float)
    kh, sh = vp.estimate(imgs, boxes, "humanoid")
    ka, sa = vp.estimate(imgs, boxes, "quadruped")
    check("ViTPose++ wrapper: shapes (B,17,2), dataset_index experts used", kh.shape == (3, 17, 2) and sh.shape == (3, 17)
          and not np.allclose(kh, ka), "random weights; real accuracy untested")
    def in_crop(i):            # the processor pads the box to the 3:4 model aspect and by 25 %
        cx, cy = (boxes[i, 0] + boxes[i, 2]) / 2, (boxes[i, 1] + boxes[i, 3]) / 2
        w, h = boxes[i, 2] - boxes[i, 0], boxes[i, 3] - boxes[i, 1]
        hw = 0.5 * 1.25 * max(w, 0.75 * h) * 1.15 + 8
        hh = hw / 0.75
        return ((np.abs(kh[i, :, 0] - cx) <= hw).all() and (np.abs(kh[i, :, 1] - cy) <= hh).all())
    inside = all(in_crop(i) for i in range(3))
    check("ViTPose++ wrapper: keypoints map back into the (padded) box", inside, f"{kh[0, :2].round(0).tolist()} box0={boxes[0].tolist()}")
    coco = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat"]
    id2 = {i: n for i, n in enumerate(coco)}
    id2 = {i: "bear" for i in range(9)}                              # whatever class the random net answers is a "bear"
    m = RTDetrV2ForObjectDetection(RTDetrV2Config(id2label=id2, label2id={v: k for k, v in id2.items()}, num_labels=len(id2), num_queries=20)).eval()
    d = detect.Detector("-", device="cpu", model=m, processor=RTDetrImageProcessor())
    out = d.detect(imgs[:2], threshold=0.0)
    check("RT-DETRv2 wrapper: detections as Det(box,score,label)", len(out) == 2 and len(out[0]) > 0 and out[0][0].label == "bear"
          and len(out[0][0].box) == 4, "random weights")


def t_vitpose_path():
    """Runs the REAL (non-mock) vitpose branch of the pipeline with a fake detector + fake 2D pose model that replay
    simulated outputs (so tracker -> box -> pose batching -> fit -> npz -> overlay are exercised without weights)."""
    from types import SimpleNamespace
    gt = S.walk_sequence("quadruped", n_frames=100, fps=25.0, seed=7)
    kp, conf, clean = S.simulate_2d(gt["joints3d"], scale=330, pitch_deg=1.0, noise_px=2.5, seed=7, image_size=(960, 540), skeleton="ap10k17")
    tmp = Path(tempfile.mkdtemp(prefix="mocap_vp_"))
    vid = tmp / "bear.mp4"
    S.render_stick_video(vid, clean, "ap10k17", 25.0, size=(960, 540))
    lo, hi = clean.min(1), clean.max(1)
    gtbox = np.concatenate([lo - 15, hi + 15], 1)

    class FakeDet:
        def __init__(self, *a, **k):
            self.i = 0

        def detect(self, frames, threshold=0.35):
            out = []
            for _ in frames:
                t = self.i
                self.i += 1
                ds = [tracker.Det(tuple(gtbox[t]), 0.93, "bear"), tracker.Det((20, 20, 120, 220), 0.6, "person")]
                out.append(ds if t not in (30, 31) else ds[1:])        # the bear is lost for two frames
            return out

    class FakePose:
        def __init__(self, *a, **k):
            self.i = 0

        def estimate(self, frames, boxes, subject):
            n = len(frames)
            sl = slice(self.i, self.i + n)
            self.i += n
            assert subject == "quadruped"
            return kp[sl].astype(np.float64), conf[sl].astype(np.float64)

    class Ctx:
        cancelled = False

        def progress(self, p, m=None):
            pass

        def register_process(self, p):
            pass

        def check(self):
            pass

    saved = (detect.Detector, pose2d.VitPose2D, models.vitpose_status, pipeline.db.get_media, pipeline.ingest_file,
             pipeline.MEDIA_DIR, pipeline.MOTION_DIR)
    try:
        detect.Detector, pose2d.VitPose2D = FakeDet, FakePose
        models.vitpose_status = lambda: {"available": True, "size_bytes": 1, "installed": {"pose": ["base"], "detector": "r50"}, "missing": []}
        pipeline.db.get_media = lambda mid: {"id": mid, "kind": "video", "path": str(vid), "name": "bear"}
        pipeline.ingest_file = lambda p, **k: {"id": "x", "kind": "video", "path": str(p), "duration": 4.0, "url": "/x"}
        pipeline.MEDIA_DIR = pipeline.MOTION_DIR = tmp
        r = pipeline.run_mocap(Ctx(), pipeline.MocapRequest(mediaId="m1", subject="auto", backend="vitpose", fps=25.0))
        m = motion_io.load_motion(r["motion_file"])
        sk = get_skeleton("ap10k17")
        pa = metrics.pa_mpjpe_seq(m["joints3d"] - m["joints3d"].mean(1, keepdims=True),
                                  gt["joints3d"] - gt["joints3d"].mean(1, keepdims=True)) / sk.size_ref
        check("vitpose branch (fake models): subject auto -> quadruped/ap10k17", m["skeleton"] == "ap10k17" and m["meta"]["subject"] == "quadruped" and m["meta"]["label"] == "bear")
        check("vitpose branch: multi-subject note + 2-frame detection gap handled", any("2 subjects" in n for n in r["notes"]) and r["stats"]["subject_coverage"] < 1.0, str(r["notes"]))
        check("vitpose branch: motion close to truth", pa < 0.10, f"PA-MPJPE {pa * 100:.1f}% of shoulder height")
        check("vitpose branch: overlay mp4 written", Path(r["overlay_media"]["path"]).stat().st_size > 10_000)
        class OnlyBear(FakeDet):
            def detect(self, frames, threshold=0.35):
                return [[tracker.Det(tuple(gtbox[0]), 0.9, "bear")] for _ in frames]
        detect.Detector = OnlyBear
        try:
            pipeline.run_mocap(Ctx(), pipeline.MocapRequest(mediaId="m1", subject="humanoid", backend="vitpose", fps=25.0, overlay=False))
            err = ""
        except pipeline.MocapError as e:
            err = str(e)
        check("vitpose branch: subject=humanoid but only a bear in view -> clear error", "No person" in err, err[:100])
    finally:
        (detect.Detector, pose2d.VitPose2D, models.vitpose_status, pipeline.db.get_media, pipeline.ingest_file,
         pipeline.MEDIA_DIR, pipeline.MOTION_DIR) = saved


def main():
    for f in (t_vitpose_path, t_tracker, t_motion_io, t_gem, t_sam3d, t_rig, t_gait, t_clean, t_backends, t_plumbing, t_fit_missing):
        print(f.__name__)
        try:
            f()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f.__name__ + " (exception)", False, repr(e)[:200])
    print(f"\n{len(OK)} passed, {len(FAIL)} failed", "" if not FAIL else FAIL)
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
