<#
 Installs ACE-Step 1.5 (local text-to-music, REST server `acestep-api` on :8001) for CutLab.
   - clones https://github.com/ACE-Step/ACE-Step-1.5 into D:\CutLab\data\models\ACE-Step-1.5
   - `uv sync` (creates its own .venv; downloads PyTorch + deps, several GB)
   - model weights (~5-10 GB DiT + LM) are downloaded by ACE-Step itself on FIRST start of acestep-api.
 Usage:   powershell -ExecutionPolicy Bypass -File D:\CutLab\scripts\setup_acestep.ps1 [-Start]
 Start later with D:\CutLab\scripts\start_acestep.ps1  (CutLab picks it up automatically via setting acestep_url).
 GPU note: ~8 GB free VRAM is enough for the 2B turbo DiT (+ optional 0.6B LM). Set ACESTEP_CONFIG_PATH in .env to change.
#>
param([switch]$Start)
$ErrorActionPreference = 'Stop'
$models = 'D:\CutLab\data\models'
$dir = Join-Path $models 'ACE-Step-1.5'
New-Item -ItemType Directory -Force $models | Out-Null
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host 'Installing uv ...'
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Test-Path (Join-Path $dir '.git'))) {
  git clone https://github.com/ACE-Step/ACE-Step-1.5.git $dir
} else {
  git -C $dir pull --ff-only
}
Push-Location $dir
try { uv sync } finally { Pop-Location }
Write-Host ''
Write-Host 'Done. Start the API server with:  powershell -File D:\CutLab\scripts\start_acestep.ps1'
Write-Host 'First start downloads the model weights (several GB). CutLab -> AI 配乐 -> 引擎 "ACE-Step" becomes available once :8001 answers /health.'
if ($Start) { & (Join-Path $PSScriptRoot 'start_acestep.ps1') }
