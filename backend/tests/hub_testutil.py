"""Shared helpers for hub tests: isolated data dir, mock HTTP router, tiny glTF/GLB/PNG/WAV builders.
Run all hub tests:  cd D:\\CutLab\\backend && .venv\\Scripts\\python.exe -m unittest discover -s tests -p "hub_test_*.py" -v"""
from __future__ import annotations

import io
import json
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="cutlab_hubtest_")
os.environ["CUTLAB_DATA"] = _TMP          # must happen before any `app` import
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.services.hub import ai, db, net, paths  # noqa: E402

DATA = Path(_TMP)


def no_ai():
    ai.clip_image = lambda p: None
    ai.clip_text = lambda q: None
    ai.text_embed = lambda texts, prefix="": None
    ai.zero_shot_tags = lambda v, **k: []


class Router:
    """httpx.MockTransport handler: register (method, url-prefix) -> callable(request) or static response."""
    def __init__(self):
        self.routes: list[tuple[str, str, object]] = []
        self.calls: list[httpx.Request] = []

    def add(self, prefix: str, resp, method: str = "GET"):
        self.routes.insert(0, (method, prefix, resp))
        return self

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        url = str(req.url)
        for m, prefix, resp in self.routes:
            if req.method == m and url.startswith(prefix):
                r = resp(req) if callable(resp) else resp
                if isinstance(r, httpx.Response):
                    return r
                if isinstance(r, (bytes, bytearray)):
                    return httpx.Response(200, content=bytes(r))
                if isinstance(r, str):
                    return httpx.Response(200, text=r)
                return httpx.Response(200, json=r)
        return httpx.Response(404, json={"detail": "not mocked: " + url})

    def install(self):
        for p in paths.CACHE_DIR.glob("*"):     # cached API pages must not leak between tests
            p.unlink(missing_ok=True)
        net.set_transport(httpx.MockTransport(self), no_sleep=True)
        return self


def fresh_db(name: str):
    import uuid
    db.reset_for_tests(DATA / "hub" / f"{name}_{uuid.uuid4().hex[:6]}.db")


def png_bytes(w=8, h=8, color=(200, 50, 50)) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


def wav_bytes(seconds=0.5, rate=8000) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(seconds * rate))
    return b.getvalue()


def gltf_json(with_skin=True, with_anim=True, bin_uri="tri.bin", image_uri=None) -> dict:
    j = {"asset": {"version": "2.0"}, "buffers": [{"uri": bin_uri, "byteLength": 36}],
         "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}],
         "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}], "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}]}
    if with_skin:
        j["skins"] = [{"joints": [0]}]
    if with_anim:
        j["animations"] = [{"name": "Walk", "channels": [], "samplers": []}, {"name": "Run", "channels": [], "samplers": []}]
    if image_uri:
        j["images"] = [{"uri": image_uri}]
        j["textures"] = [{"source": 0}]
    return j


TRI_BIN = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)


def glb_bytes(**kw) -> bytes:
    j = gltf_json(bin_uri=None, **kw)
    j["buffers"] = [{"byteLength": 36}]
    js = json.dumps(j).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(TRI_BIN)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(TRI_BIN), 0x004E4942) + TRI_BIN)


__all__ = ["DATA", "Router", "fresh_db", "png_bytes", "wav_bytes", "gltf_json", "glb_bytes", "TRI_BIN", "no_ai", "paths", "db", "net"]
