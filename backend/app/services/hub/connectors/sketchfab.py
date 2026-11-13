"""Sketchfab - Data API v3 search (public) + Download API (needs the user's own API token,
`Authorization: Token <token>`, from https://sketchfab.com/settings/password). Only Creative-Commons-licensed,
downloadable models are used; Editorial/Store licences are always excluded. Guidelines require showing each model's
licence and crediting the author + Sketchfab; download URLs expire after ~5 minutes and are never cached."""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import meta, net
from ..ziputil import safe_extract
from .base import Connector, ConnectorError, DLContext, Downloaded

API = "https://api.sketchfab.com/v3"
SLUG_TO_LICENSE = {"cc0": "CC0-1.0", "by": "CC-BY-4.0", "by-sa": "CC-BY-SA-4.0", "by-nd": "CC-BY-ND-4.0", "by-nc": "CC-BY-NC-4.0",
                   "by-nc-sa": "CC-BY-NC-SA-4.0", "by-nc-nd": "CC-BY-NC-ND-4.0"}
POLICY_SLUGS = {"cc0": {"cc0"}, "commercial": {"cc0", "by", "by-sa"}, "any": set(SLUG_TO_LICENSE)}
LABEL_TO_SLUG = {"cc0 public domain": "cc0", "cc attribution": "by", "cc attribution-sharealike": "by-sa",
                 "cc attribution-noderivs": "by-nd", "cc attribution-noncommercial": "by-nc",
                 "cc attribution-noncommercial-sharealike": "by-nc-sa", "cc attribution-noncommercial-noderivs": "by-nc-nd"}


def license_slug(lic) -> str | None:
    if isinstance(lic, dict):
        s = (lic.get("slug") or "").lower()
        if s:
            return s
        lab = (lic.get("label") or lic.get("fullName") or "").lower()
        return LABEL_TO_SLUG.get(lab)
    if isinstance(lic, str):
        s = lic.lower()
        return s if s in SLUG_TO_LICENSE else LABEL_TO_SLUG.get(s)
    return None


class Sketchfab(Connector):
    id = "sketchfab"
    name = "Sketchfab"
    homepage = "https://sketchfab.com"
    kinds = ["model", "animation"]
    key_name = "sketchfab"
    key_required = False          # search works without; downloads need the token
    min_interval = 0.6
    terms_url = "https://sketchfab.com/developers/download-api/guidelines"
    terms_summary = ("Search is public; downloads need your own API token. Only CC licences are used (CC0, CC-BY, CC-BY-SA by default; NC/ND only with policy 'any'). "
                     "You must display each model's licence and credit the author and Sketchfab. Calls may be throttled (HTTP 429).")

    def available(self) -> tuple[bool, str]:
        return True, "ready" if self.key() else "search only - add your API token to download"

    def _cand(self, m: dict) -> dict | None:
        slug = license_slug(m.get("license"))
        if slug not in SLUG_TO_LICENSE:
            return None
        imgs = (m.get("thumbnails") or {}).get("images") or []
        thumb = min(imgs, key=lambda i: abs(int(i.get("width") or 0) - 512))["url"] if imgs else None
        user = m.get("user") or {}
        anim = int(m.get("animationCount") or 0)
        c = self.candidate(source_id=m["uid"], kind="model",name=m.get("name") or m["uid"],
                           description=(m.get("description") or "")[:2000],
                           tags=[t.get("name") if isinstance(t, dict) else str(t) for t in m.get("tags") or []],
                           source_url=m.get("viewerUrl") or f"https://sketchfab.com/3d-models/{m['uid']}",
                           author=user.get("displayName") or user.get("username"), author_url=user.get("profileUrl"),
                           license=SLUG_TO_LICENSE[slug], preview_url=thumb, formats=["glb"],
                           stats={k: v for k, v in {"polycount": m.get("faceCount"), "vertices": m.get("vertexCount"),
                                                    "animation_count": anim or None}.items() if v},
                           downloadable=bool(m.get("isDownloadable", True)))
        if anim:
            c["tags"].append("animated")
        return c

    def search(self, query: str, kind: str | None = None, page: int = 1, policy: str = "commercial") -> dict:
        if kind and kind not in self.kinds:
            return {"items": [], "page": page, "total": 0, "has_more": False}
        size = 24
        params = {"type": "models", "q": query or "", "downloadable": "true", "count": size, "cursor": (max(1, page) - 1) * size}
        slugs = POLICY_SLUGS.get(policy, POLICY_SLUGS["commercial"])
        if policy == "cc0":
            params["license"] = "cc0"
        if kind == "animation":
            params["animated"] = "true"
        tok = self.key()
        j = net.get_json(f"{API}/search", params=params, cache_ttl=3600, min_interval=self.min_interval,
                         headers={"Authorization": f"Token {tok}"} if tok else None)
        items = []
        for m in j.get("results") or []:
            c = self._cand(m)
            if c and license_slug(m.get("license")) in slugs:
                items.append(c)
        return {"items": items, "page": page, "total": int(j.get("totalCount") or 0) or None, "has_more": bool(j.get("next"))}

    def get(self, source_id: str) -> dict:
        m = net.get_json(f"{API}/models/{source_id}", cache_ttl=3600, min_interval=self.min_interval)
        c = self._cand(m)
        if not c:
            raise ConnectorError("This model does not carry a Creative Commons licence")
        return c

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        token = self.key()
        if not token:
            raise ConnectorError("Sketchfab downloads need your API token - add it under Sources & keys")
        cand = cand or self.get(source_id)
        j = net.get_json(f"{API}/models/{source_id}/download", headers={"Authorization": f"Token {token}"},
                         min_interval=self.min_interval)
        if (j.get("glb") or {}).get("url"):
            p = dest / f"{source_id}.glb"
            ctx.fetch(j["glb"]["url"], p, min_interval=self.min_interval)
            return Downloaded(files=[(p, "main", None)])
        if (j.get("gltf") or {}).get("url"):
            zp = dest / f"{source_id}_gltf.zip"
            ctx.fetch(j["gltf"]["url"], zp, min_interval=self.min_interval)
            src = dest / "_gltf"
            files = safe_extract(zp, src, max_total=3_000_000_000)
            zp.unlink(missing_ok=True)
            g = next((f for f in files if f.suffix.lower() == ".gltf"), None)
            if not g:
                raise ConnectorError("The glTF archive had no .gltf file")
            glb = dest / f"{source_id}.glb"
            meta.gltf_to_glb(g, glb)
            shutil.rmtree(src, ignore_errors=True)
            return Downloaded(files=[(glb, "main", None)])
        raise ConnectorError("Sketchfab offered no glTF/GLB download for this model")
