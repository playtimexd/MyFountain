"""Openverse - official API (https://api.openverse.org/v1/, run by WordPress.org). Searches openly licensed images and
audio aggregated from Flickr, Wikimedia Commons, Freesound, Jamendo, museums and more; every result carries its own
Creative Commons licence / public-domain mark, creator and landing page, which we store per asset.

Anonymous use is allowed and rate limited (20 requests/min, 200/day at the time of writing), so we wait ~3 s
between calls and cache search pages. Files are fetched from the original provider's URL that Openverse returns.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlparse

from .. import licenses, net
from .base import Connector, ConnectorError, DLContext, Downloaded

API = "https://api.openverse.org/v1"
# Freesound items carry no Openverse category, so sound effects are searched by source instead
AUDIO_FILTER = {"sfx": {"source": "freesound"}, "music": {"category": "music"}}
PER_FILE_CAP = 30_000_000          # skip giant originals (e.g. 50 MB Flickr scans)
POLICY_FILTER ={"cc0": {"license": "cc0,pdm"}, "commercial": {"license_type": "commercial,modification"}}


def _license(r: dict) -> str:
    lic = str(r.get("license") or "").lower()
    ver = str(r.get("license_version") or "")
    if lic == "cc0":
        return "CC0-1.0"
    if lic == "pdm":
        return "PDM-1.0"
    if lic.startswith("by"):
        lid = f"CC-{lic.upper()}-{ver or '4.0'}"
        return lid if lid in licenses.LICENSES else licenses.from_text(r.get("license_url") or f"cc {lic} {ver}")
    return licenses.from_text(r.get("license_url") or lic)


class Openverse(Connector):
    id = "openverse"
    name = "Openverse"
    homepage = "https://openverse.org"
    kinds = ["image", "sfx", "music"]
    min_interval = 3.2
    page_size = 20                      # anonymous maximum
    terms_url = "https://docs.openverse.org/terms_of_service.html"
    terms_summary = ("No key needed. Openly licensed images and audio from Flickr, Wikimedia Commons, Freesound, Jamendo, museums… "
                     "Each item keeps its own licence (CC0 / public domain / CC BY / CC BY-SA…) - credit the creator when it says BY. "
                     "Anonymous API use is limited to about 20 searches a minute and 200 a day.")
    default_license = "custom:unknown"

    def _media(self, kind: str | None) -> str:
        return "audio" if kind in ("sfx", "music", "voice") else "images"

    def _cand(self, r: dict, media: str) -> dict:
        lic = _license(r)
        tags = [t.get("name") for t in r.get("tags") or [] if isinstance(t, dict) and t.get("name")][:20]
        prov = r.get("provider") or r.get("source") or ""
        if media == "audio":
            cat = str(r.get("category") or "")
            genres = r.get("genres") or []
            kind = "music" if cat == "music" or genres or prov == "jamendo" else "sfx"
            stats = {k: v for k, v in {"duration": (r.get("duration") or 0) / 1000 or None, "bytes": r.get("filesize")}.items() if v}
            fmt = (r.get("filetype") or Path(urlparse(r.get("url") or "").path).suffix.lstrip(".") or "mp3").lower()
            tags += list(genres)
        else:
            kind = "image"
            cat = str(r.get("category") or "")
            stats = {k: v for k, v in {"width": r.get("width"), "height": r.get("height"), "bytes": r.get("filesize")}.items() if v}
            fmt = (r.get("filetype") or Path(urlparse(r.get("url") or "").path).suffix.lstrip(".") or "jpg").lower()
        c = self.candidate(source_id=f"{media}:{r['id']}", kind=kind, name=(r.get("title") or r["id"])[:200], tags=tags,
                           source_url=r.get("foreign_landing_url"), author=r.get("creator"), author_url=r.get("creator_url"),
                           license=lic, preview_url=r.get("thumbnail") if media == "images" else None, formats=[fmt], stats=stats,
                           extra={"provider": prov, "category": cat, "file_url": r.get("url"), "filetype": fmt,
                                  "openverse_attribution": r.get("attribution"), "openverse_id": r["id"]})
        return c

    def search(self, query: str, kind: str | None = None, page: int = 1, policy: str = "commercial") -> dict:
        if kind and kind not in self.kinds:
            return {"items": [], "page": page, "total": 0, "has_more": False}
        media = self._media(kind)
        params: dict = {"q": (query or "").strip() or ("sound effect" if kind == "sfx" else "music" if kind == "music" else "texture"),
                        "page": max(1, int(page)), "page_size": self.page_size, "mature": "false"}
        params.update(POLICY_FILTER.get(policy, {}))
        if media == "audio" and kind in AUDIO_FILTER:
            params.update(AUDIO_FILTER[kind])
        j = net.get_json(f"{API}/{media}/", params=params, cache_ttl=6 * 3600, min_interval=self.min_interval)
        items = [self._cand(r, media) for r in j.get("results") or [] if r.get("id") and r.get("url")]
        total = int(j.get("result_count") or 0)
        return {"items": items, "page": page, "total": total, "has_more": page < int(j.get("page_count") or 0)}

    def get(self, source_id: str) -> dict:
        media, _, oid = str(source_id).partition(":")
        if media not in ("images", "audio") or not oid:
            raise ConnectorError("Bad Openverse id")
        r = net.get_json(f"{API}/{media}/{quote(oid)}/", cache_ttl=24 * 3600, min_interval=self.min_interval)
        if not r or not r.get("url"):
            raise ConnectorError("Openverse item not found")
        return self._cand(r, media)

    def attribution(self, c: dict) -> str:
        return (c.get("extra") or {}).get("openverse_attribution") or super().attribution(c)

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        cand = cand if cand and (cand.get("extra") or {}).get("file_url") else self.get(source_id)
        url = cand["extra"]["file_url"]
        if not str(url).startswith("https://") and not str(url).startswith("http://"):
            raise ConnectorError("No file URL for this item")
        ext = (cand["extra"].get("filetype") or "bin").lower().lstrip(".")
        ext = {"jpeg": "jpg"}.get(ext, ext)
        p = dest / f"{source_id.split(':', 1)[1]}.{ext}"
        rem = ctx.remaining()
        lim = PER_FILE_CAP if rem is None else min(rem, PER_FILE_CAP)
        r = net.download(url, p, min_interval=1.0, max_bytes=lim, check=ctx.check)   # CapExceeded -> skipped
        ctx.used += r["bytes"]
        return Downloaded(files=[(p, "main", None)])
