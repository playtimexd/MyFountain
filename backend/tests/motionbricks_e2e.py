"""MotionBricks transitions / loops: offline join checks on Kimodo SOMA clips, then the API on a humanoid character.

Needs the MotionBricks model in data/models/motionbricks/soma and at least two Kimodo (SOMA) source clips in the library.
Usage: python tests/motionbricks_e2e.py [base_url]
"""
import os
import sys
import tempfile
import time

import numpy as np
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.anim import db, motionbricks as mb  # noqa: E402

B = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8787")
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


def wait(j, t=1800):
    t0 = time.time()
    while j["status"] in ("queued", "running"):
        if time.time() - t0 > t:
            raise TimeoutError
        time.sleep(2)
        j = requests.get(f"{B}/api/jobs/{j['id']}").json()
    return j


st = mb.status()
check(st["available"], f"model available ({st['model']}, {st['licence']})")
if not st["available"]:
    print("SUMMARY FAIL (model missing)")
    sys.exit(1)

kimodo = [c for c in db.list_clips() if not c.get("rig_id") and ((c.get("source") or {}).get("path") or "").lower().endswith(".bvh")
          and "kimodo" in ((c.get("source") or {}).get("path") or "").lower()]
check(len(kimodo) >= 2, f"{len(kimodo)} Kimodo source clips to work with")
a, b = kimodo[0], kimodo[1]
pa, pb = a["source"]["path"], b["source"]["path"]


def speeds(path):
    m = mb.read_bvh(path)
    p, _ = mb.fk(mb.resample(m))
    return np.linalg.norm(np.diff(p, axis=0), axis=2).max(1) * mb.FPS


with tempfile.TemporaryDirectory() as d:
    r = mb.join(pa, pb, os.path.join(d, "t.bvh"))
    v = speeds(r["path"])
    va, vb = speeds(pa), speeds(pb)
    lim = 1.5 * max(va.max(), vb.max(), 1.0)
    na, nt = r["a_frames"], r["transition_frames"]
    check(r["frames"] == na + nt + r["b_frames"] and 16 <= nt <= 56, f"join: {na} + {nt} generated + {r['b_frames']} frames ({r['tokens']} tokens)")
    check(v[na - 2:na + 1].max() < lim and v[na + nt - 2:na + nt + 1].max() < lim,
          f"seams continuous (max joint speed {v[na - 2:na + 1].max():.2f} / {v[na + nt - 2:na + nt + 1].max():.2f} m/s, limit {lim:.2f})")
    check(v[na:na + nt].max() < lim, f"transition speed {v[na:na + nt].max():.2f} m/s within the sources' range ({lim:.2f})")
    m = mb.read_bvh(r["path"])
    p, _ = mb.fk(m)
    hips = m.names.index("Hips")
    bones = [j for j, par in enumerate(m.parents) if par >= 0 and j != hips]     # Hips is placed by its position channel
    lens = np.linalg.norm(p[:, bones] - p[:, [m.parents[j] for j in bones]], axis=2)
    check(np.ptp(lens, axis=0).max() < 1e-4, "bone lengths unchanged")
    r2 = mb.join(pa, pa, os.path.join(d, "l.bvh"), loop=True)
    v2 = speeds(r2["path"])
    check(r2["b_frames"] == 0 and v2.max() < lim, f"loop: {r2['transition_frames']} generated frames back to the start, max speed {v2.max():.2f} m/s")

rig = next((x for x in requests.get(f"{B}/api/anim/rigs").json() if x.get("kind") == "humanoid"), None)
check(rig is not None, "humanoid character available")
j = requests.post(f"{B}/api/anim/quick/transition", json={"clipA": a["id"], "loop": True, "rigId": rig["id"]}).json()
j = wait(j)
res = j.get("result") or {}
check(j["status"] == "done", f"API loop on {rig['name']}: {j.get('error')}")
if res:
    check(res["clip"]["loop"] and res.get("glb_url") and (res["clip"].get("score") or 0) >= 80,
          f"looped clip {res['clip']['id']} score {res['clip'].get('score')} GLB {res.get('glb_url')}")
bad = requests.post(f"{B}/api/anim/quick/transition", json={"clipA": a["id"]})
check(bad.status_code == 400, "clipB or loop required")
print("SUMMARY " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
sys.exit(1 if fails else 0)
