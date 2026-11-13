"""Procedural TEST characters (skinned meshes + armatures) so the pipeline can be exercised without the user's models.

args: {"kind": "bear"|"humanoid", "out_blend": path, "out_glb"?: path, "result"?: path}
Conventions of the generated rigs: Blender units (m), Z up, character faces -Y (= +Z in glTF), left side at +X, feet on z=0.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

import common  # noqa: E402

V = Vector


def mirror(spec, suffix_l=".L", suffix_r=".R"):
    """Expand {'name{S}': (head, tail, parent)} into left (+X) and right (-X) bones."""
    out = {}
    for k, (h, t, p) in spec.items():
        for suf, sgn in ((suffix_l, 1), (suffix_r, -1)):
            nm = k.replace("{S}", suf)
            pp = p.replace("{S}", suf) if p else p
            out[nm] = ((h[0] * sgn, h[1], h[2]), (t[0] * sgn, t[1], t[2]), pp)
    return out


BEAR_BONES = {
    "root": ((0, 0, 0), (0, -0.2, 0), None),
    "pelvis": ((0, 0.45, 0.62), (0, 0.25, 0.64), "root"),
    "spine_01": ((0, 0.25, 0.64), (0, 0.02, 0.66), "pelvis"),
    "spine_02": ((0, 0.02, 0.66), (0, -0.22, 0.68), "spine_01"),
    "spine_03": ((0, -0.22, 0.68), (0, -0.40, 0.70), "spine_02"),
    "neck": ((0, -0.40, 0.70), (0, -0.52, 0.80), "spine_03"),
    "head": ((0, -0.52, 0.80), (0, -0.78, 0.77), "neck"),
    "tail_01": ((0, 0.45, 0.62), (0, 0.60, 0.66), "pelvis"),
    "tail_02": ((0, 0.60, 0.66), (0, 0.72, 0.66), "tail_01"),
    "tail_03": ((0, 0.72, 0.66), (0, 0.82, 0.62), "tail_02"),
}
BEAR_BONES.update(mirror({
    "ear{S}": ((0.09, -0.57, 0.88), (0.11, -0.56, 0.96), "head"),
    "front_upper_leg{S}": ((0.17, -0.30, 0.60), (0.17, -0.27, 0.36), "spine_03"),
    "front_lower_leg{S}": ((0.17, -0.27, 0.36), (0.17, -0.33, 0.13), "front_upper_leg{S}"),
    "front_paw{S}": ((0.17, -0.33, 0.13), (0.17, -0.46, 0.03), "front_lower_leg{S}"),
    "hind_upper_leg{S}": ((0.17, 0.42, 0.58), (0.17, 0.34, 0.33), "pelvis"),
    "hind_lower_leg{S}": ((0.17, 0.34, 0.33), (0.17, 0.45, 0.13), "hind_upper_leg{S}"),
    "hind_paw{S}": ((0.17, 0.45, 0.13), (0.17, 0.34, 0.03), "hind_lower_leg{S}"),
}))

# part: (kind, params..., material, allowed bones)
def bear_parts():
    P = []
    P.append(("ell", (0, 0.10, 0.66), (0.26, 0.50, 0.25), "fur", ["pelvis", "spine_01", "spine_02", "spine_03"]))
    P.append(("ell", (0, 0.38, 0.60), (0.25, 0.22, 0.25), "fur", ["pelvis", "spine_01", "tail_01"]))
    P.append(("ell", (0, -0.28, 0.68), (0.27, 0.28, 0.28), "fur", ["spine_02", "spine_03", "neck"]))
    P.append(("limb", (0, -0.40, 0.70), (0, -0.52, 0.80), 0.14, "fur", ["spine_03", "neck", "head"]))
    P.append(("ell", (0, -0.62, 0.80), (0.16, 0.20, 0.15), "fur", ["neck", "head"]))
    P.append(("ell", (0, -0.78, 0.765), (0.085, 0.12, 0.075), "light", ["head"]))
    P.append(("ell", (0, -0.89, 0.775), (0.035, 0.035, 0.035), "dark", ["head"]))
    for s in (1, -1):
        P.append(("ell", (0.075 * s, -0.72, 0.84), (0.022, 0.022, 0.022), "dark", ["head"]))
        P.append(("ell", (0.10 * s, -0.565, 0.92), (0.05, 0.035, 0.05), "fur", ["head"]))
    P.append(("limb", (0, 0.45, 0.62), (0, 0.60, 0.66), 0.06, "fur", ["pelvis", "tail_01", "tail_02"]))
    P.append(("limb", (0, 0.60, 0.66), (0, 0.72, 0.66), 0.05, "fur", ["tail_01", "tail_02", "tail_03"]))
    P.append(("limb", (0, 0.72, 0.66), (0, 0.82, 0.62), 0.04, "fur", ["tail_02", "tail_03"]))
    for suf, s in ((".L", 1), (".R", -1)):
        b = {k: BEAR_BONES[k + suf] for k in ("front_upper_leg", "front_lower_leg", "front_paw", "hind_upper_leg", "hind_lower_leg", "hind_paw")}
        P.append(("limb", b["front_upper_leg"][0], b["front_upper_leg"][1], 0.095, "fur", ["spine_03", "front_upper_leg" + suf, "front_lower_leg" + suf]))
        P.append(("limb", b["front_lower_leg"][0], b["front_lower_leg"][1], 0.065, "fur", ["front_upper_leg" + suf, "front_lower_leg" + suf, "front_paw" + suf]))
        P.append(("ell", (0.17 * s, -0.385, 0.06), (0.065, 0.115, 0.06), "fur", ["front_lower_leg" + suf, "front_paw" + suf]))
        P.append(("limb", b["hind_upper_leg"][0], b["hind_upper_leg"][1], 0.11, "fur", ["pelvis", "hind_upper_leg" + suf, "hind_lower_leg" + suf]))
        P.append(("limb", b["hind_lower_leg"][0], b["hind_lower_leg"][1], 0.07, "fur", ["hind_upper_leg" + suf, "hind_lower_leg" + suf, "hind_paw" + suf]))
        P.append(("ell", (0.17 * s, 0.395, 0.06), (0.07, 0.13, 0.06), "fur", ["hind_lower_leg" + suf, "hind_paw" + suf]))
    return P


M = "mixamorig:"
HUMANOID_BONES = {
    M + "Hips": ((0, 0, 0.95), (0, 0, 1.02), None),
    M + "Spine": ((0, 0, 1.02), (0, 0, 1.15), M + "Hips"),
    M + "Spine1": ((0, 0, 1.15), (0, 0, 1.28), M + "Spine"),
    M + "Spine2": ((0, 0, 1.28), (0, 0, 1.42), M + "Spine1"),
    M + "Neck": ((0, 0, 1.42), (0, 0, 1.52), M + "Spine2"),
    M + "Head": ((0, 0, 1.52), (0, 0, 1.76), M + "Neck"),
}
# Mixamo names put the side in front ("mixamorig:LeftArm"), so expand by hand
def _hum_side(side, sgn):
    S = side
    d = {
        f"{M}{S}Shoulder": ((0.03 * sgn, 0, 1.42), (0.12 * sgn, 0, 1.44), M + "Spine2"),
        f"{M}{S}Arm": ((0.12 * sgn, 0, 1.44), (0.40 * sgn, 0, 1.44), f"{M}{S}Shoulder"),
        f"{M}{S}ForeArm": ((0.40 * sgn, 0, 1.44), (0.66 * sgn, 0, 1.44), f"{M}{S}Arm"),
        f"{M}{S}Hand": ((0.66 * sgn, 0, 1.44), (0.80 * sgn, 0, 1.44), f"{M}{S}ForeArm"),
        f"{M}{S}UpLeg": ((0.09 * sgn, 0, 0.93), (0.09 * sgn, 0, 0.50), M + "Hips"),
        f"{M}{S}Leg": ((0.09 * sgn, 0, 0.50), (0.09 * sgn, 0, 0.09), f"{M}{S}UpLeg"),
        f"{M}{S}Foot": ((0.09 * sgn, 0, 0.09), (0.09 * sgn, -0.13, 0.03), f"{M}{S}Leg"),
        f"{M}{S}ToeBase": ((0.09 * sgn, -0.13, 0.03), (0.09 * sgn, -0.22, 0.0), f"{M}{S}Foot"),
    }
    return d


HUMANOID_BONES.update(_hum_side("Left", 1))
HUMANOID_BONES.update(_hum_side("Right", -1))


def humanoid_parts():
    P = []
    hips, sp, sp1, sp2, neck, head = (M + n for n in ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head"))
    P.append(("ell", (0, 0, 1.0), (0.17, 0.11, 0.12), "cloth", [hips, sp]))
    P.append(("ell", (0, 0, 1.2), (0.16, 0.10, 0.17), "cloth", [sp, sp1, sp2]))
    P.append(("ell", (0, 0, 1.38), (0.20, 0.11, 0.10), "cloth", [sp1, sp2, neck]))
    P.append(("limb", (0, 0, 1.42), (0, 0, 1.54), 0.055, "skin", [sp2, neck, head]))
    P.append(("ell", (0, 0, 1.64), (0.095, 0.11, 0.12), "skin", [neck, head]))
    P.append(("ell", (0, -0.1, 1.64), (0.015, 0.02, 0.015), "dark", [head]))
    for S, s in (("Left", 1), ("Right", -1)):
        b = lambda n: HUMANOID_BONES[f"{M}{S}{n}"]
        P.append(("limb", b("Arm")[0], b("Arm")[1], 0.05, "cloth", [M + S + "Shoulder", M + S + "Arm", M + S + "ForeArm"]))
        P.append(("limb", b("ForeArm")[0], b("ForeArm")[1], 0.04, "skin", [M + S + "Arm", M + S + "ForeArm", M + S + "Hand"]))
        P.append(("limb", b("Hand")[0], b("Hand")[1], 0.035, "skin", [M + S + "ForeArm", M + S + "Hand"]))
        P.append(("limb", b("UpLeg")[0], b("UpLeg")[1], 0.085, "pants", [hips, M + S + "UpLeg", M + S + "Leg"]))
        P.append(("limb", b("Leg")[0], b("Leg")[1], 0.06, "pants", [M + S + "UpLeg", M + S + "Leg", M + S + "Foot"]))
        P.append(("ell", (0.09 * s, -0.07, 0.045), (0.055, 0.14, 0.045), "dark", [M + S + "Leg", M + S + "Foot", M + S + "ToeBase"]))
    return P


MATS = {"fur": (0.36, 0.21, 0.11), "light": (0.62, 0.48, 0.34), "dark": (0.03, 0.03, 0.035),
        "cloth": (0.15, 0.35, 0.75), "skin": (0.85, 0.65, 0.5), "pants": (0.2, 0.2, 0.28)}


def make_material(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    n = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    c = MATS[name]
    n.inputs["Base Color"].default_value = (*c, 1)
    n.inputs["Roughness"].default_value = 0.85
    m.diffuse_color = (*c, 1)
    return m


def seg_dist(P, a, b):
    ab = b - a
    t = np.clip(((P - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
    return np.linalg.norm(P - (a + t[:, None] * ab), axis=1)


def build(kind):
    bones = BEAR_BONES if kind == "bear" else HUMANOID_BONES
    parts = bear_parts() if kind == "bear" else humanoid_parts()
    # --- armature
    adata = bpy.data.armatures.new("Armature")
    arm = bpy.data.objects.new("Armature", adata)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = {}
    for name, (h, t, p) in bones.items():
        b = adata.edit_bones.new(name)
        b.head, b.tail = V(h), V(t)
        eb[name] = b
    for name, (h, t, p) in bones.items():
        if p:
            eb[name].parent = eb[p]
    bpy.ops.object.mode_set(mode="OBJECT")
    arm["cutlab_kind"] = "quadruped" if kind == "bear" else "humanoid"
    # --- mesh
    bm = bmesh.new()
    mats = {}
    vert_part = []     # per vertex: index into parts
    face_mat = []
    for pi, part in enumerate(parts):
        before = len(bm.verts)
        if part[0] == "ell":
            _, c, r, mat, _a = part
            ret = bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=1.0)
            mtx = Matrix.Translation(V(c)) @ Matrix.Diagonal(V(r)).to_4x4()
        else:
            _, a, b, rad, mat, _a = part
            a, b = V(a), V(b)
            d = (b - a)
            L = d.length
            ret = bmesh.ops.create_uvsphere(bm, u_segments=14, v_segments=10, radius=1.0)
            rot = V((0, 0, 1)).rotation_difference(d.normalized()).to_matrix().to_4x4()
            mtx = Matrix.Translation((a + b) / 2) @ rot @ Matrix.Diagonal(V((rad, rad, L / 2 * 1.12))).to_4x4()
        bmesh.ops.transform(bm, matrix=mtx, verts=ret["verts"])
        mats.setdefault(mat, len(mats))
        vert_part.append((pi, list(ret["verts"])))
        fs = {f for v in ret["verts"] for f in v.link_faces}
        for f in fs:
            f.material_index = mats[mat]
        for f in fs:
            f.smooth = True
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    vert_part = [(vi.index, pi) for pi, vs in vert_part for vi in vs]
    mesh = bpy.data.meshes.new("Body")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Body", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for mname in sorted(mats, key=mats.get):
        mesh.materials.append(make_material(mname))
    # --- weights
    co = np.array([v.co[:] for v in mesh.vertices])
    vg = {n: obj.vertex_groups.new(name=n) for n in bones}
    vp = np.zeros(len(co), int)
    for vi, pi in vert_part:
        vp[vi] = pi
    for pi, part in enumerate(parts):
        allowed = part[-1]
        idx = np.where(vp == pi)[0]
        pts = co[idx]
        ds = np.stack([seg_dist(pts, np.array(bones[b][0]), np.array(bones[b][1])) for b in allowed], axis=1)
        w = np.exp(-(ds - ds.min(axis=1, keepdims=True)) / 0.022)
        w /= w.sum(axis=1, keepdims=True)
        for k, b in enumerate(allowed):
            for j, vi in enumerate(idx):
                if w[j, k] > 0.02:
                    vg[b].add([int(vi)], float(w[j, k]), "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    obj.parent = arm
    for p in mesh.polygons:
        p.use_smooth = True
    return arm, obj


def main(args):
    common.reset_scene()
    kind = args.get("kind", "bear")
    arm, obj = build(kind)
    bpy.context.scene.unit_settings.system = "METRIC"
    out = {"kind": kind, "bones": len(arm.data.bones)}
    os.makedirs(os.path.dirname(args["out_blend"]), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=args["out_blend"])
    if args.get("out_glb"):
        bpy.ops.export_scene.gltf(filepath=args["out_glb"], export_format="GLB", export_animations=False)
    common.progress(1.0, "test rig built")
    return out


common.run(main)
