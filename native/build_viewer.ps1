param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = 'Stop'

$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $PSScriptRoot 'image_viewer_d2d.cpp'
$outDir = Join-Path $PSScriptRoot 'build'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$dst = Join-Path $outDir 'image_viewer_d2d.exe'

Write-Host "Building native D2D viewer -> $dst"

& g++ -std=c++17 -O2 -municode -o $dst $src -ld2d1 -ldwrite -lwindowscodecs -lole32 -loleaut32 -lgdi32 -luuid
if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE"
}

Write-Host "Done."

