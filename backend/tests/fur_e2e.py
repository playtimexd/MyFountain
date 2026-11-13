"""Fur pipeline end to end against a running engine (CUTLAB_BASE, default http://127.0.0.1:8787):
groom job -> fur-ready GLB (attributes, axes, bare parts) + Cycles render + groom .blend + fur.json; shell settings save/clamp; file guard."""
import json
import os
import struct
import sys
import time

import numpy as np
import requests

B = os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8787")
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def glb_arrays(path):
    b = open(path, "rb").read()
    n = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + n])
    binc = b[20 + n + 8:]

    def acc(i):
        a = j["accessors"][i]
        bv = j["bufferViews"][a["bufferView"]]
        k = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[a["type"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return np.frombuffer(binc[off:off + a["count"] * k * 4], dtype=np.float32).reshape(-1, k)
    prims = [p for m in j["meshes"] for p in m["primitives"]]
    return j, prims, acc


srcs = requests.get(f"{B}/api/anim/fur/sources").json()
check(len(srcs) > 0, f"fur sources listed ({len(srcs)})")
src = next((s for s in srcs if s["animated"] and "Sample Bear" in s["name"]), None) or next((s for s in srcs if s["animated"]), srcs[0])
check(requests.post(f"{B}/api/anim/fur", json={"source": "export:nope"}).status_code == 400, "unknown source rejected")
check(requests.post(f"{B}/api/anim/fur", json={"source": src["id"], "render": {"samples": 0}}).status_code == 400, "bad render samples rejected")

t0 = time.time()
j = requests.post(f"{B}/api/anim/fur", json={"source": src["id"], "name": "fur e2e", "render": {"size": [480, 300], "samples": 16}}).json()
while j["status"] in ("queued", "running"):
    time.sleep(1)
    j = requests.get(f"{B}/api/jobs/{j['id']}").json()
check(j["status"] == "done", f"groom job done in {time.time() - t0:.1f}s ({j.get('error')})")
if j["status"] != "done":
    sys.exit(1)
rec = j["result"]
fid = rec["id"]
check(rec["schema"] == "cutlab.fur/1", "fur.json schema")
check((rec["groom"]["hair_count"] or 0) > 50000, f"strand count {rec['groom']['hair_count']}")
for k in ("glb", "render", "blend"):
    r = requests.get(B + rec["urls"][k])
    check(r.status_code == 200 and len(r.content) > 10000, f"{k} served ({len(r.content)} bytes)")

js, prims, acc = glb_arrays(rec["files"]["glb"])
check(all({"_FUR_LEN", "_FUR_DIR", "_FUR_DENS"} <= set(p["attributes"]) for p in prims), "GLB primitives carry _FUR_LEN/_FUR_DIR/_FUR_DENS")
check(bool(js.get("animations")), f"GLB keeps animations {[a.get('name') for a in js.get('animations', [])]}")
dots, lens, dens = [], [], []
for p in prims:
    N, D = acc(p["attributes"]["NORMAL"]), acc(p["attributes"]["_FUR_DIR"])
    dens.append(acc(p["attributes"]["_FUR_DENS"])[:, 0])
    lens.append(acc(p["attributes"]["_FUR_LEN"])[:, 0])
    m = dens[-1] > 0
    dots.append(np.abs((N[m] * D[m]).sum(1)))
dots = np.concatenate(dots)
check(dots.mean() < 0.05, f"comb direction lies in the surface in glTF axes (mean |dir.n| = {dots.mean():.4f})")
dens = np.concatenate(dens)
lens = np.concatenate(lens)
check((dens <= 0).sum() > 0, f"bare vertices (nose/eyes) have no fur ({int((dens <= 0).sum())})")
h = rec["groom"]["height_m"]
check(0.005 * h < np.median(lens) < 0.15 * h, f"median strand length {np.median(lens) * 100:.1f} cm for a {h:.2f} m character")

r = requests.put(f"{B}/api/anim/fur/{fid}/shell", json={"shell": {"shells": 999, "thickness": 5, "rootTint": [0.1, 0.1, 0.1], "bogus": 1}}).json()
check(r["shell"]["shells"] == 64 and r["shell"]["thickness"] == 0.3 and "bogus" not in r["shell"], "shell settings clamped and saved")
check(json.loads(requests.get(B + rec["urls"]["json"]).text)["shell"]["shells"] == 64, "fur.json updated on disk")
tr = requests.get(f"{B}/api/anim/fur/{fid}/file/..%2F..%2Fanim.db")
check(not tr.content.startswith(b"SQLite") and "html" in tr.headers.get("content-type", "") or tr.status_code == 404,
      "file endpoint never serves files outside the groom (traversal falls through to the app page)")
check(requests.get(f"{B}/api/anim/fur/{fid}/file/anim.db").status_code == 404, "unknown file types are 404")
check(any(g["id"] == fid for g in requests.get(f"{B}/api/anim/fur").json()), "groom listed")
check(requests.delete(f"{B}/api/anim/fur/{fid}").status_code == 200 and requests.get(f"{B}/api/anim/fur/{fid}").status_code == 404, "groom deleted")

print("SUMMARY", "PASS" if not fails else f"FAIL ({len(fails)})")
sys.exit(1 if fails else 0)
