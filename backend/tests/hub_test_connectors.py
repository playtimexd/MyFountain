"""Connector tests with a mock transport: search parsing, pagination, licence mapping, downloads, key handling."""
from __future__ import annotations

import io
import json
import unittest
import zipfile

import httpx

from hub_testutil import DATA, Router, TRI_BIN, glb_bytes, gltf_json, paths, png_bytes, wav_bytes

from app.services.hub.connectors import REGISTRY, ConnectorError, DLContext, NeedsKey
from app.services.hub.connectors.googlefonts import parse_metadata_pb
from app.services.hub import meta


def dest(name):
    d = DATA / "dl" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


PH_ASSETS = {f"Chair_{i:02d}": {"name": f"Chair {i}", "type": 2, "categories": ["furniture"], "tags": ["chair", "wood"],
                                "authors": {"Kirill": "All"}, "polycount": 1000 + i, "download_count": i,
                                "thumbnail_url": f"https://cdn.polyhaven.com/asset_img/thumbs/Chair_{i:02d}.png?width=256&height=256"} for i in range(30)}
PH_ASSETS["Rock_01"] = {"name": "Rock", "type": 2, "categories": ["rocks"], "tags": ["stone"], "authors": {"A": "All"}, "download_count": 999}


class PolyHavenTests(unittest.TestCase):
    def setUp(self):
        self.r = Router().add("https://api.polyhaven.com/assets", PH_ASSETS).install()
        self.c = REGISTRY["polyhaven"]

    def test_search_pagination_and_license(self):
        p1 = self.c.search("chair", "model", 1)
        self.assertEqual(p1["total"], 30)
        self.assertTrue(p1["has_more"])
        self.assertEqual(len(p1["items"]), 24)
        self.assertEqual(p1["items"][0]["source_id"], "Chair_29")       # sorted by downloads
        it = p1["items"][0]
        self.assertEqual(it["license"], "CC0-1.0")
        self.assertTrue(it["commercial_ok"])
        self.assertIn("width=512", it["preview_url"])
        p2 = self.c.search("chair", "model", 2)
        self.assertEqual(len(p2["items"]), 6)
        self.assertFalse(p2["has_more"])
        self.assertEqual(self.c.search("x", "sfx", 1)["items"], [])
        self.assertEqual(self.r.calls[0].url.params["t"], "models")

    def test_download_model_packs_glb(self):
        files = {"gltf": {"1k": {"gltf": {"url": "https://dl.polyhaven.org/m/Chair_01_1k.gltf", "size": 10, "include": {
            "Chair_01.bin": {"url": "https://dl.polyhaven.org/m/4k/Chair_01.bin"},
            "textures/c_diff_1k.png": {"url": "https://dl.polyhaven.org/m/c_diff_1k.png"},
            "../../evil.txt": {"url": "https://dl.polyhaven.org/evil"}}}}}}
        j = gltf_json(bin_uri="Chair_01.bin", image_uri="textures/c_diff_1k.png")
        self.r.add("https://api.polyhaven.com/files/Chair_01", files)
        self.r.add("https://dl.polyhaven.org/m/Chair_01_1k.gltf", json.dumps(j))
        self.r.add("https://dl.polyhaven.org/m/4k/Chair_01.bin", TRI_BIN)
        self.r.add("https://dl.polyhaven.org/m/c_diff_1k.png", png_bytes())
        self.r.add("https://dl.polyhaven.org/evil", b"nope")
        cand = self.c._cand("Chair_01", PH_ASSETS["Chair_01"])
        d = dest("ph_model")
        got = self.c.download("Chair_01", d, DLContext(variant={"texture_res": "1k"}), cand)
        main = got.files[0][0]
        self.assertEqual(main.suffix, ".glb")
        self.assertEqual(meta.extract(main)["polycount"], 1)
        self.assertFalse((d / "_gltf").exists())
        self.assertFalse((DATA / "dl" / "evil.txt").exists())

    def test_download_texture_maps_and_res_fallback(self):
        files = {"Diffuse": {"2k": {"jpg": {"url": "https://dl.polyhaven.org/t/d_2k.jpg"}}},
                 "nor_gl": {"2k": {"jpg": {"url": "https://dl.polyhaven.org/t/n_2k.jpg"}}},
                 "nor_dx": {"2k": {"jpg": {"url": "https://dl.polyhaven.org/t/ndx_2k.jpg"}}},
                 "blend": {"2k": {"blend": {"url": "https://dl.polyhaven.org/t/x.blend"}}}}
        self.r.add("https://api.polyhaven.com/files/Bricks", files).add("https://dl.polyhaven.org/t/", png_bytes())
        cand = self.c.candidate(source_id="Bricks", kind="material", name="Bricks")
        got = self.c.download("Bricks", dest("ph_tex"), DLContext(variant={"texture_res": "1k"}), cand)
        roles = sorted(r for _, r, _ in got.files)
        self.assertEqual(roles, ["main", "map:albedo", "map:normal"])
        self.assertEqual(got.files[0][2], "2k")


