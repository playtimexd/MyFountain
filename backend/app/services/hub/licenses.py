"""License normalisation (SPDX-style ids), classification and attribution text."""
from __future__ import annotations

import re

# id -> (label, url, commercial_ok, modifiable, attribution_required)
LICENSES: dict[str, tuple[str, str, bool, bool, bool]] = {
    "CC0-1.0": ("CC0 1.0 (Public Domain)", "https://creativecommons.org/publicdomain/zero/1.0/", True, True, False),
    "CC-BY-3.0": ("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0/", True, True, True),
    "CC-BY-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/", True, True, True),
    "CC-BY-SA-3.0": ("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/", True, True, True),
    "CC-BY-SA-4.0": ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/", True, True, True),
    "CC-BY-ND-4.0": ("CC BY-ND 4.0", "https://creativecommons.org/licenses/by-nd/4.0/", True, False, True),
    "CC-BY-NC-3.0": ("CC BY-NC 3.0", "https://creativecommons.org/licenses/by-nc/3.0/", False, True, True),
    "CC-BY-NC-4.0": ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/", False, True, True),
    "CC-BY-NC-SA-4.0": ("CC BY-NC-SA 4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/", False, True, True),
    "CC-BY-NC-ND-4.0": ("CC BY-NC-ND 4.0", "https://creativecommons.org/licenses/by-nc-nd/4.0/", False, False, True),
    "OFL-1.1": ("SIL Open Font License 1.1", "https://openfontlicense.org/", True, True, False),
    "Apache-2.0": ("Apache License 2.0", "https://www.apache.org/licenses/LICENSE-2.0", True, True, False),
    "UFL-1.0": ("Ubuntu Font Licence 1.0", "https://ubuntu.com/legal/font-licence", True, True, False),
    # custom licences we know about
    "custom:quaternius-qal-1.0": ("Quaternius Asset License 1.0 (free incl. commercial, no credit; do not redistribute as an asset pack)",
                                  "https://quaternius.com/license.html", True, True, False),
    "custom:cmu-mocap": ("CMU Graphics Lab mocap terms (free for all uses; do not resell the data itself)",
                         "http://mocap.cs.cmu.edu/faqs.php", True, True, False),
    "custom:mixamo": ("Adobe Mixamo terms (royalty-free in projects; do not redistribute raw files)",
                      "https://helpx.adobe.com/creative-cloud/faq/mixamo-faq.html", True, True, False),
    "custom:truebones": ("Truebones royalty-free licence (check the readme in your purchase)", "https://truebones.gumroad.com/", True, True, False),
    "custom:sampling-plus": ("Sampling+ 1.0", "https://creativecommons.org/licenses/sampling+/1.0/", False, True, True),
    "custom:unknown": ("Unknown licence - check before use", "", False, False, True),
    "PDM-1.0": ("Public Domain Mark 1.0 (no known copyright)", "https://creativecommons.org/publicdomain/mark/1.0/", True, True, False),
    "custom:nasa-media": ("NASA media usage guidelines (generally not copyrighted; credit NASA, no endorsement or logo use; "
                          "check third-party credits)", "https://www.nasa.gov/nasa-brand-center/images-and-media/", True, True, True),
}

# Every Creative Commons variant/version that catalogues such as Openverse report (CC BY-SA 2.0 etc.).
_CC_FLAGS = {"BY": (True, True), "BY-SA": (True, True), "BY-ND": (True, False), "BY-NC": (False, True),
             "BY-NC-SA": (False, True), "BY-NC-ND": (False, False)}
for _v in ("1.0", "2.0", "2.5", "3.0", "4.0"):
    for _code, (_com, _mod) in _CC_FLAGS.items():
        LICENSES.setdefault(f"CC-{_code}-{_v}", (f"CC {_code} {_v}", f"https://creativecommons.org/licenses/{_code.lower()}/{_v}/",
                                                 _com, _mod, True))

COMMERCIAL_SAFE, ATTRIBUTION, NON_COMMERCIAL, RESTRICTED = "commercial-safe", "attribution", "non-commercial", "restricted"
LICENSE_CLASSES = [COMMERCIAL_SAFE, ATTRIBUTION, NON_COMMERCIAL, RESTRICTED]


