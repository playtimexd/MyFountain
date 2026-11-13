# Starts the ACE-Step 1.5 REST API (http://127.0.0.1:8001) in the foreground. Run setup_acestep.ps1 first.
$dir = 'D:\CutLab\data\models\ACE-Step-1.5'
if (-not (Test-Path $dir)) { Write-Error 'ACE-Step-1.5 not installed. Run D:\CutLab\scripts\setup_acestep.ps1 first.'; exit 1 }
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
Push-Location $dir
try { uv run acestep-api } finally { Pop-Location }
