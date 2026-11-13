"""Mocap job orchestration:  video -> frames -> subject track -> 2D pose -> 3D fit -> motion.npz (+ overlay video)."""
from __future__ import annotations

import gc
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ... import db
from ...config import DATA, MEDIA_DIR, PORT
from ...ingest import ingest_file
from . import backends, detect, frames as frames_mod, gem, models, motion_io, pose2d, postprocess, rig, sam3d, tracker
from .fit3d import FitConfig, canonicalize, fit_motion
from .skeletons import draw_edges, get_skeleton, skeleton_for_subject

MOTION_DIR = DATA / "mocap"
MOTION_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class MocapRequest:
    mediaId: str
    start: float | None = None
    end: float | None = None
    subject: str = "auto"                 # humanoid | quadruped | auto
    backend: str | None = "auto"
    rigId: str | None = None
    fps: float | None = None
    box: list[float] | None = None        # optional first-frame subject box [x, y, w, h] in SOURCE pixels
    size_m: float | None = None           # real body size (human height / quadruped shoulder height), metres
    pose_variant: str | None = None
    overlay: bool = True
    camera_pitch: float | None = None     # degrees the camera looks DOWN; None = estimate automatically


PITCH_CANDIDATES = (0.0, 15.0, 30.0, 45.0)


def pick_camera_pitch(kp, conf, skeleton: str, fps: float, *, image_size=(1280, 720), bone_lengths=None, size=1.0,
                      candidates=PITCH_CANDIDATES, progress=None) -> tuple[float, dict]:
    """Estimate how far the camera looks down: run a short fit for each candidate pitch and keep the one with the
    lowest total objective (reprojection + physical priors: ground contact, no foot sliding, joint limits). A wrong
    pitch forces the body to bob / tilt to explain the foreshortening, which the priors penalise."""
    from .fit3d import FitConfig, fit_motion
    quick = FitConfig(n_starts=4, iters_stage1=80, iters_stage2=200, iters_refine=60)
    scores = {}
    for i, p in enumerate(candidates):
        if progress:
            progress(i / len(candidates), f"Estimating camera pitch ({p:.0f}°)")
        r = fit_motion(kp, conf, skeleton, fps, cfg=quick, size=size, bone_lengths=bone_lengths,
                       image_size=image_size, ground_pitch_deg=p)
        scores[p] = float(r.total_loss)
    best = min(scores, key=scores.get)
    return float(best), scores


class MocapError(RuntimeError):
    pass


def _free_gpu():
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def clean_keypoints(kp: np.ndarray, conf: np.ndarray, boxes: np.ndarray, det_mask: np.ndarray, thr: float = 0.15):
    """Reject temporal outliers (jump away from the local median by > 25 % of the box diagonal) and down-weight frames
    where the box was interpolated instead of detected."""
    T = len(kp)
    conf = conf.copy()
    diag = np.linalg.norm(boxes[:, 2:] - boxes[:, :2], axis=1)
    k = 2
    for t in range(T):
        lo, hi = max(0, t - k), min(T, t + k + 1)
        med = np.median(kp[lo:hi], axis=0)
        d = np.linalg.norm(kp[t] - med, axis=-1)
        bad = d > 0.25 * diag[t]
        conf[t, bad] = np.minimum(conf[t, bad], 0.05)
    conf[~det_mask] *= 0.6
    return kp, conf


_LR_GROUPS = (("shoulder", "elbow", "front_paw", "wrist"), ("hip", "knee", "back_paw", "ankle"), ("eye", "ear"))


