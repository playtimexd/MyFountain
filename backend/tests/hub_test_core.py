"""Hub core: licences, keys/redaction, polite HTTP (retries, cache, key-free cache keys), metadata, DB search/facets."""
from __future__ import annotations

import json
import unittest

import httpx

from hub_testutil import DATA, Router, TRI_BIN, db, fresh_db, glb_bytes, gltf_json, net, paths, png_bytes

from app.services.hub import licenses, meta


class LicenseTests(unittest.TestCase):
    def test_from_text(self):
        cases = {
            "Creative Commons 0": "CC0-1.0", "http://creativecommons.org/publicdomain/zero/1.0/": "CC0-1.0", "CC0 1.0": "CC0-1.0",
            "Attribution": "CC-BY-4.0", "http://creativecommons.org/licenses/by/3.0/": "CC-BY-3.0",
            "https://creativecommons.org/licenses/by/4.0/": "CC-BY-4.0", "Attribution NonCommercial": "CC-BY-NC-4.0",
            "https://creativecommons.org/licenses/by-nc/3.0/": "CC-BY-NC-3.0", "CC-BY 4.0": "CC-BY-4.0", "by-sa": "CC-BY-SA-4.0",
            "by-nc-nd": "CC-BY-NC-ND-4.0", "by-nd": "CC-BY-ND-4.0", "OFL": "OFL-1.1", "APACHE2": "Apache-2.0",
            "Sampling+": "custom:sampling-plus", "ed": "custom:unknown", "": "custom:unknown", None: "custom:unknown",
        }
        for src, want in cases.items():
            self.assertEqual(licenses.from_text(src), want, src)

    def test_policy_and_class(self):
        self.assertTrue(licenses.allowed("CC0-1.0", "cc0"))
        self.assertFalse(licenses.allowed("CC-BY-4.0", "cc0"))
        self.assertTrue(licenses.allowed("CC-BY-4.0", "commercial"))
        self.assertTrue(licenses.allowed("OFL-1.1", "commercial"))
        self.assertFalse(licenses.allowed("CC-BY-NC-4.0", "commercial"))
        self.assertFalse(licenses.allowed("CC-BY-ND-4.0", "commercial"))
        self.assertTrue(licenses.allowed("CC-BY-NC-4.0", "any"))
        self.assertFalse(licenses.allowed("custom:unknown", "any"))
        self.assertEqual(licenses.license_class("CC0-1.0"), "commercial-safe")
        self.assertEqual(licenses.license_class("CC-BY-4.0"), "attribution")
        self.assertEqual(licenses.license_class("CC-BY-NC-4.0"), "non-commercial")
        self.assertEqual(licenses.license_class("CC-BY-ND-4.0"), "restricted")
        a = licenses.attribution("Chair", "Bob", "Poly Pizza", "https://poly.pizza/m/x", "CC-BY-4.0")
        self.assertIn("Bob", a)
        self.assertIn("CC BY 4.0", a)
        self.assertIn("https://creativecommons.org/licenses/by/4.0/", a)


class KeyTests(unittest.TestCase):
    def test_keys_never_returned_and_redacted(self):
        st = paths.set_keys({"freesound": "SECRETKEY123", "bogus": "x"})
        self.assertEqual(st["freesound"], True)
        self.assertNotIn("bogus", st)
        self.assertNotIn("SECRETKEY123", json.dumps(st))
        self.assertEqual(paths.get_key("freesound"), "SECRETKEY123")
        self.assertEqual(paths.redact("bad SECRETKEY123 here"), "bad *** here")
        self.assertEqual(paths.redact("https://x/y?key=abcd&b=1"), "https://x/y?key=***&b=1")
        stored = json.loads(paths.KEYS_PATH.read_text())
        self.assertIn("hub_keys", stored)
        paths.set_keys({"freesound": ""})
        self.assertFalse(paths.key_status()["freesound"])


