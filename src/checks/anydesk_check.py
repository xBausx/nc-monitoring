import logging

from clients.api_client import APIClient
from clients.anydesk_client import AnyDeskClient
from clients.sheets_client import SheetsClient
from clients.slack_client import SlackClient

logger = logging.getLogger(__name__)

def run_anydesk_check() -> None:
    """
    New flow (high-level):
        1. Use APIClient to fetch relevant licenses (no XLSX, no Selenium).
        2. For each license with AnyDesk ID + password:
            - connect via AnyDeskClient
            - capture screenshot + OCR
            - determine status (online / offline / wrong password / etc.)
        3. Write results to Sheets and send Slack alerts as needed.
    """
    api = APIClient()
    anydesk = AnyDeskClient()
    sheets = SheetsClient()
    slack = SlackClient()

    logger.info("Starting AnyDesk check...")

    # TODO: implement – in the next step we'll wire this up using your existing logic.
    # For now it's just a scaffold.
    licenses = api.get_licenses_for_anydesk_check()

    for lic in licenses:
        logger.debug("Checking license %s", lic.license_id)
        # ...
