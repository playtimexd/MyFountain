<#
 Installs the portable Real-ESRGAN ncnn-Vulkan binary (+ x4plus / animevideov3 models, ~45 MB zip) into
 D:\CutLab\data\models\realesrgan so CutLab "画质增强" uses AI super-resolution instead of Lanczos. Works on any Vulkan GPU.
 Usage: powershell -ExecutionPolicy Bypass -File D:\CutLab\scripts\setup_realesrgan.ps1
#>
$ErrorActionPreference = 'Stop'
$dst = 'D:\CutLab\data\models\realesrgan'
New-Item -ItemType Directory -Force $dst | Out-Null
$zip = Join-Path $env:TEMP 'realesrgan-ncnn.zip'
$url = 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip'
Invoke-WebRequest $url -OutFile $zip
Expand-Archive $zip -DestinationPath $dst -Force
Remove-Item $zip
Get-ChildItem $dst -Recurse -Filter 'realesrgan-ncnn-vulkan.exe' | Select-Object -First 1 | ForEach-Object { Write-Host "OK: $($_.FullName)" }
