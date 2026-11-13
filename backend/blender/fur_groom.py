"""Fur groom for a skinned character (GLB in -> groomed .blend, strand render, fur-ready GLB + cutlab.fur/1 JSON out).

args: {
  "input": path to a skinned .glb (mesh + armature, animations optional),
  "out_dir": folder for the outputs,
  "params": {length_m?, density_per_m2, comb, lift, gravity, root_color, tip_color, melanin, redness, seed},
  "render": {"enabled": true, "frame": int|null, "size": [w, h], "samples": 48, "camera": "three_quarter"|"side"|"front"},
  "result": path
}
What it does (all deterministic, no network):
  1. Per-vertex fur fields on the bind pose, written as mesh attributes that survive glTF export:
       _fur_len  (float, metres)   strand length: region rules from the bone weights + dorsal/ventral boost, smoothed
       _fur_dir  (vec3, mesh space, glTF Y-up) comb direction: away from the head and downward, along the surface
       _fur_dens (float, 0..1)     strand density (thinner on paws and the face)
  2. A Geometry Nodes strand groom placed after the Armature modifier: points are scattered on the REST mesh (so hairs do
     not pop while the body deforms) and pinned to the deformed surface with Sample Nearest Surface. Renders in Cycles.
  3. A Cycles still of the groomed character (optional) and a GLB with the fur attributes for the real-time shell shader.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

import common  # noqa: E402


def try_export(fn, kwargs):
    """Call an exporter operator; drop keyword arguments this Blender version does not know (same as export.py)."""
    kw = dict(kwargs)
    for _ in range(12):
        try:
            return fn(**kw)
        except TypeError as e:
            bad = next((k for k in kw if f'"{k}"' in str(e) or f"'{k}'" in str(e)), None)
            if bad is None:
                raise
            common.log(f"exporter option '{bad}' not supported here; skipped")
            kw.pop(bad)
    raise RuntimeError("exporter kept rejecting options")

# strand length multiplier per body region, matched against bone names (first hit wins)
REGION_RULES = [
    (("paw", "foot", "hand", "toe", "finger", "claw"), 0.45, 0.7),
    (("ear",), 0.55, 0.85),
    (("head", "jaw", "nose", "snout", "face"), 0.6, 1.0),
    (("tail",), 0.9, 1.0),
    (("lower", "calf", "shin", "forearm", "lowerarm", "lowerleg", "knee", "elbow"), 0.6, 1.0),
    (("upper", "thigh", "shoulder", "upperarm", "upperleg", "clavicle", "hip"), 0.85, 1.0),
    (("neck",), 1.15, 1.0),
    (("spine", "chest", "pelvis", "hips", "torso", "back", "body", "root"), 1.0, 1.0),
]

DEFAULTS = dict(length_m=None, density_per_m2=40000.0, comb=0.6, lift=0.8, gravity=0.2, frizz=0.3, curl=0.15,
                root_color=[0.11, 0.065, 0.035], tip_color=[0.42, 0.28, 0.16], melanin=0.82, redness=0.55, seed=7)


BARE_MATERIAL_KEYS = ("nose", "eye", "pupil", "iris", "cornea", "claw", "nail", "teeth", "tooth", "tongue", "mouth", "lip", "gum", "pad", "hoof", "horn", "beak")
SHORT_MATERIAL_KEYS = ("muzzle", "snout", "face", "light")


def material_class(mat) -> str:
    """'bare' (no fur: nose, eyes, claws, ... or an untextured near-black material), 'short' (muzzle) or 'fur'."""
    if mat is None:
        return "fur"
    name = mat.name.lower()
    if any(k in name for k in BARE_MATERIAL_KEYS):
        return "bare"
    if any(k in name for k in SHORT_MATERIAL_KEYS):
        return "short"
    pb = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if mat.node_tree else None
    if pb and not pb.inputs["Base Color"].is_linked:
        c = pb.inputs["Base Color"].default_value
        if 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] < 0.05:
            return "bare"
    return "fur"


def region_factor(bone: str) -> tuple[float, float]:
    b = bone.lower()
    for keys, ln, dn in REGION_RULES:
        if any(k in b for k in keys):
            return ln, dn
    return 1.0, 1.0


def find_rig():
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and len(o.data.vertices) > 0]
    if not meshes:
        raise RuntimeError("The file has no mesh to groom.")
    skinned = [m for m in meshes if any(md.type == "ARMATURE" for md in m.modifiers)]
    return (arms[0] if arms else None), (skinned or meshes)


def body_axes(arm, meshes):
    """World 'forward' (toward the head) and the character height, in Blender space (Z up)."""
    pts = np.concatenate([np.array([m.matrix_world @ v.co for v in m.data.vertices]) for m in meshes])
    lo, hi = pts.min(0), pts.max(0)
    height = float(hi[2] - lo[2]) or 1.0
    fwd = Vector((0, -1, 0))                                       # CutLab rig convention: faces -Y
    if arm:
        mw = arm.matrix_world
        bones = {b.name.lower(): b for b in arm.data.bones}
        head = next((b for n, b in bones.items() if "head" in n), None)
        back = next((b for n, b in bones.items() if n in ("pelvis", "hips", "hip")), None) or \
            next((b for n, b in bones.items() if "tail" in n or "pelvis" in n or "hip" in n), None)
        if head and back:
            v = (mw @ head.head_local) - (mw @ back.head_local)
            v.z = 0
            if v.length > 1e-4:
                fwd = v.normalized()
    return fwd, height, lo, hi


def vertex_neighbors(me):
    n = len(me.vertices)
    e = np.zeros(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", e)
    e = e.reshape(-1, 2)
    nb = [[] for _ in range(n)]
    for a, b in e:
        nb[a].append(b)
        nb[b].append(a)
    return nb


def smooth(values, nb, iters=4, keep=0.5):
    v = values.copy()
    for _ in range(iters):
        out = v.copy()
        for i, js in enumerate(nb):
            if js:
                out[i] = keep * v[i] + (1 - keep) * v[js].mean(0)
        v = out
    return v


def fur_fields(ob, arm, fwd, height, p):
    me = ob.data
    n = len(me.vertices)
    mw = ob.matrix_world
    rot = mw.to_3x3()
    rot_inv = rot.inverted()
    co = np.array([mw @ v.co for v in me.vertices])
    nrm = np.array([(rot @ v.normal).normalized() for v in me.vertices])
    gnames = {g.index: g.name for g in ob.vertex_groups}
    bone_dirs = {}
    if arm:
        amw = arm.matrix_world
        for b in arm.data.bones:
            d = (amw @ b.tail_local) - (amw @ b.head_local)
            bone_dirs[b.name] = np.array(d.normalized()) if d.length > 1e-6 else np.zeros(3)
    base = p["length_m"] or 0.05 * height
    down = np.array([0.0, 0.0, -1.0])
    back = -np.array(fwd)
    flow = back * 0.8 + down * 0.45                          # hair flows away from the head and downward
    L = np.zeros(n)
    D = np.zeros(n)
    C = np.zeros((n, 3))
    for i, v in enumerate(me.vertices):
        ws = [(gnames.get(g.group, ""), g.weight) for g in v.groups
              if g.weight > 1e-3 and (not bone_dirs or gnames.get(g.group, "") in bone_dirs)]
        tot = sum(w for _, w in ws) or 1.0
        lf = df = 0.0
        axis = np.zeros(3)
        for name, w in ws:
            a, d = region_factor(name)
            lf += a * w / tot
            df += d * w / tot
            bd = bone_dirs.get(name)
            if bd is not None and bd.any():
                axis += (bd if bd @ flow >= 0 else -bd) * (w / tot)   # bone axis oriented along the flow
        if not ws:
            lf, df = 1.0, 1.0
        nz = nrm[i][2]
        lf *= 1.0 + 0.2 * max(nz, 0.0) + 0.15 * max(-nz, 0.0)   # longer along the back ridge and the belly (shaggy)
        L[i], D[i] = lf, df
        C[i] = flow + 0.9 * axis
    # face / paw-pad thinning by height: the lowest 4% of the body is mostly paw soles
    z0 = co[:, 2].min()
    sole = (co[:, 2] - z0) < 0.04 * height
    D[sole & (nrm[:, 2] < -0.2)] *= 0.3
    nb = vertex_neighbors(me)
    L = smooth(L[:, None], nb, 5)[:, 0]
    D = smooth(D[:, None], nb, 3)[:, 0]
    # material classes: no fur on bare parts (nose, eyes, claws), short fur on the muzzle; a vertex is bare only when all its faces are
    cls = [material_class(sl.material) for sl in ob.material_slots] or ["fur"]
    fur_faces = np.zeros(n, dtype=np.int32)
    short_faces = np.zeros(n, dtype=np.int32)
    for poly in me.polygons:
        c = cls[poly.material_index] if poly.material_index < len(cls) else "fur"
        if c != "bare":
            fur_faces[list(poly.vertices)] += 1
        if c == "short":
            short_faces[list(poly.vertices)] += 1
    bare = fur_faces == 0
    D[bare] = 0.0
    L[short_faces > 0] *= 0.35
    C = smooth(C, nb, 4)
    # project the comb direction onto the surface and normalise (world), then express it in mesh space
    C = C - nrm * (C * nrm).sum(1, keepdims=True)
    ln = np.linalg.norm(C, axis=1, keepdims=True)
    fallback = np.cross(nrm, np.array([1.0, 0.0, 0.0]))
    C = np.where(ln > 1e-5, C / np.maximum(ln, 1e-9), fallback)
    C_local = np.array([np.array(rot_inv @ Vector(c)) for c in C])
    C_local /= np.maximum(np.linalg.norm(C_local, axis=1, keepdims=True), 1e-9)
    scale = (rot.to_scale()[0] + rot.to_scale()[1] + rot.to_scale()[2]) / 3 or 1.0
    L_local = L * base / scale                                   # attribute lengths are in mesh units
    return L_local.astype(np.float32), C_local.astype(np.float32), np.clip(D, 0, 1).astype(np.float32), base


def write_attr(me, name, data, kind):
    if name in me.attributes:
        me.attributes.remove(me.attributes[name])
    a = me.attributes.new(name, kind, "POINT")
    a.data.foreach_set("vector" if kind == "FLOAT_VECTOR" else "value", data.ravel())


# --------------------------------------------------------------------------------------------- strand groom (Geometry Nodes)
def sock(node, name, out=False, idx=0):
    socks = node.outputs if out else node.inputs
    hits = [s for s in socks if s.name == name and getattr(s, "enabled", True)]
    if not hits:
        hits = [s for s in socks if s.name == name]
    if not hits:
        raise KeyError(f"{node.bl_idname} has no {'output' if out else 'input'} {name!r}: {[s.name for s in socks]}")
    return hits[min(idx, len(hits) - 1)]


def hair_material(p):
    mat = bpy.data.materials.new("Fur_Strands")
    mat.use_nodes = True
    nt = mat.node_tree
    for nd in list(nt.nodes):
        nt.nodes.remove(nd)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    try:
        h = nt.nodes.new("ShaderNodeBsdfHairPrincipled")
        try:
            h.parametrization = "MELANIN"
        except Exception:  # noqa: BLE001
            pass
        for nm, val in (("Melanin", p["melanin"]), ("Melanin Redness", p["redness"]), ("Roughness", 0.38),
                        ("Radial Roughness", 0.45), ("Coat", 0.1), ("Random Roughness", 0.1)):
            try:
                sock(h, nm).default_value = val
            except KeyError:
                pass
        # root-to-tip lightening via the curve parameter
        try:
            info = nt.nodes.new("ShaderNodeHairInfo")
            mr = nt.nodes.new("ShaderNodeMapRange")
            sock(mr, "From Min").default_value = 0.0
            sock(mr, "To Min").default_value = p["melanin"] + 0.08
            sock(mr, "To Max").default_value = max(0.0, p["melanin"] - 0.14)
            nt.links.new(sock(info, "Intercept", out=True), sock(mr, "Value"))
            nt.links.new(sock(mr, "Result", out=True), sock(h, "Melanin"))
        except Exception as e:  # noqa: BLE001
            common.log("hair gradient skipped:", e)
        nt.links.new(h.outputs[0], sock(out, "Surface"))
    except Exception as e:  # noqa: BLE001
        common.log("Principled Hair BSDF unavailable, using Principled BSDF:", e)
        b = nt.nodes.new("ShaderNodeBsdfPrincipled")
        sock(b, "Base Color").default_value = (*p["tip_color"], 1)
        nt.links.new(b.outputs[0], sock(out, "Surface"))
    return mat


def build_groom(ob, p, base_len):
    """Strands live on their own hair-curves object (only Curves objects render GN curves as hair); its node tree reads the
    deformed body through Object Info, so the fur follows the armature without being parented to it."""
    ob.add_rest_position_attribute = True
    hair_data = bpy.data.hair_curves.new(f"{ob.name}_Fur")
    hair = bpy.data.objects.new(f"{ob.name}_Fur", hair_data)
    for c in ob.users_collection:
        c.objects.link(hair)
    hair.matrix_world = ob.matrix_world.copy()
    ng = bpy.data.node_groups.new("CutLab_Fur", "GeometryNodeTree")
    try:
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    except AttributeError:                                       # Blender < 4.0
        ng.inputs.new("NodeSocketGeometry", "Geometry")
        ng.outputs.new("NodeSocketGeometry", "Geometry")
    N = ng.nodes
    Lk = ng.links.new
    gin, gout = N.new("NodeGroupInput"), N.new("NodeGroupOutput")

    def named(name, dtype="FLOAT_VECTOR"):
        nd = N.new("GeometryNodeInputNamedAttribute")
        nd.data_type = dtype
        sock(nd, "Name").default_value = name
        return sock(nd, "Attribute", out=True)

    def store(geo, name, value, dtype="FLOAT_VECTOR"):
        nd = N.new("GeometryNodeStoreNamedAttribute")
        nd.data_type = dtype
        nd.domain = "POINT"
        Lk(geo, sock(nd, "Geometry"))
        sock(nd, "Name").default_value = name
        Lk(value, sock(nd, "Value"))
        return sock(nd, "Geometry", out=True)

    def vmath(op, a, b=None, scale=None):
        nd = N.new("ShaderNodeVectorMath")
        nd.operation = op
        Lk(a, nd.inputs[0]) if not isinstance(a, (tuple, list)) else setattr(nd.inputs[0], "default_value", a)
        if b is not None:
            Lk(b, nd.inputs[1]) if not isinstance(b, (tuple, list)) else setattr(nd.inputs[1], "default_value", b)
        if scale is not None:
            s = sock(nd, "Scale")
            Lk(scale, s) if not isinstance(scale, (int, float)) else setattr(s, "default_value", scale)
        return sock(nd, "Vector", out=True) if op not in ("LENGTH", "DOT_PRODUCT") else sock(nd, "Value", out=True)

    def sample(mesh, value, pos, dtype="FLOAT_VECTOR"):
        """Nearest-surface lookup restricted to the strand's own mesh island, so lookups near the seams of overlapping
        body parts (leg inside chest, neck inside head) never jump to the neighbouring part."""
        nd = N.new("GeometryNodeSampleNearestSurface")
        nd.data_type = dtype
        Lk(mesh, sock(nd, "Mesh"))
        Lk(value, sock(nd, "Value"))
        Lk(pos, sock(nd, "Sample Position"))
        try:
            Lk(named("fur_island", "INT"), sock(nd, "Group ID"))
            Lk(named("fur_island", "INT"), sock(nd, "Sample Group ID"))
        except KeyError:                                         # Blender < 4.1: no group ids
            pass
        return sock(nd, "Value", out=True)

    oi = N.new("GeometryNodeObjectInfo")
    oi.transform_space = "RELATIVE"
    sock(oi, "Object").default_value = ob
    deformed = sock(oi, "Geometry", out=True)
    g = store(deformed, "fur_dpos", sock(N.new("GeometryNodeInputPosition"), "Position", out=True))
    g = store(g, "fur_dnrm", sock(N.new("GeometryNodeInputNormal"), "Normal", out=True))
    sp = N.new("GeometryNodeSetPosition")
    Lk(g, sock(sp, "Geometry"))
    Lk(named("rest_position"), sock(sp, "Position"))
    rest = store(sock(sp, "Geometry", out=True), "fur_island", sock(N.new("GeometryNodeInputMeshIsland"), "Island Index", out=True), "INT")

    dist = N.new("GeometryNodeDistributePointsOnFaces")
    dist.distribute_method = "RANDOM"
    Lk(rest, sock(dist, "Mesh"))
    sock(dist, "Density").default_value = float(p["density_per_m2"])
    Lk(named("_fur_dens", "FLOAT"), sock(dist, "Density Factor"))
    sock(dist, "Seed").default_value = int(p["seed"])
    pts = sock(dist, "Points", out=True)
    ppos = sock(N.new("GeometryNodeInputPosition"), "Position", out=True)

    P = sample(rest, named("fur_dpos"), ppos)
    Nn = vmath("NORMALIZE", sample(rest, named("fur_dnrm"), ppos))
    Lr = sample(rest, named("_fur_len", "FLOAT"), ppos, "FLOAT")
    dir_rest = sample(rest, named("fur_dir_z"), ppos)
    eps = 0.01
    P2 = sample(rest, named("fur_dpos"), vmath("ADD", ppos, vmath("SCALE", dir_rest, scale=eps)))
    D = vmath("NORMALIZE", vmath("SUBTRACT", P2, P))             # comb direction after deformation
    jit = N.new("FunctionNodeRandomValue")
    jit.data_type = "FLOAT_VECTOR"
    sock(jit, "Min").default_value = (-1.0, -1.0, -1.0)
    sock(jit, "Max").default_value = (1.0, 1.0, 1.0)
    sock(jit, "Seed").default_value = int(p["seed"]) + 11
    V = vmath("ADD", vmath("ADD", vmath("SCALE", Nn, scale=p["lift"]), vmath("SCALE", D, scale=p["comb"])),
              (0.0, 0.0, -float(p["gravity"])))
    V = vmath("ADD", V, vmath("SCALE", sock(jit, "Value", out=True), scale=p["frizz"]))   # break up the uniform sheen

    setp = N.new("GeometryNodeSetPosition")
    Lk(pts, sock(setp, "Geometry"))
    Lk(P, sock(setp, "Position"))

    al = N.new("FunctionNodeAlignRotationToVector")
    al.axis = "Z"
    Lk(V, sock(al, "Vector"))
    rnd = N.new("FunctionNodeRandomValue")
    rnd.data_type = "FLOAT"
    sock(rnd, "Min", idx=1).default_value = 0.65
    sock(rnd, "Max", idx=1).default_value = 1.1
    rmul = N.new("ShaderNodeMath")
    rmul.operation = "MULTIPLY"
    Lk(Lr, rmul.inputs[0])
    Lk(sock(rnd, "Value", out=True, idx=1), rmul.inputs[1])

    line = N.new("GeometryNodeCurvePrimitiveLine")
    sock(line, "Start").default_value = (0, 0, 0)
    sock(line, "End").default_value = (0, 0, 1)
    rs = N.new("GeometryNodeResampleCurve")
    Lk(sock(line, "Curve", out=True), sock(rs, "Curve"))
    sock(rs, "Count").default_value = 5
    # a gentle arc in the template strand; the instance rotation leaves its roll random, so arcs vary per hair
    bend = N.new("GeometryNodeSetPosition")
    Lk(sock(rs, "Curve", out=True), sock(bend, "Geometry"))
    tpar = N.new("GeometryNodeSplineParameter")
    sq = N.new("ShaderNodeMath")
    sq.operation = "POWER"
    Lk(sock(tpar, "Factor", out=True), sq.inputs[0])
    sq.inputs[1].default_value = 2.0
    cx = N.new("ShaderNodeCombineXYZ")
    cmul = N.new("ShaderNodeMath")
    cmul.operation = "MULTIPLY"
    Lk(sock(sq, "Value", out=True), cmul.inputs[0])
    cmul.inputs[1].default_value = float(p["curl"])
    Lk(sock(cmul, "Value", out=True), sock(cx, "X"))
    Lk(sock(cx, "Vector", out=True), sock(bend, "Offset"))

    inst = N.new("GeometryNodeInstanceOnPoints")
    Lk(sock(setp, "Geometry", out=True), sock(inst, "Points"))
    Lk(sock(bend, "Geometry", out=True), sock(inst, "Instance"))
    Lk(sock(al, "Rotation", out=True), sock(inst, "Rotation"))
    Lk(sock(rmul, "Value", out=True), sock(inst, "Scale"))
    real = N.new("GeometryNodeRealizeInstances")
    Lk(sock(inst, "Instances", out=True), sock(real, "Geometry"))

    par = N.new("GeometryNodeSplineParameter")
    mr = N.new("ShaderNodeMapRange")
    Lk(sock(par, "Factor", out=True), sock(mr, "Value"))
    r_root = max(0.0004, base_len * 0.018)
    sock(mr, "To Min").default_value = r_root
    sock(mr, "To Max").default_value = r_root * 0.15
    rad = N.new("GeometryNodeSetCurveRadius")
    Lk(sock(real, "Geometry", out=True), sock(rad, "Curve"))
    Lk(sock(mr, "Result", out=True), sock(rad, "Radius"))
    sm = N.new("GeometryNodeSetMaterial")
    Lk(sock(rad, "Curve", out=True), sock(sm, "Geometry"))
    mat = hair_material(p)
    sock(sm, "Material").default_value = mat
    hair_data.materials.append(mat)
    Lk(sock(sm, "Geometry", out=True), sock(gout, "Geometry"))

    mod = hair.modifiers.new("CutLab Fur", "NODES")
    mod.node_group = ng
    return hair


# --------------------------------------------------------------------------------------------- render
def place_camera(cam, lo, hi, fwd, kind="three_quarter"):
    center = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2))
    size = Vector(hi) - Vector(lo)
    radius = max(size.x, size.y, size.z)
    side = Vector((1, 0, 0)) if abs(fwd.x) < 0.7 else Vector((0, 1, 0))
    if kind == "side":
        dirv = side
    elif kind == "front":
        dirv = Vector(fwd)
    else:
        dirv = (side * 0.8 + Vector(fwd) * 0.6).normalized()
    cam.location = center + dirv * radius * 1.75 + Vector((0, 0, radius * 0.35))
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()


def setup_render(lo, hi, fwd, opts):
    sc = bpy.context.scene
    try:
        sc.render.engine = "CYCLES"
    except TypeError as e:
        raise RuntimeError(f"Cycles is not available: {e}")
    cy = sc.cycles
    cy.samples = int(opts.get("samples", 48))
    cy.use_denoising = True
    device = "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                devs = [d for d in prefs.devices if d.type == kind]
                if devs:
                    for d in prefs.devices:
                        d.use = d.type == kind
                    cy.device = "GPU"
                    device = kind
                    break
            except TypeError:
                continue
    except Exception as e:  # noqa: BLE001
        common.log("GPU setup skipped:", e)
    w, h = opts.get("size") or [960, 600]
    sc.render.resolution_x, sc.render.resolution_y = int(w), int(h)
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "AgX" if "AgX" in [i.identifier for i in type(sc.view_settings).bl_rna.properties["view_transform"].enum_items] else "Filmic"

    world = sc.world or bpy.data.worlds.new("World")
    sc.world = world
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs[0].default_value = (0.32, 0.36, 0.42, 1)
    bg.inputs[1].default_value = 0.6

    cam_data = bpy.data.cameras.new("FurCam")
    cam_data.lens = 60
    cam = bpy.data.objects.new("FurCam", cam_data)
    sc.collection.objects.link(cam)
    place_camera(cam, lo, hi, fwd, opts.get("camera", "three_quarter"))
    sc.camera = cam
    center = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2))
    radius = max(Vector(hi) - Vector(lo))

    key = bpy.data.objects.new("Key", bpy.data.lights.new("Key", "SUN"))
    key.data.energy = 3.2
    key.data.angle = math.radians(8)
    key.rotation_euler = (math.radians(50), math.radians(10), math.radians(140))
    sc.collection.objects.link(key)
    rim = bpy.data.objects.new("Rim", bpy.data.lights.new("Rim", "SUN"))
    rim.data.energy = 2.0
    rim.rotation_euler = (math.radians(70), 0, math.radians(-40))
    sc.collection.objects.link(rim)

    bpy.ops.mesh.primitive_plane_add(size=radius * 12, location=(center.x, center.y, lo[2]))
    ground = bpy.context.active_object
    ground.name = "FurGround"
    gm = bpy.data.materials.new("Ground")
    gm.use_nodes = True
    pb = next(n for n in gm.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    pb.inputs["Base Color"].default_value = (0.23, 0.22, 0.2, 1)
    pb.inputs["Roughness"].default_value = 0.9
    ground.data.materials.append(gm)
    return device


def darken_skin(meshes, p):
    """The skin under the fur should read as the root colour, otherwise gaps between strands look light."""
    for m in meshes:
        for slot in m.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            pb = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if pb and not pb.inputs["Base Color"].is_linked:
                c = pb.inputs["Base Color"].default_value
                pb.inputs["Base Color"].default_value = (c[0] * 0.5, c[1] * 0.5, c[2] * 0.5, 1)


def main(args):
    p = dict(DEFAULTS)
    p.update({k: v for k, v in (args.get("params") or {}).items() if v is not None})
    out_dir = args["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    stem = args.get("stem") or os.path.splitext(os.path.basename(args["input"]))[0]

    common.progress(0.02, "Importing the character")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args["input"])
    arm, meshes = find_rig()
    fwd, height, lo, hi = body_axes(arm, meshes)

    common.progress(0.12, "Computing fur length, density and comb direction")
    stats = []
    base_len = None
    for m in meshes:
        L, C, D, base_len = fur_fields(m, arm, fwd, height, p)
        write_attr(m.data, "_fur_len", L, "FLOAT")
        # glTF custom attributes are written raw (no Z-up -> Y-up conversion like POSITION/NORMAL get), so the exported copy
        # is pre-converted; the groom reads the Blender-space copy.
        write_attr(m.data, "_fur_dir", np.stack([C[:, 0], C[:, 2], -C[:, 1]], 1).astype(np.float32), "FLOAT_VECTOR")
        write_attr(m.data, "fur_dir_z", C, "FLOAT_VECTOR")
        write_attr(m.data, "_fur_dens", D, "FLOAT")
        stats.append({"mesh": m.name, "vertices": len(L), "len_min": float(L.min()), "len_max": float(L.max()), "len_mean": float(L.mean()),
                      "bare_vertices": int((D <= 0).sum()), "materials": {sl.material.name: material_class(sl.material) for sl in m.material_slots if sl.material}})

    common.progress(0.22, "Exporting the fur-ready GLB")
    glb = os.path.join(out_dir, f"{stem}_fur.glb")
    for o in bpy.context.scene.objects:
        o.select_set(o in meshes or o == arm)
    has_anim = bool(bpy.data.actions)
    try_export(bpy.ops.export_scene.gltf, dict(
        filepath=glb, export_format="GLB", use_selection=True, export_apply=False, export_attributes=True,
        export_animations=has_anim, export_animation_mode="ACTIONS", export_force_sampling=True, export_yup=True,
        export_skins=True, export_image_format="AUTO", export_def_bones=False))

    common.progress(0.3, "Building the strand groom")
    hairs = [build_groom(m, p, base_len) for m in meshes]
    hair_count = 0
    dg = bpy.context.evaluated_depsgraph_get()
    for m in hairs:
        ev = m.evaluated_get(dg)
        try:
            gs = ev.evaluated_geometry()
            if gs.curves:
                hair_count += len(gs.curves.curves)
        except Exception:  # noqa: BLE001
            hair_count = -1
    blend = os.path.join(out_dir, f"{stem}_groom.blend")
    out = {"glb": glb, "blend": blend, "hair_count": hair_count, "base_length_m": base_len, "height_m": height,
           "forward": list(fwd), "meshes": stats, "params": p, "has_animation": has_anim}

    ropts = args.get("render") or {}
    if ropts.get("enabled", True):
        common.progress(0.35, "Setting up the Cycles render")
        darken_skin(meshes, p)
        device = setup_render(lo, hi, fwd, ropts)
        sc = bpy.context.scene
        if arm and arm.animation_data and arm.animation_data.action:
            fr = arm.animation_data.action.frame_range
            sc.frame_set(int(ropts.get("frame") or (fr[0] + (fr[1] - fr[0]) * 0.3)))
        png = os.path.join(out_dir, f"{stem}_fur_render.png")
        sc.render.filepath = png
        common.progress(0.4, f"Rendering strands in Cycles ({device})")
        bpy.ops.render.render(write_still=True)
        out.update({"render": png, "render_device": device})
    bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
    return out


if __name__ == "__main__":
    common.run(main)
