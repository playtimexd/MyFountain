"""Catalog-only sources: no API and/or automation not permitted. We list official pack pages (opened in the
user's browser) and the user imports the zip they downloaded by hand via 'Import pack zip' (licence pre-filled).

* Kenney - CC0. kenney.nl/robots.txt disallows /assets/ and /downloads/ for automated agents, and downloads go
  through a per-request signed link, so nothing on kenney.nl is fetched automatically.
* Quaternius - packs hosted on Google Drive / itch.io. Since 2026-08-28 new downloads fall under the Quaternius Asset
  License 1.0 (free incl. commercial, no credit required, no redistribution as an asset pack); older downloads stay CC0.
* Mixamo (Adobe) - no public API; royalty-free in projects, raw files must not be redistributed.
* Truebones - sold/promoted on Gumroad (checkout required); royalty-free licence per product.
"""
from __future__ import annotations

from .base import Connector, match, page_slice

K = "https://kenney.nl/assets/"
KENNEY_PACKS = [
    # (slug, name, kind, tags)
    ("animated-characters-1", "Animated Characters 1", "model", "character rigged animated 3d"),
    ("animated-characters-2", "Animated Characters 2", "model", "character rigged animated 3d"),
    ("animated-characters-3", "Animated Characters 3", "model", "character rigged animated 3d"),
    ("mini-characters", "Mini Characters", "model", "character animated 3d low-poly"),
    ("blocky-characters", "Blocky Characters", "model", "character animated 3d voxel"),
    ("platformer-kit", "Platformer Kit", "model", "platformer level 3d"),
    ("nature-kit", "Nature Kit", "model", "nature tree rock 3d"),
    ("city-kit-roads", "City Kit (Roads)", "model", "city road 3d"),
    ("city-kit-commercial", "City Kit (Commercial)", "model", "city building 3d"),
    ("city-kit-suburban", "City Kit (Suburban)", "model", "city house 3d"),
    ("castle-kit", "Castle Kit", "model", "castle medieval 3d"),
    ("pirate-kit", "Pirate Kit", "model", "pirate ship 3d"),
    ("space-kit", "Space Kit", "model", "space sci-fi 3d"),
    ("tower-defense-kit", "Tower Defense Kit", "model", "tower defense 3d"),
    ("racing-kit", "Racing Kit", "model", "racing track 3d"),
    ("car-kit", "Car Kit", "model", "vehicle car 3d"),
    ("furniture-kit", "Furniture Kit", "model", "furniture interior 3d"),
    ("food-kit", "Food Kit", "model", "food 3d"),
    ("graveyard-kit", "Graveyard Kit", "model", "graveyard halloween 3d"),
    ("survival-kit", "Survival Kit", "model", "survival tools 3d"),
    ("prototype-textures", "Prototype Textures", "texture", "grid prototype texture"),
    ("impact-sounds", "Impact Sounds", "sfx", "impact hit audio"),
    ("interface-sounds", "Interface Sounds", "sfx", "ui click audio"),
    ("rpg-audio", "RPG Audio", "sfx", "rpg footsteps audio"),
    ("digital-audio", "Digital Audio", "sfx", "digital laser audio"),
    ("sci-fi-sounds", "Sci-fi Sounds", "sfx", "sci-fi laser audio"),
    ("casino-audio", "Casino Audio", "sfx", "casino cards audio"),
    ("music-jingles", "Music Jingles", "music", "jingle music audio"),
    ("voiceover-pack", "Voiceover Pack", "voice", "voice announcer audio"),
    ("ui-pack", "UI Pack", "sprite", "ui buttons 2d"),
    ("tiny-dungeon", "Tiny Dungeon", "sprite", "dungeon pixel tiles 2d"),
    ("tiny-town", "Tiny Town", "sprite", "town pixel tiles 2d"),
    ("pixel-platformer", "Pixel Platformer", "sprite", "platformer pixel tiles 2d"),
    ("1-bit-pack", "1-Bit Pack", "sprite", "1-bit pixel tiles 2d"),
    ("particle-pack", "Particle Pack", "sprite", "particles vfx 2d"),
    ("input-prompts", "Input Prompts", "sprite", "controller keyboard icons 2d"),
    ("game-icons", "Game Icons", "sprite", "icons ui 2d"),
]

Q = "https://quaternius.com/packs/"
QUATERNIUS_PACKS = [
    ("ultimatedanimatedcharacter", "Ultimate Animated Character Pack", "model", "character rigged animated fbx blend",
     "https://drive.google.com/drive/folders/1sNi1AfenfPRrvRt5yfaj5QMMd6KKcUJ5"),
    ("universalanimationlibrary", "Universal Animation Library", "animation", "animation humanoid rigged glb fbx",
     "https://quaternius.itch.io/universal-animation-library"),
    ("universalanimationlibrary2", "Universal Animation Library 2", "animation", "animation humanoid rigged",
     "https://quaternius.itch.io/universal-animation-library-2"),
    ("universalbasecharacters", "Universal Base Characters", "model", "character rigged base mesh gltf",
     "https://quaternius.itch.io/universal-base-characters"),
    ("ultimatemonsters", "Ultimate Monsters", "model", "monster creature animated gltf",
     "https://drive.google.com/drive/folders/18m4KpzpEzhC9wl7jzr6dUc0N8Jozr79C"),
    ("animatedwomen", "Animated Women", "model", "character woman animated",
     "https://drive.google.com/drive/folders/1c13R--fMqdR6r2MRlcKKsbPky0__T-yJ"),
    ("ultimateanimatedanimals", "Ultimate Animated Animals", "model", "animal quadruped animated gltf",
     "https://drive.google.com/drive/folders/1uJ3N5HfB7jKTseJUNQr3N4YaN0UuEtHk"),
]


