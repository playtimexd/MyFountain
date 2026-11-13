"""Auto-tagging + embeddings. OpenCLIP ViT-B/32 (locally cached weights only; never downloads) for zero-shot
tags and image embeddings of thumbnails; Ollama `nomic-embed-text` for text embeddings of name/description/tags.
Every function degrades to None when the model is unavailable."""
from __future__ import annotations

import importlib.util
import threading

import httpx
import numpy as np

from ... import config

CLIP_REPOS = [("ViT-B-32", "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"), ("ViT-B-32", "timm/vit_base_patch32_clip_224.openai")]

LABELS = [
    "character", "humanoid", "robot", "monster", "animal", "dog", "cat", "horse", "bird", "fish", "dragon", "insect", "skeleton",
    "zombie", "knight", "soldier", "wizard", "tree", "plant", "flower", "grass", "bush", "rock", "mountain", "terrain",
    "building", "house", "castle", "tower", "ruins", "wall", "fence", "door", "window", "bridge", "road", "street",
    "furniture", "chair", "table", "bed", "lamp", "shelf", "barrel", "crate", "box", "chest", "food", "fruit",
    "vehicle", "car", "truck", "spaceship", "airplane", "boat", "train", "weapon", "sword", "gun", "bow", "shield", "tool",
    "coin", "gem", "potion", "key", "sign", "ui icon", "button", "font", "text", "tile", "pixel art", "sprite",
    "wood", "metal", "stone", "brick", "concrete", "tiles", "fabric", "leather", "sand", "dirt", "snow", "ice", "water",
    "lava", "marble", "plaster", "asphalt", "bark", "moss", "rust", "paint", "plastic", "glass", "gravel", "roof",
    "sky", "sunset", "night sky", "clouds", "indoor room", "outdoor landscape", "forest", "desert", "city", "beach",
    "low poly", "cartoon", "realistic", "sci-fi", "fantasy", "medieval", "modern", "nature", "kitchen", "dungeon",
]

_lock = threading.Lock()
_CLIP: dict = {}


def clip_status() -> dict:
    if importlib.util.find_spec("open_clip") is None:
        return {"ready": False, "message": "open_clip is not installed"}
    try:
        from huggingface_hub import snapshot_download
        for arch, repo in CLIP_REPOS:
            try:
                snapshot_download(repo, local_files_only=True)
                return {"ready": True, "arch": arch, "repo": repo, "message": "OpenCLIP ViT-B/32 ready (local cache)"}
            except Exception:
                continue
    except Exception:
        pass
    return {"ready": False, "message": "OpenCLIP weights are not cached locally (no download is attempted)"}


def _clip():
    with _lock:
        if "m" in _CLIP:
            return _CLIP["m"]
        st = clip_status()
        if not st["ready"]:
            _CLIP["m"] = None
            return None
        import open_clip
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"

        def make(device):
            try:   # open_clip >= 3: the hf-hub id is the model name
                return open_clip.create_model_and_transforms(f"hf-hub:{st['repo']}", device=device)
            except Exception:
                return open_clip.create_model_and_transforms(st["arch"], pretrained=f"hf-hub:{st['repo']}", device=device)
        try:
            model, _, pre = make(dev)
        except Exception:
            dev = "cpu"
            model, _, pre = make(dev)
        model.eval()
        tk = open_clip.get_tokenizer(f"hf-hub:{st['repo']}")
        with torch.no_grad():
            t = model.encode_text(tk([f"a 3D game asset or texture of {l}" for l in LABELS]).to(dev)).float()
            label_vecs = torch.nn.functional.normalize(t, dim=-1).cpu().numpy()
        _CLIP["m"] = (model, pre, tk, dev, label_vecs)
        return _CLIP["m"]


def clip_image(path: str) -> np.ndarray | None:
    try:
        c = _clip()
        if c is None:
            return None
        import torch
        from PIL import Image
        model, pre, _, dev, _ = c
        with _lock, torch.no_grad():
            im = Image.open(path)
            if im.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", im.size, (128, 128, 128))
                im = im.convert("RGBA")
                bg.paste(im, mask=im.split()[-1])
                im = bg
            x = pre(im.convert("RGB")).unsqueeze(0).to(dev)
            f = model.encode_image(x).float()
            return torch.nn.functional.normalize(f, dim=-1).cpu().numpy()[0].astype(np.float32)
    except Exception:
        return None


def clip_text(q: str) -> np.ndarray | None:
    try:
        c = _clip()
        if c is None:
            return None
        import torch
        model, _, tk, dev, _ = c
        with _lock, torch.no_grad():
            f = model.encode_text(tk([q]).to(dev)).float()
            return torch.nn.functional.normalize(f, dim=-1).cpu().numpy()[0].astype(np.float32)
    except Exception:
        return None


def zero_shot_tags(img_vec: np.ndarray, k: int = 4, min_prob: float = 0.08) -> list[str]:
    c = _clip()
    if c is None or img_vec is None:
        return []
    label_vecs = c[4]
    logits = 100.0 * (label_vecs @ img_vec)
    p = np.exp(logits - logits.max())
    p /= p.sum()
    order = np.argsort(-p)[:k]
    return [LABELS[i] for i in order if p[i] >= min_prob]


def text_embed(texts: list[str], prefix: str = "search_document: ") -> np.ndarray | None:
    if not texts:
        return None
    s = config.load_settings()
    try:
        r = httpx.post(s["ollama_url"].rstrip("/") + "/api/embed", timeout=60,
                       json={"model": s.get("embed_model") or "nomic-embed-text", "input": [prefix + t[:1500] for t in texts]})
        r.raise_for_status()
        a = np.array(r.json()["embeddings"], np.float32)
        a /= np.linalg.norm(a, axis=1, keepdims=True) + 1e-9
        return a
    except Exception:
        return None


def query_vectors(q: str) -> tuple[np.ndarray | None, np.ndarray | None]:
    tv = text_embed([q], prefix="search_query: ")
    return (tv[0] if tv is not None else None), clip_text(q)


def status() -> dict:
    s = config.load_settings()
    ok = text_embed(["ping"]) is not None
    return {"clip": clip_status(), "text_embed": {"ready": ok, "model": s.get("embed_model"),
                                                  "message": "Ollama embeddings ready" if ok else "Ollama embedding model not reachable"}}
