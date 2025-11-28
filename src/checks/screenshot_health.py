import logging
import os
import re
import json

from datetime import datetime, time, timedelta
from io import BytesIO
from typing import Any, Dict, List, Optional
from pathlib import Path

import gspread
import pytesseract
import pytz
import logging
import requests
from google.oauth2.service_account import Credentials
from PIL import Image

from clients.api_client import APIClient
from clients.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

# Error phrases based on your old error_checker.py
ERROR_MESSAGES = {
    "something went wrong, please contact your administrator",
    "getting player data",
    "downloading player assets",
    "setting up programmatic",
    "getting host schedule",
    "refetch started",
    "player is healthy",
    "updates are available",
    "downloading updates",
}

# Timezone used for naming the daily sheet and the "Last Checked" column.
# Default is Texas time (US/Central). You can override this with NC_MONITORING_TZ.
MONITORING_TZ_NAME = os.getenv("NC_MONITORING_TZ", "US/Central")

# Configure pytesseract to use TESSERACT_CMD if provided, or fall back to
# the repo-local Tesseract-OCR\tesseract.exe path.
TESSERACT_CMD = os.getenv("TESSERACT_CMD")

if not TESSERACT_CMD:
    # Try to auto-detect the bundled Tesseract in the repo:
    # <repo_root>/Tesseract-OCR/tesseract.exe
    try:
        repo_root = Path(__file__).resolve().parents[2]
        candidate = repo_root / "Tesseract-OCR" / "tesseract.exe"
        if candidate.exists():
            TESSERACT_CMD = str(candidate)
    except Exception:
        # Best effort only; we'll fall back to default search if this fails.
        pass

if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def get_formatted_date_us_central() -> str:
    """
    Get the current date formatted as 'YYYY-MM-DD' in the monitoring timezone.

    By default this is US/Central (Texas time) so that daily tabs line up with
    Texas dates even if the script runs on a machine in another timezone.
    """
    try:
        texas_timezone = pytz.timezone(MONITORING_TZ_NAME)
        now_in_texas = datetime.now(texas_timezone)
        formatted_date = now_in_texas.strftime("%Y-%m-%d")
        logger.info("Formatted date (%s): %s", MONITORING_TZ_NAME, formatted_date)
        return formatted_date
    except Exception as error:
        logger.error("Error formatting date: %s", error, exc_info=True)
        # Fallback to naive today if something goes wrong
        return datetime.utcnow().strftime("%Y-%m-%d")

