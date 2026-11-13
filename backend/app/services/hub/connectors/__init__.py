"""Connector registry."""
from __future__ import annotations

from .ambientcg import AmbientCG
from .base import Connector, ConnectorError, DLContext, Downloaded, NeedsKey
from .catalogs import Kenney, Local, Mixamo, Quaternius, Truebones
from .cmu import CMUMocap
from .freesound import Freesound
from .googlefonts import GoogleFonts
from .nasa import NASA
from .openverse import Openverse
from .polyhaven import PolyHaven
from .polypizza import PolyPizza
from .sketchfab import Sketchfab

REGISTRY: dict[str, Connector] = {c.id: c for c in (
    PolyHaven(), AmbientCG(), PolyPizza(), Sketchfab(), Freesound(), GoogleFonts(), CMUMocap(), Openverse(), NASA(),
    Kenney(), Quaternius(), Mixamo(), Truebones(), Local(),
)}


def get(source: str) -> Connector:
    c = REGISTRY.get(source)
    if not c:
        raise ConnectorError(f"Unknown source '{source}'")
    return c


__all__ = ["REGISTRY", "get", "Connector", "ConnectorError", "DLContext", "Downloaded", "NeedsKey"]
