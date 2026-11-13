"""ambientCG - official API v3 (https://docs.ambientcg.com/api/v3/). CC0 PBR materials, HDRIs, decals, 3D models.
No key needed. Docs note the site is run by one person (be gentle) and discourage bulk-downloading huge volumes."""
from __future__ import annotations

import re
from pathlib import Path

from .. import net
from ..ziputil import safe_extract
from .base import Connector, ConnectorError, DLContext, Downloaded

API = "https://ambientcg.com/api/v3/assets"
TYPE_OF_KIND = {"material": "material,substance", "hdri": "hdri", "model": "3d-model", "texture": "decal,atlas,plain-image,terrain"}
KIND_OF_TYPE = {"material": "material", "substance": "material", "hdri": "hdri", "3d-model": "model", "decal": "texture",
                "atlas": "texture", "plain-image": "texture", "terrain": "texture", "brush": "texture", "hdri-element": "hdri"}
MAP_SUFFIX = {"color": "albedo", "normalgl": "normal", "roughness": "roughness", "metalness": "metalness",
              "ambientocclusion": "ao", "displacement": "displacement", "opacity": "opacity", "emission": "emission"}
INCLUDE = "type,title,tags,downloads,thumbnails,url,shortDescription"


class AmbientCG(Connector):
    id = "ambientcg"
    name = "ambientCG"
    homepage = "https://ambientcg.com"
    kinds = ["material", "hdri", "model", "texture"]
    min_interval = 0.6
    terms_url = "https://docs.ambientcg.com/license/"
    terms_summary = ("All assets CC0 (credit optional). Official API v3, no key. Single-maintainer site: keep request volume "
                     "modest; bulk downloading whole categories is discouraged.")
    default_license = "CC0-1.0"

    def _cand(self, a: dict) -> dict:
        t = (a.get("type") or "material").lower()
        thumbs = a.get("thumbnails") or {}
        thumb = thumbs.get("512-PNG") or thumbs.get("256-PNG") or next(iter(thumbs.values()), None)
        sid = a.get("id") or a.get("assetId")
        downloads = a.get("downloads") or []
        return self.candidate(source_id=sid, kind=KIND_OF_TYPE.get(t, "material"), name=a.get("title") or sid,
                              description=a.get("shortDescription") or "", tags=a.get("tags") or [],
                              source_url=a.get("url") or f"https://ambientcg.com/view?id={sid}", author="ambientCG",
                              author_url="https://ambientcg.com", preview_url=thumb,
                              formats=sorted({(d.get("attributes") or "").split("-")[-1].lower() for d in downloads if d.get("attributes")}),
                              extra={"downloads": [{"attributes": d.get("attributes"), "url": d.get("url"), "size": d.get("size"),
                                                    "extension": d.get("extension")} for d in downloads]})

    def search(self, query: str, kind: str | None = None, page: int = 1) -> dict:
        if kind and kind not in TYPE_OF_KIND:
            return {"items": [], "page": page, "total": 0, "has_more": False}
        params = {"limit": self.page_size, "offset": (max(1, page) - 1) * self.page_size, "include": INCLUDE,
                  "sort": "popular"}
        if query:
            params["q"] = query
        if kind:
            params["type"] = TYPE_OF_KIND[kind]
        j = net.get_json(API, params=params, cache_ttl=3600, min_interval=self.min_interval)
        items = [self._cand(a) for a in j.get("assets") or []]
        total = int(j.get("totalResults") or len(items))
        return {"items": items, "page": page, "total": total, "has_more": bool(j.get("nextPageHttp")) and page * self.page_size < total}

    def get(self, source_id: str) -> dict:
        j = net.get_json(API, params={"id": source_id, "include": INCLUDE}, cache_ttl=3600, min_interval=self.min_interval)
        a = (j.get("assets") or [None])[0]
        if not a:
            raise ConnectorError(f"ambientCG asset '{source_id}' not found")
        return self._cand(a)

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        cand = cand or self.get(source_id)
        dls = (cand.get("extra") or {}).get("downloads") or []
        if not dls:
            raise ConnectorError("No downloads offered")
        res = (ctx.variant.get("texture_res") or "1k").upper()
        fmt = "HDR" if cand["kind"] == "hdri" else "JPG"

        def score(d):
            at = (d.get("attributes") or "").upper()
            m = re.match(r"(\d+)K", at)
            k = int(m.group(1)) if m else 99
            want = int(res.rstrip("K")) if res.rstrip("K").isdigit() else 1
            return (0 if at == f"{res}-{fmt}" else 1, 0 if fmt in at else 1, abs(k - want), k)
        d = sorted(dls, key=score)[0]
        url = d["url"]
        ext = (d.get("extension") or "zip").lower()
        out: list[tuple[Path, str, str | None]] = []
        resolution = (d.get("attributes") or "").split("-")[0].lower() or None
        if ext != "zip":
            p = dest / f"{source_id}_{d.get('attributes') or 'file'}.{ext}"
            ctx.fetch(url, p, min_interval=self.min_interval)
            return Downloaded(files=[(p, "main", resolution)])
        zp = dest / f"{source_id}.zip"
        ctx.fetch(url, zp, min_interval=self.min_interval)
        extracted = safe_extract(zp, dest, max_total=(ctx.remaining() or 2_000_000_000) * 4)
        zp.unlink(missing_ok=True)
        main = None
        for p in extracted:
            stem = p.stem.lower()
            suf = stem.rsplit("_", 1)[-1] if "_" in stem else ""
            if suf == "normaldx" or p.suffix.lower() in (".mtlx", ".usda", ".usdc", ".tres"):
                p.unlink(missing_ok=True)
                continue
            if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                if stem.endswith(("_preview", "_thumb")):
                    out.append((p, "preview", None))
                    continue
                role = MAP_SUFFIX.get(suf)
                if role:
                    out.append((p, f"map:{role}", resolution))
                    if role == "albedo":
                        main = p
                    continue
            if p.suffix.lower() in (".glb", ".gltf", ".obj", ".fbx", ".hdr", ".exr"):
                out.append((p, "main", resolution))
                main = main or p
                continue
            out.append((p, "extra", None))
        if cand["kind"] in ("material", "texture") and main is not None and not any(r == "main" for _, r, _ in out):
            out.append((main, "main", resolution))
        if not any(r == "main" for _, r, _ in out):
            first = next((f for f in out if f[1].startswith("map:")), None)
            if not first:
                raise ConnectorError("The downloaded archive contained no usable files")
            out.append((first[0], "main", resolution))
        return Downloaded(files=out)
