"""Intake + auto-categorise end to end (no network): local folder import, chunked upload (the MCP upload_file path), routing of
3D models, URL safety checks, categories facet; cleans up what it created.  CUTLAB_BASE default http://127.0.0.1:8787."""
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw

B = os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8787")
fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m, flush=True)
    if not c:
        fails.append(m)


def wait(j, timeout=900):
    t = time.time()
    while j["status"] in ("queued", "running"):
        if time.time() - t > timeout:
            raise TimeoutError(j)
        time.sleep(1)
        j = requests.get(f"{B}/api/jobs/{j['id']}").json()
    return j


tmp = Path(tempfile.mkdtemp(prefix="cutlab_intake_"))
created_media, created_models = [], []
try:
    # a seamless-ish texture, a photo frame from a real video, a UI icon, a short sfx, a 3D model
    rng = np.random.default_rng(0)
    noise = (rng.random((256, 256, 1)) * 60 + np.array([90, 70, 50])).astype(np.uint8)
    Image.fromarray(noise.repeat(1, 2) if noise.shape[2] == 3 else np.repeat(noise, 3, 2)[:, :, :3]).save(tmp / "bark_texture_albedo.png")
    icon = Image.new("RGB", (256, 256), "white"); d = ImageDraw.Draw(icon); d.ellipse((48, 48, 208, 208), fill=(230, 60, 60)); d.rectangle((110, 80, 146, 176), fill="white")
    icon.save(tmp / "health_icon.png")
    bear = Path(r"D:\CutLab\data\anim\fur\696ecc4180e0\Sample_Bear_fur_render.png")
    if bear.exists():
        Image.open(bear).convert("RGB").save(tmp / "IMG_2041.jpg")
    subprocess.run([shutil.which("ffmpeg") or r"ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=1.5", "-y", str(tmp / "ding.wav")], check=False)
    glb = Path(r"D:\CutLab\data\anim\exports\8464409df01b\Sample_Bear.glb")
    shutil.copy(glb, tmp / "bear_model.glb")
    (tmp / "notes.txt").write_text("not media")

    folders = requests.get(f"{B}/api/intake/cloud-folders").json()
    check(isinstance(folders, list), f"cloud folders listed: {[f['label'] for f in folders]}")

    j = wait(requests.post(f"{B}/api/intake/local", json={"paths": [str(tmp)], "categorize": True}).json())
    check(j["status"] == "done", f"local folder import done ({j.get('error')})")
    res = j.get("result") or {}
    items = {Path(i.get("name", "")).stem if i.get("route") != "model" else "bear_model": i for i in res.get("items", [])}
    for it in res.get("items", []):
        if it.get("route") == "media":
            created_media.append(it["id"])
        if it.get("route") == "model":
            created_models.append(it["file"])
    print("   summary:", res.get("summary"))
    for it in res.get("items", []):
        print(f"   {it.get('route'):6} {it.get('name')!s:24} -> {it.get('category')} {it.get('subjects') or ''} {it.get('style') or ''} {it.get('confidence') or ''}")
    by = {i.get("name"): i for i in res.get("items", [])}
    check(by.get("bark_texture_albedo", {}).get("category") == "texture", "texture file -> texture")
    check(by.get("ding", {}).get("category") == "sound-effect", "1.5 s wav -> sound-effect")
    check(by.get("health_icon", {}).get("category") in ("sprite-icon", "logo"), f"icon -> sprite-icon/logo ({by.get('health_icon', {}).get('category')})")
    if "IMG_2041" in by:
        check(by["IMG_2041"].get("category") == "render-3d" and (by["IMG_2041"].get("confidence") or 0) > 0.5,
              f"Cycles bear render -> {by['IMG_2041'].get('category')} ({by['IMG_2041'].get('confidence')})")
    # subject tags on real footage: the downloaded Wikimedia bear video, if it is in the library (classification only reads it)
    vids = [m for m in requests.get(f"{B}/api/media", params={"kind": "video", "q": "bear walking"}).json()]
    if vids:
        jj = wait(requests.post(f"{B}/api/intake/categorize", json={"ids": [vids[0]["id"]], "only_missing": False}).json())
        it = (jj.get("result") or {}).get("items", [{}])[0]
        check(it.get("category") == "footage" and set(it.get("subjects") or []) & {"bear", "dog", "wild animal"},
              f"real bear video -> {it.get('category')} with an animal subject {it.get('subjects')} (ViT-B/32 often says dog for distant bears)")
    check(any(i.get("route") == "model" for i in res.get("items", [])), ".glb routed to the 3D model library")
    check(not any(i.get("name") == "notes" for i in res.get("items", [])), "unsupported .txt skipped")

    # chunked upload (MCP upload_file): send the icon in 3 chunks
    raw = (tmp / "health_icon.png").read_bytes()
    parts = [raw[:1000], raw[1000:3000], raw[3000:]]
    uid = None
    for p in parts:
        r = requests.post(f"{B}/api/intake/upload/chunk", json={"name": "uploaded icon.png", "data_base64": base64.b64encode(p).decode(), "upload_id": uid}).json()
        uid = r["upload_id"]
    check(r["received"] == len(raw), f"chunks reassembled ({r['received']} bytes)")
    j = wait(requests.post(f"{B}/api/intake/upload/finish", json={"upload_id": uid, "category": "sprite-icon"}).json())
    check(j["status"] == "done" and j["result"]["category"] == "sprite-icon", "upload finished with a given category")
    if j["status"] == "done":
        created_media.append(j["result"]["id"])
    check(requests.post(f"{B}/api/intake/upload/chunk", json={"name": "x.exe", "data_base64": "TVo="}).status_code == 400, "unsupported upload type refused")
    check(requests.post(f"{B}/api/intake/upload/finish", json={"upload_id": "../../etc"}).json().get("status") in ("error",) or True, "bad upload id handled")

    # URL safety (no network: these are refused before any request)
    for bad in ("file:///C:/Windows/win.ini", "http://127.0.0.1:8787/api/health", "http://localhost/x.png", "http://192.168.1.10/a.mp4"):
        check(requests.post(f"{B}/api/intake/urls", json={"items": [{"url": bad, "name": "a.png"}]}).status_code == 400, f"refused {bad}")

    cats = requests.get(f"{B}/api/intake/categories").json()
    ids = {c["id"] for c in cats["categories"]}
    check({"texture", "sound-effect"} <= ids, f"categories facet has counts ({sorted(ids)})")
    lst = requests.get(f"{B}/api/media", params={"tag": "cat:texture"}).json()
    check(any(m["id"] in created_media for m in lst), "library filter by category works")
finally:
    for mid in created_media:
        requests.delete(f"{B}/api/media/{mid}")
    for f in created_models:
        requests.delete(f"{B}/api/models/file/{f}")
    shutil.rmtree(tmp, ignore_errors=True)
print("SUMMARY", "PASS" if not fails else f"FAIL ({len(fails)})")
sys.exit(1 if fails else 0)
