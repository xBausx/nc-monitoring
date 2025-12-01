import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from clients.api_client import APIClient
from clients.anydesk_client import AnyDeskClient
from clients.sheets_client import SheetsClient

logger = logging.getLogger(__name__)


def _derive_anydesk_password_from_license_id(license_id: str) -> Optional[str]:
    """
    Derive the AnyDesk password from the licenseId.

    Backend rule:
        password = "<4th-guid-block>-<5th-guid-block>"

    Example:
        licenseId = "c71ea4b0-eb1a-4a9b-9cb9-89f0458fdd5d"
        password  = "9cb9-89f0458fdd5d"
    """
    if not license_id:
        return None

    parts = license_id.split("-")
    if len(parts) < 5:
        logger.warning(
            "Unexpected licenseId format for password derivation: %s",
            license_id,
        )
        return None

    return f"{parts[3]}-{parts[4]}"


def _fetch_licenses_for_anydesk() -> List[Dict[str, Any]]:
    """
    Fetch licenses that are candidates for AnyDesk checking.

    We call the backend's /api/license/getallwithduration endpoint directly,
    using the same parameters you captured from the browser:

        page=0
        pageSize=0  (means "all")
        piStatus=0
        daysOfflineFrom=6
        daysOfflineTo=30
        etc.

    This returns licenses that have been offline 6–30 days, including:
        - licenseId
        - licenseKey
        - anydeskId
        - offlineDays
        - timezoneName
        ...
    """
    base_url = os.getenv("NC_API_BASE_URL", "https://nctvapi.n-compass.online").rstrip("/")
    username = os.getenv("NC_API_USERNAME")
    password = os.getenv("NC_API_PASSWORD")

    if not username or not password:
        logger.error("NC_API_USERNAME or NC_API_PASSWORD not set; cannot fetch licenses for AnyDesk check.")
        return []

    session = requests.Session()

    # 1) Login to get a fresh JWT
    login_url = f"{base_url}/api/account/login"
    try:
        resp = session.post(
            login_url,
            json={"username": username, "password": password},
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error("Error logging into NC API for AnyDesk check: %s", e)
        return []

    if resp.status_code != 200:
        logger.error("Login failed for AnyDesk check: status=%s body=%s", resp.status_code, resp.text[:300])
        return []

    token = resp.json().get("token")
    if not token:
        logger.error("Login response for AnyDesk check had no token: %s", resp.text[:300])
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    # 2) Call /api/license/getallwithduration with the same params as your curl
    url = f"{base_url}/api/license/getallwithduration"

    params = {
        "page": 0,
        "search": "",
        "sortColumn": "TimeIn",
        "sortOrder": "desc",
        "pageSize": 0,          # backend uses 0 here to mean "all"
        "includeAdmin": "false",
        "piStatus": 0,
        "daysOfflineFrom": 6,
        "daysOfflineTo": 30,
        "active": "",
        "daysInstalled": "",
        "timezone": "",
        "dealerId": "",
        "hostId": "",
        "assigned": "",
        "pending": "",
        "online": "",
        "isActivated": "",
    }

    try:
        resp = session.get(url, headers=headers, params=params, timeout=60)
    except requests.RequestException as e:
        logger.error("Error calling getallwithduration for AnyDesk check: %s", e)
        return []

    if resp.status_code != 200:
        logger.error(
            "getallwithduration failed for AnyDesk check: status=%s body=%s",
            resp.status_code,
            resp.text[:300],
        )
        return []

    data = resp.json() if resp.content else {}
    licenses = data.get("licenses") or []
    logger.info("Fetched %s offline licenses for AnyDesk check.", len(licenses))
    return licenses


def _init_anydesk_sheet() -> Optional[SheetsClient]:
    """
    Initialize a SheetsClient for the AnyDesk monitoring sheet.

    Prefers ANYDESK_SHEETS_SPREADSHEET_ID; falls back to SHEETS_SPREADSHEET_ID.
    If neither is set, returns None and we only log to console.
    """
    target_id = os.getenv("ANYDESK_SHEETS_SPREADSHEET_ID") or os.getenv(
        "SHEETS_SPREADSHEET_ID",
        "",
    )

    if not target_id:
        logger.warning(
            "No ANYDESK_SHEETS_SPREADSHEET_ID or SHEETS_SPREADSHEET_ID set. "
            "AnyDesk results will NOT be written to Google Sheets."
        )
        return None

    logger.info("Using spreadsheet ID %s for AnyDesk status.", target_id)
    return SheetsClient(spreadsheet_id=target_id)


def _ensure_anydesk_worksheet(sheets: SheetsClient):
    """
    Get or create the 'AnyDesk Status' worksheet with proper headers.
    """
    ws = sheets.get_or_create_worksheet("AnyDesk Status", rows=2000, cols=6)
    headers = [
        "License Key",
        "License ID",
        "AnyDesk ID",
        "Host / Business Name",
        "Dealer",
        "Timezone",
        "PS Version",
        "UI Version",
        "Memory",
        "Storage",
        "AnyDesk Status",
        "Last Checked (UTC)",
        "Notes",
    ]

    sheets.ensure_headers(ws, headers)
    return ws


def _update_anydesk_row(
    sheets: Optional[SheetsClient],
    ws,
    *,
    license_key: str,
    license_id: str,
    anydesk_id: str,
    status: str,
    host_name: str,
    dealer_name: str,
    timezone_name: str,
    ps_version: str,
    ui_version: str,
    memory: str,
    storage: str,
    notes: str = "",
) -> None:
    """
    Upsert a row in the AnyDesk Status sheet for a given license.

    Key = License Key (column 1).

    Columns:
      1  License Key
      2  License ID
      3  AnyDesk ID
      4  Host / Business Name
      5  Dealer
      6  Timezone
      7  PS Version
      8  UI Version
      9  Memory
      10 Storage
      11 AnyDesk Status
      12 Last Checked (UTC)
      13 Notes
    """
    if sheets is None or ws is None:
        return

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    values = [
        license_key,
        license_id,
        anydesk_id,
        host_name,
        dealer_name,
        timezone_name,
        ps_version,
        ui_version,
        memory,
        storage,
        status,
        timestamp,
        notes,
    ]

    # Use upsert so each license has a single row that gets updated each run
    sheets.upsert_row(
        ws,
        key_value=license_key,
        values=values,
        key_col=1,  # column 1 = License Key
    )


def _extract_anydesk_info(license_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Extract AnyDesk ID and password for a license.

    - AnyDesk ID comes from the API field: anydeskId
    - Password is derived from licenseId using the backend rule:
          password = "<4th-guid-block>-<5th-guid-block>"
    """
    license_id = str(license_data.get("licenseId", "") or "").strip()
    anydesk_id = str(license_data.get("anydeskId", "") or "").strip()

    if not anydesk_id:
        logger.info(
            "License %s has no AnyDesk ID; skipping.",
            license_data.get("licenseKey"),
        )
        return None

    if not license_id:
        logger.warning(
            "Missing licenseId for license %s (AnyDesk ID=%s); skipping.",
            license_data.get("licenseKey"),
            anydesk_id,
        )
        return None

    password = _derive_anydesk_password_from_license_id(license_id)
    if not password:
        logger.warning(
            "Unable to derive AnyDesk password for license %s (AnyDesk ID=%s); skipping.",
            license_data.get("licenseKey"),
            anydesk_id,
        )
        return None

    return {"id": anydesk_id, "password": password}


def run_anydesk_check() -> None:
    """
    Main entry point for AnyDesk connectivity check.

    Flow:
      1. Use APIClient to fetch licenses.
      2. For each license, extract AnyDesk ID + password (derived from licenseId).
      3. Use AnyDeskClient to open a session and classify the result.
      4. Log a summary of statuses and write/update rows in the
         'AnyDesk Status' worksheet when a Sheets ID is configured.

    This function is intended to run ONLY on a Windows agent with:
      - AnyDesk installed & on PATH
      - An active desktop session
      - Tesseract configured for pytesseract
    """
    logger.info("Starting AnyDesk connectivity check...")

    anydesk_client = AnyDeskClient()

    # Initialize Sheets (optional – you already have this wired)
    sheets: Optional[SheetsClient] = _init_anydesk_sheet()
    ws = _ensure_anydesk_worksheet(sheets) if sheets is not None else None

    licenses = _fetch_licenses_for_anydesk()
    if not licenses:
        logger.info("No licenses to process for AnyDesk check.")
        return

    status_counts: Dict[str, int] = {
        "Online": 0,
        "Offline": 0,
        "Wrong Password": 0,
        "Errored": 0,
        "Skipped": 0,
    }

    for lic in licenses:
        license_key = str(lic.get("licenseKey", ""))
        license_id = str(lic.get("licenseId", ""))

        # Extra metadata for the sheet
        host_name = str(
            lic.get("hostName")
            or lic.get("businessName")
            or ""
        )
        dealer_name = str(
            lic.get("dealerName")
            or lic.get("dealerId")
            or ""
        )
        timezone_name = str(lic.get("timezoneName") or "")
        ps_version = str(lic.get("serverVersion", ""))
        ui_version = str(lic.get("uiVersion", ""))
        memory = str(lic.get("memory", ""))
        # Combine total & free storage into a single field
        total_storage = str(lic.get("totalStorage", ""))
        free_storage = str(lic.get("freeStorage", ""))
        storage = (
            f"{total_storage} (free {free_storage})"
            if total_storage or free_storage
            else ""
        )

        info = _extract_anydesk_info(lic)
        if not info:
            status_counts["Skipped"] += 1
            logger.info(
                "Skipping license %s: missing AnyDesk ID or password.",
                license_key,
            )
            # Still upsert a row with status "Skipped" so it's visible
            _update_anydesk_row(
                sheets,
                ws,
                license_key=license_key,
                license_id=license_id,
                anydesk_id="",
                status="Skipped",
                host_name=host_name,
                dealer_name=dealer_name,
                timezone_name=timezone_name,
                ps_version=ps_version,
                ui_version=ui_version,
                memory=memory,
                storage=storage,
                notes="Missing AnyDesk ID or password",
            )
            continue

        anydesk_id = info["id"]
        password = info["password"]

        logger.info(
            "Checking AnyDesk connectivity for license %s (ID=%s)...",
            license_key,
            anydesk_id,
        )

        status = anydesk_client.check_session(anydesk_id, password)
        status_counts.setdefault(status, 0)
        status_counts[status] += 1

        logger.info(
            "License %s AnyDesk status: %s",
            license_key,
            status,
        )

        _update_anydesk_row(
            sheets,
            ws,
            license_key=license_key,
            license_id=license_id,
            anydesk_id=anydesk_id,
            status=status,
            host_name=host_name,
            dealer_name=dealer_name,
            timezone_name=timezone_name,
            ps_version=ps_version,
            ui_version=ui_version,
            memory=memory,
            storage=storage,
            notes="",
        )

    logger.info("AnyDesk connectivity check complete. Summary:")
    for status, count in status_counts.items():
        logger.info("  %s: %s", status, count)
