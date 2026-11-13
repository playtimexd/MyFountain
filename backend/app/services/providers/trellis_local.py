"""Local image -> 3D with TRELLIS.2 (Microsoft, MIT) running natively in CutLab's ComfyUI (data/comfyui, v0.45+).

The graph mirrors Comfy-Org's official template `3d_pixal3d_trellis2_image_to_model` with the TRELLIS.2 branch selected:
  background removal (BiRefNet) -> crop to the subject (1024 px) -> DINOv3 conditioning -> sparse structure (32^3)
  -> shape (512) -> shape upsample (1536) -> texture -> remesh + decimate + UV unwrap -> bake base colour / PBR / AO /
  normal maps -> SaveGLB.
Text prompts are first turned into a reference image with Z-Image Turbo (Apache-2.0) when those weights are present.
Output is a textured, UNRIGGED mesh: rigging still needs Tripo / Meshy / Mixamo (or the bone tools in Animation).
Weights (huggingface.co/Comfy-Org, MIT repacks; DINOv3 originates under Meta's DINOv3 licence):
  diffusion_models/trellis_2_int8_convrot.safetensors 5.25 GB, vae/trellis_2_shape_vae_bf16 1.10 GB,
  vae/trellis_2_texture_vae_bf16 0.95 GB, clip_vision/dino_v3_vit_l 1.21 GB, background_removal/birefnet 0.44 GB.
"""
from __future__ import annotations

import math
import random
import time
import uuid
from pathlib import Path

from ...jobs import JobCancelled
from . import common as C
from .common import ProviderError

REQUIRED = [   # (pick key, ComfyUI model folder, accepted file names - first is the one CutLab downloads)
    ("unet", "diffusion_models", ["trellis_2_int8_convrot.safetensors", "trellis_2_bf16.safetensors"]),
    ("vae_shape", "vae", ["trellis_2_shape_vae_bf16.safetensors"]),
    ("vae_tex", "vae", ["trellis_2_texture_vae_bf16.safetensors"]),
    ("clip_vision", "clip_vision", ["dino_v3_vit_l.safetensors", "dino_v3_L_naf_fp32.safetensors"]),
    ("bg", "background_removal", ["birefnet.safetensors"]),
]
ZIMAGE = {"diffusion_models": ["z_image_turbo_bf16.safetensors"], "text_encoders": ["qwen_3_4b.safetensors"], "vae": ["ae.safetensors"]}
QUALITY = {   # target faces / texture size; "draft" skips the 1536 upsample pass
    "draft": {"faces": 100_000, "tex": 1024, "upsample": False},
    "standard": {"faces": 300_000, "tex": 2048, "upsample": True},
    "high": {"faces": 700_000, "tex": 4096, "upsample": True},
}
EST_SECONDS = {"draft": 140, "standard": 420, "high": 600}   # measured on an RTX 3090 Ti: draft 136 s; the 1536 upsample pass is ~22 s/step


def _model_roots() -> list[Path]:
    from . import comfy_local
    roots = [comfy_local.ROOT / "models"]
    extra = comfy_local.ROOT / "extra_model_paths.yaml"
    try:
        for line in extra.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("base_path:"):
                roots.append(Path(line.split(":", 1)[1].strip()))
    except OSError:
        pass
    return roots


def _find(folder: str, names: list[str]) -> str | None:
    alias = {"diffusion_models": ["diffusion_models", "unet"], "text_encoders": ["text_encoders", "clip"]}.get(folder, [folder])
    for name in names:
        for root in _model_roots():
            for sub in alias:
                if (root / sub / name).is_file():
                    return name
    return None


def picks() -> tuple[dict, list[str]]:
    got, missing = {}, []
    for key, folder, names in REQUIRED:
        got[key] = _find(folder, names)
        if not got[key]:
            missing.append(f"{folder}/{names[0]}")
    return got, missing


def zimage_ready() -> bool:
    return all(_find(k, v) for k, v in ZIMAGE.items())


