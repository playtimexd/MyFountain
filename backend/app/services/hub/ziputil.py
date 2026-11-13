"""Safe zip extraction: no path traversal, no absolute paths, uncompressed-size cap (zip-bomb guard)."""
from __future__ import annotations

import zipfile
from pathlib import Path


class UnsafeArchive(RuntimeError):
    pass


def safe_extract(zp: str | Path, dest: str | Path, *, max_total: int = 4_000_000_000, max_files: int = 20000) -> list[Path]:
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with zipfile.ZipFile(zp) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > max_files:
            raise UnsafeArchive(f"Archive has {len(infos)} files (limit {max_files})")
        if sum(i.file_size for i in infos) > max_total:
            raise UnsafeArchive("Archive expands beyond the size limit")
        for i in infos:
            name = i.filename.replace("\\", "/")
            if name.startswith("/") or ":" in name.split("/")[0] or "__MACOSX" in name:
                continue
            target = (dest / name).resolve()
            if dest not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, open(target, "wb") as f:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            out.append(target)
    return out
