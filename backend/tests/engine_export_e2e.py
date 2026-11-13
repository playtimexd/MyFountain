"""Export to engine end to end against scratch projects in data/test/engines (Godot, Unity, Unreal, three.js): characters with clips,
fur, Asset Hub / local models (with format conversion), media; checks formats, folders, the Unreal import script and safety rules."""
import json
import os
import py_compile
import struct
import sys
import time
from pathlib import Path

import requests

B = os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8787")
T = Path(r"D:\CutLab\data\test\engines")
P = {"godot": T / "GodotTest", "unity": T / "UnityTest", "unreal": T / "UnrealTest", "threejs": T / "ThreeTest"}
fails = []
# start clean: remove earlier exports from the scratch projects (they live under CutLab's own test data)
import shutil  # noqa: E402
for sub in ("GodotTest/assets/cutlab", "UnityTest/Assets/CutLab", "UnrealTest/SourceArt/CutLab", "ThreeTest/public/assets/cutlab", "ThreeTest/src/cutlab"):
    shutil.rmtree(T / sub, ignore_errors=True)


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


def run(asset, engine, name=None, project=None, overwrite=False):
    r = requests.post(f"{B}/api/engine/export", json={"asset": asset, "engine": engine, "project": str(project or P[engine]), "name": name, "overwrite": overwrite})
    if r.status_code != 200:
        return {"http": r.status_code, "detail": r.json().get("detail")}
    j = r.json()
    while j["status"] in ("queued", "running"):
        time.sleep(1)
        j = requests.get(f"{B}/api/jobs/{j['id']}").json()
    return j


def glb_json(p):
    b = Path(p).read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + n]) if b[:4] == b"glTF" else None


def is_fbx(p):
    return Path(p).read_bytes()[:18] == b"Kaydara FBX Binary"


projs = {p["engine"] for p in requests.get(f"{B}/api/engine/projects", params={"root": str(T), "refresh": "true"}).json()}
check(projs == {"godot", "unity", "unreal", "threejs"}, f"detected test projects {sorted(projs)}")

rig = next((r for r in requests.get(f"{B}/api/anim/rigs").json() if r["name"] == "Sample Bear"), None)
assert rig, "Sample Bear rig missing"
ch = {"type": "character", "rig_id": rig["id"]}

j = run(ch, "godot", "Bear")
r = j.get("result") or {}
g = next((f for f in r.get("files", []) if f.endswith(".glb")), None)
check(j.get("status") == "done" and g and Path(g).parent == P["godot"] / "assets" / "cutlab" / "Bear", f"Godot: GLB in assets/cutlab/Bear ({j.get('error')})")
if g:
    gj = glb_json(g)
    check(bool(gj and gj.get("animations") and gj.get("skins")), f"Godot GLB is skinned with {len(gj.get('animations') or [])} animation(s)")
    check(any("res://assets/cutlab/Bear" in n for n in r.get("notes", [])), "Godot note gives the res:// path")

j = run(ch, "unity", "Bear")
r = j.get("result") or {}
f = next((x for x in r.get("files", []) if x.endswith(".fbx")), None)
check(j.get("status") == "done" and f and is_fbx(f) and "Assets" in f, f"Unity: binary FBX in Assets/CutLab ({j.get('error')})")

j = run(ch, "unreal", "Bear")
r = j.get("result") or {}
sc = r.get("unreal_script")
check(j.get("status") == "done" and sc and Path(sc).exists() and "SourceArt" in sc, f"Unreal: FBX + import script in SourceArt/CutLab ({j.get('error')})")
if sc:
    try:
        py_compile.compile(sc, doraise=True); ok = True
    except py_compile.PyCompileError as e:
        ok = False; print(e)
    txt = Path(sc).read_text("utf-8")
    meta = json.loads((Path(sc).parent / "cutlab_export.json").read_text("utf-8"))
    check(ok and "/Game/CutLab/Bear" in txt and "Bear.fbx" in txt, "Unreal import script compiles and targets /Game/CutLab/Bear")
    check(meta.get("skinned") and meta.get("animations"), "Unreal export metadata: skeletal mesh with animations")

j = run(ch, "threejs", "Bear")
r = j.get("result") or {}
check(j.get("status") == "done" and any(x.endswith("Bear.glb") and "public" in x for x in r.get("files", [])), "three.js: GLB in public/assets/cutlab")

# no overwrite by default -> numbered sibling
j = run(ch, "godot", "Bear")
check((j.get("result") or {}).get("name") == "Bear_2", f"second export does not overwrite (got {(j.get('result') or {}).get('name')})")

# fur -> three.js copies shellFur.ts
grooms = requests.get(f"{B}/api/anim/fur").json()
if grooms:
    j = run({"type": "fur", "groom_id": grooms[0]["id"]}, "threejs", "BearFur")
    r = j.get("result") or {}
    check(j.get("status") == "done" and (P["threejs"] / "src" / "cutlab" / "shellFur.ts").exists() and any(x.endswith(".fur.json") for x in r.get("files", [])),
          "three.js fur: GLB + fur.json + src/cutlab/shellFur.ts")
    gj = glb_json(next(x for x in r["files"] if x.endswith(".glb")))
    check(any("_FUR_LEN" in p["attributes"] for m in gj["meshes"] for p in m["primitives"]), "fur GLB keeps the _FUR_* attributes")

# Asset Hub model GLB -> Unity converts to FBX, with CREDITS
hub = next((m for m in requests.get(f"{B}/api/models").json() if m.get("hub_id")), None)
if hub:
    j = run({"type": "model", "ref": f"hub:{hub['hub_id']}"}, "unity")
    r = j.get("result") or {}
    f = next((x for x in r.get("files", []) if x.endswith(".fbx")), None)
    check(j.get("status") == "done" and f and is_fbx(f) and any(x.endswith("CREDITS.txt") for x in r["files"]), f"Unity: hub GLB converted to FBX + CREDITS ({hub['name']}; {j.get('error')})")

# local Mixamo FBX animation -> Godot converts to GLB with the animation
mx = requests.get(f"{B}/api/models/local", params={"kind": "animation", "tag": "mixamo", "q": "walk", "limit": 20}).json()["items"]
if mx:
    a = sorted(mx, key=lambda x: x["size"])[0]
    j = run({"type": "model", "ref": f"local:{a['id']}"}, "godot")
    r = j.get("result") or {}
    g = next((x for x in r.get("files", []) if x.endswith(".glb")), None)
    gj = glb_json(g) if g else None
    check(j.get("status") == "done" and gj and gj.get("animations"), f"Godot: Mixamo FBX converted to GLB with animation ({a['name']}; {j.get('error')})")

# media -> Unreal
img = (requests.get(f"{B}/api/media", params={"kind": "image", "limit": 1}).json() or [None])[0]
if img:
    j = run({"type": "media", "media_id": img["id"]}, "unreal")
    r = j.get("result") or {}
    check(j.get("status") == "done" and r.get("unreal_script") and len(r.get("files", [])) == 1, "Unreal: image copied + import script")

# safety
check(run(ch, "unity", project=P["godot"]).get("http") == 400, "exporting to the wrong engine type is refused")
check(run({"type": "nope"}, "godot").get("http") == 400, "unknown asset type refused")
print("SUMMARY", "PASS" if not fails else f"FAIL ({len(fails)})")
sys.exit(1 if fails else 0)
