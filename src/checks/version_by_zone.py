import logging
import os
from typing import Any, Dict, List, Set

from clients.api_client import APIClient
from clients.sheets_client import SheetsClient
from clients.socket_client import SocketClient

logger = logging.getLogger(__name__)

# Zones to check – same idea as your old script
ZONES: List[str] = ["Eastern", "Central", "Mountain", "Pacific"]

# Expected versions (can be overridden via env vars)
EXPECTED_SERVER_VERSION = os.getenv("EXPECTED_SERVER_VERSION", "2.9.4")
EXPECTED_UI_VERSION = os.getenv("EXPECTED_UI_VERSION", "3.0.47")

# Portal URL pattern for direct links
PORTAL_LICENSE_URL = os.getenv(
    "PORTAL_LICENSE_URL_PATTERN",
    "https://portal.n-compass.online/administrator/licenses/{license_id}/{license_key}",
)


def _fetch_zone_licenses(api: APIClient, zone: str) -> List[Dict[str, Any]]:
    """
    Fetch all licenses for a given timezone zone label using /api/license/getall
    with simple pagination.

    NOTE:
        The exact filter params (timezoneName vs timezone, etc.) should be
        adjusted to match the API contract. This version uses 'timezoneName'
        as a query parameter and assumes the response has a 'licenses' list.
    """
    licenses: List[Dict[str, Any]] = []
    page = 1
    page_size = 100

    logger.info("Fetching licenses for zone '%s'...", zone)

    while True:
        params = {
            "page": page,
            "pageSize": page_size,
            "search": "",
            "sortColumn": "PiStatus",
            "sortOrder": "desc",
            "includeAdmin": "false",
            # Filters – adjust if your API expects different names
            "piStatus": 1,          # online
            "active": "true",
            "assigned": "true",
            "timezoneName": zone,
        }

        data = api.get_licenses(params=params)
        if not data:
            logger.warning("No data returned for zone '%s' page %s", zone, page)
            break

        page_licenses = data.get("licenses", [])
        if not page_licenses:
            logger.info("No more licenses for zone '%s' (page %s).", zone, page)
            break

        logger.info(
            "Fetched %s licenses for zone '%s' on page %s.",
            len(page_licenses),
            zone,
            page,
        )
        licenses.extend(page_licenses)
        page += 1

    return licenses


def _build_portal_url(license_id: str, license_key: str) -> str:
    """Build a clickable portal URL for a license."""
    return PORTAL_LICENSE_URL.format(
        license_id=license_id,
        license_key=license_key,
    )


def _check_license_versions(
    license_data: Dict[str, Any],
    socket_client: SocketClient,
) -> Dict[str, Any]:
    """
    Evaluate a single license's versions.

    Returns a dict with:
        {
            "license_id": str,
            "versions": str,
            "url": str,
            "status": str,
            "is_mismatch": bool,
        }
    """
    license_id = str(license_data.get("licenseId", ""))
    license_key = str(license_data.get("licenseKey", ""))

    server_version = str(license_data.get("serverVersion", "") or "")
    ui_version = str(license_data.get("uiVersion", "") or "")

    versions_str = f"Server: {server_version}, UI: {ui_version}"
    url = _build_portal_url(license_id, license_key)

    logger.debug(
        "Checking versions for license %s (server=%s, ui=%s)",
        license_id,
        server_version,
        ui_version,
    )

    # If both match expected, nothing to escalate
    if (
        server_version == EXPECTED_SERVER_VERSION
        and ui_version == EXPECTED_UI_VERSION
    ):
        return {
            "license_id": license_id,
            "versions": versions_str,
            "url": url,
            "status": "OK",
            "is_mismatch": False,
        }

    # Mismatch – attempt a player restart via socket
    restart_sent = socket_client.restart_player(license_id)
    if restart_sent:
        status = "Version mismatch - player restart signal sent"
    else:
        status = "Version mismatch - FAILED to send restart signal"

    logger.warning(
        "Version mismatch for license %s. %s (expected server=%s ui=%s)",
        license_id,
        status,
        EXPECTED_SERVER_VERSION,
        EXPECTED_UI_VERSION,
    )

    return {
        "license_id": license_id,
        "versions": versions_str,
        "url": url,
        "status": status,
        "is_mismatch": True,
    }


def _sync_zone_sheet(
    sheets: SheetsClient,
    zone: str,
    mismatch_results: List[Dict[str, Any]],
) -> None:
    """
    Update the zone worksheet to reflect current mismatches only.

    - Ensures headers: ['License IDs', 'Versions', 'URL', 'Status']
    - Upserts rows for mismatched licenses.
    - Removes rows for licenses that no longer mismatch.
    """
    ws = sheets.get_or_create_worksheet(zone, rows=1000, cols=4)
    headers = ["License IDs", "Versions", "URL", "Status"]
    sheets.ensure_headers(ws, headers)

    # Build set of mismatched license IDs for this run
    mismatched_ids: Set[str] = {
        str(item["license_id"]) for item in mismatch_results if item["is_mismatch"]
    }

    # Upsert mismatched rows
    for item in mismatch_results:
        if not item["is_mismatch"]:
            continue

        values = [
            item["license_id"],
            item["versions"],
            item["url"],
            item["status"],
        ]
        sheets.upsert_row(ws, key_value=item["license_id"], values=values, key_col=1)

    # Remove stale rows (licenses that no longer mismatch)
    existing_values = ws.get_all_values()
    # existing_values[0] is header row
    rows_to_delete: List[int] = []
    for idx, row in enumerate(existing_values[1:], start=2):
        if not row:
            continue
        existing_license_id = row[0].strip()
        if existing_license_id and existing_license_id not in mismatched_ids:
            rows_to_delete.append(idx)

    # Delete from bottom to top to avoid index shifting
    for row_index in sorted(rows_to_delete, reverse=True):
        logger.info(
            "Removing resolved license from sheet '%s': row %s",
            zone,
            row_index,
        )
        ws.delete_row(row_index)


def run_version_zone_check() -> None:
    """
    Main entry point for the 'version by zone' check.

    For each zone:
      1. Fetch licenses via API.
      2. Compare server/ui versions to expected.
      3. Send restart signal for mismatches via socket.
      4. Sync a zone-named worksheet with current mismatches.
    """
    logger.info("Starting version-by-zone check...")

    api = APIClient()
    sheets = SheetsClient()
    socket_client = SocketClient()

    for zone in ZONES:
        try:
            logger.info("=== Zone: %s ===", zone)
            licenses = _fetch_zone_licenses(api, zone)

            if not licenses:
                logger.info("No licenses found for zone '%s'. Skipping.", zone)
                # Also clear sheet if there are no licenses at all
                _sync_zone_sheet(sheets, zone, mismatch_results=[])
                continue

            results: List[Dict[str, Any]] = []
            for lic in licenses:
                result = _check_license_versions(lic, socket_client)
                results.append(result)

            _sync_zone_sheet(sheets, zone, mismatch_results=results)

        except Exception as exc:
            logger.error("Error processing zone '%s': %s", zone, exc)

    logger.info("Version-by-zone check complete.")
