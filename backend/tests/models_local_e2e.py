"""3D Models API: model library list (rigs, uploads, fur, Asset Hub), local 3D/animation index queries, file serving, and importing a
Mixamo animation from the index into the animation library (then removed again). Needs a running engine and an existing index."""
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


def wait(j, timeout=600):
    t = time.time()
    while j["status"] in ("queued", "running"):
        if time.time() - t > timeout:
            raise TimeoutError(j)
        time.sleep(1)
        j = requests.get(f"{B}/api/jobs/{j['id']}").json()
    return j


lib = requests.get(f"{B}/api/models").json()
kinds = {m["kind"] for m in lib}
check("character" in kinds, f"model library lists characters ({len(lib)} items, kinds {sorted(kinds)})")
check(any(m.get("hub_id") for m in lib), "free Asset Hub models are listed")
for fmt, magic in (("glb", b"glTF"), ("fbx", b"Kaydara FBX")):
    hub = next((m for m in lib if m.get("hub_id") and m.get("format") == fmt), None)
    if hub:
        r = requests.get(B + hub["view_url"])
        check(r.status_code == 200 and r.content[:len(magic)] == magic, f"hub model served as {fmt.upper()} ({hub['name']})")

loc = requests.get(f"{B}/api/models/local", params={"limit": 5}).json()
check(loc["total"] > 0, f"local index has {loc['total']} files; kinds {loc['facets']['kinds']}")
check(loc["facets"]["mixamo"] > 0, f"Mixamo files detected: {loc['facets']['mixamo']}")
glb = requests.get(f"{B}/api/models/local", params={"ext": "glb", "limit": 1}).json()["items"]
if glb:
    r = requests.get(B + glb[0]["view_url"])
    check(r.status_code == 200 and r.content[:4] == b"glTF", "indexed GLB served for preview")
anim = requests.get(f"{B}/api/models/local", params={"kind": "animation", "tag": "mixamo", "q": "walk", "limit": 5}).json()["items"]
check(bool(anim), "search finds Mixamo walk animations")
q2 = requests.get(f"{B}/api/models/local", params={"q": "zzzz_nothing_matches"}).json()
check(q2["total"] == 0, "search with no matches returns 0")
check(requests.get(f"{B}/api/models/local/nope/file").status_code == 404, "unknown local id is 404")

if anim:
    small = sorted(anim, key=lambda a: a["size"])[0]
    j = wait(requests.post(f"{B}/api/models/local/{small['id']}/to-animation").json())
    clip = ((j.get("result") or {}).get("clip") or {})
    check(j["status"] == "done" and clip.get("id"), f"Mixamo FBX imported as an animation clip ({small['name']}: {j.get('error') or clip.get('frames')} frames)")
    if clip.get("id"):
        requests.delete(f"{B}/api/anim/clips/{clip['id']}")
print("SUMMARY", "PASS" if not fails else f"FAIL ({len(fails)})")
sys.exit(1 if fails else 0)
