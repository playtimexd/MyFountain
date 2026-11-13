"""Send hub assets to the user's Three.js engine asset library (my-engine, `pnpm assets` -> http://127.0.0.1:4173).
Uses only its HTTP API (`POST /api/assets?filename=&name=&description=&tags=&license=`, raw body); never touches
that repository's files."""
from __future__ import annotations

from pathlib import Path

import httpx

from . import db

ENGINE = "http://127.0.0.1:4173"
SUPPORTED = {".glb", ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".ogg", ".wav"}
MAX_BYTES = 200 * 1024 * 1024
NOT_RUNNING = "my-engine's asset server is not running - start it with `pnpm assets` in D:\\THreejsEngine\\my-engine"


def _client() -> httpx.Client:
    # Host header must be exactly 127.0.0.1:4173 (the engine rejects anything else)
    return httpx.Client(base_url=ENGINE, timeout=60, headers={"Host": "127.0.0.1:4173"})


def engine_status() -> dict:
    try:
        with _client() as c:
            r = c.get("/api/assets", params={"q": "cutlab"})
        return {"running": r.status_code < 500, "status": r.status_code}
    except Exception:
        return {"running": False, "message": NOT_RUNNING}


def _hints(a: dict) -> str:
    st = a.get("stats") or {}
    bits = []
    if st.get("polycount"):
        bits.append(f"{st['polycount']} tris")
    if st.get("rigged"):
        bits.append("rigged")
    if st.get("clips"):
        bits.append("clips: " + ", ".join(st["clips"][:12]))
    if st.get("dimensions_mm"):
        d = st["dimensions_mm"]
        bits.append("size ~" + " x ".join(f"{float(x) / 1000:.2f}" for x in d) + " m")
    elif a["kind"] in ("model", "animation"):
        bits.append("scale unknown - check size after import")
    if st.get("texture_size"):
        bits.append(f"{st['texture_size']}px")
    if st.get("duration"):
        bits.append(f"{float(st['duration']):.1f}s")
    return "; ".join(bits)


def pick_file(a: dict) -> dict | None:
    files = a.get("files") or []
    order = ["main", "map:albedo", "preview"] if a["kind"] in ("material", "texture") else ["main"]
    for role in order:
        for f in files:
            if f["role"] == role and Path(f["path"]).suffix.lower() in SUPPORTED:
                return f
    for f in files:
        if f["role"].startswith("alt:") and Path(f["path"]).suffix.lower() in SUPPORTED:
            return f
    return None


def send(asset_ids: list[str]) -> dict:
    st = engine_status()
    if not st.get("running"):
        return {"ok": False, "message": NOT_RUNNING, "sent": [], "skipped": []}
    sent, skipped = [], []
    with _client() as c:
        for aid in asset_ids:
            a = db.get(aid)
            if not a:
                skipped.append({"id": aid, "reason": "not found"})
                continue
            f = pick_file(a)
            if not f:
                skipped.append({"id": aid, "name": a["name"], "reason": f"no file my-engine accepts ({', '.join(sorted(SUPPORTED))})"})
                continue
            p = Path(f["path"])
            if p.stat().st_size > MAX_BYTES:
                skipped.append({"id": aid, "name": a["name"], "reason": "larger than my-engine's 200 MB limit"})
                continue
            desc = " | ".join(x for x in [a.get("description", "")[:300], _hints(a), a.get("attribution", "")] if x)
            tags = ",".join(dict.fromkeys(["cutlab", a["source"], a["kind"], *[t for t in a.get("tags", []) if "," not in t][:10]]))
            try:
                r = c.post("/api/assets", params={"filename": p.name, "name": a["name"], "description": desc, "tags": tags,
                                                  "license": a["license"]}, content=p.read_bytes(),
                           headers={"Content-Type": "application/octet-stream"})
            except httpx.HTTPError:
                return {"ok": False, "message": NOT_RUNNING, "sent": sent, "skipped": skipped}
            if r.status_code >= 400:
                try:
                    msg = r.json().get("error")
                except Exception:
                    msg = r.text[:200]
                skipped.append({"id": aid, "name": a["name"], "reason": f"my-engine refused: {msg}"})
            else:
                j = r.json()
                sent.append({"id": aid, "name": a["name"], "engine_id": j.get("id"), "file": p.name})
    return {"ok": True, "message": f"Sent {len(sent)} asset(s) to my-engine", "sent": sent, "skipped": skipped}
