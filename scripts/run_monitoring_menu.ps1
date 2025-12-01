# scripts/run_monitoring_menu.ps1
# Simple menu so support can choose which NC Monitoring tool to run.

# --- Repo paths (adjust if needed) ---
$repoRoot   = "C:\Users\Rico\Documents\nctv-repositories\nc-monitoring"
$srcDir     = Join-Path $repoRoot "src"
$scriptsDir = Join-Path $repoRoot "scripts"

# --- Conda initialization + env activation ---
$condaExe = "C:\Users\Rico\anaconda3\Scripts\conda.exe"  # adjust if Anaconda is elsewhere
if (Test-Path $condaExe) {
    (& $condaExe "shell.powershell" "hook") | Out-String | Invoke-Expression
    conda activate monitoring
} else {
    Write-Host "WARNING: conda.exe not found at $condaExe. Falling back to system Python." -ForegroundColor Yellow
}

# --- Move to repo root and load env vars ---
Set-Location $repoRoot

. (Join-Path $scriptsDir "set_monitoring_env.ps1")

function Run-ScreenshotHealth {
    Set-Location $srcDir
    Write-Host ""
    Write-Host "Running Screenshot Health check (daily sheet)..." -ForegroundColor Cyan

    python -c "import logging; logging.basicConfig(level=logging.INFO); from checks.screenshot_health import run_screenshot_health; run_screenshot_health()"

    Write-Host ""
    Write-Host "Screenshot health check finished. You can review today's date sheet." -ForegroundColor Green
}

function Run-AnyDeskCheck {
    Set-Location $srcDir
    Write-Host ""
    Write-Host "Running AnyDesk connectivity check..." -ForegroundColor Cyan

    if (-not $env:ANYDESK_MAX_LICENSES_PER_RUN) {
        $env:ANYDESK_MAX_LICENSES_PER_RUN = "10"
    }

    python -c "import logging; logging.basicConfig(level=logging.INFO); from checks.anydesk_check import run_anydesk_check; run_anydesk_check()"

    Write-Host ""
    Write-Host "AnyDesk check finished. You can review the 'AnyDesk Status' sheet." -ForegroundColor Green
}

# --- Simple menu loop ---
while ($true) {
    Clear-Host
    Write-Host "NC Monitoring Tools" -ForegroundColor Cyan
    Write-Host "===================" -ForegroundColor Cyan
    Write-Host "1) Run Screenshot Health check (Sheets, daily tab)"
    Write-Host "2) Run AnyDesk Status check"
    Write-Host "Q) Quit"
    Write-Host ""

    $choice = Read-Host "Enter your choice (1/2/Q)"

    switch ($choice.ToUpper()) {
        "1" {
            Run-ScreenshotHealth
            Write-Host ""
            Write-Host "Press Enter to return to the menu..."
            [void][System.Console]::ReadLine()
        }
        "2" {
            Run-AnyDeskCheck
            Write-Host ""
            Write-Host "Press Enter to return to the menu..."
            [void][System.Console]::ReadLine()
        }
        "Q" {
            Write-Host "Exiting NC Monitoring menu." -ForegroundColor Yellow
            break
        }
        default {
            Write-Host "Invalid choice. Please enter 1, 2, or Q." -ForegroundColor Red
            Start-Sleep -Seconds 1.5
        }
    }
}
