<#
 Downloads a faster-whisper model into D:\CutLab\data\models\whisper (used by 智能字幕 / 口播剪辑 / 搜索转写).
 Sizes: tiny ~75MB, base ~145MB, small ~484MB (default), medium ~1.5GB, large-v3 ~3GB.
 Usage: powershell -ExecutionPolicy Bypass -File D:\CutLab\scripts\setup_whisper.ps1 [-Model small]
 (Same thing as the "下载模型" button in the UI / POST /api/ai/subtitles/prepare.)
#>
param([string]$Model = 'small')
$py = 'D:\CutLab\backend\.venv\Scripts\python.exe'
& $py -c "from faster_whisper.utils import download_model; print(download_model('$Model', cache_dir=r'D:\CutLab\data\models\whisper'))"
Write-Host "Set it as default: PUT /api/settings {values:{whisper_model:'$Model'}}"
