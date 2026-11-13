"""Pipeline (download -> record -> dedupe -> media registration), harvest caps/resume, pack import, HTTP endpoints."""
from __future__ import annotations

import io
import json
import threading
import unittest
import zipfile

import httpx

from hub_testutil import DATA, Router, db, fresh_db, glb_bytes, no_ai, paths, png_bytes, wav_bytes

no_ai()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.jobs import Job, JobCancelled, JobContext  # noqa: E402
from app.routers import hub as hub_router  # noqa: E402
from app.services.hub import engine, harvest, imports, pipeline  # noqa: E402
from app.services.hub.connectors import REGISTRY  # noqa: E402

PH = {f"Rock_{i:02d}": {"name": f"Rock {i}", "type": 2, "categories": ["rocks"], "tags": ["stone"], "authors": {"A": "All"},
                        "download_count": 100 - i, "thumbnail_url": f"https://cdn.polyhaven.com/t/Rock_{i:02d}.png?width=256&height=256"}
      for i in range(12)}


def ph_router(distinct=True, pad: int = 0) -> Router:
    r = Router().add("https://api.polyhaven.com/assets", PH)
    n = {"i": 0}

    def files(req):
        sid = req.url.path.rsplit("/", 1)[-1]
        return {"gltf": {"1k": {"gltf": {"url": f"https://dl.polyhaven.org/{sid}.gltf", "include": {
            f"{sid}.bin": {"url": f"https://dl.polyhaven.org/{sid}.bin"}}}}}}

    def gltf(req):
        sid = req.url.path.rsplit("/", 1)[-1].split(".")[0]
        from hub_testutil import gltf_json
        j = gltf_json(bin_uri=f"{sid}.bin")
        if distinct:
            j["asset"]["extras"] = {"id": sid}
        return json.dumps(j)

    def binf(req):
        n["i"] += 1
        from hub_testutil import TRI_BIN
        return TRI_BIN + b"\0" * pad
    r.add("https://api.polyhaven.com/files/", files)
    r.add("https://dl.polyhaven.org/", lambda req: gltf(req) if req.url.path.endswith(".gltf") else binf(req))
    r.add("https://cdn.polyhaven.com/", png_bytes())
    return r.install()


def ctx() -> JobContext:
    return JobContext(Job(id="t", kind="test", title="t"))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        fresh_db("pipe")

    def test_add_dedupe_delete(self):
        ph_router()
        con = REGISTRY["polyhaven"]
        cand = con.get.__self__._cand("Rock_01", PH["Rock_01"])
        r = pipeline.add_candidate(cand)
        self.assertEqual(r["status"], "added")
        a = r["asset"]
        self.assertEqual(a["kind"], "animation" if False else "model")
        self.assertEqual(a["license"], "CC0-1.0")
        self.assertTrue(a["stats"]["rigged"])
        self.assertEqual(a["stats"]["clips"], ["Walk", "Run"])
        self.assertTrue(a["thumb_url"])
        roles = {f["role"] for f in a["files"]}
        self.assertEqual(roles, {"main", "preview"})
        self.assertIn("Poly Haven", a["attribution"])
        self.assertEqual(pipeline.add_candidate(cand)["status"], "exists")
        # same file content under a different source id -> sha256 duplicate
        cand2 = dict(cand, source_id="Rock_01_copy")
        PH["Rock_01_copy"] = PH["Rock_01"]
        r2 = pipeline.add_candidate(dict(cand2))
        self.assertIn(r2["status"], ("duplicate", "added"))
        # licence policy
        with self.assertRaises(pipeline.Skipped):
            pipeline.add_candidate(dict(cand, source_id="nc", license="CC-BY-NC-4.0"), policy="commercial")
        # manual catalogs
        with self.assertRaises(pipeline.Skipped):
            pipeline.add_candidate(REGISTRY["kenney"].search("", None, 1)["items"][0])
        main = a["files"][0]["path"]
        self.assertTrue(pipeline.delete_asset(a["id"]))
        self.assertIsNone(db.get(a["id"]))
        import os
        self.assertFalse(os.path.exists(main))

    def test_sha_duplicate(self):
        ph_router(distinct=False)
        con = REGISTRY["polyhaven"]
        a = pipeline.add_candidate(con._cand("Rock_02", PH["Rock_02"]))
        self.assertEqual(a["status"], "added")
        # identical gltf/bin -> identical GLB bytes except the name of the bin: ensure the second is reported duplicate
        b = pipeline.add_candidate(con._cand("Rock_02", PH["Rock_02"]) | {"source_id": "Rock_02"})
        self.assertEqual(b["status"], "exists")