class AmbientCGTests(unittest.TestCase):
    def test_search_and_zip_download(self):
        api = {"totalResults": 50, "nextPageHttp": "https://ambientcg.com/api/v3/assets?offset=24", "assets": [
            {"id": "Planks040", "title": "Planks 040", "type": "material", "tags": ["wood"],
             "thumbnails": {"256-PNG": "https://acg-media.struffelproductions.com/t/Planks040.png"},
             "downloads": [{"attributes": "1K-JPG", "extension": "zip", "url": "https://ambientcg.com/get?file=Planks040_1K-JPG.zip", "size": 10},
                           {"attributes": "2K-JPG", "extension": "zip", "url": "https://ambientcg.com/get?file=Planks040_2K-JPG.zip", "size": 20}]}]}
        zb = io.BytesIO()
        with zipfile.ZipFile(zb, "w") as z:
            for n in ("Planks040_1K-JPG_Color.jpg", "Planks040_1K-JPG_NormalGL.jpg", "Planks040_1K-JPG_NormalDX.jpg",
                      "Planks040_1K-JPG_Roughness.jpg", "Planks040.mtlx", "../escape.jpg"):
                z.writestr(n, png_bytes())
        r = Router().add("https://ambientcg.com/api/v3/assets", api).add("https://ambientcg.com/get?file=Planks040_1K", zb.getvalue()).install()
        c = REGISTRY["ambientcg"]
        res = c.search("planks", "material", 1)
        self.assertTrue(res["has_more"])
        self.assertEqual(r.calls[0].url.params["type"], "material,substance")
        cand = res["items"][0]
        self.assertEqual(cand["license"], "CC0-1.0")
        got = c.download("Planks040", dest("acg"), DLContext(variant={"texture_res": "1k"}), cand)
        roles = sorted(r for _, r, _ in got.files)
        self.assertEqual(roles, ["main", "map:albedo", "map:normal", "map:roughness"])
        self.assertFalse((DATA / "dl" / "escape.jpg").exists())


class PolyPizzaTests(unittest.TestCase):
    def setUp(self):
        paths.set_keys({"polypizza": "PPKEY"})

    def tearDown(self):
        paths.set_keys({"polypizza": ""})

    def test_requires_key(self):
        paths.set_keys({"polypizza": ""})
        Router().install()
        with self.assertRaises(NeedsKey):
            REGISTRY["polypizza"].search("tree")

    def test_search_license_and_download_without_key_leak(self):
        res = {"total": 2, "results": [
            {"ID": "abc", "Title": "Tree", "Licence": "CC-BY 4.0", "Attribution": "Tree by Bob [CC-BY] via Poly Pizza",
             "Creator": {"Username": "Bob"}, "Download": "https://static.poly.pizza/abc.glb", "Thumbnail": "https://static.poly.pizza/abc.webp",
             "TriCount": 120, "Animated": False},
            {"ID": "dog", "Title": "Dog", "Licence": "CC0 1.0", "Creator": {"Username": "Q"}, "Download": "https://static.poly.pizza/dog.glb",
             "Animated": True}]}
        r = Router().add("https://api.poly.pizza/v1.1/search/tree", res).add("https://static.poly.pizza/", glb_bytes()).install()
        c = REGISTRY["polypizza"]
        out = c.search("tree", None, 1)
        self.assertEqual(r.calls[0].headers["X-Auth-Token"], "PPKEY")
        self.assertEqual([i["license"] for i in out["items"]], ["CC-BY-4.0", "CC0-1.0"])
        self.assertEqual(c.attribution(out["items"][0]), "Tree by Bob [CC-BY] via Poly Pizza")
        self.assertIn("animated", out["items"][1]["tags"])
        self.assertEqual(len(c.search("tree", "animation", 1)["items"]), 1)
        c.download("abc", dest("pp"), DLContext(), out["items"][0])
        cdn = [x for x in r.calls if x.url.host == "static.poly.pizza"]
        self.assertTrue(cdn)
        self.assertNotIn("x-auth-token", {k.lower() for k in cdn[0].headers.keys()})