def get_last_checked_timestamp() -> str:
    """
    Return a 'YYYY-MM-DD HH:MM:SS' string in the monitoring timezone.

    This is used for the 'Last Checked' column so it reflects Texas time
    (or whatever MONITORING_TZ_NAME is set to), not your local machine time.
    """
    try:
        tz = pytz.timezone(MONITORING_TZ_NAME)
        now_in_tz = datetime.now(tz)
        return now_in_tz.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as error:
        logger.error(
            "Error formatting last-checked timestamp: %s",
            error,
            exc_info=True,
        )
        # As a fallback, still return something sane instead of crashing
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def is_store_open(store_hours_json: str, timezone_name: str) -> bool:
    """
    Decide if a store is considered OPEN *right now* based on `storeHours`.

    Supports both formats:
      1) New format with openingHourData/closingHourData:
         {
            "periods": [
              {
                "openingHourData": {"hour": 7, "minute": 30, "second": 0},
                "closingHourData": {"hour": 17, "minute": 0, "second": 0}
              }
            ]
         }

      2) Old format with string times:
         {
            "periods": [
              {"open": "10:00 AM", "close": "6:00 PM"}
            ]
         }

    On parse error, we treat the store as CLOSED (return False) to avoid
    false positives.
    """
    if not store_hours_json:
        # No store hours – safest is to treat as closed.
        return False

    # Parse the JSON
    try:
        store_hours = json.loads(store_hours_json)
    except (TypeError, json.JSONDecodeError) as exc:
        logger.warning("Invalid storeHours JSON: %s", exc)
        return False

    # Normalise timezone
    try:
        tz = pytz.timezone(timezone_name or "US/Central")
    except Exception:
        logger.warning("Unknown timezone '%s', defaulting to US/Central", timezone_name)
        tz = pytz.timezone("US/Central")

    now = datetime.now(tz)
    current_time = now.time()
    current_day_name = now.strftime("%A")  # e.g. "Monday"

    def _parse_period(period: dict) -> Optional[tuple]:
        """Return (opening_time, closing_time) as datetime.time, or None if unusable."""
        # New nested format
        if "openingHourData" in period and "closingHourData" in period:
            o = period["openingHourData"]
            c = period["closingHourData"]
            opening_time = time(
                hour=o.get("hour", 0),
                minute=o.get("minute", 0),
                second=o.get("second", 0),
            )
            closing_time = time(
                hour=c.get("hour", 0),
                minute=c.get("minute", 0),
                second=c.get("second", 0),
            )
            return opening_time, closing_time

        # Old string format: "open": "10:00 AM", "close": "6:00 PM"
        open_str = period.get("open")
        close_str = period.get("close")
        if not open_str or not close_str:
            return None

        for fmt in ("%I:%M %p", "%H:%M"):
            try:
                o_dt = datetime.strptime(open_str, fmt)
                c_dt = datetime.strptime(close_str, fmt)
                return o_dt.time(), c_dt.time()
            except ValueError:
                continue

        logger.warning("Could not parse store hours period: %s", period)
        return None

    # store_hours is usually a list of day objects
    for day in store_hours:
        # Skip disabled days
        if not day.get("status", True):
            continue

        # Match by day name when present
        day_name = day.get("day")
        if day_name and day_name != current_day_name:
            continue

        periods = day.get("periods") or []
        for period in periods:
            parsed = _parse_period(period)
            if not parsed:
                continue

            opening_time, closing_time = parsed

            if opening_time <= closing_time:
                # Same-day closing
                if opening_time <= current_time <= closing_time:
                    return True
            else:
                # Overnight window (e.g. 20:00–02:00)
                if current_time >= opening_time or current_time <= closing_time:
                    return True

    # No matching open period found
    return False

def is_black_screen(image: Image.Image) -> bool:
    """
    Determine if an image is predominantly black (> 90% black pixels),
    adapted from your old is_black_screen.
    """
    try:
        grayscale_image = image.convert("L")
        histogram = grayscale_image.histogram()

        if not histogram:
            logger.warning("Empty histogram. Unable to analyze image.")
            return False

        total_pixels = sum(histogram)
        black_pixels = histogram[0]
        black_ratio = black_pixels / total_pixels if total_pixels else 0.0

        is_black = black_ratio > 0.9
        if is_black:
            logger.warning("Detected a black screen (black ratio %.2f).", black_ratio)
        return is_black
    except Exception as e:
        logger.error("Error analyzing black screen: %s", e)
        return False


def filter_screenshots_for_today(
    file_urls: List[str],
    timezone_name: str,
    license_key: str,
) -> List[str]:
    """
    Filter screenshot URLs whose filenames start with today's date (YYYYMMDD)
    in the store's timezone, based on your old process_screenshot_names.
    """
    matching_screenshots: List[str] = []

    try:
        store_timezone = pytz.timezone(timezone_name)
    except pytz.UnknownTimeZoneError:
        logger.error("Invalid timezone for license %s: %s", license_key, timezone_name)
        return []

    current_time = datetime.now(store_timezone)
    current_date = current_time.strftime("%Y%m%d")

    for url in file_urls[:10]:  # limit to first 10
        if not url:
            continue

        filename = url.split("/")[-1]
        filename_wo_ext = filename.replace(".jpg", "")
        screenshot_date = filename_wo_ext[:8]

        if screenshot_date == current_date:
            matching_screenshots.append(url)

    if matching_screenshots:
        logger.info(
            "Matching date screenshots [%s] found for license %s.",
            len(matching_screenshots),
            license_key,
        )
    else:
        logger.error(
            "Error: screenshots for current date not found for license %s.",
            license_key,
        )

    return matching_screenshots


