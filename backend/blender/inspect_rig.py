"""Import a character (GLB/GLTF/FBX/BLEND), normalise it to the CutLab rig convention and describe it.

args: {"input": path, "out_json": path, "out_blend": path, "thumb"?: png path, "armature"?: name, "normalize"?: true, "result"?: path}
Normalised rig (what retarget / export / preview expect): Z up, metres, armature + meshes at identity transforms,
character faces -Y (= +Z in glTF), feet on z = 0 and centred on the origin, embedded actions removed.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

import common  # noqa: E402

sk = common.load_local_module("skeletons")


def transform_all(mat: Matrix) -> None:
    cs = common.custom_shape_objects()
    for o in bpy.data.objects:
        if o in cs:
            continue
        if o.parent is None or o.parent.type != "ARMATURE":
            o.matrix_world = mat @ o.matrix_world


def detect(arm):
    """-> (kind, forward_xy | None, notes)"""
    rd = common.rest_data(arm)
    names, head, tail = rd["names"], rd["head"], rd["tail"]
    parsed = [sk.parse_name(n) for n in names]

    def find(role):
        return [i for i, p in enumerate(parsed) if p["role"] == role]
    pel, hd = find("pelvis"), find("head") or find("neck")
    notes = []
    kind = arm.get("cutlab_kind") if "cutlab_kind" in arm.keys() else None
    fwd = None
    if pel and hd:
        v = head[hd[0]] - head[pel[0]]
        h = np.array([v[0], v[1]])
        if np.linalg.norm(h) > 0.5 * abs(v[2]):
            kind = kind or "quadruped"
            fwd = h / np.linalg.norm(h)
        else:
            kind = kind or "humanoid"
    if fwd is None:
        feet = [i for i, p in enumerate(parsed) if p["role"] == "toe" or (p["role"] == "end" and p["limb"] == "hind")]
        if feet:
            d = np.mean([[tail[i][0] - head[i][0], tail[i][1] - head[i][1]] for i in feet], axis=0)
            if np.linalg.norm(d) > 0.02:
                fwd = d / np.linalg.norm(d)
    if kind is None:
        kind = "unknown"
        notes.append("Could not classify the rig (humanoid / quadruped); set the bone map manually.")
    return kind, fwd, notes


def main(args):
    common.progress(0.05, "importing")
    src = args["input"]
    fmt = common.import_model(src)
    notes = []
    acts = [{"name": a.name, "frame_range": list(a.frame_range)} for a in bpy.data.actions]
    arm = common.pick_armature(args.get("armature"))
    pre = {"armature_scale": list(arm.scale), "armature_rotation_deg": [math.degrees(x) for x in arm.rotation_euler]}
    for o in bpy.data.objects:
        if o.animation_data:
            o.animation_data_clear()
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    if acts:
        notes.append(f"Removed {len(acts)} embedded action(s) from the rig; import the file as a clip to keep that animation.")
    for o in common.scene_armatures():                      # glTF import adds icosphere bone-shape helpers: not part of the character
        for pb in o.pose.bones:
            pb.custom_shape = None
    for o in [o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith("Icosphere") and not o.parent and not any(md.type == "ARMATURE" for md in o.modifiers)]:
        bpy.data.objects.remove(o, do_unlink=True)
    # rigid characters (low-poly robots, mechs): mesh parts attached to bones instead of skinned. The pipeline (normalisation, retarget,
    # export) works on skinned meshes, so turn each part into a mesh skinned 100% to its bone, keeping its rest placement.
    rigid = [o for o in bpy.data.objects if o.type == "MESH" and o.parent is not None and o.parent.type == "ARMATURE" and o.parent_type == "BONE"
             and o.parent_bone and not any(md.type == "ARMATURE" for md in o.modifiers)]
    if rigid:
        prev = {a: a.data.pose_position for a in common.scene_armatures()}
        for a in prev:
            a.data.pose_position = "REST"
        bpy.context.view_layer.update()
        for o in rigid:
            parm, bone = o.parent, o.parent_bone
            mw = o.matrix_world.copy()
            o.parent = parm
            o.parent_type = "OBJECT"
            o.matrix_world = mw
            vg = o.vertex_groups.get(bone) or o.vertex_groups.new(name=bone)
            vg.add(list(range(len(o.data.vertices))), 1.0, "REPLACE")
            md = o.modifiers.new("Armature", "ARMATURE")
            md.object = parm
        for a, pp in prev.items():
            a.data.pose_position = pp
        bpy.context.view_layer.update()
        notes.append(f"{len(rigid)} rigid part(s) attached to bones were converted to skinned meshes (one bone each).")
    if args.get("normalize", True):
        common.progress(0.2, "normalising")
        bpy.context.view_layer.update()
        notes += common.apply_all_transforms()
        bb = common.mesh_bbox_world()
        rd = common.rest_data(arm)
        lo, hi = (bb if bb is not None else (rd["head"].min(axis=0), rd["tail"].max(axis=0)))
        size = hi - lo
        sc = 1.0
        if size.max() > 15:
            sc = 0.01
        elif size.max() < 0.05:
            sc = 100.0
        if sc != 1.0:
            notes.append(f"Model size {size.max():.3g} looked like the wrong unit; scaled by {sc}.")
            transform_all(Matrix.Scale(sc, 4))
            common.apply_all_transforms()
        bb = common.mesh_bbox_world()
        if bb is not None:
            size = bb[1] - bb[0]
            if args.get("up_axis") == "Y":
                notes.append("up_axis=Y requested; rotated to Z-up.")
                transform_all(Matrix.Rotation(math.radians(90), 4, "X"))
                common.apply_all_transforms()
            elif size[2] < 0.3 * max(size[0], size[1]):
                notes.append("The model is much wider than tall: if it lies on its side, re-import with up_axis='Y'.")
        kind, fwd, n2 = detect(arm)
        notes += n2
        if fwd is not None:
            a = math.atan2(fwd[1], fwd[0])
            theta = -math.pi / 2 - a
            snapped = round(theta / (math.pi / 2)) * (math.pi / 2)
            if abs(theta - snapped) < math.radians(15):
                theta = snapped
            theta = (theta + math.pi) % (2 * math.pi) - math.pi
            if abs(theta) > math.radians(1):
                notes.append(f"Rotated {math.degrees(theta):.0f} deg about Z so the character faces -Y (+Z in glTF).")
                transform_all(Matrix.Rotation(theta, 4, "Z"))
                common.apply_all_transforms()
        else:
            notes.append("Could not detect which way the character faces; assumed -Y.")
        bb = common.mesh_bbox_world()
        rd = common.rest_data(arm)
        lo, hi = (bb if bb is not None else (rd["head"].min(axis=0), rd["tail"].max(axis=0)))
        shift = Vector((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]))
        if shift.length > 1e-4:
            transform_all(Matrix.Translation(shift))
            common.apply_all_transforms()
            notes.append("Moved the character so its feet are at z=0 and centred on the origin.")
    else:
        kind, fwd, n2 = detect(arm)
    bpy.context.view_layer.update()
    rd = common.rest_data(arm)
    bb = common.mesh_bbox_world()
    lo, hi = (bb if bb is not None else (rd["head"].min(axis=0), rd["tail"].max(axis=0)))
    meshes = common.scene_meshes()
    depth = [0] * len(rd["names"])
    for i, p in enumerate(rd["parent"]):
        depth[i] = depth[p] + 1 if p >= 0 else 0
    nchild = [0] * len(rd["names"])
    for p in rd["parent"]:
        if p >= 0:
            nchild[p] += 1
    info = {
        "format": fmt, "source": os.path.basename(src), "armature": arm.name, "kind": kind,
        "bone_count": len(rd["names"]), "mesh_count": len(meshes), "vertex_count": int(sum(len(m.data.vertices) for m in meshes)),
        "material_count": len(bpy.data.materials), "actions_in_source": acts, "pre_normalize": pre,
        "bbox": {"min": lo.tolist(), "max": hi.tolist(), "size": (hi - lo).tolist()}, "height": float(hi[2] - lo[2]),
        "up_axis": "Z", "forward": "-Y", "unit": "m", "notes": notes,
        "bones": [{"name": rd["names"][i], "parent": (rd["names"][rd["parent"][i]] if rd["parent"][i] >= 0 else None),
                   "head": [round(float(x), 5) for x in rd["head"][i]], "tail": [round(float(x), 5) for x in rd["tail"][i]],
                   "deform": rd["deform"][i], "depth": depth[i], "leaf": nchild[i] == 0} for i in range(len(rd["names"]))],
        "meshes": [{"name": m.name, "vertices": len(m.data.vertices), "skinned": any(md.type == "ARMATURE" for md in m.modifiers),
                    "vertex_groups": len(m.vertex_groups)} for m in meshes],
    }
    if not any(m["skinned"] for m in info["meshes"]):
        notes.append("No skinned mesh found - the file only contains a skeleton (OK for retargeting, previews will be empty).")
    common.progress(0.7, "saving")
    os.makedirs(os.path.dirname(args["out_blend"]), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=args["out_blend"])
    if args.get("thumb"):
        try:
            common.quick_thumbnail(args["thumb"], 256)
        except Exception as e:  # noqa: BLE001
            notes.append(f"thumbnail failed: {e}")
    with open(args["out_json"], "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    return {"ok": True, "info": args["out_json"]}


common.run(main)
