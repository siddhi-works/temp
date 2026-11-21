<#
.SYNOPSIS
  Copies a clean snapshot of this project into a folder, ready to commit to another repository.

.DESCRIPTION
  Only files tracked by git are copied (git ls-files), so secrets such as .env files, databases,
  node_modules, virtual environments and build output can never be included.

.EXAMPLE
  .\scripts\export-submission.ps1 -Dest C:\work\organiser-repo -SubFolder saathi-campus
#>
param(
  [Parameter(Mandatory = $true)][string]$Dest,
  [string]$SubFolder = "saathi-campus"
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$target = Join-Path $Dest $SubFolder

Push-Location $root
try {
  $files = git ls-files
  if (-not $files) { throw "No tracked files found. Run this from inside the project's git repository." }

  # Refuse to continue if anything that looks like a secret is tracked.
  $risky = $files | Where-Object { $_ -match '(^|/)\.env($|\.local)' -or $_ -match '\.(db|sqlite|pem|key)$' }
  if ($risky) { throw "Refusing to export: these tracked files look like secrets: $($risky -join ', ')" }

  New-Item -ItemType Directory -Force $target | Out-Null
  foreach ($f in $files) {
    $to = Join-Path $target $f
    New-Item -ItemType Directory -Force (Split-Path $to) | Out-Null
    Copy-Item -LiteralPath $f -Destination $to -Force
  }
  Write-Host "Copied $($files.Count) files to $target"
  Write-Host "Next: cd into the target repository, run 'git status', then add, commit and push."
}
finally {
  Pop-Location
}
