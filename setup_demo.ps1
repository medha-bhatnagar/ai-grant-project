# One-time setup: loads the made-up demo grants into the MongoDB on THIS PC. From this folder:
#   powershell -ExecutionPolicy Bypass -File setup_demo.ps1
# It replaces the demo collections, so it refuses to run if the database holds anything
# other than the SAMPLE-xxx demo requests (for example the team's real database through ssh).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$db = "mongodb://localhost:27017/grants"

if (-not (Get-Command mongosh -ErrorAction SilentlyContinue)) {
    Write-Host "mongosh isn't installed. Run:  winget install MongoDB.Server MongoDB.Shell  then open a new PowerShell." -ForegroundColor Yellow; exit 1
}
if (-not (Test-NetConnection localhost -Port 27017 -WarningAction SilentlyContinue).TcpTestSucceeded) {
    Write-Host "MongoDB isn't running on this PC. Start it: open Services, find 'MongoDB Server', click Start." -ForegroundColor Yellow; exit 1
}
$real = mongosh $db --quiet --eval 'db.requests.countDocuments({request_id: {$not: /^SAMPLE-/}})'
if ([int]$real -gt 0) {
    Write-Host "This database has $real real (non-SAMPLE) requests, so it isn't a demo database. Not touching it." -ForegroundColor Red; exit 1
}

# Python packages go in a folder outside OneDrive (shared with run_local.ps1).
$venv = Join-Path $env:LOCALAPPDATA "ai-grant-project-venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Installing Python packages (about a minute) ..."
    python -m venv $venv
    & $py -m pip install --quiet -r judge\requirements.txt
}

Write-Host "Loading demo grants SAMPLE-001 to SAMPLE-005 ..."
mongosh $db --quiet demo_data.js | Out-Null
& $py judge\judge.py add-document samples\good_grant.docx SAMPLE-004
& $py judge\judge.py add-document samples\bad_grant.docx SAMPLE-005
Write-Host "`nDone. Now start it:  powershell -ExecutionPolicy Bypass -File run_local.ps1" -ForegroundColor Green
