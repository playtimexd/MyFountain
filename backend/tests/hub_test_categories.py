"""Library categories: rules, tree roll-up, DB migration/filters, manual pins, CLIP-tag cleanup, Openverse + NASA connectors.
Run:  cd D:\\CutLab\\backend && .venv\\Scripts\\python.exe -m unittest tests.hub_test_categories -v   (or discover -p "hub_test_*.py")"""
from __future__ import annotations

import sqlite3
import unittest

from hub_testutil import DATA, Router, no_ai, png_bytes, wav_bytes

from app.services.hub import categories, db, licenses, net, paths, pipeline
from app.services.hub.connectors import REGISTRY, DLContext

no_ai()


def rec(kind, name, source="local", tags=(), pack="", **kw):
    r = {"kind": kind, "name": name, "source": source, "source_id": name, "tags": list(tags), "extra": {"pack": pack} if pack else {},
         "license": "CC0-1.0", "stats": {}}
    r.update(kw)
    return r


class RuleTests(unittest.TestCase):
    def test_examples(self):
        cases = [
            (rec("sfx", "PistolA Fire ST02", pack="Military Weapons Dark - sound"), "audio/sfx/weapons"),
            (rec("sfx", "Foley fs 1p sneaker concrete walk 22"), "audio/sfx/foley"),
            (rec("sfx", "Explosion Simulation", tags=["bomb", "gun"]), "audio/sfx/impacts"),     # name beats tags
            (rec("music", "Le piano"), "audio/music"),
            (rec("animation", "M Neutral Walk", pack="Game Animation Sample (GASP) - animation"), "anim/gasp"),
            (rec("animation", "CMU 144_33", source="cmu"), "anim/mocap"),
            (rec("animation", "Run", pack="100STYLE"), "anim/100style"),
            (rec("hdri", "Studio Small 03", tags=["indoor", "studio"]), "surfaces/hdris/indoor"),
            (rec("hdri", "Sunflowers (Pure Sky)", tags=["pure skies"]), "surfaces/hdris/skies"),
            (rec("material", "Wood Floor 064", tags=["metal"]), "surfaces/materials/wood"),
            (rec("model", "Assault Rifle A"), "models/weapons"),
            (rec("model", "School Chair 01", source="polyhaven"), "models/furniture"),
            (rec("model", "Mystery thing", source="polyhaven"), "models/props"),
            (rec("sprite", "Broadsword", pack="game-icons.net - Lorc"), "icons/game/weapons"),
            (rec("sprite", "Earth globe", pack="game-icons.net - Lorc"), "icons/game/places"),    # 'ear' must not hit 'earth'
            (rec("font", "Inter", extra={"category": "sans serif"}), "fonts/sans"),
            (rec("font", "Lora", tags=["serif"]), "fonts/serif"),
            (rec("video", "Rocket launch", source="nasa"), "video/space"),
            (rec("image", "Forest", source="openverse", extra={"category": "photograph"}), "image/photos"),
            (rec("voice", "Ep433", source="nasa"), "audio/voice"),
        ]
        for r, want in cases:
            with self.subTest(r["name"]):
                self.assertEqual(categories.categorize(r), want)
                self.assertIn(want, categories.VALID)

    def test_clip_tags_never_decide(self):
        r = rec("sprite", "Pointy ear", pack="game-icons.net - X", tags=["fish", "spaceship"], stats={"auto_tags": ["fish", "spaceship"]})
        self.assertEqual(categories.categorize(r), "icons/game/body")

    def test_collection(self):
        self.assertEqual(categories.collection(rec("sfx", "x", pack="Military Weapons Dark - sound")), "Military Weapons Dark")
        self.assertEqual(categories.collection(rec("sprite", "x", pack="game-icons.net - Lorc")), "game-icons.net")
        self.assertEqual(categories.collection(rec("hdri", "x", source="polyhaven")), "Poly Haven")
        self.assertEqual(categories.collection(rec("image", "x", source="openverse", extra={"provider": "flickr"})), "Openverse · flickr")

    def test_tree_rollup(self):
        t = categories.tree({"audio/sfx/ui": 3, "audio/music": 2, "icons/game/weapons": 5, "audio/sfx/unknown-thing": 1})
        audio = next(n for n in t if n["id"] == "audio")
        self.assertEqual(audio["count"], 6)
        sfx = next(n for n in audio["children"] if n["id"] == "audio/sfx")
        self.assertEqual(sfx["count"], 4)                     # unknown leaf rolls into its nearest known parent
        self.assertEqual([n["id"] for n in t][:3], ["video", "image", "audio"])


