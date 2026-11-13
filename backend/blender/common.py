"""Shared helpers for CutLab's headless Blender scripts (run with:  blender -b --factory-startup -P script.py -- args.json).

Protocol with services/anim/blender.py:
  * stdout lines  `PROGRESS <0..1> <message>`   -> job progress
  * stdout line   `CUTLAB_ERROR <message>`       -> clear error text (script also exits non-zero)
  * result JSON is written to args["result"] (path)
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import traceback

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ANIM_SERVICES = os.path.normpath(os.path.join(HERE, "..", "app", "services", "anim"))


def load_local_module(name: str):
    """Load backend/app/services/anim/<name>.py by path (no `app` package import -> no fastapi dependency)."""
    spec = importlib.util.spec_from_file_location(f"cutlab_{name}", os.path.join(ANIM_SERVICES, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"cutlab_{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_args() -> dict:
    argv = sys.argv
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    if not rest:
        raise SystemExit("usage: blender -b -P script.py -- args.json")
    with open(rest[0], encoding="utf-8") as f:
        return json.load(f)


def progress(p: float, msg: str = "") -> None:
    print(f"PROGRESS {max(0.0, min(1.0, p)):.3f} {msg}".rstrip(), flush=True)


def log(*a) -> None:
    print("LOG", *a, flush=True)


def write_result(args: dict, obj: dict) -> None:
    path = args.get("result")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, default=_json_default)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def run(fn) -> None:
    """Entry-point wrapper: loads args, runs fn(args), reports errors in a machine-readable way."""
    try:
        args = load_args()
        res = fn(args)
        if res is not None:
            write_result(args, res)
        progress(1.0, "done")
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        traceback.print_exc()
        sys.stdout.flush()
        print(f"CUTLAB_ERROR {type(e).__name__}: {e}", flush=True)
        sys.exit(3)


# ------------------------------------------------------------------------------------------------ scene io
def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _import_fbx(path: str) -> None:
    # Keep leaf bones: Blender's ignore_leaf_bones also deletes real end bones (Unreal's `head`, `ball_l`, finger tips).
    # Only the dummy end-bones some exporters add ("*_end", "*Nub", "*_End") are removed afterwards.
    try:
        bpy.ops.import_scene.fbx(filepath=path, automatic_bone_orientation=False, ignore_leaf_bones=False, use_anim=True)
    except Exception as e1:  # noqa: BLE001
        log("import_scene.fbx failed, trying wm.fbx_import:", e1)
        bpy.ops.wm.fbx_import(filepath=path)
    _drop_dummy_end_bones()


def _drop_dummy_end_bones() -> None:
    import re
    dummy = re.compile(r"(_end|end|nub|_nub|_tip_end)(\.\d+)?$", re.I)
    weighted = {vg.name for o in bpy.data.objects if o.type == "MESH" for vg in o.vertex_groups}
    for arm in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
        names = [b.name for b in arm.data.bones if not b.children and dummy.search(b.name) and b.name not in weighted]
        if not names:
            continue
        prev_active = bpy.context.view_layer.objects.active
        try:
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode="EDIT")
            eb = arm.data.edit_bones
            for n in names:
                if n in eb:
                    eb.remove(eb[n])
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception as e:  # noqa: BLE001
            log("could not drop end bones:", e)
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:  # noqa: BLE001
                pass
        finally:
            bpy.context.view_layer.objects.active = prev_active


def import_model(path: str) -> str:
    """Import .blend/.glb/.gltf/.fbx into a clean scene. Returns the format name."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
        return "blend"
    reset_scene()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
        return "gltf"
    if ext == ".fbx":
        _import_fbx(path)
        return "fbx"
    if ext == ".bvh":
        bpy.ops.import_anim.bvh(filepath=path, axis_forward="-Z", axis_up="Y", rotate_mode="NATIVE", update_scene_fps=True, update_scene_duration=True)
        return "bvh"
    raise ValueError(f"Unsupported model format '{ext}' (use .glb .gltf .fbx .blend .bvh)")


def custom_shape_objects() -> set:
    s = set()
    for o in bpy.data.objects:
        if o.type == "ARMATURE" and o.pose:
            for pb in o.pose.bones:
                if pb.custom_shape:
                    s.add(pb.custom_shape)
    return s


def scene_meshes() -> list:
    cs = custom_shape_objects()
    return [o for o in bpy.data.objects if o.type == "MESH" and o not in cs and not o.name.startswith("Icosphere")]


def scene_armatures() -> list:
    return [o for o in bpy.data.objects if o.type == "ARMATURE"]


def pick_armature(name: str | None = None):
    arms = scene_armatures()
    if not arms:
        raise RuntimeError("The file contains no armature (skeleton).")
    if name:
        for a in arms:
            if a.name == name:
                return a
    meshes = scene_meshes()

    def score(a):
        deformed = sum(1 for m in meshes if any(md.type == "ARMATURE" and md.object == a for md in m.modifiers))
        return (deformed, len(a.data.bones))
    return sorted(arms, key=score, reverse=True)[0]