def ocr_image_from_url(url: str) -> Optional[str]:
    """Download an image from URL and run OCR, returning lowercased text."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        text = pytesseract.image_to_string(img)
        return text.strip().lower()
    except Exception as e:
        logger.error("Error OCR-ing image from %s: %s", url, e)
        return None


def load_image_from_url(url: str) -> Optional[Image.Image]:
    """Download an image from URL and return a PIL.Image.Image."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content))
    except Exception as e:
        logger.error("Error loading image from %s: %s", url, e)
        return None


def _reorder_monitoring_tabs_for_today(today_tab_name: str) -> None:
    """
    Reorder worksheets in the monitoring spreadsheet to match this layout:

        [ TODAY ] [ Pacific ] [ Eastern ] [ Central ] [ Mountain ]
        [ AnyDesk Status ] [ older daily tabs... ] [others]

    - "Daily" tabs are ones named like YYYY-MM-DD.
    - We don't delete anything; we just reorder.
    """
    spreadsheet_id = os.getenv("SHEETS_SPREADSHEET_ID")
    credentials_file = os.getenv("SHEETS_CREDENTIALS_FILE")

    if not spreadsheet_id or not credentials_file:
        logger.warning(
            "Cannot reorder tabs: SHEETS_SPREADSHEET_ID or SHEETS_CREDENTIALS_FILE not set."
        )
        return

    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(credentials_file, scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(spreadsheet_id)
    except Exception as e:
        logger.error("Failed to initialize gspread for tab reordering: %s", e)
        return

    try:
        worksheets = sh.worksheets()
    except Exception as e:
        logger.error("Failed to fetch worksheets for tab reordering: %s", e)
        return

    name_to_ws = {ws.title: ws for ws in worksheets}
    ordered: List[Any] = []

    def add_if_present(name: str) -> None:
        ws = name_to_ws.get(name)
        if ws and ws not in ordered:
            ordered.append(ws)

    # 1) Today first
    add_if_present(today_tab_name)

    # 2) AnyDesk Status tab
    add_if_present("AnyDesk Status")
    
    # 3) Fixed zone tabs in your preferred order
    for zone_name in ["Pacific", "Eastern", "Central", "Mountain"]:
        add_if_present(zone_name)

    # 4) All other date-like tabs (YYYY-MM-DD), newest -> oldest, excluding today's
    daily_tabs = [
        ws
        for ws in worksheets
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ws.title)
        and ws.title != today_tab_name
    ]
    daily_tabs_sorted = sorted(daily_tabs, key=lambda ws: ws.title, reverse=True)
    for ws in daily_tabs_sorted:
        if ws not in ordered:
            ordered.append(ws)

    # 5) Any remaining worksheets (e.g., Test, Offline 6-30 Days, etc.)
    for ws in worksheets:
        if ws not in ordered:
            ordered.append(ws)

    try:
        sh.reorder_worksheets(ordered)
        logger.info(
            "Reordered monitoring worksheets: %s",
            [ws.title for ws in ordered],
        )
    except Exception as e:
        logger.error("Failed to reorder worksheets: %s", e)


# --------------------------------------------------------------------------- #
# Main check
# --------------------------------------------------------------------------- #