class DbCategoryTests(unittest.TestCase):
    def setUp(self):
        db.reset_for_tests(DATA / "cat_test.db")
        if (DATA / "cat_test.db").exists():
            db.reset_for_tests(DATA / "cat_test.db")
            c = db.conn()
            c.execute("DELETE FROM assets")
            c.execute("DELETE FROM files")
            c.commit()

    def add(self, r):
        return db.upsert({**r, "files": [{"role": "main", "path": str(DATA / f"{r['name']}.bin"), "format": "bin", "bytes": 1}]})

    def test_upsert_filter_facets_pin(self):
        a = self.add(rec("sfx", "Rifle Fire", pack="Military Weapons Dark - sound"))
        self.add(rec("sfx", "Button click"))
        self.add(rec("sprite", "Broadsword", pack="game-icons.net - Lorc"))
        self.assertEqual(db.get(a["id"])["category"], "audio/sfx/weapons")
        r = db.search({"category": "audio"}, page=1, page_size=10)
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["facets"]["category"], {"audio/sfx/weapons": 1, "audio/sfx/ui": 1, "icons/game/weapons": 1})
        self.assertEqual(db.search({"category": "audio/sfx/ui"}, page=1, page_size=10)["total"], 1)
        self.assertEqual(db.search({"category": "audio/sf"}, page=1, page_size=10)["total"], 0)   # prefix must stop at '/'
        self.assertEqual(db.search({"collection": "game-icons.net"}, page=1, page_size=10)["total"], 1)
        # manual pin survives recategorize and re-import; clearing it returns to the rules
        db.set_category(a["id"], "audio/sfx/other")
        db.recategorize()
        self.add(rec("sfx", "Rifle Fire", pack="Military Weapons Dark - sound"))
        self.assertEqual(db.get(a["id"])["category"], "audio/sfx/other")
        db.set_category(a["id"], None)
        self.assertEqual(db.get(a["id"])["category"], "audio/sfx/weapons")
        with self.assertRaises(ValueError):
            db.set_category(a["id"], "nope/nothing")

    def test_clean_auto_tags(self):
        a = self.add(rec("sfx", "Rain loop", tags=["rain", "sprite", "text"], stats={"auto_tags": ["sprite", "text"]}))
        m = self.add(rec("model", "Chair", tags=["chair", "furniture"], stats={"auto_tags": ["furniture"]}))
        self.assertEqual(db.clean_auto_tags(), 1)
        self.assertEqual(db.get(a["id"])["tags"], ["rain"])
        self.assertEqual(sorted(db.get(m["id"])["tags"]), ["chair", "furniture"])     # models keep CLIP tags

    def test_migration_adds_columns(self):
        path = DATA / "old_schema.db"
        c = sqlite3.connect(path)
        c.executescript(db.SCHEMA)
        c.execute("INSERT INTO assets(id,kind,name,source,source_id,license,tags,extra) VALUES('x1','hdri','Studio','polyhaven','s','CC0-1.0','[\"indoor\"]','{}')")
        c.commit()
        c.close()
        db.reset_for_tests(path)
        self.assertEqual(db.get("x1")["category"], "surfaces/hdris/indoor")
        self.assertEqual(db.get("x1")["collection"], "Poly Haven")


OV_IMG = {"id": "abc-1", "title": "Fall Forest", "url": "https://live.staticflickr.com/1/a_b.jpg", "creator": "Jane",
          "creator_url": "https://flickr.com/jane", "license": "by-sa", "license_version": "2.0", "provider": "flickr", "source": "flickr",
          "foreign_landing_url": "https://flickr.com/p/1", "thumbnail": "https://api.openverse.org/v1/images/abc-1/thumb/",
          "filetype": "jpg", "category": "photograph", "tags": [{"name": "forest"}], "attribution": "\"Fall Forest\" by Jane is licensed under CC BY-SA 2.0."}