def select_only(objs) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def mesh_bbox_world(meshes=None):
    meshes = scene_meshes() if meshes is None else meshes
    pts = []
    for o in meshes:
        mw = o.matrix_world
        pts.extend(mw @ Vector(c) for c in o.bound_box)
    if not pts:
        return None
    a = np.array([[p.x, p.y, p.z] for p in pts])
    return a.min(axis=0), a.max(axis=0)


def apply_all_transforms() -> list[str]:
    """Bake object rotation/scale/location into data so the armature sits at identity (needed by the solver)."""
    notes = []
    for o in list(bpy.data.objects):
        if o.parent is not None:                      # keep world transform, drop the parent link when the parent is not a rig
            if o.parent.type != "ARMATURE":
                mw = o.matrix_world.copy()
                o.parent = None
                o.matrix_world = mw
    objs = [o for o in bpy.data.objects if o.type in ("ARMATURE", "MESH") and o not in custom_shape_objects()]
    bpy.ops.object.select_all(action="DESELECT")
    # make single-user so apply works
    for o in objs:
        if o.data and o.data.users > 1:
            o.data = o.data.copy()
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]
        try:
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        except Exception as e:  # noqa: BLE001
            notes.append(f"transform_apply failed: {e}")
    return notes


def hide_helpers() -> None:
    for o in custom_shape_objects():
        o.hide_render = True
        o.hide_set(True)


# ------------------------------------------------------------------------------------------------ armature data
def bones_topological(arm):
    """Bones with parents before children (stable)."""
    out, seen = [], set()

    def visit(b):
        if b.name in seen:
            return
        if b.parent:
            visit(b.parent)
        seen.add(b.name)
        out.append(b)
    for b in arm.data.bones:
        visit(b)
    return out


def rest_data(arm) -> dict:
    bs = bones_topological(arm)
    idx = {b.name: i for i, b in enumerate(bs)}
    mw = np.array(arm.matrix_world)
    rest = np.zeros((len(bs), 4, 4))
    head = np.zeros((len(bs), 3))
    tail = np.zeros((len(bs), 3))
    for i, b in enumerate(bs):
        rest[i] = mw @ np.array(b.matrix_local)
        head[i] = (mw @ np.append(np.array(b.head_local), 1))[:3]
        tail[i] = (mw @ np.append(np.array(b.tail_local), 1))[:3]
    return {"names": [b.name for b in bs], "parent": [idx[b.parent.name] if b.parent else -1 for b in bs],
            "rest": rest, "head": head, "tail": tail, "deform": [bool(b.use_deform) for b in bs]}


