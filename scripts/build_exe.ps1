param(
    [switch]$OneFile = $true,
    [string]$Name = "OBS-Screenshot-Tool",
    # UPX can cause DLL loading issues on some environments. Disable by default.
    [switch]$NoUPX = $true,
    # Build with console window for debugging bootloader issues
    [switch]$Console = $false,
    # Custom extraction directory for onefile runtime (helps if %TEMP% is restricted)
    [string]$RuntimeTmp = "$env:LOCALAPPDATA\PyInstallerCache"
)

Write-Host "Setting up venv and installing deps..."
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python is not on PATH. Install Python 3.10+ first."
    exit 1
}

# Sanity: ensure we are using 64-bit Python on 64-bit Windows (recommended)
try {
  $pyBits = & python -c "import struct; print(8*struct.calcsize('P'))"
} catch { $pyBits = $null }
if ([Environment]::Is64BitOperatingSystem -and $pyBits -and $pyBits.Trim() -ne '64') {
  Write-Warning "You are using 32-bit Python on a 64-bit OS. This often causes 'Failed to load Python DLL'. Install 64-bit Python and retry."
}

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

$buildDir = 'build'
if (-not (Test-Path $buildDir)) {
    New-Item -ItemType Directory -Path $buildDir | Out-Null
}

$iconIco = Join-Path $buildDir 'app_icon.ico'
if (Test-Path 'icon.png') {
    Write-Host "Generating ICO from icon.png..."
    try {
        # Use Pillow to convert PNG to multi-size ICO for Windows
        python -c "from PIL import Image; im=Image.open('icon.png').convert('RGBA'); im.save(r'build/app_icon.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])" 2>$null
    } catch {
        Write-Warning "Failed to generate ICO from icon.png. Ensure Pillow is installed."
    }
}

$opts = @()
if ($OneFile) { $opts += "--onefile" }
if ($OneFile -and $RuntimeTmp) { $opts += @('--runtime-tmpdir', $RuntimeTmp) }
if ($NoUPX) { $opts += "--noupx" }

# Gather VC runtime DLLs from the current Python installation to avoid
# "Failed to load Python DLL" on target machines missing VC++ redistributables.
$pyBase = & python -c "import sys,os; print(os.path.dirname(sys.executable))"

# Proactively ship common VC++ runtime DLLs alongside the EXE to avoid
# LoadLibrary failures on target machines lacking redistributables.
$dllNames = @(
  'vcruntime140.dll', 'vcruntime140_1.dll',
  'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'concrt140.dll'
)
function Find-Dll([string]$name) {
  $candidates = @(
    (Join-Path $pyBase $name),
    (Join-Path (Split-Path $pyBase -Parent) $name),
    (Join-Path "$env:SystemRoot\System32" $name)
  )
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  try { $w = & where.exe $name 2>$null; if ($w) { return $w.Split("`n")[0].Trim() } } catch {}
  return $null
}

$addBin = @()
foreach ($n in $dllNames) {
  $p = Find-Dll $n
  if ($p) { $addBin += @('--add-binary', "$p;.") }
}

# Build PyInstaller argument list explicitly to avoid quoting issues.
$pyArgs = @(
    '--noconfirm',
    '--clean',
    '--specpath', 'build',
    '--name', $Name
)
if ($Console) {
    $pyArgs += @('--console')
} else {
    $pyArgs += @('--noconsole','--windowed')
}
# Use absolute path for --icon to avoid spec/workpath double-prefix issues
$iconAbs = $null
if (Test-Path $iconIco) {
    try { $iconAbs = (Resolve-Path $iconIco).Path } catch { $iconAbs = $null }
}
if ($iconAbs) {
    $pyArgs += @('--icon', $iconAbs)
}
$pyArgs = $pyArgs + $opts + @(
    '--hidden-import', 'obswebsocket',
    '--hidden-import', 'customtkinter',
    '--hidden-import', 'darkdetect',
    '--hidden-import', 'PIL'
) + $addBin + @('combined_app.py')

& pyinstaller @pyArgs

Write-Host "Build finished. Output in dist/$Name/ or dist/$Name.exe"