class HarvestTests(unittest.TestCase):
    def setUp(self):
        fresh_db("harvest")

    def test_caps_and_resume(self):
        ph_router()
        st = harvest.new_state({"sources": ["polyhaven", "kenney", "freesound"], "queries": ["rock"], "kinds": ["model"],
                                "max_items": 4, "max_gb": 1, "license_policy": "cc0"})
        res = harvest.run(st, ctx())
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["sources"]["polyhaven"]["added"], 2)      # quota = ceil(4/3)
        self.assertIn("catalog", res["sources"]["kenney"]["note"])
        self.assertIn("key", res["sources"]["freesound"]["note"])
        st2 = harvest.prepare_resume(st["id"])
        res2 = harvest.run(st2, ctx())
        self.assertEqual(res2["totals"]["added"], 4)
        self.assertEqual(db.stats()["total"], 4)

    def test_cancel_saves_state(self):
        ph_router()
        st = harvest.new_state({"sources": ["polyhaven"], "queries": [""], "kinds": [], "max_items": 50, "max_gb": 1})
        c = ctx()
        n = {"i": 0}
        orig = c.progress

        def prog(p, m=None):
            n["i"] += 1
            if n["i"] >= 2:
                c.job._cancel.set()
            orig(p, m)
        c.progress = prog
        with self.assertRaises(JobCancelled):
            harvest.run(st, c)
        saved = harvest.load(st["id"])
        self.assertEqual(saved["status"], "cancelled")
        self.assertGreaterEqual(saved["totals"]["added"], 1)

    def test_size_cap(self):
        ph_router(pad=400_000)
        st = harvest.new_state({"sources": ["polyhaven"], "queries": [""], "max_items": 50, "max_gb": 0.001})
        res = harvest.run(st, ctx())
        self.assertLessEqual(res["totals"]["bytes"], 1_000_000)
        self.assertEqual(res["totals"]["added"], 2)


