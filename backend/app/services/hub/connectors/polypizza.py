"""Poly Pizza - official API v1.1 (https://api.poly.pizza/v1.1, header X-Auth-Token; free key at
https://poly.pizza/settings/api). Low-poly GLB models under CC0 or CC-BY. Endpoint/field names follow the site
author's own client (github.com/Chikanz/pizzabox); fields are parsed case-insensitively."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from .. import licenses, net
from .base import Connector, ConnectorError, DLContext, Downloaded

API = "https://api.poly.pizza/v1.1"


def g(d: dict, *names, default=None):
    if not isinstance(d, dict):
        return default
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] is not None:
            return low[n.lower()]
    return default


class PolyPizza(Connector):
    id = "polypizza"
    name = "Poly Pizza"
    homepage = "https://poly.pizza"
    kinds = ["model", "animation"]
    key_name = "polypizza"
    key_required = True
    min_interval = 1.0
    page_size = 32
    terms_url = "https://poly.pizza/docs/api"
    terms_summary = ("Free API key from poly.pizza/settings/api. Models are CC0 or CC-BY (credit the creator for CC-BY). "
                     "The site describes API use as free for hobby use, pay-as-you-go for commercial use; no published rate limit (we wait 1 s between calls).")

    def _headers(self) -> dict:
        return {"X-Auth-Token": self.require_key()}

    def _cand(self, m: dict) -> dict:
        creator = g(m, "Creator") or {}
        lic = licenses.from_text(g(m, "Licence", "License"))
        animated = bool(g(m, "Animated", default=False))
        sid = g(m, "ID", "Id")
        c = self.candidate(source_id=sid, kind="model", name=g(m, "Title", default=sid), description=g(m, "Description", default="") or "",
                           tags=list(g(m, "Tags", default=[]) or []) + ([g(m, "Category")] if isinstance(g(m, "Category"), str) else []),
                           source_url=f"https://poly.pizza/m/{sid}", author=g(creator, "Username"),
                           author_url=f"https://poly.pizza/u/{g(creator, 'Username')}" if g(creator, "Username") else None,
                           license=lic, preview_url=g(m, "Thumbnail"), formats=["glb"],
                           stats={k: v for k, v in {"polycount": g(m, "TriCount", "TriangleCount"), "animated": animated}.items() if v is not None},
                           extra={"download": g(m, "Download"), "attribution": g(m, "Attribution")})
        if animated:
            c["tags"].append("animated")
        return c

    def search(self, query: str, kind: str | None = None, page: int = 1) -> dict:
        if kind and kind not in self.kinds:
            return {"items": [], "page": page, "total": 0, "has_more": False}
        q = (query or "").strip()
        params = {"limit": self.page_size, "page": max(0, page - 1)}
        url = f"{API}/search/{quote(q)}" if len(q) > 2 else f"{API}/search"
        j = net.get_json(url, params=params, headers=self._headers(), cache_ttl=3600, min_interval=self.min_interval)
        results = g(j, "Results", default=[]) or []
        items = [self._cand(m) for m in results]
        if kind == "animation":
            items = [i for i in items if i["stats"].get("animated")]
        total = int(g(j, "Total", default=len(items)) or 0)
        return {"items": items, "page": page, "total": total, "has_more": page * self.page_size < total and bool(results)}

    def get(self, source_id: str) -> dict:
        j = net.get_json(f"{API}/model/{quote(source_id)}", headers=self._headers(), cache_ttl=3600, min_interval=self.min_interval)
        if not j:
            raise ConnectorError("Model not found")
        return self._cand(j)

    def attribution(self, c: dict) -> str:
        a = (c.get("extra") or {}).get("attribution")
        return a or super().attribution(c)

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        cand = cand if cand and (cand.get("extra") or {}).get("download") else self.get(source_id)
        url = cand["extra"].get("download")
        if not url:
            raise ConnectorError("No GLB download URL for this model")
        p = dest / f"{source_id}.glb"
        ctx.fetch(url, p, min_interval=self.min_interval)   # CDN link: the key is never sent there
        return Downloaded(files=[(p, "main", None)])