def _process_license(
    api: APIClient,
    ws,
    row_counter: int,
    license_data: Dict[str, Any],
) -> int:
    """
    Process a single license:
      - Check if store is open.
      - Fetch screenshots.
      - Filter for today's screenshots.
      - Detect black screens and error text.
      - Record a single row into today's sheet.

    Returns the updated row_counter.
    """
    license_id = str(license_data.get("licenseId", ""))
    license_key = str(license_data.get("licenseKey", ""))

    host_name = str(
        license_data.get("hostName")
        or license_data.get("businessName")
        or ""
    )
    dealer_name = str(
        license_data.get("dealerName")
        or license_data.get("dealerId")
        or ""
    )
    timezone_name = license_data.get("timezoneName", "UTC") or "UTC"
    store_hours_json = license_data.get("storeHours", "[]")

    # If store is closed, skip logging entirely for this run
    if not is_store_open(store_hours_json, timezone_name):
        return row_counter

    last_checked = get_last_checked_timestamp()

    screenshots_data = api.get_screenshots(license_id)
    if not screenshots_data:
        row_counter += 1
        logger.warning("No screenshots data for license %s.", license_key)
        ws.append_row(
            [
                license_key,
                license_id,
                host_name,
                dealer_name,
                timezone_name,
                "",
                "",
                "NO_SCREENSHOTS",
                "",
                last_checked,
            ],
            value_input_option="USER_ENTERED",
        )

        return row_counter

    file_urls = screenshots_data.get("files", [])
    if not file_urls:
        row_counter += 1
        logger.warning("Empty 'files' list in screenshots for license %s.", license_key)
        ws.append_row(
            [
                license_key,
                license_id,
                host_name,
                dealer_name,
                timezone_name,
                "",
                "",
                "NO_SCREENSHOTS",
                "",
                last_checked,
            ],
            value_input_option="USER_ENTERED",
        )

        return row_counter

    # Filter to today's screenshots
    todays_urls = filter_screenshots_for_today(file_urls, timezone_name, license_key)
    if not todays_urls:
        row_counter += 1
        ws.append_row(
            [
                license_key,
                license_id,
                host_name,
                dealer_name,
                timezone_name,
                "",
                "",
                "NO_SCREENSHOTS",
                "",
                last_checked,
            ],
            value_input_option="USER_ENTERED",
        )

        return row_counter

    # Choose "latest" screenshot by filename (YYYYMMDDHHMMSS.jpg)
    try:
        latest_url = max(
            todays_urls,
            key=lambda u: u.split("/")[-1],
        )
    except ValueError:
        latest_url = todays_urls[0]

    latest_ts = _extract_timestamp_from_url(latest_url, timezone_name)

    black_screens = 0
    detected_errors: List[str] = []
    error_screenshot_url: Optional[str] = None  # screenshot where we first saw an error

    for url in todays_urls[:4]:  # limit to first few
        img = load_image_from_url(url)
        if not img:
            continue

        if is_black_screen(img):
            black_screens += 1
            continue

        try:
            text = pytesseract.image_to_string(img).strip().lower()
        except pytesseract.TesseractNotFoundError as e:
            logger.error(
                "Tesseract not found while OCR-ing screenshot for license %s: %s",
                license_key,
                e,
            )
            continue
        except Exception as e:
            logger.error(
                "Unexpected OCR error for license %s: %s",
                license_key,
                e,
            )
            continue

        for err in ERROR_MESSAGES:
            if err in text:
                detected_errors.append(err)
                # Remember the first screenshot where we saw an error string
                if error_screenshot_url is None:
                    error_screenshot_url = url

    error_count = len(detected_errors)
    unique_errors = sorted(set(detected_errors))
    error_text = ", ".join(unique_errors)

        # Choose which screenshot URL to display in the sheet.
    # Default: latest screenshot of the day. If we detect any error text,
    # prefer the screenshot where the first error was seen so the image
    # matches the Detected Error Text.
    display_url = latest_url
    display_ts = latest_ts

    if error_count >= 1 and error_screenshot_url:
        display_url = error_screenshot_url
        display_ts = _extract_timestamp_from_url(display_url, timezone_name)
    
    # Decide final screenshot status
    if black_screens >= 3:
        screenshot_status = "OPEN_HOURS_BLACK_SCREEN"
    elif error_count >= 5:
        screenshot_status = "STUCK_ON_ERROR"
    elif error_count >= 1:
        screenshot_status = "ERROR_DISPLAYED"
    else:
        screenshot_status = "OK"

    row_counter += 1
    ws.append_row(
        [
            license_key,
            license_id,
            host_name,
            dealer_name,
            timezone_name,
            display_url,
            display_ts,
            screenshot_status,
            error_text,
            last_checked,
        ],
        value_input_option="USER_ENTERED",
    )

    if screenshot_status != "OK":
        logger.warning(
            "Screenshot issue for license %s: status=%s, black_screens=%s, errors=%s",
            license_key,
            screenshot_status,
            black_screens,
            error_count,
        )

    return row_counter


