"""Re-import an exported GLB/FBX in a clean Blender and describe what a game engine would see.
args: {"input": path, "result": path}"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402

import common  # noqa: E402


def main(args):
    fmt = common.import_model(args["input"])
    sc = bpy.context.scene
    fps = sc.render.fps / (sc.render.fps_base or 1.0)
    arms = common.scene_armatures()
    meshes = common.scene_meshes()
    actions = []
    for a in bpy.data.actions:
        r = a.frame_range
        n_fc = 0
        try:
            for layer in a.layers:
                for strip in layer.strips:
                    for cb in strip.channelbags:
                        n_fc += len(cb.fcurves)
        except Exception:  # noqa: BLE001
            try:
                n_fc = len(a.fcurves)
            except Exception:  # noqa: BLE001
                n_fc = -1
        actions.append({"name": a.name, "frame_start": r[0], "frame_end": r[1], "frames": int(round(r[1] - r[0])) + 1,
                        "duration_s": (r[1] - r[0]) / fps, "fcurves": n_fc})
    bb = common.mesh_bbox_world(meshes)
    skinned = [m.name for m in meshes if any(md.type == "ARMATURE" for md in m.modifiers)]
    res = {"format": fmt, "scene_fps": fps, "armatures": [a.name for a in arms], "bones": [len(a.data.bones) for a in arms],
           "bone_names": [b.name for a in arms for b in a.data.bones], "meshes": [m.name for m in meshes], "skinned_meshes": skinned,
           "actions": actions, "bbox_min": bb[0].tolist() if bb else None, "bbox_max": bb[1].tolist() if bb else None}
    return res


common.run(main)
