"""Module-level scheduler holder.

Provides a singleton reference to the running AsyncIOScheduler instance so that
executor modules (e.g. bounty_spawn_executor) can schedule one-time jobs via the
direct Python API instead of making an in-process HTTP round-trip to the scheduler
router (which is the B.23a failure mode).

Usage
-----
In main.py lifespan, after scheduler.start():

    from utils.scheduler_holder import set_scheduler
    set_scheduler(scheduler)

In executor modules that need to add a one-time job:

    from utils.scheduler_holder import get_scheduler
    scheduler = get_scheduler()
    if scheduler is not None:
        scheduler.add_job(run_job, trigger="date", run_date=..., args=[...], id=...)
"""

from __future__ import annotations

_scheduler = None


def set_scheduler(scheduler) -> None:  # type: ignore[type-arg]
    """Store the active scheduler instance.  Called once from main.py lifespan."""
    global _scheduler
    _scheduler = scheduler


def get_scheduler():
    """Return the active scheduler instance, or None if not yet initialised."""
    return _scheduler
