param(
  [switch]$WithInstaller,
  [switch]$OneFile
)

$ErrorActionPreference = 'Stop'

Write-Host "[1/3] Build native binaries (DLL/Viewer)"
Push-Location native
try {
  ./build.ps1
  ./build_viewer.ps1
  if (Test-Path ./build_automation.ps1) { ./build_automation.ps1 }
} finally {
  Pop-Location
}

Write-Host "[2/3] PyInstaller package"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  throw "Python launcher 'py' not found. Install Python 3." 
}

# Ensure dependencies are installed for correct collection
Write-Host "Installing Python dependencies (requirements.txt)"
py -3 -m pip install -r requirements.txt --disable-pip-version-check | Out-Null
py -3 -m pip install --upgrade pyinstaller | Out-Null
py -3 -m pip install pillow --disable-pip-version-check | Out-Null

function New-AppIconFromPng {
  param([string]$Src = 'icon.png', [string]$Dst = 'packaging/app.ico')
  try {
    if (-not (Test-Path $Src)) { return }
    $dstDir = Split-Path $Dst -Parent
    if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir | Out-Null }
    $py = @"
from PIL import Image
import sys
src = r"""$Src"""
dst = r"""$Dst"""
im = Image.open(src).convert('RGBA')
im.save(dst, sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
print('ICO generated at', dst)
"@
    $tmp = Join-Path $env:TEMP 'gen_ico.py'
    Set-Content -Path $tmp -Value $py -Encoding ASCII
    & py -3 $tmp | Out-Null
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  } catch {
    Write-Warning "Icon generation failed: $($_.Exception.Message)"
  }
}

# Generate packaging/app.ico from icon.png if needed (used by spec and onefile)
if (-not (Test-Path 'packaging\app.ico') -and (Test-Path 'icon.png')) {
  Write-Host "Generating packaging\\app.ico from icon.png"
  New-AppIconFromPng -Src 'icon.png' -Dst 'packaging/app.ico'
}
if ($OneFile) {
  Write-Host "Building single-file EXE (--onefile)"
  $adds = @()
  if (Test-Path native\build\thumbnail_wic.dll) { $adds += "--add-binary"; $adds += "native/build/thumbnail_wic.dll;native" }
  if (Test-Path native\build\image_viewer_d2d.exe) { $adds += "--add-binary"; $adds += "native/build/image_viewer_d2d.exe;native" }
  if (Test-Path native\build\automation.dll) { $adds += "--add-binary"; $adds += "native/build/automation.dll;native" }
  # Add MinGW runtime DLLs if present
  $gxx = $null
  try { $gxx = (Get-Command g++ -ErrorAction SilentlyContinue).Source } catch {}
  if ($gxx) {
    $mingw = Split-Path $gxx -Parent
    foreach ($dll in @('libstdc++-6.dll','libgcc_s_seh-1.dll','libwinpthread-1.dll')) {
      $p = Join-Path $mingw $dll
      if (Test-Path $p) { $adds += "--add-binary"; $adds += "$p;native" }
    }
  }
  # Prepare icon (packaging/app.ico). If missing but icon.png exists, generate.
  $ico = "packaging\app.ico"
  if (-not (Test-Path $ico) -and (Test-Path "icon.png")) {
    Write-Host "Generating packaging\\app.ico from icon.png"
    New-AppIconFromPng -Src 'icon.png' -Dst $ico
  }
  $iconArg = @()
  if (Test-Path $ico) { $iconArg = @('--icon', $ico) }
  py -3 -m PyInstaller --noconfirm --clean --noconsole --name OBS-Screenshot-Tool --onefile @adds @iconArg `
    --hidden-import customtkinter --hidden-import PIL._tkinter_finder --hidden-import obswebsocket --hidden-import darkdetect `
    combined_app.py
} else {
  py -3 -m PyInstaller --noconfirm --clean packaging/obs_screenshot_tool.spec
}

if ($OneFile) {
  Write-Host "Artifacts: dist/OBS-Screenshot-Tool.exe"
  # Place .env template next to the EXE (if not exists)
  $envT = Join-Path packaging ".env.template"
  $outEnv = Join-Path dist ".env"
  if (Test-Path $envT -PathType Leaf -and -not (Test-Path $outEnv -PathType Leaf)) {
    Copy-Item $envT $outEnv -Force
  }
} else {
  Write-Host "Artifacts: dist/OBS-Screenshot-Tool"
}

if ($WithInstaller) {
  Write-Host "[3/3] Inno Setup (installer)"
  if (-not (Get-Command iscc.exe -ErrorAction SilentlyContinue)) {
    throw "Inno Setup compiler 'iscc.exe' not found. Install Inno Setup and ensure it is in PATH."
  }
  & iscc.exe packaging\installer.iss /DSourceDir="${PWD}\dist\OBS-Screenshot-Tool"
}

Write-Host "Done."
