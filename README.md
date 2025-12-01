# nc-monitoring# NC Monitoring

NC Monitoring is a small monitoring agent that:

- Pulls license & screenshot data from the NCTV API
- Checks screenshot health (black screens, error screens, outdated content)
- Checks AnyDesk reachability for offline players
- Writes results into a shared Google Sheet for tech support

## Prerequisites

- Windows 10/11
- Anaconda or Python 3.12
- AnyDesk installed and logged in
- Tesseract OCR installed (or unpacked) and accessible via `TESSERACT_CMD`
- Google Cloud service account JSON with access to the NC Monitoring Google Sheet

## Environment variables

These must be set before running any checks (usually via `scripts/set_monitoring_env.ps1`):

- `NC_API_BASE_URL` – e.g. `https://nctvapi.n-compass.online`
- `NC_API_USERNAME` – `ncmonitoring@automation.com`
- `NC_API_PASSWORD` – password for that account
- `SHEETS_CREDENTIALS_FILE` – full path to service account JSON
- `SHEETS_SPREADSHEET_ID` – ID of the NC Monitoring spreadsheet
- `ANYDESK_SHEETS_SPREADSHEET_ID` – usually the same as above
- `TESSERACT_CMD` – full path to `tesseract.exe`
- (optional) `ANYDESK_MAX_LICENSES_PER_RUN` – limit for local testing

## Quick start (dev machine)

```powershell
cd <repo>\src
conda create -n monitoring python=3.12
conda activate monitoring
pip install -r ..\requirements.txt

# set envs for this terminal
..\scripts\set_monitoring_env.ps1

# test API
python -c "from clients.api_client import APIClient; api=APIClient(); print(len(api.get_licenses({'page':1,'pageSize':5})['licenses']))"

# run screenshot health once
python -c "from checks.screenshot_health import run_screenshot_health; run_screenshot_health()"

# run AnyDesk check once
python -c "from checks.anydesk_check import run_anydesk_check; run_anydesk_check()"

You can tweak text, but something like this is enough to avoid future “what was that env var again?” pain.

---

### Step 2 – Standardize the env helper scripts

You already have:

- `scripts/set_monitoring_env.ps1`
- `scripts/run_anydesk_check.ps1`

**Do now:**

1. Open `set_monitoring_env.ps1` and make sure it sets **all** the env vars we listed above (including `NC_API_PASSWORD`, `SHEETS_SPREADSHEET_ID`, `ANYDESK_SHEETS_SPREADSHEET_ID`, `TESSERACT_CMD`).

2. In `run_anydesk_check.ps1`, keep the pattern:

```powershell
Write-Host "Setting NC Monitoring environment variables..."
..\scripts\set_monitoring_env.ps1

Write-Host "Running AnyDesk connectivity check..."
python -c "import logging; logging.basicConfig(level=logging.INFO); from checks.anydesk_check import run_anydesk_check; run_anydesk_check()"

Write-Host "AnyDesk check finished. You can review the 'AnyDesk Status' sheet now."
Pause
