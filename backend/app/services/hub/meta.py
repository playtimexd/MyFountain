"""Metadata extraction: glTF/GLB (meshes, skins, animations -> polycount, rigged, clip names), audio (ffprobe),
images (Pillow), BVH (frames, joints), plus a pure-Python glTF(+external files) -> GLB packer."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import struct
import subprocess
import sys
from pathlib import Path

from ... import config

MODEL_EXT = {".glb", ".gltf", ".fbx", ".obj", ".blend", ".dae", ".3ds", ".stl", ".ply", ".usdz", ".usd", ".usdc"}
ANIM_EXT = {".bvh", ".amc"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tga", ".tif", ".tiff"}
HDR_EXT = {".hdr", ".exr"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2"}
_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_entry(p: Path, role: str, resolution: str | None = None) -> dict:
    return {"role": role, "path": str(p), "format": p.suffix.lower().lstrip("."), "bytes": p.stat().st_size,
            "sha256": sha256_file(p), "resolution": resolution}


# ---------------------------------------------------------------- glTF
def read_gltf_json(p: str | Path) -> dict | None:
    p = Path(p)
    try:
        if p.suffix.lower() == ".gltf":
            return json.loads(p.read_text("utf-8"))
        with open(p, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b"glTF":
                return None
            clen, ctype = struct.unpack("<II", f.read(8))
            if ctype != 0x4E4F534A:  # 'JSON'
                return None
            return json.loads(f.read(clen).decode("utf-8"))
    except Exception:
        return None


def gltf_stats(j: dict) -> dict:
    acc = j.get("accessors") or []
    tris = 0
    for m in j.get("meshes") or []:
        for prim in m.get("primitives") or []:
            mode = prim.get("mode", 4)
            if "indices" in prim and prim["indices"] < len(acc):
                n = acc[prim["indices"]].get("count", 0)
            else:
                pos = (prim.get("attributes") or {}).get("POSITION")
                n = acc[pos].get("count", 0) if pos is not None and pos < len(acc) else 0
            tris += n // 3 if mode == 4 else max(n - 2, 0) if mode in (5, 6) else 0
    anims = j.get("animations") or []
    clips = [a.get("name") or f"clip_{i}" for i, a in enumerate(anims)]
    out = {"polycount": tris, "rigged": bool(j.get("skins")), "clips": clips, "meshes": len(j.get("meshes") or []),
           "materials": len(j.get("materials") or []), "joints": sum(len(s.get("joints") or []) for s in j.get("skins") or [])}
    gen = (j.get("asset") or {}).get("generator")
    if gen:
        out["generator"] = gen
    return out


def gltf_to_glb(gltf_path: Path, out_path: Path) -> Path:
    """Pack a .gltf with external .bin/images (relative URIs) into a single self-contained .glb."""
    base = gltf_path.parent
    j = json.loads(gltf_path.read_text("utf-8"))
    blob = bytearray()
    views = j.setdefault("bufferViews", [])

    def add(data: bytes) -> tuple[int, int]:
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(data)
        return off, len(data)

    def load(uri: str) -> bytes:
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        from urllib.parse import unquote
        return (base / unquote(uri)).read_bytes()

    buf_offsets = []
    for b in j.get("buffers") or []:
        data = load(b["uri"]) if b.get("uri") else b""
        off, _ = add(data)
        buf_offsets.append(off)
    for v in views:
        v["byteOffset"] = v.get("byteOffset", 0) + buf_offsets[v.get("buffer", 0)]
        v["buffer"] = 0
    for img in j.get("images") or []:
        uri = img.get("uri")
        if not uri:
            continue
        data = load(uri)
        off, ln = add(data)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": ln})
        img["bufferView"] = len(views) - 1
        img["mimeType"] = img.get("mimeType") or (mimetypes.guess_type(uri)[0] or "image/png")
        img.pop("uri", None)
    while len(blob) % 4:
        blob.append(0)
    j["buffers"] = [{"byteLength": len(blob)}]
    js = json.dumps(j, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    with open(out_path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A))
        f.write(js)
        f.write(struct.pack("<II", len(blob), 0x004E4942))
        f.write(blob)
    return out_path


# ---------------------------------------------------------------- audio / image / bvh
def audio_info(p: str | Path) -> dict:
    try:
        out = subprocess.run([config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, **_NOWIN)
        j = json.loads(out.stdout or "{}")
    except Exception:
        return {}
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not a:
        return {}
    dur = float(j.get("format", {}).get("duration") or a.get("duration") or 0)
    return {"duration": round(dur, 3), "channels": int(a.get("channels") or 0), "sample_rate": int(a.get("sample_rate") or 0),
            "codec": a.get("codec_name")}


def image_info(p: str | Path) -> dict:
    try:
        from PIL import Image
        with Image.open(p) as im:
            return {"width": im.width, "height": im.height, "texture_size": max(im.width, im.height), "mode": im.mode}
    except Exception:
        return {}


def bvh_info(p: str | Path) -> dict:
    try:
        joints, frames, ft = 0, 0, 0.0
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith(("ROOT", "JOINT")):
                    joints += 1
                elif s.startswith("Frames:"):
                    frames = int(s.split()[1])
                elif s.startswith("Frame Time:"):
                    ft = float(s.split()[2])
                    break
        return {"joints": joints, "frames": frames, "fps": round(1 / ft, 2) if ft else 0, "duration": round(frames * ft, 3),
                "rigged": True, "clips": [Path(p).stem]}
    except Exception:
        return {}


def extract(p: str | Path) -> dict:
    """Best-effort stats for any supported file."""
    p = Path(p)
    e = p.suffix.lower()
    if e in (".glb", ".gltf"):
        j = read_gltf_json(p)
        return gltf_stats(j) if j else {}
    if e in AUDIO_EXT or e in VIDEO_EXT:
        return audio_info(p) if e in AUDIO_EXT else {}
    if e in IMAGE_EXT:
        return image_info(p)
    if e == ".bvh":
        return bvh_info(p)
    return {}


def kind_for_file(p: Path, hint: str | None = None) -> str | None:
    """Default asset kind for a file inside an imported pack (None = skip)."""
    e = p.suffix.lower()
    if e in MODEL_EXT:
        if hint == "animation":
            return "animation"
        if e in (".glb", ".gltf"):
            st = extract(p)
            if st.get("clips") and not st.get("meshes"):
                return "animation"
        return "model"
    if e in ANIM_EXT:
        return "animation"
    if e in HDR_EXT:
        return "hdri"
    if e in IMAGE_EXT:
        return hint if hint in ("sprite", "texture", "image") else "image"
    if e in AUDIO_EXT:
        if hint in ("sfx", "music", "voice"):
            return hint
        d = audio_info(p).get("duration") or 0
        return "music" if d >= 20 else "sfx"
    if e in VIDEO_EXT:
        return "video"
    if e in FONT_EXT:
        return "font"
    return None
