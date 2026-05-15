<#
  deploy-dev.ps1 — Pubblica le modifiche sull'ambiente DEV.

  La Databricks App 'dataretrival' e collegata a questa repo GitHub, quindi il
  deploy parte da GitHub: questo script fa il push del branch 'dev'; il deploy
  vero e proprio si lancia poi dalla UI Databricks (pulsante Deploy).

  Uso:
    .\scripts\deploy-dev.ps1

  Eseguibile da qualsiasi cartella.
#>
$ErrorActionPreference = "Stop"

# --- Config DEV ---------------------------------------------------------------
$DevBranch = "dev"
$AppUrl    = "https://dataretrival-8661566820370235.15.azure.databricksapps.com"
# ------------------------------------------------------------------------------

$RepoRoot = Split-Path -Parent $PSScriptRoot

$branch = (git -C $RepoRoot rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne $DevBranch) {
    Write-Warning "Sei sul branch '$branch', non '$DevBranch'."
    Write-Host    "Il deploy DEV va fatto da '$DevBranch'. Fai prima il merge della tua feature in '$DevBranch'."
    exit 1
}

# Blocca se ci sono modifiche non committate
if (git -C $RepoRoot status --porcelain) {
    Write-Warning "Ci sono modifiche non committate. Committale prima del deploy:"
    git -C $RepoRoot status --short
    exit 1
}

Write-Host "==> Push di '$DevBranch' su GitHub" -ForegroundColor Cyan
git -C $RepoRoot push origin $DevBranch
if ($LASTEXITCODE -ne 0) { throw "git push fallito." }

Write-Host ""
Write-Host "Push completato." -ForegroundColor Green
Write-Host "Ultimo passo - lancia il deploy dalla UI Databricks:" -ForegroundColor Yellow
Write-Host "  $AppUrl"
Write-Host "  App 'dataretrival' -> Deploy -> branch '$DevBranch'"