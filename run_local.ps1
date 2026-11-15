# Run everything on this Windows PC, no Docker. From this folder:
#   powershell -ExecutionPolicy Bypass -File run_local.ps1
# Needs: Python, mongosh, and a Claude login (claude auth login) for agents whose model starts with
# "claude" (or the Ollama app for local models). Then open http://localhost:8080 and pick a demo user.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Python packages go in a folder outside OneDrive (first run only, about a minute).
$venv = Join-Path $env:LOCALAPPDATA "ai-grant-project-venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv $venv }
Write-Host "Checking Python packages ..."
& $py -m pip install --quiet -r backend\requirements.txt -r judge\requirements.txt

$usesOllama = Select-String -Path agents\judge.json, agents\llms\*.json -Exclude _* -Pattern '"model":\s*"(?!claude)' -Quiet
if ($usesOllama) {
    try { Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null }
    catch { Write-Host "Ollama isn't running: start the Ollama app from the Start menu, then run this again." -ForegroundColor Yellow; exit 1 }
}
& $py judge\judge.py pull    # downloads any Ollama model the agents use that's missing (quick if all present)

if (-not (Test-NetConnection localhost -Port 27017 -WarningAction SilentlyContinue).TcpTestSucceeded) {
    Write-Host "Note: no database on localhost:27017, so the assistant can't read grant records." -ForegroundColor Yellow
}

# Medha's signed-in app, with the assistant inside. Demo sign-in: pick a person from the list.
$env:FRONTEND_DIR = Join-Path $PSScriptRoot "dashboard"
$env:PUBLIC_URL = "http://localhost:8080"
Write-Host "`nGrant Application Assistant: http://localhost:8080   (Ctrl+C to stop)`n"
& $py -m uvicorn backend.main:app --host 127.0.0.1 --port 8080
