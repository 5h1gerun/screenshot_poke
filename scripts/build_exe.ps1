param(
    [switch]$OneFile = $true,
    [string]$Name = "OBS-Screenshot-Tool"
)

Write-Host "Setting up venv and installing deps..."
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python is not on PATH. Install Python 3.10+ first."
    exit 1
}

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

$opts = @()
if ($OneFile) { $opts += "--onefile" }

pyinstaller --noconfirm --clean `
  --name $Name `
  --noconsole `
  --windowed `
  $opts `
  --hidden-import obswebsocket `
  --hidden-import customtkinter `
  --hidden-import darkdetect `
  --hidden-import PIL `
  combined_app.py

Write-Host "Build finished. Output in dist/$Name/ or dist/$Name.exe"

