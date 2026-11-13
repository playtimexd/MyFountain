"""Connector interface. Every connector maps a site's official API (or official download links / a curated
catalog) to *candidates* — unified records without local files — and can download one candidate into a folder.

Candidate: {source, source_id, kind, name, description, tags[], source_url, author, author_url, license,
            preview_url, stats{}, formats[], downloadable(bool), manual(bool: user must download it by hand), extra{}}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import licenses, net, paths


class ConnectorError(RuntimeError):
    pass


class NeedsKey(ConnectorError):
    pass


@dataclass
class DLContext:
    """Passed to download(): remaining byte budget + cancellation hook + variant options."""
    variant: dict = field(default_factory=dict)
    max_bytes: int | None = None
    check: Callable[[], None] = lambda: None
    used: int = 0

    def remaining(self) -> int | None:
        return None if self.max_bytes is None else max(0, self.max_bytes - self.used)

    def fetch(self, url: str, dest: Path, *, headers: dict | None = None, min_interval: float = 0.5) -> Path:
        r = net.download(url, dest, headers=headers, min_interval=min_interval, max_bytes=self.remaining(), check=self.check)
        self.used += r["bytes"]
        return dest


@dataclass
class Downloaded:
    files: list[tuple[Path, str, str | None]]          # (path, role, resolution)
    stats: dict = field(default_factory=dict)
    updates: dict = field(default_factory=dict)        # candidate fields to override (e.g. license after download)


class Connector:
    id = "base"
    name = "Base"
    homepage = ""
    kinds: list[str] = []
    key_name: str | None = None        # paths.KEY_NAMES entry
    key_required = False
    automated = True                   # False = catalog only, user imports zips by hand
    min_interval = 0.5
    terms_url = ""
    terms_summary = ""
    default_license = "custom:unknown"
    page_size = 24

    # --- helpers -----------------------------------------------------------------
    def key(self) -> str:
        return paths.get_key(self.key_name) if self.key_name else ""

    def require_key(self) -> str:
        k = self.key()
        if not k:
            raise NeedsKey(f"{self.name} needs an API key - add it under Sources & keys")
        return k

    def available(self) -> tuple[bool, str]:
        if self.key_required and not self.key():
            return False, "API key required"
        return True, "ready" if self.automated else "catalog only - download packs manually, then use Import pack zip"

    def describe(self) -> dict:
        ok, note = self.available()
        return {"id": self.id, "name": self.name, "homepage": self.homepage, "kinds": self.kinds, "available": ok, "note": note,
                "needs_key": self.key_required, "key_name": self.key_name, "key_set": bool(self.key()) if self.key_name else None,
                "automated": self.automated, "terms_url": self.terms_url, "terms": self.terms_summary,
                "default_license": self.default_license}

    def candidate(self, **kw) -> dict:
        c = {"source": self.id, "kind": "model", "name": "", "description": "", "tags": [], "source_url": None, "author": None,
             "author_url": None, "license": self.default_license, "preview_url": None, "stats": {}, "formats": [],
             "downloadable": self.automated, "manual": not self.automated, "extra": {}}
        c.update({k: v for k, v in kw.items() if v is not None})
        c["source_id"] = str(c["source_id"])
        li = licenses.info(c["license"])
        c["license_label"], c["license_class"], c["license_url"] = li["label"], li["class"], li["url"]
        c["commercial_ok"], c["modifiable"] = li["commercial_ok"], li["modifiable"]
        return c

    def attribution(self, c: dict) -> str:
        return licenses.attribution(c.get("name") or c["source_id"], c.get("author"), self.name, c.get("source_url"), c["license"])

    # --- interface -----------------------------------------------------------------
    def search(self, query: str, kind: str | None = None, page: int = 1) -> dict:
        raise NotImplementedError

    def get(self, source_id: str) -> dict:
        raise NotImplementedError

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        raise ConnectorError(f"{self.name} does not support automatic downloads - download the pack manually and use Import pack zip")


def page_slice(items: list, page: int, size: int) -> dict:
    page = max(1, int(page))
    chunk = items[(page - 1) * size: page * size]
    return {"items": chunk, "page": page, "total": len(items), "has_more": page * size < len(items)}


def match(query: str, *fields) -> bool:
    q = [t for t in (query or "").lower().split() if t]
    if not q:
        return True
    hay = " ".join(" ".join(f) if isinstance(f, (list, tuple, set)) else str(f or "") for f in fields).lower()
    return all(t in hay for t in q)
