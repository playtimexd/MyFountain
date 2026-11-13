"""Text-to-animation (Kimodo) in the 4-step flow: describe -> generate on UE5 Manny -> edit -> export. Needs the user's Kimodo install."""
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


def wait(j, t=1800):
    t0 = time.time()
    while j["status"] in ("queued", "running"):
        if time.time() - t0 > t:
            raise TimeoutError
        time.sleep(2)
        j = requests.get(f"{B}/api/jobs/{j['id']}").json()
    return j


st = requests.get(f"{B}/api/anim/quick/text/status").json()
check(st["available"] and all(m.startswith("Kimodo-SOMA") for m in st["models"]), f"Kimodo available, commercial checkpoints only {st['models']}")
if not st["available"]:
    sys.exit(1)
rigs = {r["name"]: r for r in requests.get(f"{B}/api/anim/rigs").json()}
manny = rigs.get("UE5 Manny") or next(r for r in rigs.values() if r["kind"] == "humanoid")
fox = next((r for r in rigs.values() if r["kind"] == "quadruped"), None)
if fox:
    check(requests.post(f"{B}/api/anim/quick/generate-text", json={"prompt": "A person waves", "rigId": fox["id"]}).status_code == 400, "quadruped refused for text-to-motion")
check(requests.post(f"{B}/api/anim/quick/generate-text", json={"prompt": "  ", "rigId": manny["id"]}).status_code == 400, "empty prompt refused")

t0 = time.time()
j = wait(requests.post(f"{B}/api/anim/quick/generate-text", json={"prompt": "A person swings a heavy two-handed sword overhead and strikes down.",
                                                                  "rigId": manny["id"], "duration": 3, "seed": 3}).json())
r = j.get("result") or {}
check(j["status"] == "done", f"text -> animation on {manny['name']} in {time.time() - t0:.0f}s ({j.get('error')})")
if j["status"] != "done":
    sys.exit(1)
check(r["clip"]["frames"] >= 80 and r.get("glb_url") and r.get("text", {}).get("prompt"), f"clip {r['clip']['frames']} frames, score {r['clip'].get('score')}, GLB")
check(requests.get(B + r["glb_url"]).content[:4] == b"glTF", "GLB served")
src = requests.get(f"{B}/api/anim/clips/{r['source_clip']['id']}").json()
check("kimodo" in (src.get("tags") or []) and src.get("caption", "").startswith("A person swings"), "source clip tagged kimodo with the prompt as caption")
j = wait(requests.post(f"{B}/api/anim/quick/edit", json={"sourceClipId": r["source_clip"]["id"], "rigId": manny["id"], "instruction": "twice as fast",
                                                          "options": r["options"], "history": r["history"]}).json())
e = j.get("result") or {}
check(j["status"] == "done" and e["clip"]["duration"] < r["clip"]["duration"] * 0.75, f"edit 'twice as fast': {r['clip']['duration']:.2f}s -> {e.get('clip', {}).get('duration', 0):.2f}s")
print("SUMMARY", "PASS" if not fails else f"FAIL ({len(fails)})")
sys.exit(1 if fails else 0)
