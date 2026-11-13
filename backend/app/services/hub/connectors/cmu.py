"""CMU Graphics Lab Motion Capture Database (http://mocap.cs.cmu.edu) - human mocap clips as BVH.
Files come from the BVH conversion by B. Hahne (credited on CMU's resources page), mirrored at
github.com/una-dinosauria/cmu-mocap (index text + per-trial raw files).
CMU terms: "free for all uses"; may be included in commercial products but must not be resold directly, even
converted. FAQ: "Please don't crawl this database for all motions" -> we only fetch user-selected trials and cap
harvests (max_per_harvest)."""
from __future__ import annotations

import re
from pathlib import Path

from .. import net
from .base import Connector, ConnectorError, DLContext, Downloaded, match, page_slice

INDEX = "https://raw.githubusercontent.com/una-dinosauria/cmu-mocap/master/cmu-mocap-index-text.txt"
RAW = "https://raw.githubusercontent.com/una-dinosauria/cmu-mocap/master/data"
ACK = ("The data used in this project was obtained from mocap.cs.cmu.edu. "
       "The database was created with funding from NSF EIA-0196217.")


def parse_index(text: str) -> list[dict]:
    out, subj, sdesc = [], None, ""
    for line in text.splitlines():
        m = re.match(r"\s*Subject #(\d+)\s*\((.*)\)", line)
        if m:
            subj, sdesc = int(m.group(1)), m.group(2).strip()
            continue
        m = re.match(r"(\d+)_(\d+)\t(.*)", line.strip("\r"))
        if m and subj is not None:
            out.append({"id": f"{m.group(1)}_{m.group(2)}", "subject": int(m.group(1)), "desc": m.group(3).strip(), "subject_desc": sdesc})
    return out


class CMUMocap(Connector):
    id = "cmu"
    name = "CMU Mocap (BVH)"
    homepage = "http://mocap.cs.cmu.edu"
    kinds = ["animation"]
    min_interval = 1.0
    max_per_harvest = 40
    terms_url = "http://mocap.cs.cmu.edu/faqs.php"
    terms_summary = ("Free for all uses incl. commercial games; do not resell the data itself (even converted). "
                     "Acknowledgement requested: '" + ACK + "' CMU asks not to crawl the whole database - only selected clips are fetched "
                     "(harvests are capped at 40 clips). BVH files from the Hahne conversion (120 fps, CMU skeleton, units not metres).")
    default_license = "custom:cmu-mocap"

    def _index(self) -> list[dict]:
        return parse_index(net.get_text(INDEX, cache_ttl=7 * 24 * 3600, min_interval=self.min_interval))

    def _cand(self, e: dict) -> dict:
        words = [w for w in re.split(r"[^a-z]+", (e["desc"] + " " + e["subject_desc"]).lower()) if len(w) > 2]
        return self.candidate(source_id=e["id"], kind="animation", name=f"CMU {e['id']} - {e['desc']}",
                              description=f"Subject #{e['subject']} ({e['subject_desc']}): {e['desc']}",
                              tags=["mocap", "bvh", "humanoid", *dict.fromkeys(words)],
                              source_url=f"http://mocap.cs.cmu.edu/search.php?subjectnumber={e['subject']}",
                              author="CMU Graphics Lab", author_url="http://mocap.cs.cmu.edu", formats=["bvh"],
                              stats={"rigged": True, "skeleton": "cmu-bvh"})

    def search(self, query: str, kind: str | None = None, page: int = 1) -> dict:
        if kind and kind != "animation":
            return page_slice([], page, self.page_size)
        rows = [e for e in self._index() if match(query, e["id"], e["desc"], e["subject_desc"])]
        rows.sort(key=lambda e: -e["subject"])   # CMU: higher-numbered subjects are better quality
        res = page_slice(rows, page, self.page_size)
        res["items"] = [self._cand(e) for e in res["items"]]
        return res

    def get(self, source_id: str) -> dict:
        e = next((x for x in self._index() if x["id"] == source_id), None)
        if not e:
            raise ConnectorError(f"CMU trial {source_id} not found")
        return self._cand(e)

    def attribution(self, c: dict) -> str:
        return f'"{c["name"]}" - CMU Graphics Lab Motion Capture Database (http://mocap.cs.cmu.edu). {ACK}'

    def download(self, source_id: str, dest: Path, ctx: DLContext, cand: dict | None = None) -> Downloaded:
        if not re.fullmatch(r"\d+_\d+", source_id):
            raise ConnectorError("Bad CMU trial id")
        subj = int(source_id.split("_")[0])
        p = dest / f"{source_id}.bvh"
        ctx.fetch(f"{RAW}/{subj:03d}/{source_id}.bvh", p, min_interval=self.min_interval)
        return Downloaded(files=[(p, "main", None)], stats={"fps": 120, "units": "CMU (inches-based, scale ~0.0564 to metres)"})
