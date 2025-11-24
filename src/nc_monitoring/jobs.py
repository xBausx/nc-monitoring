import logging

from apscheduler.schedulers.base import BaseScheduler

from checks.version_by_zone import run_version_zone_check
from checks.screenshot_health import run_screenshot_health
from checks.anydesk_check import run_anydesk_check

logger = logging.getLogger(__name__)


# --- Jobs --------------------------------------------------------------------


def job_screenshot_health() -> None:
    """
    Real job: call the screenshot health check.

    This uses:
      - APIClient       (in clients.api_client)
      - SheetsClient    (in clients.sheets_client)
      - OCR/image logic (in checks.screenshot_health)
    """
    logger.info("[job_screenshot_health] Starting screenshot health check...")
    run_screenshot_health()
    logger.info("[job_screenshot_health] Finished.")


def job_version_zone_check() -> None:
    """
    Real job: call the version-by-zone check.

    This uses:
      - APIClient       (in clients.api_client)
      - SheetsClient    (in clients.sheets_client)
      - SocketClient    (in clients.socket_client)
    """
    logger.info("[job_version_zone_check] Starting version-by-zone check...")
    run_version_zone_check()
    logger.info("[job_version_zone_check] Finished.")


def job_version_sheet_check() -> None:
    """TEMP: placeholder for version-by-sheet check."""
    logger.info("[job_version_sheet_check] Running (placeholder, no real logic yet).")


def job_anydesk_check() -> None:
    """
    Real job: call the AnyDesk connectivity check.

    This uses:
      - APIClient       (in clients.api_client)
      - AnyDeskClient   (in clients.anydesk_client)

    IMPORTANT:
      This should run ONLY on a Windows agent with:
        - AnyDesk installed and on PATH
        - An active desktop session
        - Tesseract configured for pytesseract
    """
    logger.info("[job_anydesk_check] Starting AnyDesk connectivity check...")
    run_anydesk_check()
    logger.info("[job_anydesk_check] Finished.")


# --- Job registration --------------------------------------------------------


def register_jobs(scheduler: BaseScheduler, *, is_anydesk_agent: bool) -> None:
    """
    Register all recurring jobs on the given scheduler.

    Right now:
      - screenshot + version_zone + anydesk jobs are fully implemented
      - version_sheet job is a placeholder
    """

    logger.info("Registering jobs (ANYDESK_AGENT=%s)", is_anydesk_agent)

    # Screenshot health: safe in cloud
    scheduler.add_job(
        job_screenshot_health,
        "interval",
        minutes=5,
        id="screenshot_health",
        replace_existing=True,
    )

    # Version drift by zone: safe in cloud
    scheduler.add_job(
        job_version_zone_check,
        "interval",
        minutes=10,
        id="version_zone_check",
        replace_existing=True,
    )

    # Still placeholder
    scheduler.add_job(
        job_version_sheet_check,
        "interval",
        hours=1,
        id="version_sheet_check",
        replace_existing=True,
    )

    # AnyDesk: only when acting as Windows agent
    if is_anydesk_agent:
        scheduler.add_job(
            job_anydesk_check,
            "interval",
            minutes=5,
            id="anydesk_check",
            replace_existing=True,
        )

    logger.info("Jobs registered.")
