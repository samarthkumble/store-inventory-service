# Runs every Milestone 3 check in one go and saves all output to run-log.txt
# (so it can be reviewed without copy-pasting).
#
# Usage, from the project folder:   .\scripts\run-checks.ps1
#
# Steps: tests + coverage gate -> build and start Docker stack -> reseed the
# dev database inside the container -> benchmark the containerised API.

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

$tests = Step "1. Tests with coverage (gate: 90%)" "$python -m pytest --cov --cov-report=term --cov-fail-under=90 -p no:warnings"

$up = Step "2. Build and start db + api containers" "docker compose up -d --build --wait"
Step "3. Container status" "docker compose ps" | Out-Null

if ($up -eq 0) {
    Step "4. Reseed the dev database (inside the api container)" "docker compose exec -T api python -m scripts.seed --reset" | Out-Null
    Step "5. Benchmark the containerised API" "$python -m scripts.bench" | Out-Null
} else {
    Add-Content -Path $log -Value "Skipped seed and benchmark: containers did not start." -Encoding UTF8
}

$summary = "`nDONE. tests exit=$tests, docker exit=$up. Full output saved in $log"
Write-Host $summary -ForegroundColor Green
Add-Content -Path $log -Value $summary -Encoding UTF8
