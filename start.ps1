# One-click launcher: engine (:8787) + UI dev server (:5173). Usage:  .\start.ps1   (add -Stop to stop both)
param([switch]$Stop)
$root = $PSScriptRoot
function Stop-Port($port) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
Stop-Port 8787; Stop-Port 5173
if ($Stop) { "stopped"; return }
New-Item -ItemType Directory -Force "$root\data" | Out-Null
Start-Process -FilePath "$root\backend\.venv\Scripts\python.exe" -ArgumentList "-m", "app" -WorkingDirectory "$root\backend" `
  -RedirectStandardOutput "$root\data\server.log" -RedirectStandardError "$root\data\server.err" -WindowStyle Hidden
Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory "$root\frontend" `
  -RedirectStandardOutput "$root\data\vite.log" -RedirectStandardError "$root\data\vite.err" -WindowStyle Hidden
Start-Sleep 5
try { $h = Invoke-RestMethod http://127.0.0.1:8787/api/health; "engine ok: $($h.routers -join ', ')"; if ($h.failed.PSObject.Properties.Count) { "FAILED routers: $($h.failed | ConvertTo-Json -Compress)" } } catch { "engine not responding - see data\server.err" }
"UI: http://localhost:5173"
Start-Process "http://localhost:5173"