class NetTests(unittest.TestCase):
    def test_retry_on_429_then_ok(self):
        n = {"i": 0}

        def h(req):
            n["i"] += 1
            return httpx.Response(429, headers={"Retry-After": "0"}) if n["i"] < 3 else httpx.Response(200, json={"ok": 1})
        Router().add("https://a.test/", h).install()
        self.assertEqual(net.get_json("https://a.test/x"), {"ok": 1})
        self.assertEqual(n["i"], 3)

    def test_user_agent_and_error_redaction(self):
        paths.set_keys({"google_fonts": "GFKEY999"})
        r = Router().add("https://b.test/", httpx.Response(403, json={"detail": "denied GFKEY999"})).install()
        with self.assertRaises(net.HubHTTPError) as cm:
            net.get_json("https://b.test/api", params={"key": "GFKEY999"})
        self.assertNotIn("GFKEY999", str(cm.exception))
        self.assertEqual(r.calls[0].headers["User-Agent"], "CutLab/0.1 (local asset manager)")
        paths.set_keys({"google_fonts": ""})

    def test_cache_key_ignores_secrets(self):
        r = Router().add("https://c.test/", {"v": 1}).install()
        net.get_json("https://c.test/s", params={"q": "a", "key": "K1"}, cache_ttl=60)
        net.get_json("https://c.test/s", params={"q": "a", "key": "K2"}, cache_ttl=60)
        self.assertEqual(len(r.calls), 1)
        for p in paths.CACHE_DIR.glob("*.json"):
            self.assertNotIn("K1", p.read_text())

    def test_download_cap(self):
        Router().add("https://d.test/", b"x" * 5000).install()
        with self.assertRaises(net.CapExceeded):
            net.download("https://d.test/f", DATA / "cap.bin", max_bytes=100)
        self.assertFalse((DATA / "cap.bin").exists())
        r = net.download("https://d.test/f", DATA / "ok.bin")
        self.assertEqual(r["bytes"], 5000)


class MetaTests(unittest.TestCase):
    def test_glb_stats(self):
        p = DATA / "t.glb"
        p.write_bytes(glb_bytes())
        st = meta.extract(p)
        self.assertEqual(st["polycount"], 1)
        self.assertTrue(st["rigged"])
        self.assertEqual(st["clips"], ["Walk", "Run"])

    def test_gltf_to_glb(self):
        d = DATA / "g"
        d.mkdir(exist_ok=True)
        (d / "tri.bin").write_bytes(TRI_BIN)
        (d / "tex.png").write_bytes(png_bytes())
        (d / "m.gltf").write_text(json.dumps(gltf_json(image_uri="tex.png")))
        out = meta.gltf_to_glb(d / "m.gltf", DATA / "packed.glb")
        j = meta.read_gltf_json(out)
        self.assertNotIn("uri", j["buffers"][0])
        self.assertIn("bufferView", j["images"][0])
        self.assertEqual(j["images"][0]["mimeType"], "image/png")
        self.assertEqual(meta.extract(out)["polycount"], 1)


class DbTests(unittest.TestCase):
    def setUp(self):
        fresh_db("core")

    def rec(self, sid, name, kind="model", lic="CC0-1.0", tags=(), sha="s", **kw):
        p = DATA / f"{sid}.glb"
        p.write_bytes(b"x")
        return db.upsert({"source": "polyhaven", "source_id": sid, "kind": kind, "name": name, "tags": list(tags), "license": lic,
                          "files": [{"role": "main", "path": str(p), "format": "glb", "bytes": 1, "sha256": sha + sid}], **kw})

    def test_upsert_dedupe_and_search(self):
        a = self.rec("chair1", "Wooden Chair", tags=["furniture"], stats={"rigged": False})
        b = self.rec("chair1", "Wooden Chair v2", tags=["furniture"])
        self.assertEqual(a["id"], b["id"])
        self.rec("dog", "Dog", kind="animation", lic="CC-BY-4.0", tags=["animal"], stats={"rigged": True, "clips": ["run"]})
        self.rec("rock", "Mossy Rock", kind="model", lic="CC-BY-NC-4.0")
        r = db.search({"q": "chair"})
        self.assertEqual([i["name"] for i in r["items"]], ["Wooden Chair v2"])
        r = db.search({"license": "attribution"})
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["facets"]["license"]["commercial-safe"], 1)    # facets ignore their own filter
        self.assertEqual(db.search({"rigged": True})["total"], 1)
        self.assertEqual(db.search({"animated": True})["items"][0]["name"], "Dog")
        self.assertEqual(db.search({"format": "glb"})["total"], 3)
        self.assertEqual(db.find_by_sha("sdog")["name"], "Dog")
        st = db.stats()
        self.assertEqual(st["total"], 3)

    def test_vector_ranking(self):
        import numpy as np
        a = self.rec("v1", "Alpha", embedding=np.array([1, 0, 0], np.float32))
        self.rec("v2", "Beta", embedding=np.array([0, 1, 0], np.float32))
        r = db.search({"q": "zzz"}, text_vec=np.array([1, 0, 0], np.float32))
        self.assertEqual(r["items"][0]["id"], a["id"])


if __name__ == "__main__":
    unittest.main()
