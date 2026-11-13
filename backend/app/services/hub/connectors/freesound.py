"""Freesound - official APIv2 (https://freesound.org/docs/api/), token auth (`Authorization: Token <key>`).
Previews (HQ MP3) are fetched with token auth only; original files need OAuth2 (not implemented yet).
Limits: 60 requests/min, 2000/day. API terms: free API use is for non-commercial purposes; credit Freesound and each
sound's author according to its licence; keep only copies you need (no mirroring of the database)."""
from __future__ import annotations

from pathlib import Path

from .. import licenses, net
from .base import Connector, ConnectorError, DLContext, Downloaded

API = "https://freesound.org/apiv2"
FIELDS = "id,name,tags,description,license,username,previews,duration,channels,samplerate,url,images,avg_rating,num_downloads"
POLICY_FILTER = {"cc0": 'license:"Creative Commons 0"',
                 "commercial": '(license:"Creative Commons 0" OR license:"Attribution")', "any": ""}
KIND_FILTER = {"sfx": "duration:[0 TO 20]", "music": "duration:[15 TO *] tag:music", "voice": "tag:voice"}


class Freesound(Connector):
    id = "freesound"
    name = "Freesound"
    homepage = "https://freesound.org"
    kinds = ["sfx", "music", "voice"]
    key_name = "freesound"
    key_required = True
    min_interval = 1.1           # stays under 60 requests/minute
    page_size = 24
    terms_url = "https://freesound.org/docs/api/terms_of_use.html"
    terms_summary = ("API key from freesound.org/apiv2/apply. 60 req/min, 2000 req/day. Each sound has its own licence (CC0 / CC-BY / CC-BY-NC) - "
                     "credit the author for CC-BY. Free API use is non-commercial; don't mirror the database. HQ MP3 previews only (originals need OAuth).")
    default_license = "CC0-1.0"

    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.require_key()}"}

    def _cand(self, s: dict, kind: str | None) -> dict:
        lic = licenses.from_text(s.get("license"))
        dur = float(s.get("duration") or 0)
        k = kind if kind in self.kinds else ("music" if dur >= 20 else "sfx")
        imgs = s.get("images") or {}
        return self.candidate(source_id=s["id"], kind=k, name=s.get("name") or str(s["id"]), description=(s.get("description") or "")[:2000],
                              tags=s.get("tags") or [], source_url=s.get("url") or f"https://freesound.org/s/{s['id']}/",
                              author=s.get("username"), author_url=f"https://freesound.org/people/{s.get('username')}/" if s.get("username") else None,
                              license=lic, preview_url=imgs.get("waveform_m") or imgs.get("waveform_l"), formats=["mp3"],
                              stats={"duration": dur, "channels": s.get("channels"), "sample_rate": s.get("samplerate")},
                              extra={"previews": s.get("previews") or {}, "license_raw": s.get("license"),
                                     "audio_preview": (s.get("previews") or {}).get("preview-hq-mp3")})

    def search(self, query: str, kind: str | None = None, page: int = 1, policy: str = "cc0") -> dict:
        if kind and kind not in self.kinds:
            return {"items": [], "page": page, "total": 0, "has_more": False}
        filt = " ".join(x for x in (POLICY_FILTER.get(policy, POLICY_FILTER["cc0"]), KIND_FILTER.get(kind or "", "")) if x)
        params = {"query": query or "", "fields": FIELDS, "page": max(1, page), "page_size": self.page_size, "sort": "score"}
        if filt:
            params["filter"] = filt
        j = net.get_json(f"{API}/search/", params=params, headers=self._headers(), cache_ttl=3600, min_interval=self.min_interval)
        items = [self._cand(s, kind) for s in j.get("results") or []]
        total = int(j.get("count") or 0)
        return {"items": items, "page": page, "total": total, "has_more": bool(j.get("next"))}

    def get(self, source_id: str) -> dict:
        j = net.get_json(f"{API}/sounds/{int(source_id)}/", params={"fields": FIELDS}, headers=self._headers(),
                         cache_ttl=3600, min_interval=self.min_interval)
        return self._cand(j, None)

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        cand = cand if cand and (cand.get("extra") or {}).get("previews") else self.get(source_id)
        pv = cand["extra"]["previews"]
        url = pv.get("preview-hq-mp3") or pv.get("preview-lq-mp3")
        if not url:
            raise ConnectorError("This sound has no MP3 preview")
        p = dest / f"{source_id}_preview-hq.mp3"
        ctx.fetch(url, p, min_interval=self.min_interval)    # CDN preview: token is not sent
        return Downloaded(files=[(p, "main", None)], stats={"note": "HQ MP3 preview (~128 kbps); original file requires OAuth"})
