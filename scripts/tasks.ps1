# Equivalent Windows du Makefile.
# Usage : .\scripts\tasks.ps1 <setup|up|down|init-db|ingest|ingest-small|run|evaluate|dashboard|demo|test> [arguments...]
# Ex.   : .\scripts\tasks.ps1 run --simulate-audit-flag kpi_semantic
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("setup", "up", "down", "init-db", "ingest", "ingest-small", "run", "evaluate", "dashboard", "demo", "test")]
    [string]$Task,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Continue"  # les logs Python sur stderr ne sont pas des erreurs ; on teste $LASTEXITCODE
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".\.venv\Scripts\python.exe"

switch ($Task) {
    "setup" {
        python -m venv .venv
        & $py -m pip install -e ".[dev]"
        if (-not (Test-Path .env)) { Copy-Item .env.example .env }
    }
    "up" {
        docker compose up -d --wait
        & $py -m biflow.db.init_db
    }
    "down"         { docker compose down }
    "init-db"      { & $py -m biflow.db.init_db }
    "ingest"       { & $py -m biflow.ingestion @Rest }
    "ingest-small" { & $py -m biflow.ingestion --max-rows 5000 @Rest }
    "run"          { & $py -m biflow.run @Rest }
    "evaluate"     { & $py -m biflow.evaluation @Rest }
    "dashboard"    { & $py -m streamlit run src/biflow/dashboard/app.py @Rest }
    "demo"         { & (Join-Path $PSScriptRoot "demo.ps1") @Rest }
    "test"         { & $py -m pytest @Rest }
}
exit $LASTEXITCODE
