"""NASA Image and Video Library - official public API (https://images-api.nasa.gov, no key). Photos, video and audio
from NASA centres and missions. NASA material is generally not subject to copyright in the US; NASA asks for credit,
no implied endorsement and no use of its insignia/logos. Some items are credited to third parties (contractors,
partner agencies, photographers) - that credit is kept on the asset and flagged so it can be checked before use.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from .. import net
from .base import Connector, ConnectorError, DLContext, Downloaded

API = "https://images-api.nasa.gov"
MEDIA = {"image": "image", "video": "video", "sfx": "audio", "music": "audio", "voice": "audio"}
# preferred renditions, best first (originals can be several GB for video)
PREFS = {"image": ["~large.jpg", "~medium.jpg", "~orig.jpg", "~orig.png", "~orig.tif"],
         "video": ["~medium.mp4", "~mobile.mp4", "~small.mp4", "~preview.mp4", "~large.mp4", "~orig.mp4", ".mp4"],
         "audio": ["~128k.mp3", ".mp3", "~orig.wav", ".wav", ".m4a"]}


PER_FILE_CAP = {"video": 150_000_000, "image": 40_000_000, "audio": 60_000_000}


def _credit(d: dict) -> str:
    return str(d.get("secondary_creator") or d.get("photographer") or d.get("center") or "NASA").strip()


class NASA(Connector):
    id = "nasa"
    name = "NASA Image and Video Library"
    homepage = "https://images.nasa.gov"
    kinds = ["video", "image", "voice"]
    min_interval = 1.0
    page_size = 24
    terms_url = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
    terms_summary = ("No key needed. NASA photos, video and audio are generally not copyrighted: credit NASA, do not imply "
                     "endorsement and do not use NASA logos. Items credited to someone else may be third-party material - check the credit.")
    default_license = "custom:nasa-media"

    def _kind(self, media_type: str) -> str:
        return {"image": "image", "video": "video", "audio": "voice"}.get(media_type, "image")   # audio = podcasts / mission recordings

    def _cand(self, item: dict) -> dict | None:
        d = (item.get("data") or [{}])[0]
        nid = d.get("nasa_id")
        if not nid:
            return None
        mt = d.get("media_type") or "image"
        preview = next((l.get("href") for l in item.get("links") or [] if l.get("rel") == "preview"), None)
        credit = _credit(d)
        third_party = "nasa" not in credit.lower() and credit.upper() not in {"KSC", "JSC", "MSFC", "GSFC", "JPL", "ARC", "GRC",
                                                                              "LARC", "AFRC", "SSC", "HQ", "WSTF"}
        tags = [str(k) for k in (d.get("keywords") or []) if isinstance(k, str)][:20] + ["nasa", "space"]
        return self.candidate(source_id=nid, kind=self._kind(mt), name=(d.get("title") or nid)[:200],
                              description=str(d.get("description") or "")[:2000], tags=tags,
                              source_url=f"https://images.nasa.gov/details/{quote(nid)}", author=credit,
                              license="custom:nasa-media", preview_url=preview,
                              stats={k: v for k, v in {"date": d.get("date_created"), "center": d.get("center")}.items() if v},
                              extra={"media_type": mt, "manifest": item.get("href"), "third_party_credit": third_party,
                                     "category": "space"})

    def search(self, query: str, kind: str | None = None, page: int = 1) -> dict:
        if kind and kind not in MEDIA:
            return {"items": [], "page": page, "total": 0, "has_more": False}
        params = {"q": (query or "").strip() or "earth", "page": max(1, int(page)), "page_size": self.page_size,
                  "media_type": MEDIA.get(kind or "", "image,video,audio")}
        j = net.get_json(f"{API}/search", params=params, cache_ttl=6 * 3600, min_interval=self.min_interval)
        col = j.get("collection") or {}
        items = [c for c in (self._cand(it) for it in col.get("items") or []) if c]
        total = int((col.get("metadata") or {}).get("total_hits") or len(items))
        return {"items": items, "page": page, "total": total,
                "has_more": any(l.get("rel") == "next" for l in col.get("links") or [])}

    def get(self, source_id: str) -> dict:
        j = net.get_json(f"{API}/search", params={"nasa_id": source_id}, cache_ttl=24 * 3600, min_interval=self.min_interval)
        items = (j.get("collection") or {}).get("items") or []
        c = self._cand(items[0]) if items else None
        if not c:
            raise ConnectorError("NASA item not found")
        return c

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        cand = cand if cand and (cand.get("extra") or {}).get("manifest") else self.get(source_id)
        files = net.get_json(f"{API}/asset/{quote(source_id)}", cache_ttl=24 * 3600, min_interval=self.min_interval)
        hrefs = [i.get("href") for i in ((files.get("collection") or {}).get("items") or []) if i.get("href")]
        mt = cand["extra"].get("media_type") or "image"
        picks: list[str] = []
        for suf in PREFS.get(mt, []):
            picks += [h for h in hrefs if h.lower().endswith(suf) and h not in picks]
        if not picks:
            raise ConnectorError(f"No downloadable {mt} rendition found")
        cap = PER_FILE_CAP.get(mt)
        last: Exception | None = None
        for pick in picks:
            pick = pick.replace("http://", "https://", 1)
            p = dest / Path(pick.split("?")[0]).name.replace("~", "_")
            rem = ctx.remaining()
            lim = min(x for x in (rem, cap) if x is not None) if (rem is not None or cap is not None) else None
            try:
                r = net.download(pick, p, min_interval=self.min_interval, max_bytes=lim, check=ctx.check)
            except net.CapExceeded as e:     # too big: try the next (smaller) rendition
                last = e
                continue
            ctx.used += r["bytes"]
            break
        else:
            raise last or ConnectorError("No rendition fits the size limit")
        stats = {"rendition": Path(pick).name.split("~")[-1]} if "~" in pick else {}
        return Downloaded(files=[(p, "main", None)], stats=stats)
