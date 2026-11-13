@echo off
rem Double-click to set up CutLab (runs setup.ps1 even when PowerShell scripts are blocked by policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
