"""Render the groomed character's animation with strand fur (Cycles) as a PNG sequence.

args: {
  "blend": <stem>_groom.blend written by fur_groom.py,
  "out_dir": folder for f_0001.png ...,
  "camera": "three_quarter" | "side" | "front",
  "size": [w, h], "samples": int,
  "transparent": bool   (true: no sky/ground, ground becomes a shadow catcher -> alpha PNGs with the shadow),
  "max_frames": int     (cap; frames are stepped evenly when the action is longer),
  "result": path
}
Result: {"frames", "fps", "size", "device", "frame_range"}
"""
from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import common  # noqa: E402
import fur_groom  # noqa: E402


def enable_gpu() -> str:
    sc = bpy.context.scene
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                if any(d.type == kind for d in prefs.devices):
                    for d in prefs.devices:
                        d.use = d.type == kind
                    sc.cycles.device = "GPU"
                    return kind
            except TypeError:
                continue
    except Exception as e:  # noqa: BLE001
        common.log("GPU setup skipped:", e)
    sc.cycles.device = "CPU"
    return "CPU"


def main(args):
    bpy.ops.wm.open_mainfile(filepath=args["blend"])
    sc = bpy.context.scene
    out_dir = args["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    arm, meshes = fur_groom.find_rig()
    meshes = [m for m in meshes if m.type == "MESH" and m.name != "FurGround"]
    fwd, _h, lo, hi = fur_groom.body_axes(arm, meshes)

    act = arm.animation_data.action if arm and arm.animation_data else None
    if not act:
        raise RuntimeError("This groom has no animation (groom a GLB export that contains a clip).")
    f0, f1 = (int(round(x)) for x in act.frame_range)
    fps = sc.render.fps / (sc.render.fps_base or 1.0)
    span = f1 - f0                                              # a looping clip: the last frame equals the first
    maxf = int(args.get("max_frames") or 240)
    step = max(1, math.ceil(span / maxf))
    frames = list(range(f0, f1, step)) or [f0]

    cam = sc.camera
    if cam is None:
        raise RuntimeError("The groom file has no camera.")
    fur_groom.place_camera(cam, lo, hi, fwd, args.get("camera", "three_quarter"))

    w, h = args.get("size") or [960, 540]
    sc.render.resolution_x, sc.render.resolution_y = int(w) // 2 * 2, int(h) // 2 * 2
    sc.render.resolution_percentage = 100
    sc.render.engine = "CYCLES"
    sc.cycles.samples = int(args.get("samples") or 32)
    sc.cycles.use_denoising = True
    try:
        sc.render.use_persistent_data = True                    # keep BVH/shaders between frames
    except AttributeError:
        pass
    device = enable_gpu()
    transparent = bool(args.get("transparent"))
    sc.render.film_transparent = transparent
    ground = bpy.data.objects.get("FurGround")
    if ground is not None:
        ground.is_shadow_catcher = transparent
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA" if transparent else "RGB"

    t0 = time.time()
    for i, f in enumerate(frames):
        sc.frame_set(f)
        sc.render.filepath = os.path.join(out_dir, f"f_{i + 1:04d}.png")
        bpy.ops.render.render(write_still=True)
        el = time.time() - t0
        eta = el / (i + 1) * (len(frames) - i - 1)
        common.progress(0.02 + 0.97 * (i + 1) / len(frames), f"frame {i + 1}/{len(frames)} - about {int(eta)}s left")
    return {"frames": len(frames), "fps": fps / step, "size": [sc.render.resolution_x, sc.render.resolution_y], "device": device,
            "frame_range": [f0, f1], "step": step, "seconds": round(time.time() - t0, 1), "transparent": transparent}


if __name__ == "__main__":
    common.run(main)
