"""Animation in BVH / FBX / GLB(GLTF) / BLEND  ->  motion.npz (CutLab interchange) + stats.

args: {"input": path, "out_npz": path, "result": path, "action"?: name, "armature"?: name, "scale"?: float (override unit guess),
       "fps"?: float, "start"?: int, "end"?: int}
motion.npz: fps, skeleton='custom', joint_names (J,), parents (J,) [extension: -1 = root], joints3d (T,J,3) metres, Y up,
conf (T,J), root (T,3), meta JSON.  A joint is a bone HEAD; every leaf bone also contributes its TAIL as joint '<bone>_end'.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402

import common  # noqa: E402


def main(args):
    common.progress(0.05, "importing")
    path = args["input"]
    fmt = common.import_model(path)
    sc = bpy.context.scene
    arm = None
    acts = list(bpy.data.actions)
    if args.get("armature"):
        arm = bpy.data.objects.get(args["armature"])
    if arm is None:
        arms = common.scene_armatures()
        withact = [a for a in arms if a.animation_data and a.animation_data.action]
        arm = (withact or arms or [None])[0]
    if arm is None:
        raise RuntimeError("No armature found in the file.")
    act = None
    if args.get("action"):
        act = bpy.data.actions.get(args["action"])
    if act is None and arm.animation_data and arm.animation_data.action:
        act = arm.animation_data.action
    if act is None and acts:
        act = max(acts, key=lambda a: a.frame_range[1] - a.frame_range[0])
    if act is None:
        raise RuntimeError("The file has a skeleton but no animation (no action).")
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = act
    f0, f1 = int(round(act.frame_range[0])), int(round(act.frame_range[1]))
    if args.get("start") is not None:
        f0 = max(f0, int(args["start"]))
    if args.get("end"):
        f1 = min(f1, int(args["end"]))
    fps = float(args.get("fps") or (sc.render.fps / (sc.render.fps_base or 1.0)))
    bones = common.bones_topological(arm)
    names = [b.name for b in bones]
    idx = {n: i for i, n in enumerate(names)}
    leaf = [b for b in bones if not b.children]
    jn = list(names) + [b.name + "_end" for b in leaf]
    jparent = [idx[b.parent.name] if b.parent else -1 for b in bones] + [idx[b.name] for b in leaf]
    T = f1 - f0 + 1
    J = len(jn)
    P = np.zeros((T, J, 3))
    for t, f in enumerate(range(f0, f1 + 1)):
        sc.frame_set(f)
        # per frame: Unreal FBX files carry root motion on the 'root' bone, which Blender's importer turns into the armature
        # OBJECT - its animated transform is the character's travel (reading matrix_world once dropped it)
        mw = np.array(arm.matrix_world)
        for i, b in enumerate(bones):
            pb = arm.pose.bones[b.name]
            P[t, i] = (mw @ np.append(np.array(pb.head), 1))[:3]
        for k, b in enumerate(leaf):
            pb = arm.pose.bones[b.name]
            P[t, len(bones) + k] = (mw @ np.append(np.array(pb.tail), 1))[:3]
        if t % 10 == 0:
            common.progress(0.1 + 0.8 * t / max(T, 1), f"frame {t}/{T}")
    notes = []
    # BVH files of unknown up axis: a standing skeleton must be taller than wide.  If it lies along Y, rotate it upright.
    if fmt == "bvh" and not any(k in " ".join(jn).lower() for k in ("paw", "tail", "hoof")):
        e = np.ptp(P[0], axis=0)
        if e[1] > 1.4 * e[2] and e[1] > e[0]:
            ri0 = next((i for i, p in enumerate(jparent) if p < 0), 0)
            pos = np.stack([P[..., 0], -P[..., 2], P[..., 1]], axis=-1)       # +90 deg about X: new z = old y
            neg = np.stack([P[..., 0], P[..., 2], -P[..., 1]], axis=-1)       # -90 deg about X: new z = -old y
            up_score = lambda Q: float((Q[0, :, 2] - Q[0, ri0, 2]).mean())
            P = pos if up_score(pos) >= up_score(neg) else neg
            notes.append("BVH was lying along Y: rotated to Z-up.")
    if fmt == "bvh":
        sk = common.load_local_module("skeletons")
        ri0 = next((i for i, p in enumerate(jparent) if p < 0), 0)
        roles = [sk.parse_name(n) for n in jn]
        heads = [i for i, r in enumerate(roles) if r["role"] in ("head", "neck")]
        feet = [i for i, r in enumerate(roles) if r["role"] in ("end", "toe") and r["limb"] != "front" or (r["role"] == "toe")]
        if heads and feet:
            upright = float(P[0, heads, 2].mean()) > float(P[0, feet, 2].mean())
        else:
            upright = float((P[0, :, 2] - P[0, ri0, 2]).mean()) >= 0
        if not upright:
            P = np.stack([P[..., 0], -P[..., 1], -P[..., 2]], axis=-1)       # upside down: 180 deg about X
            notes.append("BVH was upside down: flipped upright.")
    # unit guess (BVH is usually centimetres).  Body joints only: UE helper bones (center_of_mass, interaction) can sit at a
    # warp target metres away - 16 m in GASP traversal clips, which read as a 16 m character in centimetres
    d0 = np.linalg.norm(P[0] - np.median(P[0], axis=0), axis=1)
    body = d0 <= 4.0 * max(float(np.median(d0)), 1e-9)
    ext = np.ptp(P[0][body], axis=0).max()
    scale = args.get("scale")
    guess = "given"
    if scale is None:
        if ext > 12:
            scale, guess = 0.01, "cm"
        elif ext < 0.05:
            scale, guess = 100.0, "km?"
        else:
            scale, guess = 1.0, "m"
    P = P * float(scale)
    # Blender Z-up -> Y-up  (x, y, z) -> (x, z, -y)
    Y = np.stack([P[..., 0], P[..., 2], -P[..., 1]], axis=-1)
    root_i = next((i for i, p in enumerate(jparent) if p < 0), 0)
    root = Y[:, root_i]
    skeleton = "custom"
    np.savez(args["out_npz"], fps=np.float64(fps), skeleton=np.array(skeleton), joint_names=np.array(jn), parents=np.array(jparent, np.int32),
             joints3d=Y.astype(np.float32), conf=np.ones((T, J), np.float32), root=root.astype(np.float32),
             meta=np.array(json.dumps({"source": os.path.basename(path), "format": fmt, "action": act.name, "units": guess, "scale": float(scale),
                                       "frames": [f0, f1], "camera": "none"})))
    # stats
    xz = root[:, [0, 2]]
    travel = float(np.linalg.norm(np.diff(xz, axis=0), axis=1).sum()) if T > 1 else 0.0
    height = float(np.ptp(Y[0][body, 1]))
    rel = Y - root[:, None, :]
    seam = float(np.linalg.norm(rel[0] - rel[-1], axis=1).mean() / max(height, 1e-6))
    foot_guess = [jn[i] for i in np.argsort(Y[:, :, 1].min(axis=0))[:4]]
    stats = {"frames": T, "fps": fps, "duration": (T - 1) / fps, "joints": J, "height_m": height, "root_travel_m": travel,
             "root_speed_mps": travel / max((T - 1) / fps, 1e-6), "loop_seam_pct_height": 100 * seam, "units": guess,
             "action": act.name, "actions_in_file": [a.name for a in acts], "lowest_joints": foot_guess, "format": fmt, "notes": notes}
    return {"stats": stats, "joint_names": jn, "parents": jparent, "out_npz": args["out_npz"]}


common.run(main)
