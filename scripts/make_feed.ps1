param(
  [Parameter(Mandatory=$true)][string]$ExePath,
  [Parameter(Mandatory=$true)][string]$Version,
  [string]$Notes = "",
  [string]$DownloadUrl = "",
  [string]$OutFile = ""
)

if (-not (Test-Path $ExePath)) { throw "File not found: $ExePath" }

$sha = (Get-FileHash -Path $ExePath -Algorithm SHA256).Hash.ToLower()

$feed = [ordered]@{
  version = $Version
  notes   = $Notes
  win     = [ordered]@{
    url    = $DownloadUrl
    sha256 = $sha
  }
}

$json = ($feed | ConvertTo-Json -Depth 6)
if ($OutFile) {
  $json | Set-Content -LiteralPath $OutFile -Encoding UTF8
  Write-Host "Wrote feed to $OutFile"
} else {
  $json
}

