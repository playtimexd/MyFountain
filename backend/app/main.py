"""CutLab local engine. Run:  python -m app   (from backend/)  ->  http://127.0.0.1:8787
Every module in app/routers/*.py that exposes `router` is auto-registered under /api (the module
decides its own prefix). That lets independent teams add routers without touching this file."""
from __future__ import annotations

import importlib
import pkgutil
import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import routers as routers_pkg
from .config import ROOT

app = FastAPI(title="CutLab Engine", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

LOADED: list[str] = []
FAILED: dict[str, str] = {}
for m in pkgutil.iter_modules(routers_pkg.__path__):
    if m.name.startswith("_"):
        continue
    try:
        mod = importlib.import_module(f"{routers_pkg.__name__}.{m.name}")
        app.include_router(mod.router)
        LOADED.append(m.name)
    except Exception:  # a broken optional module must not take the whole engine down
        FAILED[m.name] = traceback.format_exc(limit=3)
        traceback.print_exc()

# serve the built frontend if present (npm run build) — otherwise use `npm run dev` with the vite proxy
_dist = ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        f = _dist / full_path
        if f.is_file() and f.name != "index.html":
            return FileResponse(f)
        # index.html must not be cached: after an update it points at new hashed /assets files
        return FileResponse(_dist / "index.html", headers={"Cache-Control": "no-cache"})