def listing() -> dict:
    from . import comfy_local
    _, missing = picks()
    inst = comfy_local.installed()
    ok = inst and not missing
    t2i = ok and zimage_ready()
    return {
        "id": "local", "name": "TRELLIS.2 - local (free, your GPU)", "available": ok, "needs_key": False, "key_set": True, "cloud": False,
        "verified": True, "missing": missing if inst else ["CutLab ComfyUI (scripts/install_comfyui.sh)"],
        "howto": None if ok else ("Install CutLab's ComfyUI and the TRELLIS.2 weights (~9 GB): bash scripts/install_comfyui.sh --3d" if not inst
                                  else "Missing TRELLIS.2 files (run bash scripts/install_comfyui.sh --3d): " + ", ".join(missing)),
        "cost_note": "Free: runs on this PC's GPU in CutLab's ComfyUI (Draft about 2-3 min, Standard about 7 min on an RTX 3090 Ti). Nothing is uploaded.",
        "license": "TRELLIS.2: MIT (Microsoft). Generated models are yours to use commercially.",
        "capabilities": {
            "text_to_3d": t2i, "image_to_3d": True, "refine": False, "rig": [], "animals_rig": False, "animation": [],
            "formats": ["glb"], "outputs_per_request": 1, "qualities": list(QUALITY),
            "rig_note": ("TRELLIS.2 makes a textured but unrigged mesh. Rig it afterwards with Tripo/Meshy (cloud) or Mixamo, "
                         "then use it in Animation."),
            "text_note": ("Text prompts first make a reference image with Z-Image Turbo (local), then TRELLIS.2 builds the model."
                          if t2i else "Text-to-3D needs Z-Image Turbo in ComfyUI; use an image instead."),
        },
    }


def _plan(req: dict) -> dict:
    q = (req.get("quality") or "standard").lower()
    if q not in QUALITY:
        q = "standard"
    if not req.get("image_mediaId"):
        if not (req.get("prompt") or "").strip():
            raise ProviderError("Give an image (best) or a text prompt.", 400)
        if not zimage_ready():
            raise ProviderError("Local text-to-3D needs Z-Image Turbo in ComfyUI. Pick an image of the object instead.", 400)
    return {"quality": q, **QUALITY[q]}


def estimate(req: dict) -> dict:
    p = _plan(req)
    return {"provider": "local", "currency": "none", "amount": 0, "unit": "free",
            "note": f"Free, local GPU. Roughly {EST_SECONDS[p['quality']] // 60}-{EST_SECONDS[p['quality']] // 60 + 2} min"
                    + (" plus ~20 s for the reference image." if not req.get("image_mediaId") else ".")
                    + (" The model is not rigged." if req.get("rig") else "")}


def dry_run(req: dict) -> dict:
    p = _plan(req)
    from .comfyui import base_url
    u = base_url()
    reqs = []
    if req.get("image_mediaId"):
        reqs.append(C.describe_request("POST", f"{u}/upload/image", {}, {"image": "<your image>"}))
    else:
        reqs.append(C.describe_request("POST", f"{u}/prompt", {}, {"prompt": "<Z-Image Turbo graph>"}, "text -> reference image"))
    reqs.append(C.describe_request("POST", f"{u}/prompt", {}, {"prompt": f"<TRELLIS.2 graph, {p['quality']}>"}))
    reqs.append(C.describe_request("GET", f"{u}/history/<prompt id>", {}, None, "polled until the GLB is saved"))
    return {"provider": "local", "requests": reqs, "plan": p, "estimate": estimate(req)}


# ---------------------------------------------------------------- graphs (ComfyUI API format)

def zimage_graph(prompt: str, seed: int, prefix: str) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                                                    "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep",
                                                    "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }


IMAGE_PROMPT = ("{p}. A single object, full view, centred, three-quarter front view, plain light grey studio background, "
                "soft even lighting, no shadows on the background, no text, high detail 3D asset reference.")


