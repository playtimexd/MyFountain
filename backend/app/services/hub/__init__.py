"""Asset Hub: a license-aware database of free game-ready assets (models, animations, materials, HDRIs,
sound effects, music, fonts, sprites) pulled from official APIs / official download links, plus manual pack imports.

Layout:  data/hub/hub.db (records), data/hub/files/<source>/<id>/ (files), data/hub/cache/ (API page cache),
data/hub/keys.json (connector API keys - never logged or returned), data/hub/harvest/<job>.json (resumable state).
"""
