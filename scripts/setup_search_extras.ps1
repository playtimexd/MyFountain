<#
 Optional upgrades for AI features (each downloads weights, so run only what you want):
   -Rembg      pip install rembg onnxruntime            (+ u2net.onnx ~170MB downloaded on first use)  -> better 抠图
   -Clip       pip install open_clip_torch              (+ laion/CLIP-ViT-B-32 weights ~600MB)         -> visual search
   -Lama       pip install simple-lama-inpainting       (+ big-lama ~200MB on first use)              -> better 去水印/修复
 Usage: powershell -ExecutionPolicy Bypass -File D:\CutLab\scripts\setup_search_extras.ps1 -Clip -Rembg
#>
param([switch]$Rembg, [switch]$Clip, [switch]$Lama)
$py = 'D:\CutLab\backend\.venv\Scripts\python.exe'
if ($Rembg) { uv pip install --python $py rembg }
if ($Clip) {
  uv pip install --python $py open_clip_torch
  & $py -c "from huggingface_hub import snapshot_download; print(snapshot_download('laion/CLIP-ViT-B-32-laion2B-s34B-b79K'))"
}
if ($Lama) { uv pip install --python $py simple-lama-inpainting }
Write-Host 'Done. Restart the CutLab engine so new modules are detected.'
