# Démonstration BIFlow (soutenance) : run sur les données réelles, puis dashboard.
# Usage : .\scripts\demo.ps1            # baseline sans LLM (rapide, sans réseau)
#         .\scripts\demo.ps1 -Llm ollama  # avec le modèle local (ollama pull qwen2.5:7b)
#         .\scripts\demo.ps1 -NoDashboard
param(
    [ValidateSet("none", "ollama", "mistral", "auto")]
    [string]$Llm = "none",
    [string]$File = "data/raw/nyc311_2023-01_2026-09_echantillon.parquet",
    [switch]$NoDashboard
)

$ErrorActionPreference = "Continue"  # les logs Python sur stderr ne sont pas des erreurs ; on teste $LASTEXITCODE
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".\.venv\Scripts\python.exe"
$env:BIFLOW_LLM_PROVIDER = $Llm

if (-not (Test-Path $File)) {
    Write-Host "Extrait introuvable : $File" -ForegroundColor Red
    Write-Host "Importer les pages brutes de l'équipe : python -m biflow.ingestion.import_pages <dossier raw> --name nyc311_2023-01_2026-09_echantillon"
    exit 1
}

Write-Host "`n=== Pipeline complet sur $File (LLM : $Llm) ===" -ForegroundColor Cyan
& $py -m biflow.run --no-db --file $File `
    --question "Combien de demandes de bruit dans le Bronx ?" `
    --question "Quel borough a le délai médian de résolution le plus long ?" `
    --question "Quelle agence traite le plus de demandes ?" `
    --question "Pourquoi les délais sont-ils plus longs à Staten Island ?"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $NoDashboard) {
    Write-Host "`n=== Dashboard : http://localhost:8501 (Ctrl+C pour arrêter) ===" -ForegroundColor Cyan
    & $py -m streamlit run src/biflow/dashboard/app.py
}
