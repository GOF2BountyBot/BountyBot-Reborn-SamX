"""
Auto-seeder for game-data tables.

Provides ``auto_seed_data()``, called during application startup to
populate empty game-data tables from ``import_data/`` JSON files using
the existing ``load_data()`` infrastructure.  The seeding is idempotent:
tables that already contain rows are skipped without modification.
"""

import fcntl
import os

from persist.database.manager import db_manager
from shared import bblogger
from sqlalchemy import func, select

from utils.data_loader import get_repository, load_data

flogger = bblogger.get_logger("bot-auto-seeder")

# All data categories supported by the seeder, in load order.
SEED_CATEGORIES: list[str] = [
    "ship",
    "primary_weapon",
    "secondary_weapon",
    "turret_weapon",
    "module",
    "criminal",
    "system",
    "commodity",
]

# Cross-worker file lock — prevents N uvicorn workers from racing the
# initial seed on a fresh DB.  Held only for the duration of auto_seed_data().
_SEED_LOCK_PATH = "/tmp/bountybot_auto_seed.lock"


async def table_is_empty(repo) -> bool:
    """Return True when the repository's underlying table has zero rows."""
    async with db_manager.get_session() as session:
        result = await session.execute(select(func.count()).select_from(repo._model))  # pylint: disable=not-callable
        count = result.scalar()
        return (count or 0) == 0


async def auto_seed_data() -> None:
    """
    Idempotent startup seeder.

    For each game-data category, check whether the corresponding DB table
    already contains rows.  If the table is empty, import JSON files from
    ``import_data/<category>/`` via the existing ``load_data()`` helper.
    Missing data directories are skipped gracefully so the startup sequence
    is never aborted by a partially-present asset set.

    Cross-worker safety: when uvicorn runs N workers, all N spawn this
    coroutine on startup.  A non-blocking ``fcntl.flock`` on
    ``_SEED_LOCK_PATH`` serializes them — only the first worker proceeds
    with seeding; the others log and exit immediately.  By the time those
    workers retry on the next restart, the per-table "already populated"
    check is the idempotency guard.
    """
    try:
        lock_fd = os.open(_SEED_LOCK_PATH, os.O_CREAT | os.O_WRONLY, 0o644)
    except OSError as exc:
        flogger.error(f"🌱 Could not open seed lock {_SEED_LOCK_PATH}: {exc} — proceeding without lock")
        await _run_seed_loop()
        return

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            flogger.info("🌱 Another worker holds the seed lock; skipping auto-seed in this worker.")
            return
        await _run_seed_loop()
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


async def _run_seed_loop() -> None:
    """Inner seeding loop — kept separate so the lock-acquisition wrapper stays focused."""
    flogger.info("🌱 Starting auto-seed check for game data...")

    for category in SEED_CATEGORIES:
        try:
            repo = get_repository(category)
        except RuntimeError as exc:
            flogger.error(f"  ⚠ [{category}] Could not load repository, skipping: {exc}")
            continue

        try:
            empty = await table_is_empty(repo)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.error(f"  ⚠ [{category}] Could not query table count, skipping: {exc}")
            continue

        if not empty:
            flogger.info(f"  ✅ [{category}] Table already populated, skipping seed.")
            continue

        flogger.info(f"  🔄 [{category}] Table is empty — seeding from import_data/...")
        try:
            results = await load_data(category)
            flogger.info(f"  ✅ [{category}] Seeded {len(results)} item(s).")
        except ValueError as exc:
            # import_data/<category>/ directory does not exist
            flogger.warning(f"  ⏭ [{category}] No import_data directory found, skipping: {exc}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.error(f"  ❌ [{category}] Seeding failed (continuing): {exc}")

    flogger.info("🌱 Auto-seed check complete.")