class FreesoundTests(unittest.TestCase):
    def setUp(self):
        paths.set_keys({"freesound": "FSKEY"})

    def tearDown(self):
        paths.set_keys({"freesound": ""})

    def test_search_filters_license_and_maps(self):
        res = {"count": 40, "next": "https://freesound.org/apiv2/search/?page=2", "results": [
            {"id": 1, "name": "boom", "tags": ["explosion"], "license": "http://creativecommons.org/publicdomain/zero/1.0/",
             "username": "u1", "duration": 1.2, "previews": {"preview-hq-mp3": "https://cdn.freesound.org/previews/1.mp3"}},
            {"id": 2, "name": "hit", "license": "https://creativecommons.org/licenses/by/4.0/", "username": "u2", "duration": 30,
             "previews": {"preview-hq-mp3": "https://cdn.freesound.org/previews/2.mp3"}}]}
        r = Router().add("https://freesound.org/apiv2/search/", res).add("https://cdn.freesound.org/", b"ID3fake").install()
        c = REGISTRY["freesound"]
        out = c.search("boom", "sfx", 1)
        q = r.calls[0].url.params
        self.assertIn('license:"Creative Commons 0"', q["filter"])
        self.assertIn("duration:[0 TO 20]", q["filter"])
        self.assertEqual(r.calls[0].headers["Authorization"], "Token FSKEY")
        self.assertNotIn("FSKEY", str(r.calls[0].url))
        self.assertEqual([i["license"] for i in out["items"]], ["CC0-1.0", "CC-BY-4.0"])
        self.assertTrue(out["has_more"])
        c.search("boom", None, 1, policy="commercial")
        self.assertIn('license:"Attribution"', r.calls[-1].url.params["filter"])
        got = c.download("1", dest("fs"), DLContext(), out["items"][0])
        self.assertTrue(got.files[0][0].name.endswith(".mp3"))
        self.assertNotIn("authorization", {k.lower() for k in r.calls[-1].headers.keys()})

    def test_error_redaction(self):
        Router().add("https://freesound.org/apiv2/search/", httpx.Response(401, json={"detail": "Invalid token FSKEY"})).install()
        with self.assertRaises(Exception) as cm:
            REGISTRY["freesound"].search("x")
        self.assertNotIn("FSKEY", str(cm.exception))


class SketchfabTests(unittest.TestCase):
    def test_license_filter_and_download_needs_token(self):
        res = {"next": None, "results": [
            {"uid": "u1", "name": "Knight", "license": {"slug": "by", "label": "CC Attribution"}, "user": {"username": "k"},
             "thumbnails": {"images": [{"url": "https://media.sketchfab.com/1.jpg", "width": 1024}, {"url": "https://media.sketchfab.com/2.jpg", "width": 480}]},
             "faceCount": 5000, "animationCount": 3, "isDownloadable": True, "viewerUrl": "https://sketchfab.com/3d-models/u1"},
            {"uid": "u2", "name": "Store", "license": "st"},
            {"uid": "u3", "name": "NC", "license": {"label": "CC Attribution-NonCommercial"}}]}
        r = Router().add("https://api.sketchfab.com/v3/search", res).install()
        c = REGISTRY["sketchfab"]
        out = c.search("knight", None, 1, policy="commercial")
        self.assertEqual([i["source_id"] for i in out["items"]], ["u1"])
        self.assertEqual(out["items"][0]["license"], "CC-BY-4.0")
        self.assertEqual(out["items"][0]["preview_url"], "https://media.sketchfab.com/2.jpg")
        self.assertEqual(r.calls[0].url.params["downloadable"], "true")
        self.assertEqual(len(c.search("knight", None, 1, policy="any")["items"]), 2)
        with self.assertRaises(ConnectorError):
            c.download("u1", dest("sf"), DLContext(), out["items"][0])
        paths.set_keys({"sketchfab": "SFTOKEN"})
        try:
            r.add("https://api.sketchfab.com/v3/models/u1/download", {"glb": {"url": "https://dl.sketchfab.test/u1.glb", "expires": 300}})
            r.add("https://dl.sketchfab.test/", glb_bytes())
            got = c.download("u1", dest("sf"), DLContext(), out["items"][0])
            self.assertEqual(got.files[0][0].suffix, ".glb")
            dl_call = [x for x in r.calls if "/download" in str(x.url)][0]
            self.assertEqual(dl_call.headers["Authorization"], "Token SFTOKEN")
        finally:
            paths.set_keys({"sketchfab": ""})


