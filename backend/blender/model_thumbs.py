"""Render small thumbnails of 3D model files (Workbench, studio light, transparent background).

args: {"items": [{"input": path, "output": png path}], "size": 384, "result": path}
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

import common  # noqa: E402


def load(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        return False
    return True


def main(args):
    size = int(args.get("size") or 384)
    done = []
    items = args["items"]
    for i, it in enumerate(items):
        common.progress(i / max(len(items), 1), os.path.basename(it["input"]))
        bpy.ops.wm.read_factory_settings(use_empty=True)
        try:
            if not load(it["input"]):
                continue
        except Exception as e:  # noqa: BLE001
            common.log("load failed", it["input"], e)
            continue
        sc = bpy.context.scene
        meshes = [o for o in sc.objects if o.type == "MESH"]
        if not meshes:
            continue
        dg = bpy.context.evaluated_depsgraph_get()
        pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.evaluated_get(dg).bound_box]
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        c, r = (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        sc.collection.objects.link(cam)
        cam.data.lens = 50
        d = Vector((0.9, -1.6, 0.55)).normalized()
        cam.location = c + d * r * 3.0
        cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam.data.clip_end = r * 50
        sc.camera = cam
        sc.render.engine = "BLENDER_WORKBENCH"
        sh = sc.display.shading
        sh.light = "STUDIO"
        sh.color_type = "MATERIAL" if any(m.material_slots and any(s.material for s in m.material_slots) for m in meshes) else "SINGLE"
        sh.show_cavity = True
        sc.render.film_transparent = True
        sc.render.resolution_x = sc.render.resolution_y = size
        sc.render.image_settings.file_format = "PNG"
        sc.render.image_settings.color_mode = "RGBA"
        os.makedirs(os.path.dirname(it["output"]), exist_ok=True)
        sc.render.filepath = it["output"]
        bpy.ops.render.render(write_still=True)
        done.append(it["output"])
    return {"rendered": done}


if __name__ == "__main__":
    common.run(main)
