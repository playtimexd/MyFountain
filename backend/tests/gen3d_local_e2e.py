"""Local 3D generation with TRELLIS.2 in CutLab's ComfyUI: text -> (Z-Image Turbo) -> 3D, then image -> 3D (draft).
Needs `bash scripts/install_comfyui.sh --3d` and a GPU. Checks the GLB (mesh + textures), the Model library entry and that
no cost confirmation is required."""
import json
import os
import struct
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
            raise TimeoutError(j)
        time.sleep(3)
        j = requests.get(f"{B}/api/jobs/{j['id']}").json()
    return j


def glb_info(raw: bytes) -> dict:
    if raw[:4] != b"glTF":
        return {}
    n = struct.unpack_from("<I", raw, 12)[0]
    g = json.loads(raw[20:20 + n])
    acc = g.get("accessors", [])
    tris = sum(acc[p["indices"]]["count"] // 3 for m in g.get("meshes", []) for p in m["primitives"] if "indices" in p)
    mat = (g.get("materials") or [{}])[0].get("pbrMetallicRoughness", {})
    return {"meshes": len(g.get("meshes", [])), "tris": tris, "images": len(g.get("images", [])),
            "base_color_tex": "baseColorTexture" in mat, "normal_tex": "normalTexture" in (g.get("materials") or [{}])[0],
            "mb": round(len(raw) / 1e6, 1)}


prov = {p["id"]: p for p in requests.get(f"{B}/api/gen3d/providers").json()}
loc = prov.get("local") or {}
check(loc.get("available"), f"local provider available (missing: {loc.get('missing')})")
check(not loc.get("cloud") and not loc.get("needs_key"), "local provider is free and keyless")
dr = requests.post(f"{B}/api/gen3d", json={"prompt": "a wooden treasure chest", "provider": "local", "dry_run": True}).json()
check(dr.get("estimate", {}).get("amount") == 0, f"dry run: free estimate ({dr.get('estimate', {}).get('note')})")

cases = [("text", {"prompt": "A friendly cartoon robot with round head, short legs and big hands", "quality": "standard", "name": "local_robot"})]
only = sys.argv[1:]
img_id = None
for name, body in cases + [("image", None)]:
    if only and name not in only:
        continue
    if name == "image":
        if not img_id:
            imgs = requests.get(f"{B}/api/media", params={"kind": "image", "limit": 50}).json()
            imgs = imgs.get("items", imgs) if isinstance(imgs, dict) else imgs
            img_id = next((m["id"] for m in imgs if m.get("kind") == "image"), None)
        if not img_id:
            check(False, "image: no image in the library")
            continue
        body = {"image_mediaId": img_id, "quality": "draft", "name": "local_from_image"}
    t0 = time.time()
    r = requests.post(f"{B}/api/gen3d", json={**body, "provider": "local"})   # note: no confirm_cost
    check(r.status_code == 200, f"{name}: accepted without cost confirmation (HTTP {r.status_code} {r.text[:200] if r.status_code != 200 else ''})")
    if r.status_code != 200:
        continue
    j = wait(r.json())
    res = j.get("result") or {}
    check(j["status"] == "done", f"{name}: generated in {time.time() - t0:.0f}s ({j.get('error')})")
    if j["status"] != "done":
        continue
    raw = requests.get(B + res["url"]).content
    gi = glb_info(raw)
    print(f"   {name}: {res.get('file')} {gi} info={res.get('info')}", flush=True)
    check(gi.get("meshes", 0) >= 1 and gi.get("tris", 0) > 1000, f"{name}: GLB has a real mesh ({gi.get('tris')} triangles)")
    check(gi.get("base_color_tex"), f"{name}: GLB is textured ({gi.get('images')} images)")
    check(not res.get("rigged"), f"{name}: reported as unrigged")
    if res.get("preview_media"):
        img_id = img_id or res["preview_media"]["id"]
    models = requests.get(f"{B}/api/models").json()
    items = models.get("items", models) if isinstance(models, dict) else models
    check(any(res["name"] in json.dumps(m) for m in items), f"{name}: listed in the Model library")
print("SUMMARY", "PASS" if not fails else f"FAIL ({len(fails)})")
sys.exit(1 if fails else 0)