def fix_lr_swaps(kp: np.ndarray, conf: np.ndarray, names: list[str], boxes: np.ndarray, swap_cost: float = 0.02):
    """Undo per-frame left/right label swaps (very common for side views, where the two legs overlap).
    For each limb chain (left_* vs right_*), a 2-state Viterbi picks 'keep' or 'swap' per frame so the trajectory
    is as continuous as possible. Natural crossings (paws passing each other while walking) are continuous and are
    kept; label swaps are discontinuous jumps and get undone. Returns (kp, conf, n_frames_fixed)."""
    T = len(kp)
    if T < 3:
        return kp, conf, 0
    idx = {n: i for i, n in enumerate(names)}
    diag = np.maximum(np.linalg.norm(boxes[:, 2:] - boxes[:, :2], axis=1), 1.0)
    kp, conf = kp.copy(), conf.copy()
    fixed = np.zeros(T, bool)
    for words in _LR_GROUPS:
        pairs = [(idx[f"left_{w}"], idx[f"right_{w}"]) for w in words if f"left_{w}" in idx and f"right_{w}" in idx]
        if not pairs:
            continue
        L = np.array([p[0] for p in pairs]); R = np.array([p[1] for p in pairs])

        def pos(t, s):                                   # positions of (left chain, right chain) at t under state s
            a, b = (L, R) if s == 0 else (R, L)
            return np.concatenate([kp[t, a], kp[t, b]]), np.concatenate([conf[t, a], conf[t, b]])

        def step(t, s_prev, s):
            p0, c0 = pos(t - 1, s_prev)
            p1, c1 = pos(t, s)
            w = np.sqrt(np.clip(c0, 0, 1) * np.clip(c1, 0, 1)) + 1e-3
            return float((w * np.linalg.norm(p1 - p0, axis=1)).sum() / w.sum() / diag[t])

        cost = np.array([0.0, swap_cost])
        back = np.zeros((T, 2), int)
        for t in range(1, T):
            new = np.empty(2)
            for s in (0, 1):
                cand = [cost[sp] + step(t, sp, s) for sp in (0, 1)]
                back[t, s] = int(np.argmin(cand))
                new[s] = min(cand) + (swap_cost if s else 0.0)
            cost = new
        states = np.zeros(T, int)
        states[-1] = int(np.argmin(cost))
        for t in range(T - 1, 0, -1):
            states[t - 1] = back[t, states[t]]
        for t in np.nonzero(states)[0]:
            kp[t, L], kp[t, R] = kp[t, R].copy(), kp[t, L].copy()
            conf[t, L], conf[t, R] = conf[t, R].copy(), conf[t, L].copy()
            fixed[t] = True
    return kp, conf, int(fixed.sum())


def complete_lengths(sk, lengths: dict[str, float]) -> dict[str, float]:
    """Bones missing from the rig take canonical length x (median rig/canonical ratio)."""
    _, canon = sk.rest_dirs_lengths()
    ratios = [v / canon[sk.idx(n)] for n, v in lengths.items() if n in sk.all_names and canon[sk.idx(n)] > 0]
    r = float(np.median(ratios)) if ratios else 1.0
    out = {n: float(canon[i] * r) for i, n in enumerate(sk.all_names) if i > 0}
    out.update({n: v for n, v in lengths.items() if n in out})
    return out


def _target_dir(row: dict) -> Path:
    return Path(row["path"])


def run_mocap(ctx, req: MocapRequest) -> dict:
    t0 = time.time()
    row = db.get_media(req.mediaId)
    if not row:
        raise MocapError(f"Media {req.mediaId} not found.")
    if row["kind"] != "video":
        raise MocapError("Motion capture needs a video file.")
    subject_req = req.subject if req.subject in ("humanoid", "quadruped") else "auto"
    bid = backends.resolve(req.backend, subject_req)
    info = backends.get(bid)
    if not info["available"]:
        if bid == "vitpose":
            raise MocapError("Backend vitpose needs weights (~%d MB). Run prepare." % round(models.vitpose_status()["size_bytes"] / 2**20))
        sz = "7.1 GB" if bid == "gem" else "2.8 GB"
        raise MocapError(f"Backend {bid} needs weights (~{sz}) and its own environment. Run prepare. {info['note']}")
    notes: list[str] = []
    if bid == "gem":                                   # GEM-X reads the video itself
        return _run_gem(ctx, row, req, None, notes)["result"]
    ctx.progress(0.01, "Extracting frames")
    fs = frames_mod.extract_frames(row["path"], start=req.start or 0.0, end=req.end, fps=req.fps, ctx=ctx)
    try:
        if bid in ("vitpose", "mock"):
            res = _run_lift(ctx, row, req, fs, bid, subject_req, notes)
        else:
            res = _run_sam3d(ctx, row, req, fs, notes)
    finally:
        fs.cleanup()
    res["result"]["stats"]["seconds"] = round(time.time() - t0, 1)
    return res["result"]


