<#
  deploy-dev.ps1 — Deploy del Volumes app sull'ambiente DEV.

  Cosa fa:
    1. Sincronizza la cartella app/ nel workspace Databricks
    2. Lancia il deploy dell'app DEV

  Uso:
    .\scripts\deploy-dev.ps1

  Eseguibile da qualsiasi cartella: i path sono risolti rispetto alla repo.
  Richiede: Databricks CLI nel PATH e un profilo autenticato (databricks auth login).
#>
$ErrorActionPreference = "Stop"

# --- Config DEV ---------------------------------------------------------------
$AppName    = "dataretrival"   # nome dell'App Databricks usata come DEV
$RemotePath = "/Workspace/Users/leonardo.nasso@luxottica.com/databricks_apps/dataretrival_2026_05_15-08_55/dataretrival-app"
# ------------------------------------------------------------------------------

$RepoRoot = Split-Path -Parent $PSScriptRoot
$AppDir   = Join-Path $RepoRoot "app"

if (-not (Get-Command databricks -ErrorAction SilentlyContinue)) {
    throw "Databricks CLI non trovato nel PATH. Apri un nuovo terminale o installala."
}

# Avviso se non sei sul branch dev
$branch = (git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -and $branch -ne "dev") {
    Write-Warning "Sei sul branch '$branch', non 'dev'. Il deploy DEV andrebbe fatto da 'dev'."
    if ((Read-Host "Continuare comunque? (y/N)") -ne "y") { exit 1 }
}

Write-Host "==> Sync  $AppDir" -ForegroundColor Cyan
Write-Host "         -> $RemotePath" -ForegroundColor DarkGray
databricks sync $AppDir $RemotePath --full
if ($LASTEXITCODE -ne 0) { throw "Sync fallito." }

Write-Host "==> Deploy app '$AppName'" -ForegroundColor Cyan
databricks apps deploy $AppName --source-code-path $RemotePath
if ($LASTEXITCODE -ne 0) { throw "Deploy fallito." }

Write-Host "Deploy DEV completato." -ForegroundColor Green