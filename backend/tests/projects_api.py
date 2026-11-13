"""Project store API test (routers/projects.py + services/projects.py) against a running engine.
    set CUTLAB_BASE=http://127.0.0.1:8797      (default; start a second engine with CUTLAB_PORT=8797)
    backend\\.venv\\Scripts\\python.exe backend\\tests\\projects_api.py
Creates its own projects (names start with "__test__") and removes them at the end."""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = os.environ.get("CUTLAB_BASE", "http://127.0.0.1:8797")
cli = httpx.Client(base_url=BASE, timeout=60)
OK: list[str] = []
FAIL: list[str] = []
created: list[str] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    (OK if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  -> {detail}"))


# 1x1 png
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d4944415478da63f8cfc0f01f0005000201e2b1a5f60000000049454e44ae426082")


def main() -> int:
    print(f"projects API @ {BASE}")
    r = cli.get("/api/health")
    check("engine reachable", r.status_code == 200, r.text)

    # ---- create (one per kind) + validation
    for kind in ("video", "image", "anim", "model", "segment"):
        r = cli.post("/api/projects", json={"kind": kind, "name": f"__test__ {kind}", "doc": {"hello": kind}, "meta": {"width": 1920}})
        check(f"create {kind}", r.status_code == 200 and r.json()["kind"] == kind, r.text)
        created.append(r.json()["id"])
    vid, img, anim, model, seg = created
    r = cli.post("/api/projects", json={"kind": "spreadsheet", "name": "x"})
    check("unknown kind rejected (400)", r.status_code == 400, r.status_code)

    p = cli.get(f"/api/projects/{vid}").json()
    check("metadata has rev/size/meta", p["rev"] == 1 and p["size"] > 0 and p["meta"]["width"] == 1920, p)

    # ---- save document verbatim (raw body), meta merge, rename via query
    doc = {"version": 1, "name": "x", "tracks": [{"id": "v1", "clips": []}], "unicode": "中文 ✓"}
    raw = json.dumps(doc, ensure_ascii=False).encode()
    r = cli.put(f"/api/projects/{vid}/doc", content=raw, params={"name": "__test__ renamed", "meta": json.dumps({"fps": 30}), "rev": 1},
                headers={"Content-Type": "application/json"})
    check("save doc", r.status_code == 200 and r.json()["rev"] == 2, r.text)
    p = r.json()
    check("meta merged (width kept, fps added)", p["meta"].get("width") == 1920 and p["meta"].get("fps") == 30, p["meta"])
    check("renamed through save", p["name"] == "__test__ renamed", p["name"])
    r = cli.get(f"/api/projects/{vid}/doc")
    check("doc returned byte-for-byte", r.status_code == 200 and r.content == raw, r.content[:80])

    r = cli.put(f"/api/projects/{vid}/doc", content=raw, params={"rev": 1})
    check("stale rev -> 409 conflict", r.status_code == 409, r.status_code)
    r = cli.put(f"/api/projects/{vid}/doc", content=b"not json")
    check("non-JSON body -> 400", r.status_code == 400, r.status_code)
    r = cli.put(f"/api/projects/{vid}/doc", content=raw, params={"meta": "[1]"})
    check("meta must be an object -> 400", r.status_code == 400, r.status_code)

    # ---- patch / tags
    r = cli.patch(f"/api/projects/{img}", json={"name": "__test__ poster", "tags": ["poster", "draft"], "meta": {"w": 1080}})
    check("patch name/tags/meta", r.status_code == 200 and r.json()["tags"] == ["poster", "draft"] and r.json()["meta"]["w"] == 1080, r.text)

    # ---- thumbnail
    r = cli.put(f"/api/projects/{img}/thumb", content=PNG, headers={"Content-Type": "image/png"})
    check("upload thumb", r.status_code == 200 and r.json()["thumb_url"], r.text)
    r = cli.get(r.json()["thumb_url"])
    check("get thumb", r.status_code == 200 and r.content == PNG, r.status_code)
    r = cli.put(f"/api/projects/{img}/thumb", content=b"x", headers={"Content-Type": "text/plain"})
    check("bad thumb type -> 400", r.status_code == 400, r.status_code)

    # ---- list / search / sort by opened
    time.sleep(0.02)
    cli.post(f"/api/projects/{vid}/open")
    lst = cli.get("/api/projects", params={"kind": "video"}).json()
    check("list by kind (opened first)", lst and lst[0]["id"] == vid and all(x["kind"] == "video" for x in lst), [x["name"] for x in lst[:3]])
    lst = cli.get("/api/projects", params={"q": "__test__ poster"}).json()
    check("search by name", [x["id"] for x in lst] == [img], lst)
    lst = cli.get("/api/projects", params={"q": "draft"}).json()
    check("search by tag", img in [x["id"] for x in lst], lst)

    # ---- duplicate keeps document + thumb, records lineage
    r = cli.post(f"/api/projects/{img}/duplicate", json={})
    check("duplicate", r.status_code == 200, r.text)
    dup = r.json()
    created.append(dup["id"])
    check("duplicate lineage + name", dup["parent_id"] == img and dup["name"].endswith("copy"), dup)
    check("duplicate copies doc", cli.get(f"/api/projects/{dup['id']}/doc").json() == {"hello": "image"})
    check("duplicate copies thumb", cli.get(f"/api/projects/{dup['id']}/thumb").content == PNG)

    # ---- unknown / malformed ids
    check("unknown id -> 404", cli.get("/api/projects/0123456789ab").status_code == 404)
    check("malformed id -> 404", cli.get("/api/projects/NOT-AN-ID").status_code == 404)

    # ---- activity: a cheap job (import of a path that does not exist) is persisted with its area
    r = cli.post("/api/media/import", json={"paths": ["Z:/__cutlab_no_such_dir__"], "recursive": False, "copy": False})
    if r.status_code == 200:
        jid = r.json()["id"]
        hist = []
        for _ in range(50):
            hist = cli.get("/api/projects/activity", params={"area": "library", "limit": 20}).json()
            if any(h["id"] == jid for h in hist):
                break
            time.sleep(0.1)
        h = next((h for h in hist if h["id"] == jid), None)
        check("finished job recorded in activity (area=library)", h is not None and h["status"] == "done", hist[:2])
        check("activity delete", cli.delete(f"/api/projects/activity/{jid}").json()["deleted"] == 1)
    else:
        check("media import job started", False, r.text)

    # ---- delete: the default moves the folder to _trash; the test purges its own projects
    r = cli.delete(f"/api/projects/{created[0]}")
    trash = r.json().get("trash") if r.status_code == 200 else None
    check("delete -> moved to trash", bool(trash) and os.path.isdir(trash), r.text)
    if trash:
        import shutil
        shutil.rmtree(trash, ignore_errors=True)
    created.pop(0)
    for pid in list(created):
        r = cli.delete(f"/api/projects/{pid}", params={"purge": True})
        ok = r.status_code == 200 and r.json()["ok"]
        if ok:
            created.remove(pid)
        check(f"delete {pid}", ok and cli.get(f"/api/projects/{pid}").status_code == 404, r.text)
    print(f"\n{len(OK)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for pid in created:  # leave nothing behind on failure
            cli.delete(f"/api/projects/{pid}", params={"purge": True})