# ---------------------------------------------------------------------------------------------------- lift path
def _run_lift(ctx, row, req, fs, bid, subject_req, notes) -> dict:
    T = fs.n
    if T < 8:
        raise MocapError("The selected range is too short for motion capture (need at least 8 frames).")
    boxes = det_mask = None
    if bid == "mock":
        m = pose2d.mock_estimate(row["path"], fs.times(), fs.scale)
        subject = "humanoid" if m["skeleton"] == "coco17" else "quadruped"
        kp, conf = m["kp"].astype(np.float64), m["conf"].astype(np.float64)
        lo, hi = kp.min(1), kp.max(1)
        boxes = np.concatenate([lo - 20, hi + 20], 1)
        det_mask = np.ones(T, bool)
        ctx.progress(0.5, "Mock 2D pose")
    else:
        st = models.vitpose_status()
        variant = req.pose_variant if (req.pose_variant in models.POSE_MODELS and req.pose_variant in st["installed"]["pose"]) \
            else st["installed"]["pose"][0]
        dv = st["installed"]["detector"]
        ctx.progress(0.1, "Loading detector")
        det = detect.Detector(models.det_dir(dv))
        dets = detect.detect_all(det, fs, ctx)
        del det
        _free_gpu()
        # keep only the requested subject class group
        if subject_req != "auto":
            dets = [[d for d in ds if detect.subject_of_label(d.label) == subject_req] for ds in dets]
        tracks = tracker.iou_track(dets)
        first_box = None
        if req.box:
            x, y, w, h = req.box
            k = fs.scale
            first_box = (x * k, y * k, (x + w) * k, (y + h) * k)
        main = tracker.pick_main_track(tracks, first_box)
        if main is None:
            what = {"humanoid": "person", "quadruped": "four-legged animal"}.get(subject_req, "person or animal")
            raise MocapError(f"No {what} was detected in the selected range. Try a clearer shot or give a first-frame box.")
        labels = [d.label for d in main.frames.values()]
        label = max(set(labels), key=labels.count)
        subject = detect.subject_of_label(label) or "quadruped"
        if len(tracks) > 1:
            notes.append(f"{len(tracks)} subjects tracked; using the largest/most confident one ({label}).")
        boxes, det_mask, _ = tracker.track_boxes(main, T)
        ctx.progress(0.31, f"Loading ViTPose++ ({variant})")
        pose = pose2d.VitPose2D(models.pose_dir(variant))
        kp, conf = pose2d.estimate_all(pose, fs, boxes, subject, ctx)
        del pose
        _free_gpu()
    skel_name = skeleton_for_subject(subject)
    sk = get_skeleton(skel_name)
    kp_raw = kp.copy()
    kp, conf, n_sw = fix_lr_swaps(kp, conf, list(sk.out_names), boxes)
    if n_sw:
        notes.append(f"Fixed left/right label swaps on {n_sw} frame(s).")
    kp, conf = clean_keypoints(kp, conf, boxes, det_mask)
    ctx.progress(0.55, "Fitting 3D skeleton")
    # rig proportions (optional)
    lengths = None
    if req.rigId:
        rr = rig.fetch_rig(req.rigId, PORT)
        if rr:
            ln, note = rig.bone_lengths_from_rig(rr, sk)
            notes.append(note)
            if ln:
                lengths = complete_lengths(sk, ln)
        else:
            notes.append("Could not read the rig from the engine; using canonical proportions.")
    size = (req.size_m / sk.size_ref) if (req.size_m and not lengths) else 1.0
    cfg = FitConfig()
    pitch_scores = None
    if req.camera_pitch is not None:
        pitch = float(np.clip(req.camera_pitch, 0.0, 70.0))
    else:
        pitch, pitch_scores = pick_camera_pitch(kp, conf, skel_name, fs.fps, image_size=(fs.width, fs.height),
                                                bone_lengths=lengths, size=size,
                                                progress=lambda p, m: ctx.progress(0.55 + 0.12 * p, m))
        notes.append(f"Estimated camera pitch: {pitch:.0f}° looking down.")
    fit = fit_motion(kp, conf, skel_name, fs.fps, cfg=cfg, size=size, bone_lengths=lengths,
                     image_size=(fs.width, fs.height), ground_pitch_deg=pitch,
                     progress=lambda p, m: ctx.progress(0.67 + 0.21 * min(max(p, 0), 1), m))
    ctx.progress(0.88, "Post-processing")
    can = canonicalize(fit)
    body = sk.size_ref * size
    feet = fit.feet
    names = sk.out_names
    edges = draw_edges(skel_name)
    gait = postprocess.detect_gait(can["local"], feet, fs.fps, body)
    stats = postprocess.quality(can["joints"], conf, edges, fs.fps, body, fit.reproj_px, det_mask, fit.interpolated,
                                fit.contacts, feet)
    conf_out = np.clip(conf, 0, 1)
    virt = [n for n in sk.all_names if n not in names]
    virt_idx = [sk.idx(n) for n in virt]
    meta = {
        "source": {"mediaId": row["id"], "name": row.get("name"), "path": row["path"],
                   "start": fs.start, "end": fs.start + fs.n / fs.fps, "frames": T, "extract_fps": fs.fps,
                   "extract_size": [fs.width, fs.height], "scale_vs_source": fs.scale},
        "backend": bid, "subject": subject, "label": None if bid == "mock" else label,
        "camera": {"model": "weak_perspective", "fixed": True, "pitch_deg": pitch,
                   "pitch_source": "user" if req.camera_pitch is not None else "estimated", "pitch_scores": pitch_scores,
                   "px_per_metre": fit.scale_px_per_m, "note": "gravity-aligned fixed camera; depth is inferred, not measured"},
        "coordinates": {"up": "+Y", "forward": "+Z", "left": "+X", "units": "metres", "ground_y": 0.0,
                        "joints3d": "absolute world positions (path starts at x=z=0)",
                        "root": "mid-hip (pelvis) trajectory", "joints3d_local": "pelvis-relative, facing +Z",
                        "canonical_yaw_rad": can["yaw"]},
        "body_size_m": body,
        "bone_lengths": fit.bone_lengths,
        "virtual_joints": virt,
        "hierarchy": {sk.all_names[j]: sk.all_names[p] for j, p in enumerate(sk.parent) if p >= 0},
        "gait": gait, "quality": stats, "notes": notes, "fit_losses": fit.losses,
        "rig": {"rigId": req.rigId, "lengths_from_rig": bool(lengths)},
    }
    mid = uuid.uuid4().hex[:8]
    motion_path = MOTION_DIR / f"motion_{mid}.npz"
    extras = {"joints3d_local": can["local"], "contacts": fit.contacts, "contact_joints": np.array([names[i] for i in feet]),
              "heading": fit.heading + can["yaw"] * 0, "interpolated": fit.interpolated}
    if can["full"] is not None and virt_idx:
        extras["virtual_joint_names"] = np.array(virt)
        extras["virtual_joints3d"] = can["full"][:, virt_idx]
    motion_io.save_motion(motion_path, fps=fs.fps, skeleton=skel_name, joint_names=names, joints3d=can["joints"],
                          conf=conf_out, root=can["pelvis"], meta=meta, extras=extras)
    overlay_row = None
    if req.overlay:
        out_mp4 = MEDIA_DIR / f"mocap_overlay_{mid}.mp4"
        from .overlay import render_overlay
        render_overlay(fs, out_mp4, skel_name, kp_raw, conf, boxes, fit.kp_proj, can["local"], fit.contacts, feet, ctx=ctx)
        overlay_row = ingest_file(out_mp4, origin="mocap", name=f"mocap overlay - {row.get('name', '')}"[:80], tags=["mocap", "debug"])
    ctx.progress(0.99, "Done")
    return {"result": {
        "motion_file": str(motion_path), "motion_url": f"/api/mocap/motion/{motion_path.stem}.npz",
        "overlay_media": overlay_row, "stats": {**stats, "gait_period_s": gait.get("period_s")},
        "skeleton": {"name": skel_name, "joint_names": names, "subject": subject,
                     "edges": [[names[a], names[b]] for a, b in edges]},
        "gait": gait, "backend": bid, "notes": notes}}


