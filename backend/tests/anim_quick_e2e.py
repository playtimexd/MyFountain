"""4-step animation flow: generate (video + character -> mocap -> retarget -> preview -> GLB), edit by instruction (rules or LLM),
undo/rerun, export. Uses the Wikimedia bear clip (12.4-15.2 s) and the Sample Bear rig. Needs a running engine + GPU."""
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


def wait(j, timeout=1200):
    t = time.time()
    while j["status"] in ("queued", "running"):
        if time.time() - t > timeout:
            raise TimeoutError(j)
        time.sleep(2)
        j = requests.get(f"{B}/api/jobs/{j['id']}").json()
    return j


vid = max((m for m in requests.get(f"{B}/api/media", params={"kind": "video", "q": "Bear walking", "limit": 50}).json() if m["name"].startswith("Juraparc")),
          key=lambda m: m["duration"], default=None)
rig = next((r for r in requests.get(f"{B}/api/anim/rigs").json() if r["name"] == "Sample Bear"), None)
assert vid and rig, "bear video / Sample Bear rig missing"
check(requests.post(f"{B}/api/anim/quick/generate", json={"mediaId": vid["id"], "rigId": rig["id"], "start": 3, "end": 3.2}).status_code == 400, "too-short range refused")

t0 = time.time()
j = wait(requests.post(f"{B}/api/anim/quick/generate", json={"mediaId": vid["id"], "rigId": rig["id"], "start": 12.4, "end": 15.2}).json())
r = j.get("result") or {}
check(j["status"] == "done", f"generate done in {time.time() - t0:.0f}s ({j.get('error')})")
if j["status"] != "done":
    sys.exit(1)
check(r["clip"]["frames"] > 30 and r.get("glb_url"), f"retargeted clip {r['clip']['frames']} frames, score {r['clip'].get('score')}, GLB {r['glb_url']}")
check(requests.get(B + r["glb_url"]).content[:4] == b"glTF", "GLB served for the 3D preview")
check(bool(r.get("media")), "side preview video registered")
d0 = r["clip"]["duration"]

j = wait(requests.post(f"{B}/api/anim/quick/edit", json={"sourceClipId": r["source_clip"]["id"], "rigId": rig["id"], "instruction": "make it 50% slower",
                                                          "options": r["options"], "metrics": r["metrics"], "history": r["history"]}).json())
e = j.get("result") or {}
check(j["status"] == "done", f"edit done ({j.get('error')}) via {e.get('edit', {}).get('planner')}: {e.get('edit', {}).get('explanation')}")
if e:
    # a looped clip keeps one whole cycle, so a slower loop is not necessarily longer: compare the cycle rate instead
    hz0, hz1 = (r.get("metrics") or {}).get("cycle_hz"), (e.get("metrics") or {}).get("cycle_hz")
    slower = e["clip"]["duration"] > d0 * 1.3 or (e["clip"].get("loop") and hz0 and hz1 and hz1 < hz0 * 0.8)
    check(e["options"].get("speed", 1) < 0.9 and slower,
          f"slower: speed {e['options'].get('speed')}, duration {d0:.2f}s -> {e['clip']['duration']:.2f}s, cycle {hz0} -> {hz1} Hz")
    check(len(e["history"]) == 2, "history grows")
    j = wait(requests.post(f"{B}/api/anim/quick/rerun", json={"sourceClipId": r["source_clip"]["id"], "rigId": rig["id"], "options": e["history"][0]["options"], "history": e["history"][:1]}).json())
    u = j.get("result") or {}
    check(j["status"] == "done" and abs(u["clip"]["duration"] - d0) < 0.05, f"undo restores the original timing ({u.get('clip', {}).get('duration')})")
    j = wait(requests.post(f"{B}/api/anim/export", json={"clipIds": [u["clip"]["id"]], "rigId": rig["id"], "format": "fbx", "options": {"preset": "game", "all_actions": False}}).json())
    check(j["status"] == "done" and (j.get("result") or {}).get("url", "").endswith(".fbx"), "step 4: FBX export of the edited clip")
print("SUMMARY", "PASS" if not fails else f"FAIL ({len(fails)})")
sys.exit(1 if fails else 0)
