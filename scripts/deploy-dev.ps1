<#
  deploy-dev.ps1 — Deploy del Volumes app sull'ambiente DEV.

  L'app Databricks 'dataretrival' deploia da un Git folder del workspace
  collegato a questa repo GitHub. Lo script fa l'intero ciclo:
    1. push del branch 'dev' su GitHub
    2. pull del Git folder Databricks (branch 'dev')
    3. deploy dell'app

  Uso:
    .\scripts\deploy-dev.ps1

  Eseguibile da qualsiasi cartella.
  Richiede: Databricks CLI nel PATH e un profilo autenticato.
#>
$ErrorActionPreference = "Stop"

# --- Config DEV ---------------------------------------------------------------
$DevBranch = "dev"
$AppName   = "dataretrival"
$GitFolder = "/Workspace/Users/lorenzo.muscillo@luxottica.com/volume-app"
$AppUrl    = "https://dataretrival-8661566820370235.15.azure.databricksapps.com"
# ------------------------------------------------------------------------------

$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command databricks -ErrorAction SilentlyContinue)) {
    throw "Databricks CLI non trovata nel PATH. Apri un nuovo terminale."
}

$branch = (git -C $RepoRoot rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne $DevBranch) {
    Write-Warning "Sei sul branch '$branch', non '$DevBranch'."
    Write-Host    "Fai prima il merge della tua feature in '$DevBranch'."
    exit 1
}

if (git -C $RepoRoot status --porcelain) {
    Write-Warning "Ci sono modifiche non committate. Committale prima del deploy:"
    git -C $RepoRoot status --short
    exit 1
}

Write-Host "==> 1/3  Push di '$DevBranch' su GitHub" -ForegroundColor Cyan
git -C $RepoRoot push origin $DevBranch
if ($LASTEXITCODE -ne 0) { throw "git push fallito." }

Write-Host "==> 2/3  Pull del Git folder Databricks ($DevBranch)" -ForegroundColor Cyan
databricks repos update $GitFolder --branch $DevBranch
if ($LASTEXITCODE -ne 0) { throw "repos update fallito." }

Write-Host "==> 3/3  Deploy dell'app '$AppName'" -ForegroundColor Cyan
databricks apps deploy $AppName --source-code-path $GitFolder
if ($LASTEXITCODE -ne 0) { throw "deploy fallito." }

Write-Host ""
Write-Host "Deploy DEV completato." -ForegroundColor Green
Write-Host "  $AppUrl"