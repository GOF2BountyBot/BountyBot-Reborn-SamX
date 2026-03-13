"""
Auto-seeder for game-data tables.

Provides ``auto_seed_data()``, called during application startup to
populate empty game-data tables from ``import_data/`` JSON files using
the existing ``load_data()`` infrastructure.  The seeding is idempotent:
tables that already contain rows are skipped without modification.
"""

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
]


async def table_is_empty(repo) -> bool:
    """Return True when the repository's underlying table has zero rows."""
    async with db_manager.get_session() as session:
        result = await session.execute(select(func.count()).select_from(repo._model))
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
    """
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
