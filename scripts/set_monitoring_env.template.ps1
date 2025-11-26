# scripts/set_monitoring_env.template.ps1
# COPY this file to set_monitoring_env.ps1 and fill in real values.
# This file is safe to commit (no real secrets).

Write-Host "Setting NC Monitoring environment variables..." -ForegroundColor Cyan

# --- NC API credentials ---
$env:NC_API_BASE_URL  = "https://nctvapi.n-compass.online"
$env:NC_API_USERNAME  = "<ncmonitoring user email>"
$env:NC_API_PASSWORD  = "<password>"

# --- Google Sheets ---
$env:SHEETS_CREDENTIALS_FILE       = "<absolute path to service-account JSON>"
$env:SHEETS_SPREADSHEET_ID         = "<spreadsheet ID for monitoring>"
$env:ANYDESK_SHEETS_SPREADSHEET_ID = "<spreadsheet ID for AnyDesk status (can be same as above)>"

# --- Agent flags ---
# true  = this machine is allowed to run AnyDesk automation
# false = this machine will never touch AnyDesk
$env:ANYDESK_AGENT = "true"

# --- Tesseract path for OCR ---
$env:TESSERACT_CMD = "<absolute path to tesseract.exe>"

Write-Host "NC Monitoring env vars set for this session." -ForegroundColor Green