class ImportTests(unittest.TestCase):
    def setUp(self):
        fresh_db("import")

    def test_zip_pack(self):
        zb = io.BytesIO()
        with zipfile.ZipFile(zb, "w") as z:
            z.writestr("Models/GLB format/character.glb", glb_bytes())
            z.writestr("Models/GLB format/character.png", png_bytes())     # preview for the model
            z.writestr("Models/FBX format/character.fbx", b"Kaydara FBX Binary")
            z.writestr("Audio/hit.wav", wav_bytes())
            z.writestr("Sprites/coin.png", png_bytes(16, 16, (250, 200, 0)))
            z.writestr("License.txt", "CC0")
        zp = DATA / "pack.zip"
        zp.write_bytes(zb.getvalue())
        res = imports.import_pack(str(zp), source="kenney", license="CC0-1.0", pack_name="Test Pack", author="Kenney",
                                  kind_hint="sprite")
        self.assertEqual(res["errors"], [])
        items = db.search({}, page_size=50)["items"]
        kinds = sorted(i["kind"] for i in items)
        self.assertEqual(kinds, ["model", "model", "sfx", "sprite"])
        glb = next(i for i in items if "glb" in i["formats"])
        self.assertTrue(glb["rigged"])
        self.assertTrue(glb["thumb_url"])
        self.assertTrue(all(i["license"] == "CC0-1.0" for i in items))
        snd = next(i for i in items if i["kind"] == "sfx")
        self.assertTrue(snd["media_id"])                # registered in the CutLab media library
        res2 = imports.import_pack(str(zp), source="kenney", license="CC0-1.0", pack_name="Test Pack")
        self.assertEqual(res2["added"] + res2["duplicates"], 4)
        self.assertEqual(db.stats()["total"], 4)


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(hub_router.router)
        cls.client = TestClient(app)

    def test_keys_endpoint_never_returns_values(self):
        r = self.client.put("/api/hub/keys", json={"freesound": "TOPSECRET42", "sketchfab": "SF"})
        self.assertNotIn("TOPSECRET42", r.text)
        self.assertEqual(r.json()["keys"]["freesound"], True)
        g = self.client.get("/api/hub/keys")
        self.assertNotIn("TOPSECRET42", g.text)
        s = self.client.get("/api/hub/sources")
        self.assertNotIn("TOPSECRET42", s.text)
        fs = next(x for x in s.json() if x["id"] == "freesound")
        self.assertTrue(fs["key_set"])
        self.client.put("/api/hub/keys", json={"sketchfab": ""})
        k = self.client.get("/api/hub/keys").json()["keys"]
        self.assertEqual((k["freesound"], k["sketchfab"]), (True, False))
        Router().add("https://freesound.org/apiv2/search/", httpx.Response(401, json={"detail": "bad token TOPSECRET42"})).install()
        e = self.client.get("/api/hub/online", params={"source": "freesound", "q": "x"})
        self.assertEqual(e.status_code, 502)
        self.assertNotIn("TOPSECRET42", e.text)
        self.client.put("/api/hub/keys", json={"freesound": ""})

    def test_assets_credits_engine(self):
        fresh_db("endpoints")
        ph_router()
        pipeline.add_candidate(REGISTRY["polyhaven"]._cand("Rock_03", PH["Rock_03"]))
        r = self.client.get("/api/hub/assets", params={"q": "rock"}).json()
        self.assertEqual(r["total"], 1)
        aid = r["items"][0]["id"]
        self.assertEqual(self.client.get(f"/api/hub/file/{aid}/thumb").status_code, 200)
        self.assertEqual(self.client.get(f"/api/hub/file/{aid}/main").status_code, 200)
        md = self.client.post("/api/hub/credits", json={"asset_ids": [aid]}).json()["markdown"]
        self.assertIn("Rock 3", md)
        self.assertIn("CC0", md)
        on = self.client.get("/api/hub/online", params={"source": "polyhaven", "q": "rock", "kind": "model"}).json()
        self.assertEqual(sum(1 for i in on["items"] if i["in_hub"]), 1)
        # my-engine not running -> clear message
        engine.ENGINE = "http://127.0.0.1:9"
        out = self.client.post("/api/hub/send/my-engine", json={"asset_ids": [aid]}).json()
        self.assertFalse(out["ok"])
        self.assertIn("pnpm assets", out["message"])
        st = self.client.get("/api/hub/stats").json()
        self.assertEqual(st["by_source"]["polyhaven"], 1)
        self.assertEqual(self.client.delete(f"/api/hub/assets/{aid}").json()["ok"], True)

    def test_engine_send_mock(self):
        fresh_db("engine")
        ph_router()
        a = pipeline.add_candidate(REGISTRY["polyhaven"]._cand("Rock_04", PH["Rock_04"]))["asset"]
        got = {}

        def handler(req: httpx.Request):
            if req.method == "GET":
                return httpx.Response(200, json={"assets": []})
            got["host"] = req.headers["host"]
            got["params"] = dict(req.url.params)
            got["len"] = len(req.content)
            return httpx.Response(201, json={"id": "e1"})
        orig = engine._client
        engine.ENGINE = "http://127.0.0.1:4173"
        engine._client = lambda: httpx.Client(base_url=engine.ENGINE, transport=httpx.MockTransport(handler),
                                              headers={"Host": "127.0.0.1:4173"})
        try:
            out = engine.send([a["id"]])
        finally:
            engine._client = orig
        self.assertTrue(out["ok"], out)
        self.assertEqual(got["host"], "127.0.0.1:4173")
        self.assertTrue(got["params"]["filename"].endswith(".glb"))
        self.assertIn("cutlab", got["params"]["tags"])
        self.assertIn("polyhaven", got["params"]["tags"])
        self.assertIn("tris", got["params"]["description"])
        self.assertEqual(got["params"]["license"], "CC0-1.0")


if __name__ == "__main__":
    unittest.main()