def trellis_graph(image_name: str, pk: dict, plan: dict, seed: int, prefix: str) -> dict:
    ks = lambda model, pos, neg, lat, steps, cfg, sch, s: {"class_type": "KSampler", "inputs": {   # noqa: E731
        "model": model, "positive": pos, "negative": neg, "latent_image": lat, "seed": s, "steps": steps, "cfg": cfg,
        "sampler_name": "euler", "scheduler": sch, "denoise": 1.0}}
    g = {
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "bgm": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": pk["bg"]}},
        "bg": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["bgm", 0], "image": ["img", 0]}},
        "crop": {"class_type": "ImageCropToMask", "inputs": {"images": ["img", 0], "masks": ["bg", 0], "width": 1024, "height": 1024,
                                                              "pad_factor": 1.0, "grow_mask": 0, "background": "#000000"}},
        "clipv": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": pk["clip_vision"]}},
        "cond": {"class_type": "Trellis2Conditioning", "inputs": {"clip_vision_model": ["clipv", 0], "image": ["crop", 0]}},
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": pk["unet"], "weight_dtype": "default"}},
        "vae_s": {"class_type": "VAELoader", "inputs": {"vae_name": pk["vae_shape"]}},
        "vae_t": {"class_type": "VAELoader", "inputs": {"vae_name": pk["vae_tex"]}},
        # sparse structure
        "cfg_a": {"class_type": "CFGOverride", "inputs": {"model": ["unet", 0], "cfg": 1.0, "start_percent": 0.667, "end_percent": 1.0}},
        "rs_a": {"class_type": "RescaleCFG", "inputs": {"model": ["cfg_a", 0], "multiplier": 0.7}},
        "ms_a": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["rs_a", 0], "shift": 5.0}},
        "lat0": {"class_type": "EmptyTrellis2LatentStructure", "inputs": {"batch_size": 1}},
        "ks_struct": ks(["ms_a", 0], ["cond", 0], ["cond", 1], ["lat0", 0], 12, 7.5, "normal", seed + 14),
        "vox": {"class_type": "VaeDecodeStructureTrellis2", "inputs": {"samples": ["ks_struct", 0], "vae": ["vae_s", 0], "resolution": "32"}},
        # shape
        "cfg_b": {"class_type": "CFGOverride", "inputs": {"model": ["unet", 0], "cfg": 1.0, "start_percent": 0.769, "end_percent": 1.0}},
        "rs_b": {"class_type": "RescaleCFG", "inputs": {"model": ["cfg_b", 0], "multiplier": 0.5}},
        "shape": {"class_type": "Trellis2ShapeStage", "inputs": {"positive": ["cond", 0], "negative": ["cond", 1], "voxel": ["vox", 0]}},
        "ks_shape": ks(["rs_b", 0], ["shape", 0], ["shape", 1], ["shape", 2], 20, 7.5, "normal", seed),
    }
    if plan["upsample"]:
        g["up"] = {"class_type": "Trellis2UpsampleStage", "inputs": {"positive": ["shape", 0], "negative": ["shape", 1],
                                                                     "shape_latent": ["ks_shape", 0], "vae": ["vae_s", 0], "target_resolution": 1536}}
        g["ks_up"] = ks(["rs_b", 0], ["up", 0], ["up", 1], ["up", 2], 12, 7.5, "simple", seed)
        stage, shape_lat = "up", ["ks_up", 0]
    else:
        stage, shape_lat = "shape", ["ks_shape", 0]
    g.update({
        "dec_shape": {"class_type": "VaeDecodeShapeTrellis", "inputs": {"samples": shape_lat, "vae": ["vae_s", 0]}},
        "tex": {"class_type": "Trellis2TextureStage", "inputs": {"positive": [stage, 0], "negative": [stage, 1], "shape_latent": shape_lat}},
        "ks_tex": ks(["unet", 0], ["tex", 0], ["tex", 1], ["tex", 2], 12, 1.0, "normal", seed + 1),
        "dec_tex": {"class_type": "VaeDecodeTextureTrellis", "inputs": {"samples": ["ks_tex", 0], "vae": ["vae_t", 0],
                                                                        "shape_subdivides": ["dec_shape", 1]}},
        # clean, game-friendly mesh + baked PBR textures
        "remesh": {"class_type": "RemeshMesh", "inputs": {"mesh": ["dec_shape", 0], "resolution": 768, "sign_mode": "solid",
                                                          "sign_mode.fill": 20.0, "sign_mode.qef": False, "band": 1.0, "project_back": 0.0,
                                                          "fix_poles": False, "smooth_iters": 20, "drop_small_components": 0.01,
                                                          "precluster_max_verts": 20_000_000}},
        "dec": {"class_type": "DecimateMesh", "inputs": {"mesh": ["remesh", 0], "target_face_count": plan["faces"], "placement_mode": "midpoint"}},
        "smooth": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["dec", 0], "crease_angle": 180.0}},
        "uv": {"class_type": "UnwrapMesh", "inputs": {"mesh": ["smooth", 0], "segmenter": "pec", "resolution": plan["tex"], "padding": 1,
                                                      "weld_distance": 0.0002}},
        "bake": {"class_type": "BakeTextureFromVoxel", "inputs": {"mesh": ["uv", 0], "voxel_colors": ["dec_tex", 0],
                                                                 "reference_mesh": ["dec_shape", 0], "texture_size": plan["tex"]}},
        "ao": {"class_type": "BakeAmbientOcclusion", "inputs": {"low_poly": ["uv", 0], "high_poly": ["remesh", 0], "resolution": min(plan["tex"], 1024),
                                                               "samples": 64, "max_distance": 0.71, "strength": 1.0, "bias": 0.01}},
        "nrm": {"class_type": "BakeNormalMapFromMesh", "inputs": {"low_poly": ["uv", 0], "high_poly": ["remesh", 0], "resolution": plan["tex"],
                                                                  "cage_distance": 0.05, "ignore_backfaces": True}},
        "apply": {"class_type": "ApplyTextureToMesh", "inputs": {"mesh": ["uv", 0], "base_color": ["bake", 0], "metallic": ["bake", 1],
                                                                 "roughness": ["bake", 2], "occlusion": ["ao", 0], "normal_map": ["nrm", 0]}},
        "final": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["apply", 0], "crease_angle": 180.0}},
        "save": {"class_type": "SaveGLB", "inputs": {"mesh": ["final", 0], "filename_prefix": prefix}},
        "save_crop": {"class_type": "SaveImage", "inputs": {"images": ["crop", 0], "filename_prefix": prefix + "_input"}},
    })
    return g


