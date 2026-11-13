"""Animation asset pipeline: rigs, clips, retargeting, previews, exports. Every public function that takes a `ctx` runs inside a job."""
from __future__ import annotations

import json
import math
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from fastapi import HTTPException
from PIL import Image

from ... import config, db as media_db, ingest
from ...ffutil import run_ffmpeg
from ...jobs import JobContext
from . import blender, bonemap, db, metrics, search, stick, synth
from . import skeletons as sk

MODEL_EXT = {".glb", ".gltf", ".fbx", ".blend"}
MOTION_EXT = {".bvh", ".fbx", ".glb", ".gltf", ".blend", ".npz"}
MY_ENGINE_URL = "http://127.0.0.1:4173"

# conventional game clip names for a role tag
ROLE_NAMES = {
    "idle": "Idle", "walk": "Walk", "run": "Run", "sprint": "Sprint", "jump": "Jump", "fall": "Fall", "land": "Land", "attack": "Attack",
    "hit": "Hit", "die": "Die", "death": "Die", "crouch": "Crouch", "swim": "Swim", "climb": "Climb", "roll": "Roll", "dodge": "Dodge",
    "block": "Block", "cast": "Cast", "interact": "Interact", "turn": "Turn", "strafe": "Strafe", "trot": "Trot", "gallop": "Gallop",
}


def safe_name(s: str, default: str = "clip") -> str:
    s = re.sub(r"[^A-Za-z0-9._\- ]+", "_", s or "").strip(" ._")
    return re.sub(r"_+", "_", re.sub(r"\s+", "_", s))[:60].strip("_") or default


def _pub(d: dict | None) -> dict | None:
    return None if d is None else {k: v for k, v in d.items() if not k.startswith("_")}


# ================================================================================================ rigs
def import_rig(ctx: JobContext, path: str | None = None, media_id: str | None = None, name: str | None = None, up_axis: str | None = None) -> dict:
    if media_id and not path:
        m = media_db.get_media(media_id)
        if not m:
            raise RuntimeError(f"media {media_id} not found")
        path = m["path"]
    if not path:
        raise RuntimeError("path or mediaId is required")
    src = Path(path)
    if not src.exists():
        raise RuntimeError(f"File not found: {src}")
    if src.suffix.lower() not in MODEL_EXT:
        raise RuntimeError(f"Unsupported rig format '{src.suffix}'. Use .glb .gltf .fbx or .blend.")
    rid = db.new_id()
    d = db.rig_dir(rid)
    dst = d / f"source{src.suffix.lower()}"
    shutil.copy2(src, dst)
    ctx.progress(0.05, "inspecting rig in Blender")
    res = blender.run_script("inspect_rig.py", {"input": str(dst), "out_json": str(d / "info.json"), "out_blend": str(d / "rig.blend"),
                                                "thumb": str(d / "thumb.png"), "up_axis": up_axis}, ctx, (0.05, 0.85))
    info = json.loads((d / "info.json").read_text("utf-8"))
    bones = info.pop("bones")
    rig = db.add_rig(name=name or src.stem, file=str(d / "rig.blend"), kind=info.get("kind", "unknown"), bones=bones, info=info,
                     thumb=str(d / "thumb.png") if (d / "thumb.png").exists() else None, source_path=str(src), rid=rid)
    ctx.progress(0.9, "mapping bones")
    maps = {}
    for skel in ("ap10k17", "smpl24", "coco17"):
        try:
            sug = bonemap.suggest(bones, skel)
            maps[skel] = {**sug, "source": "auto"}
            maps[skel].pop("source_joints", None)
        except Exception:  # noqa: BLE001
            pass
    rig = db.update_rig(rid, bone_maps=maps)
    return {"rig": _pub(rig)}


def rig_public(rid: str) -> dict:
    r = db.get_rig(rid, True)
    if not r:
        raise HTTPException(404, "rig not found")
    return r


