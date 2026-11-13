# CutLab first-run setup for a fresh clone (safe to re-run; it only does what is missing).
#   .\setup.ps1                       venv + packages + interface build + shortcuts
#   .\setup.ps1 -DataDir "D:\CutLab Data"   keep user data in another folder (saved in %LOCALAPPDATA%\CutLab\desktop.json)
#   .\setup.ps1 -NoShortcuts -Launch
# Double-click setup.cmd if PowerShell scripts are blocked on this machine.
param(
  [string]$DataDir = "",
  [switch]$NoShortcuts,
  [switch]$Launch,
  [switch]$SystemSitePackages   # let the venv see packages of the base Python (how the original dev install was made)
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root "backend\.venv"
$py = Join-Path $venv "Scripts\python.exe"
$pyw = Join-Path $venv "Scripts\pythonw.exe"
$launcher = Join-Path $root "desktop\cutlab_desktop.py"
$icon = Join-Path $root "desktop\cutlab.ico"

function Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "    ok   $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    warn $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host "    FAIL $t" -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------------------------------------ prerequisites
Step "Checking prerequisites"
if (Get-Command git -ErrorAction SilentlyContinue) { Ok (git --version) } else { Warn "git not found - the app runs, but 'Check for updates' needs git (https://git-scm.com)" }

$basePy = $null
foreach ($cand in @(@("py", "-3.11"), @("py", "-3.12"), @("py", "-3.10"), @("python"))) {
  $exe = $cand[0]; $rest = @($cand | Select-Object -Skip 1)
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
  try { $v = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { continue }
  if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(1[0-3])$') { $basePy = $cand; Ok "Python $v ($($cand -join ' '))"; break }
}
if (-not (Test-Path $py) -and -not $basePy) { Fail "Python 3.10-3.13 not found (3.11 recommended): https://www.python.org/downloads/ - tick 'Add to PATH'." }

if (Get-Command npm -ErrorAction SilentlyContinue) {
  $nv = (node --version)
  if ([int]($nv.TrimStart('v').Split('.')[0]) -lt 20) { Fail "Node.js $nv is too old - install Node.js 20+ from https://nodejs.org" }
  Ok "Node.js $nv"
} else { Fail "Node.js (npm) not found - install Node.js 20+ from https://nodejs.org" }

# ------------------------------------------------------------------------------------------ python env
Step "Python environment (backend\.venv)"
if (Test-Path $py) { Ok "exists" } else {
  $exe = $basePy[0]; $rest = @($basePy | Select-Object -Skip 1)
  $vargs = $rest + @("-m", "venv")
  if ($SystemSitePackages) { $vargs += "--system-site-packages" }
  & $exe @vargs $venv
  if ($LASTEXITCODE -ne 0) { Fail "could not create the venv" }
  Ok "created"
}

# ------------------------------------------------------------------------------------------ data folder
Step "Data folder"
if ($DataDir) {
  $DataDir = [System.IO.Path]::GetFullPath($DataDir)
  New-Item -ItemType Directory -Force $DataDir | Out-Null
  & $py -c "import sys; sys.path.insert(0, r'$root\desktop'); import engine; engine.save_config({'data_dir': sys.argv[1]})" $DataDir
}
$data = & $py (Join-Path $root "desktop\engine.py") data-dir
$cfgPath = & $py (Join-Path $root "desktop\engine.py") config-path
Ok "$data"
$cfgErr = & $py -c "import sys; sys.path.insert(0, r'$root\desktop'); import engine; print(engine.load_config().get('_error', ''))"
if ($cfgErr) { Warn "$cfgErr - $cfgPath could not be read, so its data_dir/port are ignored. Fix it (use / or \\ in paths) or delete it." }
if (-not (Test-Path (Join-Path $data "cutlab.db"))) { Warn "no CutLab library in this folder yet - a new, empty one is created on first start (the app asks first)." }
Write-Host "         (projects, media, asset hub, animation library, settings + API keys; change with -DataDir or in the app)"
Write-Host "         launcher config: $cfgPath"

# ------------------------------------------------------------------------------------------ packages + build
Step "Packages and interface build (pip / npm ci / vite build - only what is missing or changed)"
& $py (Join-Path $root "desktop\updater.py") sync
if ($LASTEXITCODE -ne 0) { Fail "installing packages or building the interface failed (see above)" }

# ------------------------------------------------------------------------------------------ tools the engine uses
Step "External tools"
Push-Location (Join-Path $root "backend")
try {
  $env:CUTLAB_DATA = $data
  $tools = & $py -c "import json, shutil; from app import config; from app.services.anim import blender; print(json.dumps({'ffmpeg': shutil.which(config.FFMPEG), 'ffprobe': shutil.which(config.FFPROBE), 'blender': blender.find_exe()}))" 2>$null | ConvertFrom-Json
} catch { $tools = $null } finally { Remove-Item Env:CUTLAB_DATA -ErrorAction SilentlyContinue; Pop-Location }
if ($tools) {
  if ($tools.ffmpeg) { Ok "ffmpeg   $($tools.ffmpeg)" } else { Warn "ffmpeg not found - required for video. Install it (winget install Gyan.FFmpeg) or set the FFMPEG/FFPROBE environment variables." }
  if ($tools.blender) { Ok "Blender  $($tools.blender)" } else { Warn "Blender not found - needed for animation / 3D. Install Blender 4.2+ (https://www.blender.org) or set 'Blender executable' in Settings." }
} else { Warn "could not probe ffmpeg / Blender (engine import failed - check the output of the step above)" }

# ------------------------------------------------------------------------------------------ shortcuts
if (-not $NoShortcuts) {
  Step "Shortcuts"
  $code = @"
using System; using System.Runtime.InteropServices;
public static class CutLabAumid {
  [StructLayout(LayoutKind.Sequential, Pack = 4)] public struct PropKey { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Explicit)] public struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; [FieldOffset(16)] public IntPtr pad; }
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore { void GetCount(out uint c); void GetAt(uint i, out PropKey k); void GetValue(ref PropKey k, out PropVariant v);
    void SetValue(ref PropKey k, ref PropVariant v); void Commit(); }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr pbc, int flags, ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);
  public static void Set(string lnk, string id) {
    Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"); IPropertyStore st;
    SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2, ref iid, out st);
    PropKey k = new PropKey(); k.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"); k.pid = 5;
    PropVariant v = new PropVariant(); v.vt = 31; v.p = Marshal.StringToCoTaskMemUni(id);
    try { st.SetValue(ref k, ref v); st.Commit(); } finally { Marshal.FreeCoTaskMem(v.p); Marshal.ReleaseComObject(st); }
  }
}
"@
  try { Add-Type -TypeDefinition $code -ErrorAction Stop } catch { }
  $shell = New-Object -ComObject WScript.Shell
  $targets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "CutLab.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "CutLab.lnk")
  )
  foreach ($lnk in $targets) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $pyw
    $s.Arguments = "`"$launcher`""
    $s.WorkingDirectory = $root
    $s.IconLocation = "$icon,0"
    $s.Description = "CutLab - video, image and animation studio"
    $s.Save()
    try { [CutLabAumid]::Set($lnk, "CutLab.Desktop") } catch { Warn "taskbar id not set on $lnk ($($_.Exception.Message))" }
    Ok $lnk
  }
}

Step "Done"
Write-Host "    Start CutLab from the Start menu or the desktop shortcut, or run:"
Write-Host "      backend\.venv\Scripts\pythonw.exe desktop\cutlab_desktop.py"
if ($Launch) { Start-Process -FilePath $pyw -ArgumentList "`"$launcher`"" -WorkingDirectory $root }