# ---------------------------------------------------------------- run

def _files(entry: dict, ext: tuple[str, ...]) -> list[dict]:
    out = []
    for node_out in (entry.get("outputs") or {}).values():
        for vals in node_out.values():
            if isinstance(vals, list):
                for f in vals:
                    if isinstance(f, dict) and str(f.get("filename", "")).lower().endswith(ext) and f.get("type", "output") == "output":
                        out.append(f)
    return out


def _submit_and_wait(ctx, cl, url: str, graph: dict, lo: float, hi: float, label: str, est: float) -> dict:
    from .comfyui import _interrupt, _node_errors
    r = cl.post(f"{url}/prompt", json={"prompt": graph, "client_id": uuid.uuid4().hex})
    if r.status_code != 200:
        try:
            msg = _node_errors(r.json())
        except Exception:  # noqa: BLE001
            msg = f"HTTP {r.status_code}"
        raise ProviderError(C.scrub(f"ComfyUI rejected the {label} workflow: {msg}"))
    pid = r.json().get("prompt_id")
    t0 = time.monotonic()
    try:
        while True:
            C.nap(ctx, 2.0)
            try:
                h = cl.get(f"{url}/history/{pid}").json()
            except Exception:  # noqa: BLE001
                continue
            entry = h.get(pid) if isinstance(h, dict) else None
            el = time.monotonic() - t0
            if entry:
                st = entry.get("status") or {}
                if st.get("status_str") == "error":
                    msg = ""
                    for kind, payload in st.get("messages", []):
                        if kind == "execution_error":
                            msg = f"{payload.get('node_type', '')}: {payload.get('exception_message', '')}".strip()
                    hint = " Close other GPU apps or use Draft quality." if "out of memory" in msg.lower() else ""
                    raise ProviderError(C.scrub(f"{label} failed in ComfyUI: {msg or 'execution error'}.{hint}"))
                if st.get("completed") or entry.get("outputs"):
                    return entry
            ctx.progress(min(hi - 0.01, lo + (hi - lo) * (1 - math.exp(-el / est))), f"{label} ({int(el)} s)")
    except JobCancelled:
        _interrupt(cl, url, pid)
        raise