def delete_rig(rid: str) -> dict:
    r = db.get_rig(rid)
    if not r:
        raise HTTPException(404, "rig not found")
    removed = []
    for c in db.list_clips(rig_id=rid):
        removed.append(c["id"])
        delete_clip(c["id"])
    db.delete_rig(rid)
    shutil.rmtree(db.RIGS_DIR / rid, ignore_errors=True)
    return {"deleted": rid, "clips_removed": removed}


def _clip_joints(clip: dict | None, skeleton: str | None) -> tuple[str, list[str], list[int] | None]:
    if clip and clip.get("_files", {}).get("motion"):
        m = stick.load_motion(clip["_files"]["motion"])
        return m["skeleton"], m["names"], m["parents"]
    skel = skeleton or "smpl24"
    n, p = sk.BUILTIN.get(skel, sk.BUILTIN["smpl24"])
    return skel, list(n), list(p)


def suggest_map(rid: str, skeleton: str | None = None, clip_id: str | None = None, use_llm: bool = False) -> dict:
    rig = db.get_rig(rid, True)
    if not rig:
        raise HTTPException(404, "rig not found")
    clip = db.get_clip(clip_id) if clip_id else None
    skel, names, parents = _clip_joints(clip, skeleton or bonemap.default_source(rig["kind"]))
    sug = bonemap.suggest(rig["bones"], skel, names, parents)
    if use_llm:
        prov = "anthropic" if config.load_settings().get("llm_provider") == "anthropic" else None
        sug = bonemap.refine_with_llm(rig["bones"], sug, provider=prov)
    sug["skeleton"] = skel
    sug["key"] = bonemap.skeleton_key(skel, names)
    return sug


def get_bone_map(rid: str, skeleton: str | None = None, clip_id: str | None = None) -> dict:
    rig = db.get_rig(rid, True)
    if not rig:
        raise HTTPException(404, "rig not found")
    clip = db.get_clip(clip_id) if clip_id else None
    skel, names, parents = _clip_joints(clip, skeleton or bonemap.default_source(rig["kind"]))
    key = bonemap.skeleton_key(skel, names)
    stored = rig["bone_maps"].get(key)
    if not stored:
        sug = bonemap.suggest(rig["bones"], skel, names, parents)
        stored = {**sug, "source": "auto-unsaved"}
    out = dict(stored)
    out.update({"skeleton": skel, "key": key, "source_joints": names, "bones": [b["name"] for b in rig["bones"]]})
    return out


def put_bone_map(rid: str, mapping: dict[str, str | None], skeleton: str | None = None, clip_id: str | None = None) -> dict:
    rig = db.get_rig(rid, True)
    if not rig:
        raise HTTPException(404, "rig not found")
    clip = db.get_clip(clip_id) if clip_id else None
    skel, names, parents = _clip_joints(clip, skeleton or bonemap.default_source(rig["kind"]))
    key = bonemap.skeleton_key(skel, names)
    bones = {b["name"] for b in rig["bones"]}
    clean, seen = {}, set()
    for b, j in mapping.items():
        if j in (None, ""):
            continue
        if b not in bones:
            raise HTTPException(400, f"unknown bone '{b}'")
        if j not in names:
            raise HTTPException(400, f"unknown joint '{j}' for skeleton {skel}")
        if j in seen:
            raise HTTPException(400, f"joint '{j}' is mapped to more than one bone")
        seen.add(j)
        clean[b] = j
    maps = dict(rig["bone_maps"])
    maps[key] = {"map": clean, "confidence": {b: 1.0 for b in clean}, "unmapped": [b for b in bones if b not in clean], "notes": ["edited by user"],
                 "source": "user", "overall": 1.0, "coverage": 1.0}
    db.update_rig(rid, bone_maps=maps)
    return get_bone_map(rid, skeleton, clip_id)


