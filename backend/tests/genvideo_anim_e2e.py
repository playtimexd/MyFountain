"""Local ComfyUI (Wan 2.2) reference video -> motion capture -> retarget (+ auto-tune) for a bear and for UE5 Manny.
Needs CutLab's ComfyUI (scripts/install_comfyui.sh) and a GPU. Prints timings, scores and the three-view preview paths.
CUTLAB_REUSE="bear=<mediaId>,manny=<mediaId>" skips generation and captures existing videos (compare pose models).
CUTLAB_POSE=huge|large|base forces a ViTPose++ size (default: the most accurate installed)."""
import os
import sys
import time

import requests

B = os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8787")
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


def wait(j, t=3600):
    t0 = time.time()
    while j["status"] in ("queued", "running"):
        if time.time() - t0 > t:
            raise TimeoutError(j)
        time.sleep(3)
        j = requests.get(f"{B}/api/jobs/{j['id']}").json()
    return j


rigs = {r["name"]: r for r in requests.get(f"{B}/api/anim/rigs").json()}
cases = [
    ("bear", "A large brown bear walking slowly from left to right across a flat grassy field", rigs.get("Sample Bear")),
    ("manny", "A man in plain grey clothes walking forward at a relaxed pace across an empty studio floor", rigs.get("UE5 Manny")),
]
only = sys.argv[1:] or [c[0] for c in cases]
reuse = dict(kv.split("=", 1) for kv in os.environ.get("CUTLAB_REUSE", "").split(",") if "=" in kv)
pose = os.environ.get("CUTLAB_POSE") or None
for name, prompt, rig in cases:
    if name not in only:
        continue
    if not rig:
        check(False, f"{name}: character missing")
        continue
    t0 = time.time()
    if name in reuse:
        media = {"id": reuse[name], "path": "(reused)"}
        print(f"   {name}: reusing media {media['id']}")
    else:
        media = None
    j = {"status": "done", "result": {"media": media}} if media else wait(requests.post(f"{B}/api/videogen", json={"prompt": prompt, "provider": "local", "duration": 5, "aspect": "16:9",
                                                      "capture_friendly": True}).json())
    r = j.get("result") or {}
    media = r.get("media") or {}
    check(j["status"] == "done" and media.get("id"), f"{name}: local video generated in {time.time() - t0:.0f}s ({j.get('error')}) -> {media.get('path')}")
    if not media.get("id"):
        continue
    t1 = time.time()
    j = wait(requests.post(f"{B}/api/anim/quick/generate", json={"mediaId": media["id"], "rigId": rig["id"],
                                                                         "options": {"pose_variant": pose}}).json())
    g = j.get("result") or {}
    check(j["status"] == "done", f"{name}: motion capture + retarget in {time.time() - t1:.0f}s ({j.get('error')}), score {g.get('clip', {}).get('score')}")
    if j["status"] != "done":
        continue
    best = g
    if (g["clip"].get("score") or 0) < 90:
        j = wait(requests.post(f"{B}/api/anim/quick/autotune", json={"sourceClipId": g["source_clip"]["id"], "rigId": rig["id"], "options": g["options"],
                                                                      "target": 90, "max_tries": 8, "history": g["history"]}).json())
        a = j.get("result") or {}
        if j["status"] == "done":
            best = a
            at = a.get("autotune") or {}
            print(f"   auto-tune: {[ (t['change'], t['score']) for t in at.get('tries', [])]} remaining {at.get('remaining')}")
    sc = best["clip"].get("score")
    m = best.get("metrics") or {}
    print(f"   {name}: final score {sc}, back lean {m.get('torso_back_lean_p90_deg')}, knees {m.get('knee_backward_p90_pct_height')}, "
          f"foot slide {m.get('foot_slide_pct_height_per_s')}, preview {(best.get('media') or {}).get('path')}, glb {best.get('glb_url')}", flush=True)
    check(sc is not None, f"{name}: final score {sc}")
print("SUMMARY", "PASS" if not fails else f"FAIL ({len(fails)})")
sys.exit(1 if fails else 0)
