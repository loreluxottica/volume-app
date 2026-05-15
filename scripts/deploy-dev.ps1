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

# Esegue un comando nativo (git/databricks) senza che la sua stderr abortisca
# lo script: con $ErrorActionPreference = "Stop", PowerShell 5.1 tratta ogni
# riga di stderr di un comando nativo come errore terminante — e git scrive
# l'output di 'push' su stderr anche quando va a buon fine. Qui la stderr viene
# convertita in testo normale e l'esito si verifica solo via $LASTEXITCODE.
function Invoke-Native {
    param(
        [Parameter(Mandatory)] [scriptblock] $Command,
        [Parameter(Mandatory)] [string]      $ErrorMessage
    )
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command 2>&1 | ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if ($LASTEXITCODE -ne 0) { throw $ErrorMessage }
}

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
Invoke-Native { git -C $RepoRoot push origin $DevBranch } "git push fallito."

Write-Host "==> 2/3  Pull del Git folder Databricks ($DevBranch)" -ForegroundColor Cyan
Invoke-Native { databricks repos update $GitFolder --branch $DevBranch } "repos update fallito."

Write-Host "==> 3/3  Deploy dell'app '$AppName'" -ForegroundColor Cyan
Invoke-Native { databricks apps deploy $AppName --source-code-path $GitFolder } "deploy fallito."

Write-Host ""
Write-Host "Deploy DEV completato." -ForegroundColor Green
Write-Host "  $AppUrl"