class GoogleFontsTests(unittest.TestCase):
    MD = '''name: "Open Sans"
designer: "Steve Matteson"
license: "OFL"
category: "SANS_SERIF"
fonts {
  name: "Open Sans"
  style: "normal"
  weight: 400
  filename: "OpenSans[wdth,wght].ttf"
}
fonts {
  name: "Open Sans"
  style: "italic"
  weight: 400
  filename: "OpenSans-Italic[wdth,wght].ttf"
}
axes {
  tag: "wght"
}
'''

    def test_parse_metadata(self):
        md = parse_metadata_pb(self.MD)
        self.assertEqual(md["name"], "Open Sans")
        self.assertEqual(len(md["fonts"]), 2)
        self.assertEqual(md["fonts"][1]["style"], "italic")

    def test_index_search_download(self):
        r = Router()
        r.add("https://api.github.com/repos/google/fonts/git/trees/main", {"tree": [
            {"path": "ofl", "type": "tree", "sha": "S1"}, {"path": "apache", "type": "tree", "sha": "S2"}, {"path": "README.md", "type": "blob"}]})
        r.add("https://api.github.com/repos/google/fonts/git/trees/S1", {"tree": [{"path": "opensans", "type": "tree"}, {"path": "inter", "type": "tree"}]})
        r.add("https://api.github.com/repos/google/fonts/git/trees/S2", {"tree": [{"path": "roboto", "type": "tree"}]})
        r.add("https://raw.githubusercontent.com/google/fonts/main/ofl/opensans/METADATA.pb", self.MD)
        r.add("https://raw.githubusercontent.com/google/fonts/main/ofl/opensans/OpenSans", b"\x00\x01\x00\x00fontdata")
        r.add("https://raw.githubusercontent.com/google/fonts/main/ofl/opensans/OFL.txt", "Open Font License")
        r.install()
        c = REGISTRY["googlefonts"]
        res = c.search("open sans", "font", 1)
        self.assertEqual(res["items"][0]["source_id"], "ofl/opensans")
        self.assertEqual(res["items"][0]["license"], "OFL-1.1")
        self.assertEqual(c.search("roboto", None, 1)["items"][0]["license"], "Apache-2.0")
        got = c.download("ofl/opensans", dest("gf"), DLContext())
        roles = [r_ for _, r_, _ in got.files]
        self.assertEqual(roles[0], "main")
        self.assertIn("license", roles)
        self.assertTrue(any("%5B" in str(x.url) for x in r.calls))   # brackets URL-encoded
        self.assertEqual(got.updates["author"], "Steve Matteson")


class CMUTests(unittest.TestCase):
    INDEX = "Header text\n\nSubject #1 (climb, swing)\n\n01_01\tplayground - forward jumps\n01_02\tplayground - climb\n\nSubject #143 (general)\n\n143_01\twalk forward\n"

    def test_search_and_download(self):
        r = Router().add("https://raw.githubusercontent.com/una-dinosauria/cmu-mocap/master/cmu-mocap-index-text.txt", self.INDEX)
        r.add("https://raw.githubusercontent.com/una-dinosauria/cmu-mocap/master/data/143/143_01.bvh",
              "HIERARCHY\nROOT Hips\n{\n JOINT Spine\n}\nMOTION\nFrames: 240\nFrame Time: 0.0083333\n0 0 0\n").install()
        c = REGISTRY["cmu"]
        res = c.search("walk", None, 1)
        self.assertEqual([i["source_id"] for i in res["items"]], ["143_01"])
        self.assertEqual(res["items"][0]["license"], "custom:cmu-mocap")
        self.assertTrue(res["items"][0]["commercial_ok"])
        self.assertEqual(c.search("", None, 1)["items"][0]["source_id"], "143_01")   # higher subjects first
        got = c.download("143_01", dest("cmu"), DLContext())
        st = meta.extract(got.files[0][0])
        self.assertEqual(st["frames"], 240)
        self.assertEqual(st["joints"], 2)
        self.assertIn("NSF EIA-0196217", c.attribution(res["items"][0]))
        with self.assertRaises(ConnectorError):
            c.download("../x", dest("cmu"), DLContext())


class CatalogTests(unittest.TestCase):
    def test_catalogs_are_manual(self):
        for sid in ("kenney", "quaternius", "mixamo", "truebones"):
            c = REGISTRY[sid]
            self.assertFalse(c.automated)
            res = c.search("", None, 1)
            self.assertTrue(res["items"])
            self.assertTrue(res["items"][0]["manual"])
            with self.assertRaises(ConnectorError):
                c.download(res["items"][0]["source_id"], dest("cat"), DLContext())
        self.assertEqual(REGISTRY["kenney"].search("impact", "sfx", 1)["items"][0]["license"], "CC0-1.0")
        self.assertEqual(REGISTRY["quaternius"].search("animation", None, 1)["items"][0]["license"], "custom:quaternius-qal-1.0")


if __name__ == "__main__":
    unittest.main()
