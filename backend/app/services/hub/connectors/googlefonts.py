"""Google Fonts - the official repository github.com/google/fonts (ofl/ apache/ ufl/ folders; METADATA.pb per family)
read through the GitHub API (family index, cached for a day) and raw.githubusercontent.com (files). Optional Google
Fonts Developer API key enables popularity-ranked search. Licences: OFL-1.1 (most), Apache-2.0, UFL-1.0."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from .. import net
from .base import Connector, ConnectorError, DLContext, Downloaded, match, page_slice

GH_API = "https://api.github.com/repos/google/fonts"
RAW = "https://raw.githubusercontent.com/google/fonts/main"
DIR_LICENSE = {"ofl": "OFL-1.1", "apache": "Apache-2.0", "ufl": "UFL-1.0"}
LICENSE_FILES = ("OFL.txt", "LICENSE.txt", "UFL.txt")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_metadata_pb(text: str) -> dict:
    """Tiny parser for the protobuf text format used by METADATA.pb (top-level scalars + `fonts {}` blocks)."""
    out: dict = {"fonts": []}
    cur: dict | None = None
    depth = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("{"):
            depth += 1
            if depth == 1 and line.startswith("fonts"):
                cur = {}
            continue
        if line == "}":
            depth -= 1
            if depth == 0 and cur is not None:
                out["fonts"].append(cur)
                cur = None
            continue
        m = re.match(r'(\w+):\s*"?(.*?)"?$', line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if depth == 0:
            if k in out and k != "fonts":
                out[k] = out[k] if isinstance(out[k], list) else [out[k]]
                out[k].append(v)
            else:
                out[k] = v
        elif depth == 1 and cur is not None:
            cur[k] = v
    return out


class GoogleFonts(Connector):
    id = "googlefonts"
    name = "Google Fonts"
    homepage = "https://fonts.google.com"
    kinds = ["font"]
    key_name = "google_fonts"
    key_required = False
    min_interval = 0.3
    terms_url = "https://github.com/google/fonts#license"
    terms_summary = ("Open-source fonts from the official google/fonts repository: mostly SIL OFL 1.1 (free incl. commercial, "
                     "embed in games; don't sell the font by itself), some Apache-2.0 / Ubuntu Font Licence. No key needed; "
                     "an optional Developer API key adds popularity ranking.")
    default_license = "OFL-1.1"

    def available(self) -> tuple[bool, str]:
        return True, "ready (popularity ranking enabled)" if self.key() else "ready (repository index; add an API key for popularity ranking)"

    def index(self) -> list[tuple[str, str]]:
        """[(license_dir, family_dir)] for the whole repository (5 GitHub API calls, cached 24 h)."""
        root = net.get_json(f"{GH_API}/git/trees/main", cache_ttl=24 * 3600, min_interval=self.min_interval)
        out = []
        for e in root.get("tree") or []:
            if e.get("type") == "tree" and e.get("path") in DIR_LICENSE:
                sub = net.get_json(f"{GH_API}/git/trees/{e['sha']}", cache_ttl=24 * 3600, min_interval=self.min_interval)
                out += [(e["path"], x["path"]) for x in sub.get("tree") or [] if x.get("type") == "tree"]
        if not out:
            raise ConnectorError("Could not read the google/fonts repository index")
        return out

    def _cand(self, lic_dir: str, fam_dir: str, family: str | None = None, category: str | None = None, extra=None) -> dict:
        fam = family or fam_dir
        return self.candidate(source_id=f"{lic_dir}/{fam_dir}", kind="font", name=fam, tags=[t for t in ["font", (category or "").lower()] if t],
                              source_url=f"https://fonts.google.com/specimen/{quote(fam.replace(' ', '+'), safe='+')}" if family else
                              f"https://github.com/google/fonts/tree/main/{lic_dir}/{fam_dir}",
                              author=None, license=DIR_LICENSE[lic_dir], formats=["ttf"],
                              extra={**({"category": category.lower()} if category else {}), **(extra or {})})

    def search(self, query: str, kind: str | None = None, page: int = 1) -> dict:
        if kind and kind != "font":
            return page_slice([], page, self.page_size)
        idx = self.index()
        by_norm = {norm(f): (l, f) for l, f in idx}
        key = self.key()
        if key:
            j = net.get_json("https://www.googleapis.com/webfonts/v1/webfonts", params={"key": key, "sort": "popularity"},
                             cache_ttl=24 * 3600, min_interval=self.min_interval)
            rows = []
            for it in j.get("items") or []:
                hit = by_norm.get(norm(it.get("family", "")))
                if hit and match(query, it.get("family"), it.get("category")):
                    rows.append(self._cand(hit[0], hit[1], it.get("family"), it.get("category")))
            return page_slice(rows, page, self.page_size)
        qn = norm(query or "")
        rows = [self._cand(l, f) for l, f in idx if not qn or qn in f]
        rows.sort(key=lambda c: (0 if norm(c["name"]) == qn else 1, len(c["name"]), c["name"]))
        return page_slice(rows, page, self.page_size)

    def _metadata(self, source_id: str) -> dict:
        return parse_metadata_pb(net.get_text(f"{RAW}/{source_id}/METADATA.pb", cache_ttl=24 * 3600, min_interval=self.min_interval))

    def get(self, source_id: str) -> dict:
        lic_dir, fam_dir = source_id.split("/", 1)
        if lic_dir not in DIR_LICENSE:
            raise ConnectorError("Unknown font folder")
        md = self._metadata(source_id)
        c = self._cand(lic_dir, fam_dir, md.get("name"), font_category(md))
        c["author"] = md.get("designer") if isinstance(md.get("designer"), str) else None
        c["extra"]["metadata"] = md
        return c

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        cand = cand if cand and (cand.get("extra") or {}).get("metadata") else self.get(source_id)
        md = cand["extra"]["metadata"]
        fonts = [f for f in md.get("fonts") or [] if f.get("filename")]
        if not fonts:
            raise ConnectorError("No font files listed in METADATA.pb")
        if ctx.variant.get("font_files") != "all" and len(fonts) > 4:
            fonts = [f for f in fonts if f.get("weight") in ("400", "700")] or fonts[:2]
        out: list[tuple[Path, str, str | None]] = []
        for i, f in enumerate(sorted(fonts, key=lambda f: (f.get("style") != "normal", f.get("weight") != "400"))):
            ctx.check()
            fn = f["filename"]
            p = dest / Path(fn).name
            ctx.fetch(f"{RAW}/{source_id}/{quote(fn)}", p, min_interval=self.min_interval)
            out.append((p, "main" if i == 0 else f"style:{f.get('style', 'normal')}-{f.get('weight', '400')}", None))
        for lf in LICENSE_FILES:
            try:
                p = dest / lf
                ctx.fetch(f"{RAW}/{source_id}/{lf}", p, min_interval=self.min_interval)
                out.append((p, "license", None))
                break
            except Exception:
                continue
        return Downloaded(files=out, updates={"author": cand.get("author")})


def font_category(md: dict) -> str | None:
    """'SANS_SERIF' / ['DISPLAY', ...] in METADATA.pb -> 'sans serif' / 'display'."""
    cat = md.get("category")
    if isinstance(cat, list):
        cat = cat[0] if cat else None
    return cat.replace("_", " ").lower() if isinstance(cat, str) and cat else None


def backfill_categories(progress=lambda p, m: None) -> dict:
    """Give hub fonts imported without a design category one, from their METADATA.pb (tiny text files)."""
    from .. import db
    rows = [a for a in db.search({"source": "googlefonts"}, page=1, page_size=5000)["items"]]
    done = 0
    con = GoogleFonts()
    for i, a in enumerate(rows):
        full = db.get(a["id"]) or {}
        if (full.get("extra") or {}).get("category"):
            continue
        progress(i / max(1, len(rows)), a["name"])
        try:
            cat = font_category(con._metadata(a["source_id"]))
        except Exception:
            continue
        if cat:
            db.merge_extra(a["id"], {"category": cat})
            done += 1
    return {"fonts": len(rows), "updated": done}
