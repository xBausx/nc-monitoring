# scripts/run_screenshot_health.ps1
# Scheduled / manual Screenshot Health checker for support.

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

# --- Run the check ---
Set-Location $srcDir

Write-Host ""
Write-Host "Running Screenshot Health check (scheduled)..." -ForegroundColor Cyan

python -c "import logging; logging.basicConfig(level=logging.INFO); from checks.screenshot_health import run_screenshot_health; run_screenshot_health()"

Write-Host ""
Write-Host "Screenshot health check finished." -ForegroundColor Green
