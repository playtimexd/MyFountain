<#
.SYNOPSIS
  Adds the KimodoDeps plugin to an Unreal project so the editor installs NVIDIA Kimodo's Python packages on every start.

.DESCRIPTION
  1. Copies KimodoDeps/ into <Project>/Plugins/KimodoDeps (replacing an older copy).
  2. Enables KimodoDeps and PythonScriptPlugin in the .uproject (a backup is written next to it first).
  3. With -InstallNow: installs the packages right away into <Project>/Intermediate/PipInstall with exactly the command
     the editor would run (wheels only, every package pinned with its sha256).  The first install downloads ~4 GB
     (PyTorch CUDA 12.8); without -InstallNow the editor does it on its next start and waits until it is done.

.EXAMPLE
  .\install_kimodo_deps.ps1 -Project "F:\UNREAL PROJECTS\MyProject3\MyProject3.uproject"
.EXAMPLE
  .\install_kimodo_deps.ps1 -Project "F:\UNREAL PROJECTS\MyProject3" -InstallNow
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Project,      # .uproject file or the project folder
    [switch] $InstallNow,                                  # install the packages now (close the editor first)
    [string] $EnginePath                                   # default: from the .uproject's EngineAssociation, e.g. C:\Program Files\Epic Games\UE_5.9
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pluginSrc = Join-Path $here 'KimodoDeps'
$extraIndex = 'https://download.pytorch.org/whl/cu128'

# --- the project
if (Test-Path $Project -PathType Container) {
    $uproject = Get-ChildItem -Path $Project -Filter *.uproject | Select-Object -First 1
    if (-not $uproject) { throw "No .uproject file in $Project" }
    $uproject = $uproject.FullName
} else {
    $uproject = (Resolve-Path $Project).Path
}
$projDir = Split-Path -Parent $uproject
Write-Host "Project: $uproject"

# --- 1. copy the plugin
$dst = Join-Path $projDir 'Plugins\KimodoDeps'
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
Copy-Item -Recurse $pluginSrc $dst
Write-Host "Plugin copied to $dst"

# --- 2. enable it in the .uproject
$raw = Get-Content -Raw -Encoding UTF8 $uproject
$desc = $raw | ConvertFrom-Json
if (-not $desc.PSObject.Properties['Plugins']) { $desc | Add-Member -NotePropertyName Plugins -NotePropertyValue @() }
$plugins = @($desc.Plugins)
$changed = $false
foreach ($name in 'PythonScriptPlugin', 'KimodoDeps') {
    $p = $plugins | Where-Object { $_.Name -eq $name } | Select-Object -First 1
    if (-not $p) {
        $plugins += [pscustomobject]@{ Name = $name; Enabled = $true }
        $changed = $true
    } elseif (-not $p.Enabled) {
        $p.Enabled = $true
        $changed = $true
    }
}
if ($changed) {
    $backup = "$uproject.bak-kimododeps"
    if (-not (Test-Path $backup)) { Set-Content -Encoding UTF8 -NoNewline -Path $backup -Value $raw }
    $desc.Plugins = $plugins
    $desc | ConvertTo-Json -Depth 32 | Set-Content -Encoding UTF8 $uproject
    Write-Host "Enabled PythonScriptPlugin + KimodoDeps in the .uproject (backup: $backup)"
} else {
    Write-Host 'PythonScriptPlugin + KimodoDeps already enabled in the .uproject'
}

if (-not $InstallNow) {
    Write-Host ''
    Write-Host 'Done. The editor installs the Kimodo packages on its next start (first time ~4 GB, it waits until finished).'
    Write-Host 'To install now instead (editor closed):  .\install_kimodo_deps.ps1 -Project "<same path>" -InstallNow'
    exit 0
}

# --- 3. install now, the way the editor would
# only an editor that has THIS project open uses its Python environment (other projects' editors may keep running)
$projKey = $uproject.Replace('\', '/').ToLower()
$running = Get-CimInstance Win32_Process -Filter "Name LIKE 'UnrealEditor%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Replace('\', '/').ToLower().Contains($projKey) }
if ($running) { throw "Close the Unreal editor that has this project open first (pid $($running.ProcessId -join ', ')): it uses the same Python environment." }
if (-not $EnginePath) {
    $assoc = $desc.EngineAssociation
    if (-not $assoc) { throw 'The .uproject has no EngineAssociation; pass -EnginePath "C:\Program Files\Epic Games\UE_5.x"' }
    $EnginePath = Join-Path $env:ProgramFiles "Epic Games\UE_$assoc"
}
$enginePy = Join-Path $EnginePath 'Engine\Binaries\ThirdParty\Python3\Win64\python.exe'
if (-not (Test-Path $enginePy)) { throw "Unreal's Python not found: $enginePy (pass -EnginePath)" }
$venv = Join-Path $projDir 'Intermediate\PipInstall'
$py = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host "Creating the project's pip environment $venv (same command the editor uses)"
    & $enginePy -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
# the plugin ships antlr4-python3-runtime (no wheel on PyPI); the editor links the plugin's site-packages into this
# environment with a .pth file before it runs pip - do the same so pip sees it as installed
$site = Join-Path $venv 'Lib\site-packages'
Set-Content -Encoding ASCII -Path (Join-Path $site 'kimodo_deps_plugin.pth') -Value (Join-Path $dst 'Content\Python\Lib\site-packages')
$reqs = Join-Path $env:TEMP 'kimodo_deps_requirements.txt'
(Get-Content -Raw -Encoding UTF8 (Join-Path $dst 'KimodoDeps.uplugin') | ConvertFrom-Json).PythonRequirements[0].Requirements |
    Set-Content -Encoding ASCII $reqs
Write-Host 'Installing the Kimodo packages (first time ~4 GB) ...'
& $py -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes --extra-index-url $extraIndex -r $reqs
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }
& $py -c "import torch, numpy, hydra, omegaconf, transformers; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available(), '| numpy', numpy.__version__)"
Write-Host 'Done. Start the editor: the KimodoDeps line in the Output Log confirms the packages.'
