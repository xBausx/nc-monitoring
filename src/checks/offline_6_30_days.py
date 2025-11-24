import logging
from typing import Any, Dict, List

from clients.api_client import APIClient
from clients.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

SHEET_TAB_NAME = "Offline 6-30 Days"


def _fetch_offline_licenses(api: APIClient) -> List[Dict[str, Any]]:
    """
    Fetch licenses that are offline between 6 and 30 days,
    based on the curl you provided.

    Mirrors the portal call roughly:
      /api/license/getall?page=1&search=&sortColumn=TimeIn&sortOrder=desc
          &pageSize=15&includeAdmin=false&piStatus=0
          &daysOfflineFrom=6&daysOfflineTo=30
          &active=&daysInstalled=&timezone=&dealerId=&hostId=
          &assigned=&pending=&online=&isActivated=&isFavorite=
          &serverVersion=&uiVersion=&piOrder=
    """
    licenses: List[Dict[str, Any]] = []
    page = 1
    page_size = 100  # backend confirmed we can change this

    while True:
        params = {
            "page": page,
            "pageSize": page_size,
            "search": "",
            "sortColumn": "TimeIn",
            "sortOrder": "desc",
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
            "isFavorite": "",
            "serverVersion": "",
            "uiVersion": "",
            "piOrder": "",
        }

        data = api.get_licenses(params=params)
        if not data:
            logger.warning("No data returned for offline licenses on page %s.", page)
            break

        page_licenses = data.get("licenses", [])
        if not page_licenses:
            logger.info("No more offline licenses (page %s).", page)
            break

        logger.info(
            "Fetched %s offline licenses (6–30 days) on page %s.",
            len(page_licenses),
            page,
        )
        licenses.extend(page_licenses)
        page += 1

    return licenses


def run_offline_6_30_check() -> None:
    """
    Main entry point for the offline 6–30 days report.

    - Uses APIClient + get_licenses with the filters from your curl.
    - Writes results to a dedicated worksheet "Offline 6-30 Days"
      in your monitoring spreadsheet.

    Columns:
      License ID | License Key | Timezone | Days Offline | PiStatus | Dealer | Host
    """
    logger.info("Starting offline 6–30 days check...")

    api = APIClient()
    sheets = SheetsClient()

    offline_licenses = _fetch_offline_licenses(api)
    if not offline_licenses:
        logger.info("No offline 6–30 day licenses found.")
        return

    ws = sheets.get_or_create_worksheet(SHEET_TAB_NAME, rows=2000, cols=7)
    headers = [
        "License ID",
        "License Key",
        "Timezone",
        "Days Offline",
        "PiStatus",
        "Dealer",
        "Host",
    ]
    sheets.ensure_headers(ws, headers)

    # We’ll keep it simple: wipe and re-write the entire sheet for now
    # (except headers), so this tab always represents the latest snapshot.
    # If you prefer upserts keyed by license ID instead, we can change it later.
    existing_values = ws.get_all_values()
    # Delete all rows after the header
    if len(existing_values) > 1:
        ws.delete_rows(2, len(existing_values))

    rows: List[List[Any]] = []
    for lic in offline_licenses:
        license_id = str(lic.get("licenseId", ""))
        license_key = str(lic.get("licenseKey", ""))
        timezone = str(lic.get("timezone", "") or lic.get("timezoneName", ""))
        # daysOffline field name is a guess; adjust if your payload uses a different key
        days_offline = lic.get("daysOffline", "")
        pi_status = lic.get("piStatus", "")
        dealer = lic.get("dealerName", "") or lic.get("dealer", "")
        host = lic.get("hostName", "") or lic.get("host", "")

        rows.append([
            license_id,
            license_key,
            timezone,
            days_offline,
            pi_status,
            dealer,
            host,
        ])

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    logger.info("Offline 6–30 days check complete. Wrote %s rows.", len(rows))