def run_screenshot_health() -> None:
    """
    Main entry point for screenshot health check:

        - Iterate through pages of licenses.
        - For each license, if store is open, analyze screenshots.
        - Record issues into a date-based sheet.
        - Reorder tabs so today's sheet + fixed tabs are on the left.
    """
    logger.info("Starting screenshot health check...")

    api = APIClient()
    sheets = SheetsClient()

    sheet_name = get_formatted_date_us_central()
    ws = sheets.get_or_create_worksheet(sheet_name, rows=2000, cols=10)

    headers = [
        "License Key",
        "License ID",
        "Host / Business Name",
        "Dealer",
        "Timezone",
        "Latest Screenshot URL",
        "Latest Screenshot Timestamp",
        "Screenshot Status",
        "Detected Error Text",
        f"Last Checked ({MONITORING_TZ_NAME})",
    ]

    sheets.ensure_headers(ws, headers)

    # Start row_counter after existing rows
    existing_values = ws.get_all_values()
    row_counter = max(len(existing_values), 1)

    page = 1
    page_size = 100

    while True:
        params = {
            "page": page,
            "pageSize": page_size,
            "search": "",
            "sortColumn": "PiStatus",
            "sortOrder": "desc",
            "includeAdmin": "false",
            # NOTE: We do NOT filter by piStatus here; screenshot health
            # should consider all active, assigned licenses, regardless of
            # whether the player is currently online.
            "active": "true",
            "assigned": "true",
        }

        logger.info("Screenshot health: requesting licenses with params=%s", params)
        data = api.get_licenses(params=params)

        if not data:
            logger.warning("No data returned for screenshot health on page %s.", page)
            break

        if not isinstance(data, dict):
            logger.warning(
                "Unexpected data type for screenshot health response on page %s: %s",
                page,
                type(data),
            )
            break

        logger.info(
            "Screenshot health: response keys=%s, message=%r",
            list(data.keys()),
            data.get("message"),
        )

        # Try to get licenses from the top level first
        licenses = data.get("licenses")

        # Some NC endpoints wrap payload like {"message": {...}}
        message_payload = data.get("message")
        if licenses is None and isinstance(message_payload, dict):
            licenses = message_payload.get("licenses")

        if not licenses:
            logger.info("No more licenses for screenshot health (page %s).", page)
            break

        logger.info("Processing %s licenses on page %s.", len(licenses), page)

        for lic in licenses:
            try:
                row_counter = _process_license(api, ws, row_counter, lic)
            except Exception as e:
                logger.error(
                    "Error while processing license %s: %s",
                    lic.get("licenseKey"),
                    e,
                    exc_info=True,
                )

        page += 1
    # Reorder tabs so today's date sheet + fixed tabs are on the left
    _reorder_monitoring_tabs_for_today(sheet_name)

    logger.info("Screenshot health check complete.")

def _extract_timestamp_from_url(url: str, timezone_name: str) -> str:
    """
    Extract a human-readable timestamp from a screenshot filename of the form
    YYYYMMDDHHMMSS.jpg (or at least YYYYMMDD).

    Returns an empty string on failure.
    """
    try:
        filename = url.split("/")[-1]
        name_no_ext = filename.split(".")[0]
        # Expect at least YYYYMMDD
        raw = "".join(ch for ch in name_no_ext if ch.isdigit())
        if len(raw) < 8:
            return ""

        if len(raw) >= 14:
            dt = datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
        else:
            dt = datetime.strptime(raw[:8], "%Y%m%d")

        try:
            tz = pytz.timezone(timezone_name or "UTC")
            if dt.tzinfo is None:
                dt = tz.localize(dt)
        except Exception:
            # If timezone fails, just leave naive
            pass

        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
