# Run everything on this Windows PC, no Docker. From this folder:
#   powershell -ExecutionPolicy Bypass -File run_local.ps1
# Needs: Python, mongosh, and either the Ollama app (local models) or a Claude login
# (claude auth login) for agents whose model starts with "claude". Then open http://localhost:8080
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Python packages go in a folder outside OneDrive (first run only, about a minute).
$venv = Join-Path $env:LOCALAPPDATA "ai-grant-project-venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "First run: installing Python packages ..."
    python -m venv $venv
    & $py -m pip install --quiet -r judge\requirements.txt
}

$usesOllama = Select-String -Path agents.json -Pattern '"model":\s*"(?!claude)' -Quiet
if ($usesOllama) {
    try { Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null }
    catch { Write-Host "Ollama isn't running: start the Ollama app from the Start menu, then run this again." -ForegroundColor Yellow; exit 1 }
}
& $py judge\judge.py pull    # downloads any Ollama model in agents.json that's missing (quick if all present)

if (-not (Test-NetConnection localhost -Port 27017 -WarningAction SilentlyContinue).TcpTestSucceeded) {
    Write-Host "Note: no database on localhost:27017. To use the VM's, run this in another window:" -ForegroundColor Yellow
    Write-Host "  ssh -L 27017:localhost:27017 cjw22003@uchcgrantai.cse.uconn.edu" -ForegroundColor Yellow
}

& $py judge\server.py
