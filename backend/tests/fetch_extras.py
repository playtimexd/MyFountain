"""Download + load the optional image models once (rembg u2net, LaMa big-lama, OpenCLIP ViT-B/32) and report."""
import time, traceback

def step(name, fn):
    t = time.time()
    try:
        r = fn()
        print(f"[ok]   {name} ({time.time() - t:.1f}s) {r}", flush=True)
    except Exception as e:
        print(f"[FAIL] {name} ({time.time() - t:.1f}s) {type(e).__name__}: {e}", flush=True)
        traceback.print_exc(limit=3)

def rembg_():
    from rembg import new_session
    s = new_session("u2net")
    return type(s).__name__

def lama_():
    import numpy as np
    from PIL import Image
    from simple_lama_inpainting import SimpleLama
    m = SimpleLama()
    img = Image.fromarray((np.random.rand(256, 256, 3) * 255).astype("uint8"))
    mask = Image.new("L", (256, 256), 0); mask.paste(255, (100, 100, 150, 150))
    out = m(img, mask)
    return f"out={out.size} device={getattr(m, 'device', '?')}"

def clip_():
    from huggingface_hub import snapshot_download
    p = snapshot_download("laion/CLIP-ViT-B-32-laion2B-s34B-b79K")
    import open_clip, torch
    model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained=f"{p}/open_clip_pytorch_model.bin")
    model.eval()
    with torch.no_grad():
        t = open_clip.get_tokenizer("ViT-B-32")(["a red circle"])
        e = model.encode_text(t)
    return f"dir={p} text_emb={tuple(e.shape)}"

step("rembg u2net", rembg_)
step("LaMa big-lama", lama_)
step("OpenCLIP ViT-B/32", clip_)
