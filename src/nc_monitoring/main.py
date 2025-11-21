"""
Entry point for the NC Monitoring service.

This just:
1. Configures logging.
2. Starts the background scheduler defined in scheduler/runner.py.
"""

from nc_monitoring.logging_config import configure_logging
from scheduler.runner import start_scheduler


def main() -> None:
    """Configure logging and start the scheduler."""
    configure_logging()
    start_scheduler()


if __name__ == "__main__":
    main()
