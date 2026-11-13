"""Single entry point to register a file in the media library (probe + thumbnail + DB row)."""
from __future__ import annotations

from pathlib import Path

from . import db
from .ffutil import kind_of, make_thumb, probe


def ingest_file(path: str | Path, *, origin: str = "import", source_url: str | None = None,
                name: str | None = None, tags: list[str] | None = None, meta: dict | None = None,
                dedupe: bool = True) -> dict:
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(str(p))
    if dedupe:
        existing = db.find_by_path(p)
        if existing:
            return existing
    info = probe(p)
    kind = kind_of(p) or info["kind"]
    row = db.add_media(
        kind=kind, name=name or p.stem, path=p, origin=origin, source_url=source_url,
        duration=info["duration"], width=info["width"], height=info["height"], fps=info["fps"],
        size=info["size"], has_audio=info["has_audio"], tags=tags, meta={**(meta or {}), "vcodec": info["video_codec"], "acodec": info["audio_codec"]},
    )
    make_thumb(p, kind, row["id"], info["duration"])
    return row
