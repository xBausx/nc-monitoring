import logging
import os
import re
from datetime import datetime, time, timedelta
from io import BytesIO
from typing import Any, Dict, List, Optional
from pathlib import Path

import gspread
import pytesseract
import pytz
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
    Get the current date formatted as 'YYYY-MM-DD' in US/Central timezone.
    This is used as the sheet name (same behavior as your old script).
    """
    try:
        texas_timezone = pytz.timezone("US/Central")
        now_in_texas = datetime.now(texas_timezone)
        formatted_date = now_in_texas.strftime("%Y-%m-%d")
        logger.info("Formatted date (US/Central): %s", formatted_date)
        return formatted_date
    except Exception as error:
        logger.error("Error formatting date: %s", error, exc_info=True)
        # Fallback to naive today if something goes wrong
        return datetime.utcnow().strftime("%Y-%m-%d")


def is_store_open(store_hours_json: str, timezone_name: str) -> bool:
    """
    Determine if the store is open right now based on storeHours JSON and timezone.

    This is adapted from your old is_store_open implementation, but simplified
    so it doesn't depend on a global license_key for logging.
    """
    if not store_hours_json:
        return False

    try:
        store_hours = __import__("json").loads(store_hours_json)
    except __import__("json").JSONDecodeError:
        logger.error("Invalid storeHours JSON format.")
        return False

    try:
        store_timezone = pytz.timezone(timezone_name)
    except pytz.UnknownTimeZoneError:
        logger.error("Invalid timezone: %s", timezone_name)
        return False

    current_time = datetime.now(store_timezone)
    current_day_label = current_time.strftime("%A")  # e.g., "Monday"
    current_time_only = current_time.time().replace(microsecond=0)

    try:
        for day in store_hours:
            if day.get("day") != current_day_label:
                continue

            # If status is false or missing, store is closed
            if not day.get("status", False):
                return False

            for period in day.get("periods", []):
                opening_time = time(
                    hour=period["openingHourData"].get("hour", 0),
                    minute=period["openingHourData"].get("minute", 0),
                )
                closing_time = time(
                    hour=period["closingHourData"].get("hour", 0),
                    minute=period["closingHourData"].get("minute", 0),
                )

                opening_datetime = datetime.combine(current_time.date(), opening_time)
                opening_datetime = store_timezone.localize(opening_datetime)

                # 5-minute grace period after opening: treat as "open but don't check screenshots"
                five_minutes_after_opening = opening_datetime + timedelta(minutes=5)
                if opening_datetime <= current_time <= five_minutes_after_opening:
                    logger.info(
                        "Store is OPEN but within first 5 minutes after opening. "
                        "Skipping screenshot check for this run."
                    )
                    return False

                if opening_time <= closing_time:
                    # Same-day close
                    if opening_time <= current_time_only <= closing_time:
                        logger.info("Store is OPEN.")
                        return True
                else:
                    # Overnight close (crosses midnight)
                    if current_time_only >= opening_time or current_time_only <= closing_time:
                        logger.info("Store is OPEN (overnight period).")
                        return True

            # No period matched → closed
            return False

    except Exception as e:
        logger.error("Error checking store hours: %s", e)

    # If the day is not found or any error occurs, assume closed
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

    # 2) Fixed zone tabs in your preferred order
    for zone_name in ["Pacific", "Eastern", "Central", "Mountain"]:
        add_if_present(zone_name)

    # 3) AnyDesk Status tab
    add_if_present("AnyDesk Status")

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
      - Log to sheet when something is wrong.

    Returns the updated row_counter.
    """
    license_id = str(license_data.get("licenseId", ""))
    license_key = str(license_data.get("licenseKey", ""))

    store_hours_json = license_data.get("storeHours", "[]")
    timezone_name = license_data.get("timezoneName", "UTC") or "UTC"

    # Check if store is open; if not, skip
    if not is_store_open(store_hours_json, timezone_name):
        return row_counter

    # Fetch screenshots via API
    screenshots_data = api.get_screenshots(license_id)
    if not screenshots_data:
        row_counter += 1
        logger.warning("No screenshots data for license %s.", license_key)
        ws.append_row(
            [row_counter, license_key, "", "NO_SCREENSHOTS"],
            value_input_option="USER_ENTERED",
        )
        return row_counter

    file_urls = screenshots_data.get("files", [])
    if not file_urls:
        row_counter += 1
        logger.warning("Empty 'files' list in screenshots for license %s.", license_key)
        ws.append_row(
            [row_counter, license_key, "", "NO_SCREENSHOTS"],
            value_input_option="USER_ENTERED",
        )
        return row_counter

    # Filter to today's screenshots
    todays_urls = filter_screenshots_for_today(file_urls, timezone_name, license_key)
    if not todays_urls:
        row_counter += 1
        ws.append_row(
            [row_counter, license_key, "", "SCREENSHOT_NAME_DATE_ERROR"],
            value_input_option="USER_ENTERED",
        )
        return row_counter

    black_screens = 0
    error_count = 0

    for url in todays_urls[:4]:  # limit to 4
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
            # Can't classify this image, skip to next
            continue
        except Exception as e:
            logger.error(
                "Unexpected OCR error for license %s: %s",
                license_key,
                e,
            )
            continue

        if any(err in text for err in ERROR_MESSAGES):
            error_count += 1

    # Decide what to log
    if black_screens >= 3:
        row_counter += 1
        ws.append_row(
            [row_counter, license_key, "", "OPEN_HOURS_BLACK_SCREENSHOTS"],
            value_input_option="USER_ENTERED",
        )
        logger.warning(
            "Black screen threshold exceeded for license %s (count=%s).",
            license_key,
            black_screens,
        )
    elif error_count >= 5:
        row_counter += 1
        ws.append_row(
            [row_counter, license_key, "", "STUCK_ON_ERROR"],
            value_input_option="USER_ENTERED",
        )
        logger.warning(
            "Error screenshot threshold reached for license %s (count=%s).",
            license_key,
            error_count,
        )
    elif 1 <= error_count <= 5:
        row_counter += 1
        ws.append_row(
            [row_counter, license_key, "", "ERROR_DISPLAYED"],
            value_input_option="USER_ENTERED",
        )
        logger.info(
            "Error screenshots detected for license %s (count=%s).",
            license_key,
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
    ws = sheets.get_or_create_worksheet(sheet_name, rows=2000, cols=4)
    headers = ["Row", "License Key", "URL", "Type"]
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
            "piStatus": 1,   # online
            "active": "true",
            "assigned": "true",
        }

        data = api.get_licenses(params=params)
        if not data:
            logger.warning("No data returned for screenshot health on page %s.", page)
            break

        licenses = data.get("licenses", [])
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