# ================================================================================================ source clips
def _finish_source_clip(ctx: JobContext | None, cid: str, name: str, motion: Path, stats: dict, source: dict, tags: list[str] | None = None) -> dict:
    files = {"motion": str(motion)}
    try:
        mp4 = stick.render(motion, db.clip_dir(cid) / "stick.mp4")
        files["mp4"] = str(mp4)
    except Exception as e:  # noqa: BLE001
        source["preview_error"] = str(e)[:200]
    sk_name = stats.get("skeleton", "custom")
    clip = db.add_clip(cid=cid, name=name, skeleton=sk_name, rig_id=None, source=source, fps=stats.get("fps", 30), frames=stats.get("frames", 0),
                       duration=stats.get("duration", 0), loop=bool(stats.get("loop_seam_vs_step", 9) < 2.0), root_motion=stats.get("root_travel_m", 0) > 0.2 * max(stats.get("height_m", 1), 0.1),
                       tags=tags or [], files=files, stats=stats)
    try:
        from . import review
        review.heuristic_annotate(cid)
        search.ensure_embedding(cid)
    except Exception:  # noqa: BLE001
        pass
    return db.get_clip(cid)  # type: ignore[return-value]


def import_clip(ctx: JobContext, path: str, name: str | None = None, options: dict | None = None) -> dict:
    src = Path(path)
    if not src.exists():
        raise RuntimeError(f"File not found: {src}")
    ext = src.suffix.lower()
    if ext not in MOTION_EXT:
        raise RuntimeError(f"Unsupported motion format '{ext}'. Use .bvh .fbx .glb .gltf .blend or a motion.npz")
    options = options or {}
    cid = db.new_id()
    d = db.clip_dir(cid)
    motion = d / "motion.npz"
    notes: list[str] = []
    if ext == ".npz":
        ctx.progress(0.1, "reading motion.npz")
        data = np.load(src, allow_pickle=False)
        for k in ("fps", "joint_names", "joints3d"):
            if k not in data.files:
                raise RuntimeError(f"motion.npz is missing '{k}' (see docs/ANIMATION.md)")
        shutil.copy2(src, motion)
        stats = stick.stats(motion)
        meta = {}
        try:
            meta = json.loads(str(data["meta"])) if "meta" in data.files else {}
        except Exception:  # noqa: BLE001
            pass
        source = {"path": str(src), "format": "npz", "meta": meta}
    else:
        res = blender.run_script("import_motion.py", {"input": str(src), "out_npz": str(motion), **{k: v for k, v in options.items() if k in ("action", "armature", "scale", "fps", "start", "end")}},
                                 ctx, (0.05, 0.8))
        stats = {**stick.stats(motion), **{k: v for k, v in res["stats"].items() if k in ("units", "action", "actions_in_file", "format")}}
        notes = res["stats"].get("notes", [])
        source = {"path": str(src), "format": src.suffix.lstrip("."), "action": res["stats"].get("action"), "notes": notes}
    ctx.progress(0.85, "preview + metadata")
    clip = _finish_source_clip(ctx, cid, name or src.stem, motion, stats, source)
    return {"clip": _pub(clip)}


def demo_clip(ctx: JobContext, kind: str = "quadruped_walk", name: str | None = None) -> dict:
    if kind not in ("quadruped_walk", "biped_walk"):
        raise RuntimeError("kind must be quadruped_walk or biped_walk")
    cid = db.new_id()
    motion = db.clip_dir(cid) / "motion.npz"
    synth.write_demo(kind, motion)
    stats = stick.stats(motion)
    ctx.progress(0.6, "preview")
    clip = _finish_source_clip(ctx, cid, name or ("Synthetic quadruped walk" if kind == "quadruped_walk" else "Synthetic biped walk"), motion, stats,
                               {"format": "synthetic", "generator": kind}, ["synthetic", "walk", "loop"])
    return {"clip": _pub(clip)}


def delete_clip(cid: str) -> bool:
    c = db.get_clip(cid)
    if not c:
        return False
    for key in ("webm", "mp4"):
        pass
    mid = c.get("preview_media_id")
    if mid:
        try:
            media_db.delete_media(mid)
        except Exception:  # noqa: BLE001
            pass
    ok = db.delete_clip(cid)
    shutil.rmtree(db.CLIPS_DIR / cid, ignore_errors=True)
    shutil.rmtree(db.PREVIEWS_DIR / cid, ignore_errors=True)
    shutil.rmtree(db.EXPORTS_DIR / cid, ignore_errors=True)
    for dependants in db.list_clips():
        if dependants.get("parent_clip_id") == cid:
            db.update_clip(dependants["id"], source={**dependants["source"], "parent_deleted": True})
    return ok


