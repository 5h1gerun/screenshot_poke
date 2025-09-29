param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = 'Stop'

$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $PSScriptRoot 'thumbnail_wic.cpp'
$outDir = Join-Path $PSScriptRoot 'build'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$dst = Join-Path $outDir 'thumbnail_wic.dll'

Write-Host "Building native thumbnail DLL -> $dst"

& g++ -std=c++17 -O2 -shared -o $dst $src -lole32 -loleaut32 -lwindowscodecs
if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE"
}

Write-Host "Done."
