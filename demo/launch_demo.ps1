param(
    [ValidateRange(1024, 65535)][int]$Port = 8501,
    [switch]$Headless,
    [switch]$CheckOnly,
    [switch]$CheckOllama
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error 'Python du projet absent. Creer le venv puis installer requirements.txt.'
    exit 1
}
$preflightArgs = @('-m', 'bi_analyst.demo_preflight', '--root', $projectRoot)
if ($CheckOllama) { $preflightArgs += @('--check-ollama', '--check-data') }
& $pythonPath @preflightArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Precontrole echoue. Installation : .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit $LASTEXITCODE
}
if ($CheckOnly) { exit 0 }
$streamlitArgs = @('-m', 'streamlit', 'run', (Join-Path $PSScriptRoot 'streamlit_app.py'),
    '--server.address', '127.0.0.1', '--server.port', [string]$Port,
    '--server.headless', ([string]$Headless.IsPresent).ToLowerInvariant(), '--browser.gatherUsageStats', 'false')
Write-Host "Demonstration locale : http://127.0.0.1:$Port"
Write-Host "Mode initial : restitution enregistree. Le mode direct se choisit explicitement dans l'interface."
& $pythonPath @streamlitArgs
exit $LASTEXITCODE
