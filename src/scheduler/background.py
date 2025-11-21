"""
Thin wrapper around APScheduler's BackgroundScheduler.

Usage elsewhere:
    from scheduler.background import BackgroundScheduler
"""

from apscheduler.schedulers.background import BackgroundScheduler

__all__ = ["BackgroundScheduler"]