def info(lic: str) -> dict:
    lid = lic if lic in LICENSES else ("custom:unknown" if not lic.startswith("custom:") else lic)
    label, url, com, mod, attr = LICENSES.get(lid, (lic.removeprefix("custom:") + " (custom licence)", "", False, False, True))
    return {"id": lic, "label": label, "url": url, "commercial_ok": com, "modifiable": mod, "attribution_required": attr,
            "class": license_class(lic)}


def license_class(lic: str) -> str:
    t = LICENSES.get(lic)
    if not t:
        return RESTRICTED
    _, _, com, mod, attr = t
    if not com:
        return NON_COMMERCIAL
    if not mod:
        return RESTRICTED
    return ATTRIBUTION if attr else COMMERCIAL_SAFE


def allowed(lic: str, policy: str) -> bool:
    """policy: 'cc0' = public domain only (CC0 or the Public Domain Mark); 'commercial' = commercial use + modification allowed (CC0, CC-BY, OFL...);
    'any' = everything except unknown."""
    if policy == "cc0":
        return lic in ("CC0-1.0", "PDM-1.0")
    if policy == "commercial":
        t = LICENSES.get(lic)
        return bool(t and t[2] and t[3])
    return lic != "custom:unknown"


def from_text(s: str | None) -> str:
    """Map free-form licence strings / CC deed URLs / site slugs to our ids."""
    if not s:
        return "custom:unknown"
    t = str(s).strip().lower()
    if t in {k.lower() for k in LICENSES}:
        return next(k for k in LICENSES if k.lower() == t)
    if "zero" in t or t in {"cc0", "cc0 1.0", "creative commons 0", "public domain", "cc-0"} or "cc0" in t:
        return "CC0-1.0"
    if "publicdomain/mark" in t or t in {"pdm", "public domain mark"}:
        return "PDM-1.0"
    if "sampling" in t:
        return "custom:sampling-plus"
    if t in {"ofl", "ofl-1.1"} or "open font" in t:
        return "OFL-1.1"
    if t in {"apache2", "apache", "apache-2.0"}:
        return "Apache-2.0"
    if t in {"ufl", "ubuntu"}:
        return "UFL-1.0"
    vm = re.search(r"(?<![\d.])([1-4]\.[05])(?![\d])", t)
    ver = vm.group(1) if vm and vm.group(1) in ("1.0", "2.0", "2.5", "3.0", "4.0") else "4.0"
    url_m = re.search(r"licenses/([a-z\-]+)/", t)
    parts = set(url_m.group(1).split("-")) if url_m else set()
    words = t.replace("_", " ").replace("-", " ")
    nc = "nc" in parts or "noncommercial" in words.replace(" ", "") or re.search(r"\bnc\b", words)
    nd = "nd" in parts or "noderiv" in words.replace(" ", "") or re.search(r"\bnd\b", words)
    sa = "sa" in parts or "sharealike" in words.replace(" ", "") or re.search(r"\bsa\b", words)
    if "by" in parts or "attribution" in words or re.search(r"\bby\b", words):
        if nc and nd:
            return f"CC-BY-NC-ND-{ver}"
        if nc and sa:
            return f"CC-BY-NC-SA-{ver}"
        if nc:
            return f"CC-BY-NC-{ver}"
        if nd:
            return f"CC-BY-ND-{ver}"
        if sa:
            return f"CC-BY-SA-{ver}"
        return f"CC-BY-{ver}"
    return "custom:unknown"


def attribution(name: str, author: str | None, source_label: str, source_url: str | None, lic: str,
                author_url: str | None = None) -> str:
    """Ready-to-paste credit line (TASL: title, author, source, licence)."""
    i = info(lic)
    who = f" by {author}" if author else ""
    where = f" ({source_url})" if source_url else ""
    lic_txt = i["label"] + (f" - {i['url']}" if i["url"] else "")
    return f'"{name}"{who}, {source_label}{where}. Licence: {lic_txt}'