# ================================================================================================ retarget
def retarget(ctx: JobContext, clip_id: str, rig_id: str, options: dict | None = None) -> dict:
    options = dict(options or {})
    src = db.get_clip(clip_id)
    if not src:
        raise RuntimeError("clip not found")
    if not src["_files"].get("motion"):
        raise RuntimeError("This clip is already retargeted; pick the source motion clip.")
    rig = db.get_rig(rig_id, True)
    if not rig:
        raise RuntimeError("rig not found")
    ctx.progress(0.02, "bone map")
    skel, names, parents = _clip_joints(src, None)
    key = bonemap.skeleton_key(skel, names)
    bmap = options.pop("bone_map", None)
    map_notes: list[str] = []
    if not bmap:
        stored = rig["bone_maps"].get(key)
        if stored and (stored.get("source") == "user" or not options.get("use_llm")):
            bmap = stored["map"]
        else:
            sug = bonemap.suggest(rig["bones"], skel, names, parents)
            if options.get("use_llm"):
                sug = bonemap.refine_with_llm(rig["bones"], sug)
            bmap = sug["map"]
            map_notes = sug.get("notes", [])
            maps = dict(rig["bone_maps"])
            maps[key] = {**{k: v for k, v in sug.items() if k != "source_joints"}, "source": "llm" if options.get("use_llm") else "auto"}
            db.update_rig(rig_id, bone_maps=maps)
    if not bmap:
        raise RuntimeError("No bone mapping could be found between this rig and the motion skeleton. Edit the bone map first.")
    cid = db.new_id()
    d = db.clip_dir(cid)
    opts = {k: options[k] for k in ("in_place", "loop", "loop_window", "scale", "foot_lock", "ground_fix", "fps", "smooth", "start", "end", "secondary", "speed")
            if k in options}
    opts.setdefault("loop", False)
    # same skeleton family (UE5 Manny/Quinn animations...): copy the source file's bone rotations instead of re-solving them
    # from joint positions. Automatic when most core bones share names with the rig; options {"direct": false} turns it off.
    spath = (src.get("source") or {}).get("path") or ""
    # uneven-ground detection (ledges, climbs, cliff jumps) needs clean foot positions: animation files only - poses
    # estimated from video are too noisy (a walking bear was read as stepping onto a 32 cm ledge)
    opts["terrain"] = bool(options.get("terrain", Path(spath).suffix.lower() in (".fbx", ".bvh", ".glb", ".gltf", ".blend")))
    if options.get("direct", True) and Path(spath).suffix.lower() in (".fbx", ".glb", ".gltf") and Path(spath).is_file():
        rig_core = [b["name"] for b in rig["bones"] if b.get("deform", True)]
        shared = len(set(rig_core) & set(names))
        if rig_core and shared >= max(15, 0.6 * len(rig_core)):
            opts["direct_source"] = spath
    res = blender.run_script("retarget.py", {"rig_blend": rig["file"], "motion": src["_files"]["motion"], "bone_map": bmap, "out_bake": str(d / "bake.npz"),
                                              "options": opts, "armature": rig.get("info", {}).get("armature")}, ctx, (0.05, 0.7 if options.get("preview", True) else 0.98))
    m = res["metrics"]
    if "loop" in (res.get("info") or {}):            # loop_window "require": the retargeter drops the loop when no cycle repeats
        opts["loop"] = bool(res["info"]["loop"])
    in_place = bool(options.get("in_place", False))
    name = options.get("name") or f"{src['name']} on {rig['name']}"
    clip = db.add_clip(cid=cid, name=name, skeleton=src["skeleton"], rig_id=rig_id,
                       source={"parent": clip_id, "options": opts, "bone_map_key": key, "info": res.get("info", {}), "notes": res.get("notes", []) + map_notes,
                               "check": res.get("check", {})},
                       fps=res["fps"], frames=res["frames"], duration=(res["frames"] - 1) / res["fps"], loop=bool(opts.get("loop")), root_motion=not in_place,
                       tags=list(src["tags"]), caption=src["caption"], files={"bake": str(d / "bake.npz"), "motion": None, "source_motion": src["_files"]["motion"]},
                       stats=m, role=src.get("role", ""), parent_clip_id=clip_id)
    try:
        from . import review
        review.heuristic_annotate(cid)
    except Exception:  # noqa: BLE001
        pass
    out: dict[str, Any] = {"clip": _pub(db.get_clip(cid)), "metrics": m, "notes": res.get("notes", []) + map_notes}
    if options.get("preview", True):
        try:
            pv = render_preview(ctx, cid, {"camera": "side", "size": 384, "bg": "transparent"}, progress=(0.7, 0.98))
            out["media"] = pv.get("media")
        except Exception as e:  # noqa: BLE001
            out["preview_error"] = str(e)[:300]
    out["clip"] = _pub(db.get_clip(cid))
    try:
        search.ensure_embedding(cid)
    except Exception:  # noqa: BLE001
        pass
    return out