# ---------------------------------------------------------------------------------------------------- direct 3D paths
def _track_for_direct(ctx, fs, req):
    st = models.vitpose_status()
    if not st["installed"]["detector"]:
        # direct backends bring their own detector, but our box prompts need one; fall back to a centred full-frame box
        boxes = np.tile(np.array([0, 0, fs.width, fs.height], float), (fs.n, 1))
        return boxes, np.ones(fs.n, bool)
    det = detect.Detector(models.det_dir(st["installed"]["detector"]))
    dets = detect.detect_all(det, fs, ctx)
    del det
    _free_gpu()
    dets = [[d for d in ds if detect.subject_of_label(d.label) == "humanoid"] for ds in dets]
    main = tracker.pick_main_track(tracker.iou_track(dets))
    if main is None:
        raise MocapError("No person was detected in the selected range.")
    b, m, _ = tracker.track_boxes(main, fs.n)
    return b, m


def _finish_direct(ctx, row, req, fs, bid, motion: dict, notes, boxes, extra_meta) -> dict:
    mid = uuid.uuid4().hex[:8]
    J = motion["joints3d"]
    meta = {"source": {"mediaId": row["id"], "name": row.get("name"), "path": row["path"], "start": req.start or 0.0,
                       "frames": int(len(J)), "extract_fps": motion["fps"]}, "backend": bid, "subject": "humanoid",
            "coordinates": {"up": "+Y", "units": "metres", "ground_y": 0.0,
                            "note": "world axes follow the backend; forward axis not normalised"},
            "notes": notes, **extra_meta}
    p = MOTION_DIR / f"motion_{mid}.npz"
    motion_io.save_motion(p, fps=motion["fps"], skeleton=motion["skeleton"], joint_names=motion["joint_names"],
                          joints3d=J, conf=motion["conf"], root=motion["root"], meta=meta)
    overlay_row = None
    if req.overlay and boxes is not None:
        from .overlay import render_overlay
        out_mp4 = MEDIA_DIR / f"mocap_overlay_{mid}.mp4"
        dummy = np.zeros((fs.n, 17, 2))
        render_overlay(fs, out_mp4, "coco17", dummy - 10, np.zeros((fs.n, 17)), boxes, ctx=ctx)
        overlay_row = ingest_file(out_mp4, origin="mocap", name=f"mocap overlay - {row.get('name', '')}"[:80], tags=["mocap", "debug"])
    stats = postprocess.quality(J, motion["conf"], [], motion["fps"], 1.7, float("nan"))
    return {"result": {"motion_file": str(p), "motion_url": f"/api/mocap/motion/{p.stem}.npz", "overlay_media": overlay_row,
                       "stats": stats, "skeleton": {"name": motion["skeleton"], "joint_names": motion["joint_names"],
                                                    "subject": "humanoid", "edges": []},
                       "gait": None, "backend": bid, "notes": notes}}


def _run_sam3d(ctx, row, req, fs, notes) -> dict:
    boxes, mask = _track_for_direct(ctx, fs, req)
    out = sam3d.run(fs, boxes, mask, MOTION_DIR, ctx)
    motion = sam3d.mhr70_to_motion(out["kp3d"], out["cam_t"], fs.fps, valid=out["valid"])
    notes.append("SAM 3D Body runs per frame; the sequence was Gaussian-smoothed. Global trajectory comes from pred_cam_t (noisy).")
    return _finish_direct(ctx, row, req, fs, "sam3d", motion, notes, boxes, {})


def _run_gem(ctx, row, req, fs, notes) -> dict:
    out = gem.run(Path(row["path"]), req.start or 0.0, req.end, MOTION_DIR, ctx)
    motion = gem.soma77_to_motion(out["joints"], out["fps"], up=out["up"])
    notes.append("GEM-X output converted from world-space SOMA joints; the forward axis is not normalised.")
    return _finish_direct(ctx, row, req, fs, "gem", motion, notes, None, {"gem": out["info"]})
