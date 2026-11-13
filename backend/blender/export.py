"""Export a rig + one or more baked clips as GLB / FBX / BLEND.

args: {
  "rig_blend": path, "out_file": path, "format": "glb"|"fbx"|"blend",
  "clips": [{"bake": bake.npz, "name": "Walk", "in_place": true}],     # every clip becomes one action (all exported together)
  "fps": 30, "armature_only": false (skeleton + animation, no meshes -> per-clip animation GLB),
  "target": "generic"|"unity"|"unreal"|"godot"|"threejs",  "result": path }
Game conventions: Y-up (glTF) / Z-up->Y-up conversion by the exporters, metres, character faces +Z in glTF, origin at the feet
(the rig is normalised at import), 30 fps baked keys, no leaf bones in FBX.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import common  # noqa: E402


def try_export(fn, kwargs):
    """Call an exporter operator; drop keyword arguments this Blender version does not know."""
    kw = dict(kwargs)
    for _ in range(12):
        try:
            return fn(**kw)
        except TypeError as e:
            msg = str(e)
            bad = None
            for k in list(kw):
                if f'"{k}"' in msg or f"'{k}'" in msg or f" {k} " in msg:
                    bad = k
                    break
            if bad is None:
                raise
            common.log(f"exporter option '{bad}' not supported here; skipped")
            kw.pop(bad)
    raise RuntimeError("exporter kept rejecting options")


def main(args):
    common.progress(0.05, "opening rig")
    bpy.ops.wm.open_mainfile(filepath=args["rig_blend"])
    common.hide_helpers()
    arm = common.pick_armature(args.get("armature"))
    fps = float(args.get("fps") or 30)
    fmt = args.get("format", "glb")
    clips = args.get("clips", [])
    if arm.animation_data is None:
        arm.animation_data_create()
    # one action per clip, each kept on an NLA track so exporters see them all
    info_clips = []
    for i, c in enumerate(clips):
        common.progress(0.1 + 0.4 * i / max(len(clips), 1), f"baking {c.get('name')}")
        bake = common.resample_bake(common.load_bake(c["bake"]), fps)
        act = common.apply_bake(arm, bake, c.get("name") or f"clip{i}", in_place=bool(c.get("in_place", True)), fps=fps)
        frames = bake["quat"].shape[0]
        track = arm.animation_data.nla_tracks.new()
        track.name = act.name
        strip = track.strips.new(act.name, 1, act)
        strip.name = act.name
        arm.animation_data.action = None
        info_clips.append({"name": act.name, "frames": frames, "fps": fps, "duration": (frames - 1) / fps,
                           "rootMotion": not bool(c.get("in_place", True)), "loop": bool(c.get("loop", False))})
    if clips:
        arm.animation_data.action = None
    bpy.context.scene.render.fps = int(round(fps))
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = max([c["frames"] for c in info_clips] or [1])
    if args.get("armature_only"):
        for o in list(common.scene_meshes()):
            bpy.data.objects.remove(o, do_unlink=True)
    for pb in arm.pose.bones:
        pb.matrix_basis.identity()
    out = args["out_file"]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    common.progress(0.6, f"writing {fmt}")
    if fmt == "blend":
        bpy.ops.wm.save_as_mainfile(filepath=out, copy=True)
    elif fmt == "glb":
        try_export(bpy.ops.export_scene.gltf, dict(
            filepath=out, export_format="GLB", use_selection=False, export_apply=False,
            export_animations=bool(clips), export_animation_mode="NLA_TRACKS", export_force_sampling=True,
            export_optimize_animation_size=False, export_yup=True, export_skins=True, export_image_format="AUTO",
            export_anim_single_armature=True, export_def_bones=False))
    elif fmt == "fbx":
        unreal = args.get("target") == "unreal"
        try_export(bpy.ops.export_scene.fbx, dict(
            filepath=out, use_selection=False, object_types={"ARMATURE", "MESH"}, add_leaf_bones=False,
            bake_anim=bool(clips), bake_anim_use_all_actions=False, bake_anim_use_nla_strips=True,
            bake_anim_force_startend_keying=True, bake_anim_step=1.0, bake_anim_simplify_factor=0.0,
            apply_scale_options="FBX_SCALE_ALL", axis_forward="-Z", axis_up="Y", path_mode="COPY", embed_textures=True,
            mesh_smooth_type="FACE", use_armature_deform_only=True, primary_bone_axis="Y", secondary_bone_axis="X",
            armature_nodetype="NULL", apply_unit_scale=True, use_space_transform=True, use_mesh_modifiers=True,
            global_scale=1.0))
    else:
        raise ValueError(f"unknown format {fmt}")
    rd = common.rest_data(arm)
    meshes = common.scene_meshes()
    return {"file": out, "format": fmt, "clips": info_clips, "bones": rd["names"],
            "meshes": [m.name for m in meshes], "size": os.path.getsize(out)}


common.run(main)
