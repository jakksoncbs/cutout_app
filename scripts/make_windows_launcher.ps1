# Build a Windows launcher folder with a taskbar-pinnable shortcut.
#
# Produces <OutDir>\Cutout\ containing:
#   app\               - copy of the app source
#   Cutout.bat         - console launcher (shows first-run setup progress)
#   Cutout.vbs         - silent launcher (no console window)
#   Cutout.lnk         - shortcut you can pin to the taskbar / Start menu
#
# The target PC needs Python 3.11+ installed (with "Add to PATH").
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\make_windows_launcher.ps1 [OutDir]

param([string]$OutDir = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrEmpty($OutDir)) { $OutDir = Join-Path $Root "dist" }

$Dest = Join-Path $OutDir "Cutout"
$App  = Join-Path $Dest "app"
Write-Host "Building $Dest"
if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
New-Item -ItemType Directory -Force -Path $App | Out-Null

# --- copy source ---------------------------------------------------------
foreach ($f in @("launch.py","server.py","cutout.py","requirements.txt","README.md")) {
  Copy-Item (Join-Path $Root $f) (Join-Path $App $f)
}

# --- console launcher (good for first run / seeing progress) -------------
@"
@echo off
cd /d "%~dp0app"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required. Install it from https://www.python.org/downloads/
  echo Tick "Add Python to PATH" during install, then try again.
  pause
  exit /b 1
)
python launch.py
pause
"@ | Set-Content -Encoding ASCII (Join-Path $Dest "Cutout.bat")

# --- silent launcher (no console window) ---------------------------------
@"
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir & "\app"
sh.Run "pythonw """ & dir & "\app\launch.py""", 0, False
"@ | Set-Content -Encoding ASCII (Join-Path $Dest "Cutout.vbs")

# --- pinnable shortcut (targets the console launcher) --------------------
$shortcut = Join-Path $Dest "Cutout.lnk"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($shortcut)
$lnk.TargetPath = Join-Path $Dest "Cutout.bat"
$lnk.WorkingDirectory = $Dest
$lnk.Description = "Cutout - make transparent PNGs"
$icon = Join-Path $Root "assets\Cutout.ico"
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Save()

Write-Host "Done: $Dest"
Write-Host "Right-click Cutout.lnk -> Pin to taskbar (or Pin to Start)."
