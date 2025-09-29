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
} finally {
  Pop-Location
}

Write-Host "[2/3] PyInstaller package"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  throw "Python launcher 'py' not found. Install Python 3." 
}

py -3 -m pip install --upgrade pyinstaller | Out-Null
if ($OneFile) {
  Write-Host "Building single-file EXE (--onefile)"
  $adds = @()
  if (Test-Path native\build\thumbnail_wic.dll) { $adds += "--add-binary"; $adds += "native/build/thumbnail_wic.dll;native" }
  if (Test-Path native\build\image_viewer_d2d.exe) { $adds += "--add-binary"; $adds += "native/build/image_viewer_d2d.exe;native" }
  py -3 -m PyInstaller --noconsole --name OBS-Screenshot-Tool --onefile @adds combined_app.py
} else {
  py -3 -m PyInstaller packaging/obs_screenshot_tool.spec
}

if ($OneFile) {
  Write-Host "Artifacts: dist/OBS-Screenshot-Tool.exe"
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