class Catalog(Connector):
    automated = False
    entries: list[dict] = []

    def search(self, query: str, kind: str | None = None, page: int = 1) -> dict:
        rows = [e for e in self.entries if (not kind or e["kind"] == kind) and match(query, e["name"], e["tags"], e["kind"])]
        res = page_slice(rows, page, self.page_size)
        res["items"] = [self.candidate(**e) for e in res["items"]]
        return res

    def get(self, source_id: str) -> dict:
        e = next((e for e in self.entries if e["source_id"] == source_id), None)
        if not e:
            from .base import ConnectorError
            raise ConnectorError("Not in the catalog")
        return self.candidate(**e)


class Kenney(Catalog):
    id = "kenney"
    name = "Kenney"
    homepage = "https://kenney.nl/assets"
    kinds = ["model", "sprite", "texture", "sfx", "music", "voice"]
    terms_url = "https://kenney.nl/support"
    terms_summary = ("All packs CC0 (credit 'Kenney' optional). No API; kenney.nl's robots.txt disallows automated access to "
                     "/assets/ and /downloads/, so CutLab never downloads from it. Open the pack page, download the zip, then use Import pack zip.")
    default_license = "CC0-1.0"
    entries = [{"source_id": s, "name": n, "kind": k, "tags": t.split() + ["pack"], "source_url": K + s, "author": "Kenney",
                "author_url": "https://kenney.nl", "extra": {"pack": True}} for s, n, k, t in KENNEY_PACKS]


class Quaternius(Catalog):
    id = "quaternius"
    name = "Quaternius"
    homepage = "https://quaternius.com"
    kinds = ["model", "animation"]
    terms_url = "https://quaternius.com/license.html"
    terms_summary = ("Low-poly packs incl. rigged + animated characters. No API; downloads via Google Drive / itch.io. "
                     "Licence page (updated 2026-08-28): Quaternius Asset License 1.0 - free for personal and commercial use, no credit "
                     "required, but no redistribution as an asset pack. Packs downloaded earlier under CC0 stay CC0 (choose the licence on import).")
    default_license = "custom:quaternius-qal-1.0"
    entries = [{"source_id": s, "name": n, "kind": k, "tags": t.split() + ["pack", "low-poly"], "source_url": Q + s + ".html",
                "author": "Quaternius", "author_url": "https://quaternius.com", "extra": {"pack": True, "download_host": host}}
               for s, n, k, t, host in QUATERNIUS_PACKS]


class Mixamo(Catalog):
    id = "mixamo"
    name = "Mixamo"
    homepage = "https://www.mixamo.com"
    kinds = ["animation", "model"]
    terms_url = "https://helpx.adobe.com/creative-cloud/faq/mixamo-faq.html"
    terms_summary = ("Free with an Adobe ID; characters and animations are royalty-free in games/films, but raw files must not be "
                     "redistributed. No public API: download FBX files in the browser, then use Import FBX folder.")
    default_license = "custom:mixamo"
    entries = [{"source_id": "mixamo", "name": "Mixamo character & animation library (download FBX manually)", "kind": "animation",
                "tags": ["mixamo", "fbx", "humanoid", "rigged", "pack"], "source_url": "https://www.mixamo.com", "author": "Adobe Mixamo",
                "extra": {"pack": True}}]


class Truebones(Catalog):
    id = "truebones"
    name = "Truebones"
    homepage = "https://truebones.gumroad.com"
    kinds = ["animation", "model"]
    terms_url = "https://truebones.gumroad.com/"
    terms_summary = ("Animal / creature mocap (FBX/BVH) sold on Gumroad (occasional free promotions; checkout with email required). "
                     "Royalty-free licence per product - check the readme. No automated downloads: import the zip you bought/claimed.")
    default_license = "custom:truebones"
    entries = [{"source_id": "zoo", "name": "Truebones Zoo - 75+ animated animals (Gumroad)", "kind": "animation",
                "tags": ["animal", "quadruped", "mocap", "fbx", "bvh", "pack"], "source_url": "https://truebones.gumroad.com/l/skZMC",
                "author": "Truebones", "extra": {"pack": True}}]


class Local(Catalog):
    id = "local"
    name = "Local import"
    homepage = ""
    kinds = []
    terms_summary = "Files you imported from disk with a licence you chose."
    entries = []

    def available(self) -> tuple[bool, str]:
        return True, "use Import local folder / zip"
