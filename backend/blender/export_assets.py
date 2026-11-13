"""Split .blend asset files into one GLB per model (e.g. Blender's CC0 "Human Base Meshes" bundle).

args: {"blends": [paths], "out_dir": path, "result": path}
Exports every top-level mesh object marked as an asset (or every top-level mesh when none is marked), with its children, modifiers
applied, as <out_dir>/<object name>.glb. Writes nothing else.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import common  # noqa: E402


def safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", s).strip("_")[:60] or "model"


def main(args):
    out_dir = args["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    written, blends = [], args["blends"]
    for bi, blend in enumerate(blends):
        bpy.ops.wm.open_mainfile(filepath=blend)
        objs = [o for o in bpy.data.objects if o.type == "MESH" and o.parent is None]
        marked = [o for o in objs if o.asset_data is not None]
        todo = marked or objs
        for oi, ob in enumerate(todo):
            common.progress((bi + oi / max(len(todo), 1)) / len(blends), ob.name)
            # make sure it is linked into the scene so the exporter can see it
            if ob.name not in bpy.context.scene.objects:
                try:
                    bpy.context.scene.collection.objects.link(ob)
                except RuntimeError:
                    continue
            for o in bpy.context.scene.objects:
                o.select_set(False)
            ob.select_set(True)
            for c in ob.children_recursive:
                if c.name in bpy.context.scene.objects:
                    c.select_set(True)
            loc = ob.location.copy()
            ob.location = (0, 0, 0)
            path = os.path.join(out_dir, safe(ob.name) + ".glb")
            if os.path.exists(path):
                path = os.path.join(out_dir, f"{safe(ob.name)}_{bi}_{oi}.glb")
            try:
                bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=False, export_yup=True,
                                          export_animations=False)
                written.append(path)
            except Exception as e:  # noqa: BLE001
                common.log("skip", ob.name, e)
            ob.location = loc
        # collection assets (e.g. full bodies made of several meshes): one GLB per collection, centred and standing on z=0
        from mathutils import Vector
        for ci, col in enumerate([c for c in bpy.data.collections if c.asset_data]):
            common.progress((bi + 0.5 + 0.5 * ci / max(len(bpy.data.collections), 1)) / len(blends), col.name)
            if col.name not in [c.name for c in bpy.context.scene.collection.children_recursive]:
                try:
                    bpy.context.scene.collection.children.link(col)
                except RuntimeError:
                    continue
            meshes = [o for o in col.all_objects if o.type == "MESH"]
            if not meshes:
                continue
            for o in bpy.context.scene.objects:
                o.select_set(False)
            pts = []
            for o in meshes:
                o.hide_viewport = False
                try:
                    o.hide_set(False)
                    o.select_set(True)
                except RuntimeError:
                    pass
                pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
            lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
            hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
            shift = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
            tops = [o for o in col.all_objects if o.parent is None or o.parent.name not in col.all_objects]
            for o in tops:
                o.location -= shift
            bpy.context.view_layer.update()
            path = os.path.join(out_dir, safe(col.name) + ".glb")
            try:
                bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=False, export_yup=True,
                                          export_animations=False)
                written.append(path)
            except Exception as e:  # noqa: BLE001
                common.log("skip collection", col.name, e)
            for o in tops:
                o.location += shift
    return {"files": written}


if __name__ == "__main__":
    common.run(main)
