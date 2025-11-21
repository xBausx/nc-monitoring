import logging
import os
import time

from scheduler.background import BackgroundScheduler
from nc_monitoring.jobs import register_jobs

logger = logging.getLogger(__name__)


def start_scheduler() -> None:
    """
    Create a BackgroundScheduler, register jobs, and keep the process alive.
    The ANYDESK_AGENT env var controls whether AnyDesk-specific jobs are added.
    """
    scheduler = BackgroundScheduler()
    is_anydesk_agent = os.getenv("ANYDESK_AGENT", "false").lower() == "true"

    register_jobs(scheduler, is_anydesk_agent=is_anydesk_agent)
    scheduler.start()

    logger.info("Scheduler started (ANYDESK_AGENT=%s)", is_anydesk_agent)

    try:
        # Keep the scheduler running in this process
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler...")
        scheduler.shutdown()