def _fetch(cl, url: str, f: dict) -> bytes:
    r = cl.get(f"{url}/view", params={"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
    C.check(r, "ComfyUI download")
    return r.content


def run(ctx, req: dict, dest_dir: Path, base_name: str) -> dict:
    from . import comfy_local
    from .comfyui import _cl, base_url
    plan = _plan(req)
    pk, missing = picks()
    if missing:
        raise ProviderError("TRELLIS.2 weights missing in CutLab's ComfyUI: " + ", ".join(missing))
    ctx.progress(0.01, "Starting local ComfyUI")
    if not comfy_local.ensure_running(progress=lambda m: ctx.progress(0.01, m)):
        raise ProviderError("CutLab's ComfyUI did not start (see data/comfyui/server.log).")
    url = base_url()
    seed = random.randint(0, 2**31 - 1000)
    stamp = C.stamp()
    preview = None
    t0 = time.monotonic()
    with _cl(120.0) as cl:
        if req.get("image_mediaId"):
            ctx.progress(0.03, "Uploading image to ComfyUI")
            data, mime = C.image_bytes_for_upload(C.media_image_path(req["image_mediaId"]), max_side=2048, fmt="png")
            fname = f"cutlab3d_{uuid.uuid4().hex[:8]}{'.png' if mime == 'image/png' else '.jpg'}"
        else:
            e = _submit_and_wait(ctx, cl, url, zimage_graph(IMAGE_PROMPT.format(p=req["prompt"].strip().rstrip(".")), seed, f"cutlab3d/ref_{stamp}"),
                                 0.03, 0.15, "Reference image (Z-Image Turbo)", 25)
            imgs = _files(e, (".png", ".jpg", ".webp"))
            if not imgs:
                raise ProviderError("Z-Image Turbo produced no image.")
            data, mime = _fetch(cl, url, imgs[0]), "image/png"
            preview = dest_dir / f"{base_name}_ref.png"
            preview.write_bytes(data)
            fname = f"cutlab3d_{uuid.uuid4().hex[:8]}.png"
        r = C.check(cl.post(f"{url}/upload/image", files={"image": (fname, data, mime)}, data={"overwrite": "true", "type": "input"}),
                    "ComfyUI image upload")
        j = r.json()
        image_name = (j.get("subfolder") + "/" if j.get("subfolder") else "") + j["name"]
        e = _submit_and_wait(ctx, cl, url, trellis_graph(image_name, pk, plan, seed, f"cutlab3d/{base_name}"), 0.16, 0.94,
                             f"TRELLIS.2 ({plan['quality']})", EST_SECONDS[plan["quality"]])
        glbs = _files(e, (".glb",))
        if not glbs:
            raise ProviderError("TRELLIS.2 finished but saved no GLB.")
        ctx.progress(0.95, "Fetching the model")
        out = dest_dir / f"{base_name}.glb"
        out.write_bytes(_fetch(cl, url, glbs[0]))
        if preview is None:
            crops = _files(e, (".png",))
            if crops:
                preview = dest_dir / f"{base_name}_input.png"
                preview.write_bytes(_fetch(cl, url, crops[0]))
        try:   # shared GPU: give the VRAM back
            cl.post(f"{url}/free", json={"unload_models": True, "free_memory": True})
        except Exception:  # noqa: BLE001
            pass
    meta = {"engine": "TRELLIS.2 (ComfyUI)", "quality": plan["quality"], "faces": plan["faces"], "texture": plan["tex"], "seed": seed,
            "seconds": round(time.monotonic() - t0), "rigged": False, "license": "MIT (TRELLIS.2)",
            "note": "Unrigged textured mesh. Rig it with Tripo/Meshy or Mixamo before animating."}
    if req.get("rig"):
        meta["rig_skipped"] = "TRELLIS.2 does not rig; the model was generated without a skeleton."
    return {"files": {"model": str(out)}, "preview_image": str(preview) if preview else None, "meta": meta, "credits": 0, "credit_unit": "free"}