# ------------------------------------------------------------------------------------------------ baked animation
def load_bake(path: str) -> dict:
    d = np.load(path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    out["names"] = [str(n) for n in out["names"]]
    out["fps"] = float(out["fps"])
    return out


def apply_bake(arm, bake: dict, action_name: str, in_place: bool = False, fps: float | None = None, set_active: bool = True):
    """Create an Action on `arm` with one keyframe per frame from bake['quat'] (T,B,4 wxyz) and bake['loc'|'loc_inplace'] (T,B,3)."""
    scene = bpy.context.scene
    T = bake["quat"].shape[0]
    f0 = float(bake["fps"])
    scene.render.fps = int(round(fps or f0))
    scene.render.fps_base = 1.0
    scene.frame_start, scene.frame_end = 1, T
    loc = bake["loc_inplace"] if (in_place and "loc_inplace" in bake) else bake["loc"]
    quat = bake["quat"]
    if arm.animation_data is None:
        arm.animation_data_create()
    act = bpy.data.actions.new(action_name)
    act.use_fake_user = True
    arm.animation_data.action = act
    bpy.context.view_layer.objects.active = arm
    pbones = arm.pose.bones
    for pb in pbones:
        pb.rotation_mode = "QUATERNION"
    ident = np.array([1.0, 0, 0, 0])
    for bi, name in enumerate(bake["names"]):
        pb = pbones.get(name)
        if pb is None:
            continue
        q = quat[:, bi]
        l = loc[:, bi]
        has_q = np.abs(q - ident).max() > 1e-6
        has_l = np.abs(l).max() > 1e-7
        if not (has_q or has_l):
            continue
        for t in range(T):
            if has_q:
                pb.rotation_quaternion = q[t]
                pb.keyframe_insert("rotation_quaternion", frame=t + 1, group=name)
            if has_l:
                pb.location = l[t]
                pb.keyframe_insert("location", frame=t + 1, group=name)
    if not set_active:
        arm.animation_data.action = None
    for pb in pbones:       # leave the pose at the first frame
        pb.matrix_basis = Matrix.Identity(4)
    scene.frame_set(1)
    return act


def set_pose_frame(arm, bake: dict, frame_index: int, in_place: bool = False) -> None:
    """Directly pose the armature from the bake (no action) - used by renders."""
    loc = bake["loc_inplace"] if (in_place and "loc_inplace" in bake) else bake["loc"]
    for bi, name in enumerate(bake["names"]):
        pb = arm.pose.bones.get(name)
        if pb is None:
            continue
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = bake["quat"][frame_index, bi]
        pb.location = loc[frame_index, bi]


# ------------------------------------------------------------------------------------------------ materials / render
def sync_viewport_colors() -> None:
    """Workbench 'Material' colour uses material.diffuse_color; copy it from Principled base colour."""
    for m in bpy.data.materials:
        if m.use_nodes and m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == "BSDF_PRINCIPLED":
                    c = n.inputs["Base Color"].default_value
                    m.diffuse_color = (c[0], c[1], c[2], 1.0)
                    break


def setup_render(engine: str, w: int, h: int, transparent: bool, bg=(0.12, 0.13, 0.16), samples: int = 16) -> None:
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = int(w), int(h), 100
    sc.render.film_transparent = bool(transparent)
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    sc.render.image_settings.compression = 15
    if engine == "eevee":
        sc.render.engine = "BLENDER_EEVEE"
        try:
            sc.eevee.taa_render_samples = samples
        except Exception:  # noqa: BLE001
            pass
        if sc.world is None:
            sc.world = bpy.data.worlds.new("w")
        sc.world.use_nodes = True
        bgn = next((n for n in sc.world.node_tree.nodes if n.type == "BACKGROUND"), None)
        if bgn:
            bgn.inputs[0].default_value = (0.55, 0.57, 0.62, 1)
            bgn.inputs[1].default_value = 0.9
        sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
        sun.data.energy = 3.0
        sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
        sc.collection.objects.link(sun)
    else:
        sc.render.engine = "BLENDER_WORKBENCH"
        sh = sc.display.shading
        sh.light = "STUDIO"
        sh.color_type = "MATERIAL"
        sh.show_cavity = False
        sh.show_shadows = True
        sh.shadow_intensity = 0.35
        sh.show_object_outline = False
        sh.studio_light = "studio.sl" if "studio.sl" in [s.name for s in bpy.context.preferences.studio_lights] else sh.studio_light
        try:
            sc.display.render_aa = "8"
        except Exception:  # noqa: BLE001
            pass
        sync_viewport_colors()
    if not transparent:
        sc.render.film_transparent = False
        if sc.world is None:
            sc.world = bpy.data.worlds.new("w")
        sc.world.use_nodes = True
        bgn = next((n for n in sc.world.node_tree.nodes if n.type == "BACKGROUND"), None)
        if bgn and engine == "eevee":
            pass
        if engine != "eevee":
            sc.display.shading.background_type = "WORLD"
            sc.world.color = bg


def quick_thumbnail(out_png: str, size: int = 256, view: str = "three_quarter") -> None:
    """One transparent Workbench still of everything visible, three-quarter view, auto-framed by the mesh bbox."""
    bb = mesh_bbox_world()
    if bb is None:
        return
    lo, hi = bb
    c = (lo + hi) / 2
    ext = hi - lo
    sc = bpy.context.scene
    setup_render("workbench", size, size, True)
    cam = bpy.data.objects.new("thumbcam", bpy.data.cameras.new("thumbcam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = float(max(ext[0], ext[1], ext[2]) * 1.45) if view != "side" else float(max(ext[1], ext[2]) * 1.3)
    cam.data.clip_start, cam.data.clip_end = 0.01, 1000.0
    d = Vector((-1, -1, 0)).normalized() if view == "three_quarter" else Vector((-1, 0, 0))
    el = math.radians(18)
    d = Vector((d.x * math.cos(el), d.y * math.cos(el), math.sin(el)))
    tgt = Vector((float(c[0]), float(c[1]), float(c[2])))
    cam.location = tgt + d * (max(ext) * 4 + 5)
    cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()
    sc.render.filepath = out_png
    bpy.ops.render.render(write_still=True)


def resample_bake(bake: dict, fps: float) -> dict:
    """Linear / slerp resample of a bake to another frame rate (keeps the clip duration)."""
    f0 = float(bake["fps"])
    if abs(f0 - fps) < 1e-6:
        return bake
    T = bake["quat"].shape[0]
    n = max(2, int(round((T - 1) / f0 * fps)) + 1)
    tt = np.linspace(0, T - 1, n)
    i0 = np.floor(tt).astype(int).clip(0, T - 1)
    i1 = np.minimum(i0 + 1, T - 1)
    w = (tt - i0)[:, None]
    out = dict(bake)
    for k in ("loc", "loc_inplace", "head", "tail", "travel"):
        if k in bake:
            a = bake[k]
            out[k] = a[i0] * (1 - w[..., None]) + a[i1] * w[..., None]
    q0, q1 = bake["quat"][i0], bake["quat"][i1]
    d = (q0 * q1).sum(-1, keepdims=True)
    q1 = np.where(d < 0, -q1, q1)
    q = q0 * (1 - w[..., None]) + q1 * w[..., None]
    out["quat"] = q / np.linalg.norm(q, axis=-1, keepdims=True)
    out["fps"] = fps
    if "root_path" in bake:
        out["root_path"] = bake["root_path"][i0] * (1 - w) + bake["root_path"][i1] * w
    return out
