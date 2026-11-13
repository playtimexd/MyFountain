#!/usr/bin/env bash
# CutLab's own ComfyUI for local video generation (Wan 2.2 TI2V-5B, Apache-2.0), in data/comfyui.
# Run from Git Bash:  bash scripts/install_comfyui.sh      (~3 GB PyTorch + ~17 GB models; resumes partial downloads)
#        add --3d      for local image -> 3D with TRELLIS.2 (MIT, ~9 GB more: 3D Models > Generate > "TRELLIS.2 - local")
# CutLab starts this ComfyUI by itself when a reference video is generated (services/providers/comfy_local.py).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
B="$ROOT/data/comfyui"
mkdir -p "$B"/{custom_nodes,input,output,user} "$B"/models/{diffusion_models,text_encoders,vae,checkpoints,unet}
[ -d "$B/ComfyUI/.git" ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$B/ComfyUI"
[ -x "$B/venv/Scripts/python.exe" ] || uv venv "$B/venv" --python 3.12
uv pip install --python "$B/venv/Scripts/python.exe" torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
uv pip install --python "$B/venv/Scripts/python.exe" -r "$B/ComfyUI/requirements.txt"
dl() { mkdir -p "$B/models/$2"; [ -s "$B/models/$2/$3" ] && return 0
       curl -L --fail --retry 3 -C - -o "$B/models/$2/$3.part" "$1" && mv "$B/models/$2/$3.part" "$B/models/$2/$3"; }
HF=https://huggingface.co/Comfy-Org
dl "$HF/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors" vae wan2.2_vae.safetensors
dl "$HF/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" text_encoders umt5_xxl_fp8_e4m3fn_scaled.safetensors
dl "$HF/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" diffusion_models wan2.2_ti2v_5B_fp16.safetensors
if [ "$1" = "--3d" ]; then
  dl "$HF/BiRefNet/resolve/main/background_removal/birefnet.safetensors" background_removal birefnet.safetensors
  dl "$HF/TRELLIS.2/resolve/main/clip_vision/dino_v3_vit_l.safetensors" clip_vision dino_v3_vit_l.safetensors
  dl "$HF/TRELLIS.2/resolve/main/vae/trellis_2_shape_vae_bf16.safetensors" vae trellis_2_shape_vae_bf16.safetensors
  dl "$HF/TRELLIS.2/resolve/main/vae/trellis_2_texture_vae_bf16.safetensors" vae trellis_2_texture_vae_bf16.safetensors
  dl "$HF/TRELLIS.2/resolve/main/diffusion_models/trellis_2_int8_convrot.safetensors" diffusion_models trellis_2_int8_convrot.safetensors
fi
"$B/venv/Scripts/python.exe" -c "import torch; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available())"
echo "ComfyUI installed in $B"
