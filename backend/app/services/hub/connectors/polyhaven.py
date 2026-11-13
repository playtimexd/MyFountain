"""Poly Haven - official public API (https://api.polyhaven.com). CC0 models, textures (PBR materials) and HDRIs.
API ToS (github.com/Poly-Haven/Public-API/blob/master/ToS.md): free incl. commercial use, no key; a unique
User-Agent is required; apps using the API live must make clear the content comes from Poly Haven; no abusive
traffic. The website itself must not be scraped - we only use the API."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .. import meta, net
from .base import Connector, ConnectorError, DLContext, Downloaded, match, page_slice

API = "https://api.polyhaven.com"
TYPE_OF_KIND = {"model": "models", "material": "textures", "texture": "textures", "hdri": "hdris"}
KIND_OF_TYPE = {0: "hdri", 1: "material", 2: "model"}
MAP_ROLES = {"diffuse": "albedo", "nor_gl": "normal", "rough": "roughness", "metal": "metalness", "ao": "ao",
             "displacement": "displacement", "arm": "arm", "bump": "bump", "spec": "specular", "translucency": "translucency",
             "alpha": "opacity", "emission": "emission"}
SKIP_KEYS = {"blend", "gltf", "fbx", "usd", "mtlx", "nor_dx", "hdri", "backplates", "tonemapped", "colorchart"}
RES_ORDER = ["1k", "2k", "4k", "8k", "16k", "24k"]


def pick_res(avail, want: str) -> str | None:
    avail = list(avail)
    if not avail:
        return None
    if want in avail:
        return want
    ranked = sorted(avail, key=lambda r: RES_ORDER.index(r) if r in RES_ORDER else 99)
    lower = [r for r in ranked if r in RES_ORDER and RES_ORDER.index(r) <= (RES_ORDER.index(want) if want in RES_ORDER else 0)]
    return (lower[-1] if lower else ranked[0])


class PolyHaven(Connector):
    id = "polyhaven"
    name = "Poly Haven"
    homepage = "https://polyhaven.com"
    kinds = ["model", "material", "hdri"]
    min_interval = 0.25
    terms_url = "https://github.com/Poly-Haven/Public-API/blob/master/ToS.md"
    terms_summary = ("All assets CC0 (no attribution needed). Official API, no key. API terms: unique User-Agent required, "
                     "show users that content comes from Poly Haven, no abusive traffic; the website must not be scraped.")
    default_license = "CC0-1.0"

    def _all(self, t: str) -> dict:
        return net.get_json(f"{API}/assets", params={"t": t}, cache_ttl=6 * 3600, min_interval=self.min_interval)

    def _cand(self, sid: str, a: dict) -> dict:
        kind = KIND_OF_TYPE.get(a.get("type"), "model")
        thumb = a.get("thumbnail_url") or f"https://cdn.polyhaven.com/asset_img/thumbs/{sid}.png?width=256&height=256"
        thumb = re.sub(r"width=\d+", "width=512", re.sub(r"height=\d+", "height=512", thumb))
        authors = list((a.get("authors") or {}).keys())
        stats = {}
        if a.get("polycount"):
            stats["polycount"] = a["polycount"]
        if a.get("max_resolution"):
            stats["max_resolution"] = a["max_resolution"]
        if a.get("dimensions"):
            stats["dimensions_mm"] = a["dimensions"]
        return self.candidate(source_id=sid, kind=kind, name=a.get("name") or sid, description=a.get("description") or "",
                              tags=list(dict.fromkeys((a.get("tags") or []) + (a.get("categories") or []))),
                              source_url=f"https://polyhaven.com/a/{sid}", author=", ".join(authors) or None,
                              author_url=f"https://polyhaven.com/all?a={authors[0]}" if authors else None,
                              preview_url=thumb, stats=stats,
                              formats=["glb"] if kind == "model" else ["hdr"] if kind == "hdri" else ["jpg"],
                              extra={"download_count": a.get("download_count", 0)})

    def search(self, query: str, kind: str | None = None, page: int = 1) -> dict:
        t = TYPE_OF_KIND.get(kind or "", "all")
        if kind and kind not in TYPE_OF_KIND:
            return page_slice([], page, self.page_size)
        data = self._all(t)
        rows = [(sid, a) for sid, a in data.items()
                if match(query, sid, a.get("name"), a.get("tags") or [], a.get("categories") or [])]
        rows.sort(key=lambda kv: -int(kv[1].get("download_count") or 0))
        res = page_slice(rows, page, self.page_size)
        res["items"] = [self._cand(sid, a) for sid, a in res["items"]]
        return res

    def get(self, source_id: str) -> dict:
        a = net.get_json(f"{API}/info/{source_id}", cache_ttl=6 * 3600, min_interval=self.min_interval)
        if not isinstance(a, dict) or "type" not in a:
            raise ConnectorError(f"Poly Haven asset '{source_id}' not found")
        return self._cand(source_id, a)

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        cand = cand or self.get(source_id)
        files = net.get_json(f"{API}/files/{source_id}", cache_ttl=3600, min_interval=self.min_interval)
        want = (ctx.variant.get("texture_res") or "1k").lower()
        out: list[tuple[Path, str, str | None]] = []
        kind = cand["kind"]
        if kind == "model":
            g = files.get("gltf") or {}
            res = pick_res(g.keys(), want)
            if not res or "gltf" not in g[res]:
                raise ConnectorError("No glTF download offered for this model")
            entry = g[res]["gltf"]
            src = dest / "_gltf"
            gpath = src / Path(entry["url"]).name
            ctx.fetch(entry["url"], gpath, min_interval=self.min_interval)
            for rel, inc in (entry.get("include") or {}).items():
                ctx.check()
                target = (src / rel).resolve()
                if src.resolve() not in target.parents:
                    continue  # never write outside the asset folder
                ctx.fetch(inc["url"], target, min_interval=self.min_interval)
            glb = dest / f"{source_id}_{res}.glb"
            meta.gltf_to_glb(gpath, glb)
            shutil.rmtree(src, ignore_errors=True)
            out.append((glb, "main", res))
        elif kind == "hdri":
            h = files.get("hdri") or {}
            res = pick_res(h.keys(), want)
            if not res:
                raise ConnectorError("No HDRI file offered")
            e = h[res].get("hdr") or h[res].get("exr")
            p = dest / Path(e["url"]).name
            ctx.fetch(e["url"], p, min_interval=self.min_interval)
            out.append((p, "main", res))
        else:
            got_albedo = False
            for key, by_res in files.items():
                if key.lower() in SKIP_KEYS or not isinstance(by_res, dict):
                    continue
                res = pick_res(by_res.keys(), want)
                if not res:
                    continue
                fm = by_res[res].get("jpg") or by_res[res].get("png")
                if not fm or "url" not in fm:
                    continue
                ctx.check()
                p = dest / Path(fm["url"]).name
                ctx.fetch(fm["url"], p, min_interval=self.min_interval)
                role = MAP_ROLES.get(key.lower(), key.lower())
                out.append((p, f"map:{role}", res))
                if role == "albedo":
                    out.append((p, "main", res))
                    got_albedo = True
            if not out:
                raise ConnectorError("No texture maps offered")
            if not got_albedo:
                out.append((out[0][0], "main", out[0][2]))
        return Downloaded(files=out)
