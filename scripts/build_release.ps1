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

# Ensure dependencies are installed for correct collection
Write-Host "Installing Python dependencies (requirements.txt)"
py -3 -m pip install -r requirements.txt --disable-pip-version-check | Out-Null
py -3 -m pip install --upgrade pyinstaller | Out-Null

# Ensure .ico exists (convert from icon.png if needed)
$ico = Join-Path $PSScriptRoot '..' | Join-Path -ChildPath 'packaging' | Join-Path -ChildPath 'app.ico'
$png = Join-Path $PSScriptRoot '..' | Join-Path -ChildPath 'icon.png'
try {
  if (-not (Test-Path $ico -PathType Leaf) -and (Test-Path $png -PathType Leaf)) {
    Write-Host "Converting icon.png -> packaging\\app.ico"
    py -3 scripts/convert_icon.py
  }
} catch {
  Write-Warning "Icon conversion failed: $($_.Exception.Message)"
}
if ($OneFile) {
  Write-Host "Building single-file EXE (--onefile)"
  $adds = @()
  if (Test-Path native\build\thumbnail_wic.dll) { $adds += "--add-binary"; $adds += "native/build/thumbnail_wic.dll;native" }
  if (Test-Path native\build\image_viewer_d2d.exe) { $adds += "--add-binary"; $adds += "native/build/image_viewer_d2d.exe;native" }
  $iconArgs = @()
  if (Test-Path $ico -PathType Leaf) { $iconArgs += @('--icon', 'packaging/app.ico') }
  py -3 -m PyInstaller --noconfirm --clean --noconsole --name OBS-Screenshot-Tool --onefile @adds @iconArgs `
    --hidden-import customtkinter --hidden-import PIL._tkinter_finder `
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
