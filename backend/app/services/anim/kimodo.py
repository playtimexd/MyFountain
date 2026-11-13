"""Text -> motion with NVIDIA Kimodo (kinematic motion diffusion), using the user's existing Kimodo install.

Kimodo lives in its own Python environment (its torch / transformers pins must not touch CutLab's venv); we only call its CLI
`kimodo_gen` and read the BVH it writes. Humanoid skeleton only (SOMA, 77 joints) - animals still come from video capture.

Licences: only the SOMA checkpoints are offered (NVIDIA Open Model License, commercial use allowed). The SMPL-X checkpoint is
R&D-only and is never selected. The text encoder (Llama-3-8B-Instruct + LLM2Vec) must already be in the Hugging Face cache: runs are
offline (HF_HUB_OFFLINE=1) and nothing is ever downloaded from here.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import time
from pathlib import Path

from ... import config
from ...jobs import JobContext

OUT_DIR = config.DATA / "anim" / "kimodo"
OUT_DIR.mkdir(parents=True, exist_ok=True)
HF_HUB = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "hub"
COMMERCIAL_PREFIX = "Kimodo-SOMA-"          # NVIDIA Open Model License
EXAMPLES = [
    "A person walks forward cautiously, looking left and right.",
    "A person swings a heavy two-handed sword overhead and strikes down.",
    "A person is hit in the chest, staggers back two steps and recovers.",
    "A person sneaks forward in a low crouch.",
    "A person waves with the right hand, then puts it down.",
    "A person jumps over a small obstacle and keeps running.",
    "A person drinks from a bottle, then wipes the mouth.",
    "A person casts a spell, pushing both hands forward.",
]


def _candidates() -> list[Path]:
    s = config.load_settings()
    out = []
    if s.get("kimodo_gen"):
        out.append(Path(s["kimodo_gen"]))
    home = Path.home()
    pats = []
    for d in ("Downloads", "Desktop", "Documents", ""):
        for venv in ("", ".venv", "venv"):
            base = home / d if d else home
            pats += [str(base / "*kimodo*" / venv / "Scripts" / "kimodo_gen.exe"), str(base / "*" / "*kimodo*" / venv / "Scripts" / "kimodo_gen.exe")]
    pats += [r"D:\*kimodo*\Scripts\kimodo_gen.exe", r"D:\*\*kimodo*\Scripts\kimodo_gen.exe", r"D:\*\*kimodo*\.venv\Scripts\kimodo_gen.exe"]
    for p in pats:
        out += [Path(x) for x in glob.glob(p)]
    return out


_found: dict = {}


def find_cli() -> Path | None:
    hit = _found.get("cli")
    if hit and hit.is_file():
        return hit
    for p in _candidates():
        if p.is_file():
            _found["cli"] = p
            return p
    return None


def cached_models() -> list[str]:
    return sorted(d.name.split("--", 2)[-1] for d in HF_HUB.glob("models--nvidia--Kimodo-*")
                  if d.name.split("--", 2)[-1].startswith(COMMERCIAL_PREFIX) and any(d.glob("snapshots/*/model.safetensors")))


def text_encoder_ready() -> bool:
    llama = HF_HUB / "models--meta-llama--Meta-Llama-3-8B-Instruct"
    return any(llama.glob("snapshots/*/model-0000*-of-0000*.safetensors")) and (HF_HUB / "models--McGill-NLP--LLM2Vec-Meta-Llama-3-8B-Instruct-mntp").exists()


def status() -> dict:
    cli = find_cli()
    models = cached_models()
    enc = text_encoder_ready()
    ok = bool(cli and models and enc)
    why = "" if ok else ("Kimodo is not installed (no kimodo_gen found)." if not cli else
                         "No commercially licensed Kimodo checkpoint (Kimodo-SOMA-*) in the Hugging Face cache." if not models else
                         "The Kimodo text encoder (Llama-3-8B-Instruct + LLM2Vec) is not in the Hugging Face cache.")
    return {"available": ok, "cli": str(cli) if cli else None, "models": models, "default_model": models[-1] if models else None,
            "text_encoder": enc, "skeleton": "humanoid (SOMA 77 joints)", "license": "NVIDIA Open Model License (SOMA checkpoints)",
            "note": why, "examples": EXAMPLES}


def generate(ctx: JobContext, prompt: str, duration: float = 4.0, num_samples: int = 1, seed: int | None = None, model: str | None = None,
             lo: float = 0.0, hi: float = 1.0) -> list[Path]:
    """Run kimodo_gen; returns the BVH file(s) (standard T-pose rest, 30 fps)."""
    st = status()
    if not st["available"]:
        raise RuntimeError(st["note"])
    model = model if model in st["models"] else st["default_model"]
    prompt = re.sub(r"\s+", " ", prompt).strip()
    if not prompt:
        raise ValueError("Describe the motion.")
    run = OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    stem = run / "motion"
    args = [st["cli"], prompt, "--model", model, "--duration", f"{max(1.0, min(10.0, float(duration))):.2f}",
            "--num_samples", str(max(1, min(4, int(num_samples)))), "--output", str(stem), "--bvh", "--bvh_standard_tpose"]
    if seed is not None:
        args += ["--seed", str(int(seed))]
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8",
           "TEXT_ENCODER_DEVICE": config.load_settings().get("kimodo_text_encoder_device") or "cpu"}
    ctx.progress(lo, "Kimodo: loading the model (about a minute on the first run)")
    p = subprocess.Popen(args, cwd=str(run), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=0x08000000 if os.name == "nt" else 0)
    ctx.register_process(p)
    tail: list[str] = []
    t0 = time.time()
    for line in p.stdout:                                       # type: ignore[union-attr]
        line = line.rstrip()
        if line:
            tail = (tail + [line])[-30:]
        el = time.time() - t0
        msg = "Kimodo: generating the motion" if "inference" in line.lower() or "denois" in line.lower() else \
              "Kimodo: writing BVH" if "Saving" in line else None
        ctx.progress(lo + (hi - lo) * min(0.95, el / 90.0), msg)
        ctx.check()
    p.wait()
    (run / "kimodo.log").write_text("\n".join(tail), "utf-8")
    bvhs = sorted(run.glob("**/*.bvh"))
    if p.returncode != 0 or not bvhs:
        err = next((l for l in reversed(tail) if "Error" in l or "error" in l), tail[-1] if tail else f"exit {p.returncode}")
        raise RuntimeError(f"Kimodo failed: {err[:300]}")
    (run / "prompt.txt").write_text(f"{prompt}\nmodel={model} duration={duration} seed={seed}", "utf-8")
    ctx.progress(hi, "Kimodo: done")
    return bvhs