# ================================================================================================ previews
def _ffmpeg_png_seq(pattern: Path, fps: float, out: Path, kind: str, size: tuple[int, int], bg: str, ctx: JobContext | None, total: float) -> None:
    w, h = size
    if kind == "webm":
        args = ["-framerate", f"{fps:.3f}", "-i", str(pattern), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30",
                "-auto-alt-ref", "0", "-row-mt", "1", "-an", str(out)]
    else:
        color = bg.lstrip("#") or "20232b"
        args = ["-framerate", f"{fps:.3f}", "-i", str(pattern), "-f", "lavfi", "-i", f"color=c=0x{color}:s={w}x{h}:r={fps:.3f}",
                "-filter_complex", "[1:v][0:v]overlay=shortest=1:format=auto,format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-movflags", "+faststart", "-an", str(out)]
    run_ffmpeg(args, total_duration=total, ctx=ctx)


def render_preview(ctx: JobContext, clip_id: str, opts: dict | None = None, progress: tuple[float, float] = (0.0, 1.0)) -> dict:
    opts = opts or {}
    c = db.get_clip(clip_id)
    if not c:
        raise RuntimeError("clip not found")
    lo, hi = progress
    if not c["rig_id"]:
        # source motion: stick figure
        mp4 = stick.render(c["_files"]["motion"], db.clip_dir(clip_id) / "stick.mp4")
        db.set_file(clip_id, "mp4", str(mp4))
        return {"media": None, "file": str(mp4), "note": "Source motions have no character; showing a stick figure. Retarget onto a rig for a real preview."}
    rig = db.get_rig(c["rig_id"], True)
    if not rig:
        raise RuntimeError("the clip's rig was deleted")
    camera = opts.get("camera", "side")
    bg = opts.get("bg", "transparent")
    transparent = bg == "transparent"
    size = opts.get("size", 384)
    if isinstance(size, (int, float)):
        size = [int(size), int(size)]
    w, h = int(size[0]) // 2 * 2, int(size[1]) // 2 * 2
    pv_dir = db.PREVIEWS_DIR / clip_id / f"{camera}_{int(time.time())}"
    seq = pv_dir / "png"
    maxf = int(opts.get("frames") or 150)
    step = max(1, math.ceil(c["frames"] / maxf))
    in_place = bool(opts.get("in_place", not c["root_motion"]))
    ctx.progress(lo, "rendering in Blender")
    res = blender.run_script("render_preview.py", {"rig_blend": rig["file"], "bake": c["_files"]["bake"], "out_dir": str(seq), "camera": camera, "size": [w, h],
                                                    "transparent": transparent, "bg": bg if not transparent else "#20232b", "engine": opts.get("engine", "workbench"),
                                                    "in_place": in_place, "step": step, "armature": rig.get("info", {}).get("armature")},
                             ctx, (lo, lo + (hi - lo) * 0.7))
    fps = res["fps"]
    n = res["frames"]
    files: dict[str, str] = {}
    pattern = seq / "f_%04d.png"
    total = n / fps
    base = safe_name(c["name"])
    ext_main = "webm" if transparent else "mp4"
    main = pv_dir / f"{base}.{ext_main}"
    ctx.progress(lo + (hi - lo) * 0.72, f"encoding {ext_main}")
    _ffmpeg_png_seq(pattern, fps, main, ext_main, (w, h), bg if not transparent else "#20232b", ctx, total)
    files[ext_main] = str(main)
    if transparent:                                    # solid-colour copy for browsers / the library grid (always playable)
        mp4 = pv_dir / f"{base}.mp4"
        _ffmpeg_png_seq(pattern, fps, mp4, "mp4", (w, h), "#20232b", ctx, total)
        files["mp4"] = str(mp4)
    # poster: middle frame composited over the dark background
    pngs = sorted(seq.glob("f_*.png"))
    poster = pv_dir / f"{base}.png"
    if pngs:
        im = Image.open(pngs[len(pngs) // 2]).convert("RGBA")
        bgim = Image.new("RGBA", im.size, tuple(int(("20232b")[i:i + 2], 16) for i in (0, 2, 4)) + (255,))
        bgim.alpha_composite(im)
        bgim.convert("RGB").save(poster)
        files["png"] = str(poster)
    shutil.rmtree(seq, ignore_errors=True)
    # register with the media library (origin='anim') so the video editor can use it
    ctx.progress(lo + (hi - lo) * 0.95, "registering media")
    old = c.get("preview_media_id")
    row = ingest.ingest_file(main, origin="anim", name=f"{c['name']} ({camera})", tags=["anim", rig["name"], *c["tags"][:4]],
                             meta={"anim": {"clipId": clip_id, "rigId": c["rig_id"], "camera": camera, "alpha": transparent, "fps": fps, "frames": n}})
    if poster.exists():
        try:
            shutil.copyfile(poster, config.THUMB_DIR / f"{row['id']}.jpg")
        except Exception:  # noqa: BLE001
            pass
    cur = db.get_clip(clip_id)
    nf = dict(cur["_files"])  # type: ignore[index]
    nf.update(files)
    db.update_clip(clip_id, files=nf, preview_media_id=row["id"])
    if old and old != row["id"]:
        try:
            media_db.delete_media(old)
        except Exception:  # noqa: BLE001
            pass
    return {"media": row, "files": {k: f"/api/anim/file/{clip_id}.{k}" for k in files}, "alpha": transparent,
            "ffmpeg_alpha_input": "-c:v libvpx-vp9 -i <file.webm>  (the native vp9 decoder drops the alpha channel)"}


# ================================================================================================ export
def _resolve_export_clips(clip_id: str | None, clip_ids: list[str] | None, rig_id: str | None, all_actions: bool) -> tuple[dict, list[dict]]:
    ids: list[str] = list(clip_ids or [])
    if clip_id and clip_id not in ids:
        ids.insert(0, clip_id)
    clips = [db.get_clip(i) for i in ids]
    if any(c is None for c in clips):
        raise RuntimeError("clip not found")
    rid = rig_id or next((c["rig_id"] for c in clips if c and c["rig_id"]), None)
    if not rid:
        raise RuntimeError("Pick a retargeted clip (or a rigId): a source motion has no character to export.")
    rig = db.get_rig(rid, True)
    if not rig:
        raise RuntimeError("rig not found")
    chosen = [c for c in clips if c]
    for c in chosen:
        if not c["rig_id"]:
            raise RuntimeError(f"Clip '{c['name']}' is a source motion; retarget it onto a rig first.")
        if c["rig_id"] != rid:
            raise RuntimeError("All clips of one export must belong to the same rig.")
    if all_actions or not chosen:
        have = {c["id"] for c in chosen}
        for c in reversed(db.list_clips(rig_id=rid)):               # oldest first
            if c["id"] not in have and c["has_bake"]:
                chosen.append(c)
    if not chosen:
        raise RuntimeError("This rig has no retargeted clips yet.")
    return rig, chosen


def _unique_names(clips: list[dict]) -> list[str]:
    used: dict[str, int] = {}
    out = []
    for c in clips:
        base = ROLE_NAMES.get((c.get("role") or "").lower()) or safe_name(c["name"], "Clip")
        n = used.get(base, 0)
        used[base] = n + 1
        out.append(base if n == 0 else f"{base}_{n + 1}")
    return out


def export(ctx: JobContext, clip_id: str | None = None, fmt: str = "glb", options: dict | None = None, clip_ids: list[str] | None = None, rig_id: str | None = None) -> dict:
    options = dict(options or {})
    fmt = fmt.lower()
    if fmt not in ("glb", "fbx", "blend"):
        raise RuntimeError("format must be glb, fbx or blend")
    preset = options.get("preset", "game")
    per_clip = bool(options.get("per_clip", False))
    all_actions = bool(options.get("all_actions", preset == "game" and not clip_ids))
    rig, clips = _resolve_export_clips(clip_id, clip_ids, rig_id, all_actions)
    fps = float(options.get("fps") or 30)
    root_motion = bool(options.get("root_motion", False))
    names = _unique_names(clips)
    results = []
    groups = [([c], [n]) for c, n in zip(clips, names)] if per_clip else [(clips, names)]
    total = len(groups)
    for gi, (gc, gn) in enumerate(groups):
        eid_base = safe_name(rig["name"])
        file_base = eid_base if not per_clip else f"{eid_base}_{gn[0]}"
        pending_id = db.new_id()
        odir = db.EXPORTS_DIR / pending_id
        odir.mkdir(parents=True, exist_ok=True)
        out_file = odir / f"{file_base}.{fmt}"
        spec = [{"bake": c["_files"]["bake"], "name": n, "in_place": (not root_motion) if preset == "game" else (not c["root_motion"] if options.get("in_place") is None else bool(options["in_place"])),
                 "loop": c["loop"]} for c, n in zip(gc, gn)]
        lo, hi = gi / total, (gi + 1) / total
        res = blender.run_script("export.py", {"rig_blend": rig["file"], "out_file": str(out_file), "format": fmt, "clips": spec, "fps": fps,
                                                "armature_only": bool(options.get("armature_only", per_clip)), "target": options.get("target", "generic"),
                                                "armature": rig.get("info", {}).get("armature")}, ctx, (lo, lo + (hi - lo) * 0.8))
        verify = None
        if options.get("verify", True) and fmt in ("glb", "fbx"):
            try:
                v = blender.run_script("verify_export.py", {"input": str(out_file)}, ctx, (lo + (hi - lo) * 0.8, hi))
                problems = []
                if not v.get("armatures"):
                    problems.append("no armature in the exported file")
                if not options.get("armature_only", per_clip) and not v.get("meshes"):
                    problems.append("no mesh in the exported file")
                if not options.get("armature_only", per_clip) and not v.get("skinned_meshes"):
                    problems.append("mesh is not skinned to the armature")
                if len(v.get("actions", [])) < len(gc):
                    problems.append(f"expected {len(gc)} animation(s), found {len(v.get('actions', []))}")
                for a, expected in zip(sorted(v.get("actions", []), key=lambda a: a["name"]), sorted(res["clips"], key=lambda c: c["name"])):
                    if abs(a["duration_s"] - expected["duration"]) > 2.0 / fps + 0.05:
                        problems.append(f"animation '{a['name']}' lasts {a['duration_s']:.2f}s, expected {expected['duration']:.2f}s")
                v["problems"], v["ok"] = problems, not problems
                verify = {k: v[k] for k in v if k != "bone_names"}
            except Exception as e:  # noqa: BLE001
                verify = {"ok": False, "problems": [f"verification failed: {str(e)[:200]}"]}
        sidecar = None
        if fmt in ("glb", "fbx"):
            sc = {"schema": "cutlab.anim/1", "character": rig["name"], "file": out_file.name, "format": fmt, "upAxis": "Y", "forward": "+Z", "unit": "m", "fps": fps,
                  "originAtFeet": True, "skeleton": {"boneCount": len(res["bones"]), "bones": res["bones"]},
                  "clips": [{"name": ci["name"], "duration": round(ci["duration"], 4), "frames": ci["frames"], "fps": ci["fps"], "loop": ci["loop"],
                             "rootMotion": ci["rootMotion"], "role": (c.get("role") or "") or None, "source": c["id"]} for ci, c in zip(res["clips"], gc)]}
            sidecar = odir / f"{file_base}.anim.json"
            sidecar.write_text(json.dumps(sc, indent=2, ensure_ascii=False), "utf-8")
        rec = db.add_export(rig_id=rig["id"], clip_ids=[c["id"] for c in gc], fmt=fmt, file=str(out_file), sidecar=str(sidecar) if sidecar else None,
                            info={"clips": res["clips"], "verify": verify, "options": options, "size": res.get("size")})
        for c in gc:                                    # remember the latest file per clip so /file/{clipId}.glb works
            nf = dict(c["_files"])
            nf[fmt] = str(out_file)
            db.update_clip(c["id"], files=nf)
        results.append({**rec, "verify": verify, "clips": res["clips"]})
    first = results[0]
    return {"file": first["file"], "url": first["url"], "sidecar_url": first.get("sidecar_url"), "format": fmt, "exports": results, "verify": first.get("verify"),
            "clips": first["clips"], "rig": {"id": rig["id"], "name": rig["name"]}}


# ================================================================================================ my-engine
def my_engine_status() -> dict:
    try:
        r = httpx.get(f"{MY_ENGINE_URL}/api/assets", timeout=httpx.Timeout(1.0, connect=0.6))
        return {"reachable": True, "url": MY_ENGINE_URL, "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "url": MY_ENGINE_URL, "hint": "Start the asset server: run `pnpm assets` in D:\\THreejsEngine\\my-engine", "error": str(e)[:160]}


def send_to_my_engine(ctx: JobContext, clip_ids: list[str] | None, rig_id: str | None, options: dict | None = None) -> dict:
    options = dict(options or {})
    st = my_engine_status()
    if not st["reachable"]:
        raise RuntimeError("my-engine asset server is not running at " + MY_ENGINE_URL + ". Start it with `pnpm assets` in D:\\THreejsEngine\\my-engine, then try again.")
    exp = export(ctx, None, "glb", {"preset": "game", "all_actions": not clip_ids, "verify": True, **{k: v for k, v in options.items() if k in ("fps", "root_motion", "per_clip", "armature_only")}},
                 clip_ids=clip_ids, rig_id=rig_id)
    e = exp["exports"][0]
    ctx.progress(0.9, "uploading to my-engine")
    rig = exp["rig"]
    fname = Path(e["file"]).name
    tags = ",".join(options.get("tags") or ["animation", "cutlab"])
    desc = options.get("description") or f"{rig['name']} with {len(exp['clips'])} animation clip(s): " + ", ".join(c["name"] for c in exp["clips"])
    params = {"filename": fname, "name": options.get("name") or rig["name"], "description": desc, "tags": tags, "license": options.get("license", "")}
    try:
        with open(e["file"], "rb") as f:
            r = httpx.post(f"{MY_ENGINE_URL}/api/assets", params=params, content=f.read(), headers={"content-type": "application/octet-stream"}, timeout=120)
    except httpx.HTTPError as ex:
        raise RuntimeError(f"Could not reach the my-engine asset server ({MY_ENGINE_URL}): {ex}. Start it with `pnpm assets` in D:\\THreejsEngine\\my-engine.")
    if r.status_code >= 400:
        raise RuntimeError(f"my-engine rejected the upload ({r.status_code}): {r.text[:300]}")
    try:
        asset = r.json()
    except Exception:  # noqa: BLE001
        asset = {"raw": r.text[:300]}
    return {"sent": True, "server": MY_ENGINE_URL, "filename": fname, "asset": asset, "export": {"url": e["url"], "sidecar_url": e.get("sidecar_url")}, "clips": exp["clips"],
            "verify": exp.get("verify")}
