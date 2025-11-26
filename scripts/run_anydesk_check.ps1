# scripts/run_anydesk_check.ps1
# Manual AnyDesk Status checker for support.

# --- Repo & Python paths (adjust if needed) ---
$repoRoot  = "C:\Users\Rico\Documents\nctv-repositories\nc-monitoring"
$srcDir    = Join-Path $repoRoot "src"
$scriptsDir = Join-Path $repoRoot "scripts"
$pythonExe = "C:\Users\Rico\anaconda3\envs\monitoring\python.exe"

# --- Load env vars for this session ---
. (Join-Path $scriptsDir "set_monitoring_env.ps1")

# Limit how many licenses per manual run (so it doesn't take forever)
$env:ANYDESK_MAX_LICENSES_PER_RUN = "10"

# --- Run the check ---
Set-Location $srcDir

Write-Host "Running AnyDesk connectivity check..." -ForegroundColor Cyan

& $pythonExe -c "import logging; logging.basicConfig(level=logging.INFO); from checks.anydesk_check import run_anydesk_check; run_anydesk_check()"

Write-Host ""
Write-Host "AnyDesk check finished. You can review the 'AnyDesk Status' sheet now." -ForegroundColor Green
Write-Host "Press Enter to close this window."
[void][System.Console]::ReadLine()
