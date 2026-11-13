"""Render a retargeted clip (or the rest pose) to a PNG sequence with auto-framing.

args: {
  "rig_blend": path, "bake"?: bake.npz, "out_dir": dir, "prefix"?: "f",
  "camera": "side"|"front"|"back"|"three_quarter"|"orbit"|"top", "size": [w,h] | int,
  "transparent": true, "bg": "#20232b", "engine": "workbench"|"eevee",
  "in_place"?: bool (strip root travel), "follow"?: bool (camera tracks the root, default true when the clip travels),
  "step"?: int (render every n-th frame), "max_frames"?: int, "frames_list"?: [0-based indexes] (stills instead of a sequence),
  "result": path }
Character faces -Y in Blender (= +Z in glTF). 'side' puts the camera on -X so the character walks to the right of the screen.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

import common  # noqa: E402


def hex_rgb(h):
    h = (h or "#20232b").lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def view_dirs(kind):
    """unit vector from the target towards the camera (Blender coords), elevation baked in."""
    if kind == "top":                                      # straight down, head of the character towards the top of the frame
        return Vector((0.0, -1e-4, 1.0)).normalized(), 0
    el = math.radians({"side": 8, "front": 8, "back": 8, "three_quarter": 18, "orbit": 16}.get(kind, 8))
    az = {"side": -90, "front": -180 + 180, "back": 0, "three_quarter": -50, "orbit": -50}.get(kind, -90)
    # azimuth measured from +Y axis towards +X?  Build explicitly instead:
    base = {"side": (-1, 0), "front": (0, -1), "back": (0, 1), "three_quarter": (-1, -1), "orbit": (-1, -1)}[kind]
    h = Vector((base[0], base[1], 0)).normalized()
    return Vector((h.x * math.cos(el), h.y * math.cos(el), math.sin(el))), az


def main(args):
    common.progress(0.02, "opening rig")
    bpy.ops.wm.open_mainfile(filepath=args["rig_blend"])
    common.hide_helpers()
    arm = common.pick_armature(args.get("armature"))
    sc = bpy.context.scene
    size = args.get("size", 512)
    w, h = (size, size) if isinstance(size, (int, float)) else size
    w, h = int(w) // 2 * 2, int(h) // 2 * 2
    transparent = bool(args.get("transparent", True))
    engine = args.get("engine", "workbench")
    common.setup_render(engine, w, h, transparent, bg=hex_rgb(args.get("bg")))
    in_place = bool(args.get("in_place", False))

    bake = common.load_bake(args["bake"]) if args.get("bake") else None
    T = 1
    pts = None
    if bake is not None:
        T = bake["quat"].shape[0]
        common.apply_bake(arm, bake, "preview", in_place=in_place)
        head, tail = bake["head"], bake["tail"]
        if in_place and "travel" in bake:
            head = head - bake["travel"][:, None, :]
            tail = tail - bake["travel"][:, None, :]
        try:                                                 # frame the body only: engine root bones / IK helpers would widen the shot
            body = common.load_local_module("metrics").body_bones([str(n) for n in bake["names"]])
        except Exception:  # noqa: BLE001
            body = list(range(head.shape[1]))
        head, tail = head[:, body], tail[:, body]
        pts = np.concatenate([head, tail], axis=1)           # (T, 2B, 3)
        root_path = pts[:, :, :2].mean(axis=1)
    else:
        rd = common.rest_data(arm)
        pts = np.concatenate([rd["head"], rd["tail"]], axis=0)[None]
        root_path = pts[:, :, :2].mean(axis=1)
    bb = common.mesh_bbox_world()
    char_h = float(bb[1][2] - bb[0][2]) if bb else float(pts[..., 2].max())
    char_h = max(char_h, 0.1)

    travel = float(np.linalg.norm(root_path.max(axis=0) - root_path.min(axis=0))) if T > 1 else 0.0
    follow = args.get("follow")
    if follow is None:
        follow = travel > 0.25 * char_h
    # camera target path (xy follows the body; z fixed at mid height)
    if follow:
        k = max(1, int(round(bake["fps"] if bake else 24) // 3)) | 1
        path = np.stack([np.convolve(np.pad(root_path[:, i], (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid") for i in range(2)], axis=1)
    else:
        path = np.tile(root_path.mean(axis=0), (T, 1))
    rel = pts.copy()
    rel[..., :2] -= path[:, None, :]
    lo = np.array([rel[..., 0].min(), rel[..., 1].min(), min(0.0, rel[..., 2].min())])
    hi = np.array([rel[..., 0].max(), rel[..., 1].max(), rel[..., 2].max()])
    center_xy_off = (lo[:2] + hi[:2]) / 2
    zmid = (min(0.0, lo[2]) + max(hi[2], char_h)) / 2
    radius_xy = float(max(np.abs(rel[..., :2] - center_xy_off).max(), 0.1))

    kind = args.get("camera", "side")
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.data.type = "ORTHO"
    cam.data.clip_start, cam.data.clip_end = 0.05, 400.0
    base_dir, _ = view_dirs(kind)
    dist = 30.0 + radius_xy * 4
    aspect = w / h
    height_need = (max(hi[2], char_h) - min(0.0, lo[2])) * 1.18
    if kind == "top":
        height_need = (hi[1] - lo[1]) * 1.18
    # horizontal need along screen-right axis
    if kind in ("side", "back"):
        width_need = (hi[1] - lo[1])
    elif kind in ("front", "top"):
        width_need = (hi[0] - lo[0])
    else:
        width_need = 2 * radius_xy
    if kind == "orbit":
        width_need = 2 * radius_xy
    width_need = max(width_need * 1.18, char_h * 0.3)
    ortho = max(width_need, height_need * aspect) if aspect >= 1 else max(height_need, width_need / aspect)
    cam.data.ortho_scale = ortho
    common.log(f"framing kind={kind} follow={follow} travel={travel:.2f} char_h={char_h:.2f} lo={np.round(lo, 2).tolist()} hi={np.round(hi, 2).tolist()} "
               f"width_need={width_need:.2f} height_need={height_need:.2f} ortho={ortho:.2f}")

    frames = list(range(T))
    step = int(args.get("step", 1) or 1)
    sc.frame_start, sc.frame_end, sc.frame_step = 1, T, step
    for i in range(T):
        tgt = Vector((path[i][0] + center_xy_off[0], path[i][1] + center_xy_off[1], zmid if kind != "top" else max(hi[2], char_h) * 0.5))
        d = base_dir
        if kind == "orbit":
            ang = 2 * math.pi * i / max(T, 1)
            c, s = math.cos(ang), math.sin(ang)
            d = Vector((base_dir.x * c - base_dir.y * s, base_dir.x * s + base_dir.y * c, base_dir.z))
        loc = tgt + d * dist
        cam.location = loc
        cam.rotation_euler = ((tgt - loc).to_track_quat("-Z", "Y").to_euler() if kind != "top"
                              else (0.0, 0.0, math.pi))          # look down; character faces -Y -> head at the top of the image
        cam.keyframe_insert("location", frame=i + 1)
        cam.keyframe_insert("rotation_euler", frame=i + 1)

    out_dir = args["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    prefix = args.get("prefix", "f")
    files = []
    if args.get("frames_list") is not None:
        idxs = [min(max(int(i), 0), T - 1) for i in args["frames_list"]]
        for n, i in enumerate(idxs):
            sc.frame_set(i + 1)
            sc.render.filepath = os.path.join(out_dir, f"{prefix}_{n:02d}.png")
            bpy.ops.render.render(write_still=True)
            files.append(sc.render.filepath)
            common.progress(0.1 + 0.88 * (n + 1) / len(idxs), f"still {n + 1}/{len(idxs)}")
    else:
        total = len(range(1, T + 1, step))
        count = [0]

        def on_post(_scene, *_a):
            count[0] += 1
            common.progress(0.1 + 0.88 * count[0] / total, f"frame {count[0]}/{total}")
        bpy.app.handlers.render_post.append(on_post)
        sc.render.filepath = os.path.join(out_dir, prefix + "_")
        bpy.ops.render.render(animation=True)
        files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith(prefix + "_") and f.endswith(".png"))
    return {"files": files, "frames": len(files), "size": [w, h], "camera": kind, "follow": bool(follow), "fps": (bake["fps"] if bake else 24) / step,
            "char_height": char_h, "ortho_scale": ortho}


common.run(main)
