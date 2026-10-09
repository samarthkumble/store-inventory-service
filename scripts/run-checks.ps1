# Runs every check in one go and saves all output to run-log.txt
# (so it can be reviewed without copy-pasting).
#
# Usage, from the project folder:   .\scripts\run-checks.ps1
#
# Steps: fresh Docker stack (6 containers) -> tests + coverage gate (they use
# the stack's Postgres and Redis, in separate test databases) -> reseed ->
# event pipeline demo with timing -> benchmark.
# NOTE: starts from empty volumes, so local dev data is reset each run.

$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
$python = ".\.venv\Scripts\python.exe"
$log = "run-log.txt"
Set-Content -Path $log -Value "run-checks.ps1 started $(Get-Date -Format s)" -Encoding UTF8

function Step($title, $command) {
    $header = "`n===== $title =====`n> $command"
    Write-Host $header -ForegroundColor Cyan
    Add-Content -Path $log -Value $header -Encoding UTF8
    # cmd /c merges stderr into stdout without PowerShell turning it into errors.
    cmd /c "$command 2>&1" | ForEach-Object {
        Write-Host $_
        Add-Content -Path $log -Value $_ -Encoding UTF8
    }
    $code = $LASTEXITCODE
    Add-Content -Path $log -Value "[exit code $code]" -Encoding UTF8
    return $code
}

# The Docker API needs port 8000. A dev server (uvicorn) left running would block it.
$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $owner = (Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    if ($owner -like "python*" -or $owner -like "uvicorn*") {
        $msg = "Port 8000 is used by the dev server ($owner). Press Ctrl+C in that window, then run this script again."
        Write-Host $msg -ForegroundColor Red
        Add-Content -Path $log -Value $msg -Encoding UTF8
        exit 1
    }
}

Step "0. Install/update Python packages (needs internet)" "$python -m pip install -q -r requirements-dev.txt" | Out-Null
Step "1. Remove old containers and volumes (fresh start)" "docker compose down -v --remove-orphans" | Out-Null
$up = Step "2. Build and start all 6 containers" "docker compose up -d --build --wait"
Step "3. Container status" "docker compose ps" | Out-Null
if ($up -ne 0) {
    # Everything after this needs the containers, so stop instead of
    # letting every integration test time out one by one.
    $msg = "`nSTOPPED: the containers did not start (see step 2 above). A 'Read timed out' there means the internet connection dropped during the build - check it and run this script again."
    Write-Host $msg -ForegroundColor Red
    Add-Content -Path $log -Value $msg -Encoding UTF8
    exit 1
}

$tests = Step "4. Tests with coverage (gate: 90%)" "$python -m pytest --cov --cov-report=term --cov-fail-under=90 -p no:warnings"

Step "5. Seed the dev database (inside the api container)" "docker compose exec -T api python -m scripts.seed --reset" | Out-Null
Step "6. Event pipeline demo: sale -> reorder suggestion (timed)" "$python -m scripts.demo_events" | Out-Null
Step "7. Benchmark the containerised API" "$python -m scripts.bench" | Out-Null

$summary = "`nDONE. tests exit=$tests, docker exit=$up. Full output saved in $log"
Write-Host $summary -ForegroundColor Green
Add-Content -Path $log -Value $summary -Encoding UTF8
