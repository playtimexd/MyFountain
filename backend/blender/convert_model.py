"""Convert any model file Blender can read into a game-engine format.

args: {"input": path (.glb/.gltf/.fbx/.obj/.blend/.dae), "output": path (.fbx or .glb), "target": "unity"|"unreal"|"godot"|"threejs"|"generic",
       "result": path}
FBX for Unity/Unreal: -Z forward / Y up, scale applied, no leaf bones, embedded textures. GLB: Y up, textures embedded, animations kept.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import common  # noqa: E402


def try_op(fn, kw):
    kw = dict(kw)
    for _ in range(12):
        try:
            return fn(**kw)
        except TypeError as e:
            bad = next((k for k in kw if f'"{k}"' in str(e) or f"'{k}'" in str(e)), None)
            if bad is None:
                raise
            kw.pop(bad)
    raise RuntimeError("operator kept rejecting options")


def main(args):
    src, out = args["input"], args["output"]
    ext = os.path.splitext(src)[1].lower()
    common.progress(0.05, "Loading")
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=src)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=src)
        elif ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=src, use_anim=True)
        elif ext == ".obj":
            try:
                bpy.ops.wm.obj_import(filepath=src)
            except AttributeError:
                bpy.ops.import_scene.obj(filepath=src)
        elif ext == ".dae":
            bpy.ops.wm.collada_import(filepath=src)
        else:
            raise RuntimeError(f"unsupported input {ext}")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    common.progress(0.6, "Writing")
    has_anim = bool(bpy.data.actions)
    if out.lower().endswith(".fbx"):
        try_op(bpy.ops.export_scene.fbx, dict(
            filepath=out, use_selection=False, object_types={"ARMATURE", "MESH", "EMPTY"}, add_leaf_bones=False,
            bake_anim=has_anim, bake_anim_use_all_actions=True, bake_anim_use_nla_strips=False, bake_anim_simplify_factor=0.0,
            apply_scale_options="FBX_SCALE_ALL", axis_forward="-Z", axis_up="Y", path_mode="COPY", embed_textures=True,
            mesh_smooth_type="FACE", primary_bone_axis="Y", secondary_bone_axis="X"))
    else:
        try_op(bpy.ops.export_scene.gltf, dict(
            filepath=out, export_format="GLB", use_selection=False, export_apply=False, export_animations=has_anim,
            export_animation_mode="ACTIONS", export_yup=True, export_skins=True, export_image_format="AUTO"))
    meshes = [o.name for o in bpy.data.objects if o.type == "MESH"]
    arms = [o.name for o in bpy.data.objects if o.type == "ARMATURE"]
    return {"output": out, "meshes": len(meshes), "armatures": len(arms), "actions": [a.name for a in bpy.data.actions],
            "size": os.path.getsize(out) if os.path.exists(out) else 0}


if __name__ == "__main__":
    common.run(main)