OV_AUD = {"id": "snd-1", "title": "Click", "url": "https://cdn.freesound.org/previews/1/1-hq.mp3", "creator": "Bob", "license": "cc0",
          "license_version": "1.0", "provider": "freesound", "filetype": "mp3", "duration": 1200, "category": None, "genres": None, "tags": []}


class OpenverseNasaTests(unittest.TestCase):
    def setUp(self):
        db.reset_for_tests(DATA / "conn_test.db")
        self.r = Router()
        self.r.install()

    def tearDown(self):
        net.set_transport(None)

    def test_openverse_search_licence_and_policy(self):
        seen = {}

        def page(req):
            seen.update(dict(req.url.params))
            return {"result_count": 1, "page_count": 1, "results": [OV_AUD] if "audio" in str(req.url) else [OV_IMG]}
        self.r.add("https://api.openverse.org/v1/", page)
        ov = REGISTRY["openverse"]
        img = ov.search("forest", "image", 1, policy="commercial")["items"][0]
        self.assertEqual((img["license"], img["kind"], seen.get("license_type")), ("CC-BY-SA-2.0", "image", "commercial,modification"))
        self.assertEqual(categories.categorize(img), "image/photos")
        snd = ov.search("click", "sfx", 1, policy="cc0")["items"][0]
        self.assertEqual((snd["license"], snd["kind"], seen.get("source"), seen.get("license")), ("CC0-1.0", "sfx", "freesound", "cc0,pdm"))
        self.assertIn("Jane", ov.attribution(img))

    def test_openverse_download(self):
        self.r.add("https://cdn.freesound.org/", wav_bytes())
        ov = REGISTRY["openverse"]
        cand = ov._cand(OV_AUD, "audio")
        d = DATA / "ovdl"
        d.mkdir(exist_ok=True)
        got = ov.download(cand["source_id"], d, DLContext(), cand)
        self.assertTrue(got.files[0][0].exists())

    def test_nasa_search_and_rendition_fallback(self):
        item = {"href": "https://images-assets.nasa.gov/video/V1/collection.json",
                "data": [{"nasa_id": "V1", "title": "Launch", "media_type": "video", "photographer": "SpaceX", "keywords": ["rocket"]}],
                "links": [{"rel": "preview", "href": "https://images-assets.nasa.gov/video/V1/V1~thumb.jpg"}]}
        self.r.add("https://images-api.nasa.gov/search", {"collection": {"items": [item], "metadata": {"total_hits": 1}, "links": []}})
        self.r.add("https://images-api.nasa.gov/asset/V1", {"collection": {"items": [
            {"href": "http://images-assets.nasa.gov/video/V1/V1~medium.mp4"}, {"href": "http://images-assets.nasa.gov/video/V1/V1~mobile.mp4"}]}})
        self.r.add("https://images-assets.nasa.gov/video/V1/V1~medium.mp4", b"x" * 5000)
        self.r.add("https://images-assets.nasa.gov/video/V1/V1~mobile.mp4", b"y" * 100)
        na = REGISTRY["nasa"]
        c = na.search("rocket", "video", 1)["items"][0]
        self.assertEqual((c["kind"], c["license"], c["extra"]["third_party_credit"]), ("video", "custom:nasa-media", True))
        self.assertEqual(licenses.info(c["license"])["class"], "attribution")
        self.assertTrue(licenses.allowed("PDM-1.0", "cc0"))
        import app.services.hub.connectors.nasa as nasa_mod
        old = dict(nasa_mod.PER_FILE_CAP)
        nasa_mod.PER_FILE_CAP["video"] = 1000          # medium is too big -> falls back to mobile
        try:
            d = DATA / "nasadl"
            d.mkdir(exist_ok=True)
            got = na.download("V1", d, DLContext(), c)
        finally:
            nasa_mod.PER_FILE_CAP.update(old)
        self.assertEqual(got.files[0][0].name, "V1_mobile.mp4")
        self.assertEqual(got.stats["rendition"], "mobile.mp4")


if __name__ == "__main__":
    unittest.main()
