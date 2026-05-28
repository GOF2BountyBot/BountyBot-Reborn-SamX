"""
Database schema management for BountyBot.

This module handles database schema versioning, migrations, and initialization.
It implements a simple yet effective schema versioning system using a dedicated
'schema' table to track the current database schema version.

Key Features:
- Simple schema versioning with 'schema' table
- Automatic database initialization if not exists
- Sequential schema migration support
- Schema version validation and health reporting
- Future-ready for Alembic integration
"""

from shared import bblogger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from persist.models.base import Base
from persist.models.schema_version import SchemaVersion

flogger = bblogger.get_logger("bot-schema-manager")

CURRENT_SCHEMA_VERSION = "1.0.0"  # Update as necessary


class SchemaManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    async def initialize_database(self, run_create_all: bool = False):
        """Initialise the database layer.

        Args:
            run_create_all: When ``True`` the legacy ``create_all()`` path is
                used to create tables.  This should only be set for unit tests
                that use an in-memory SQLite database.  In production the
                Alembic migration runner (invoked from ``main.py``) is
                responsible for DDL — passing ``False`` (the default) skips
                the redundant ``create_all()`` call.
        """
        flogger.info("Initializing database...")
        flogger.debug(f"Database initialization: run_create_all={run_create_all}")
        if run_create_all:
            flogger.debug("Using legacy create_all() path (test mode)")
            await self.create_tables_if_not_exist()
        await self._verify_schema_version()

    async def create_tables_if_not_exist(self):
        """Creates all tables if they don't already exist.

        .. deprecated::
            Use Alembic migrations instead.  This method is retained for
            backward-compatibility with integration tests that use an
            in-memory SQLite database and cannot run Alembic migrations
            against a real PostgreSQL instance.
        """
        flogger.trace("create_tables_if_not_exist() called")
        try:
            flogger.debug("Acquiring connection to execute Base.metadata.create_all()")
            async with self.db_manager.engine.begin() as conn:
                # run_sync wraps the sync create_all call
                flogger.debug("Running Base.metadata.create_all() via run_sync()")
                await conn.run_sync(Base.metadata.create_all)
            flogger.info("Database tables ensured.")
            flogger.debug("All DDL operations completed successfully")
        except Exception as e:
            flogger.error(f"Error creating tables: {e}")
            raise

    async def _verify_schema_version(self):
        """Checks and initializes schema version info."""
        flogger.trace("_verify_schema_version() called")
        async with self.db_manager.get_session() as session:
            flogger.debug("Querying SchemaVersion table")
            result = await session.execute(select(SchemaVersion))
            schema_version = result.scalars().first()
            flogger.debug(f"SchemaVersion query returned: {schema_version}")

            if schema_version is None:
                # if no schema version, set the current one
                flogger.debug("No existing SchemaVersion found; initializing with current version")
                stmt = (
                    insert(SchemaVersion)
                    .values(
                        version=CURRENT_SCHEMA_VERSION,
                        description="Initial Schema Version",
                    )
                    .on_conflict_do_nothing(index_elements=["version"])
                )
                await session.execute(stmt)
                flogger.debug(f"Committing new SchemaVersion record: {CURRENT_SCHEMA_VERSION}")
                await session.commit()
                flogger.info(f"Initialized schema version to {CURRENT_SCHEMA_VERSION}")
            elif schema_version.version != CURRENT_SCHEMA_VERSION:
                # Schema version mismatch here means migrations are needed
                flogger.debug(
                    f"Schema version comparison: "
                    f"db_version={schema_version.version}, "
                    f"current_version={CURRENT_SCHEMA_VERSION} — MISMATCH"
                )
                # Schema version mismatch here means migrations are needed (suggest using Alembic)
                flogger.warning(
                    f"Schema version mismatch detected: "
                    f"DB version = {schema_version.version}, "
                    f"Expected version = {CURRENT_SCHEMA_VERSION}. "
                    f"Consider running migrations."
                )
            else:
                flogger.debug(
                    f"Schema version comparison: "
                    f"db_version={schema_version.version}, "
                    f"current_version={CURRENT_SCHEMA_VERSION} — MATCH"
                )
                flogger.info(f"Schema version is up-to-date ({CURRENT_SCHEMA_VERSION}).")

    async def get_current_version(self) -> str | None:
        """Retrieve the current schema version from the database."""
        flogger.trace("get_current_version() called")
        async with self.db_manager.get_session() as session:
            flogger.debug("Querying SchemaVersion table for current version")
            result = await session.execute(select(SchemaVersion))
            schema_version = result.scalars().first()
            current = schema_version.version if schema_version else None
            flogger.debug(f"Retrieved current schema version: {current}")
            return current

    async def get_schema_health_info(self) -> dict:
        """Retrieve detailed schema health information."""
        flogger.trace("get_schema_health_info() called")
        try:
            flogger.debug("Fetching current schema version for health check")
            current_version = await self.get_current_version()
            version_match = current_version == CURRENT_SCHEMA_VERSION
            flogger.debug(
                f"Schema health: current_version={current_version}, "
                f"expected_version={CURRENT_SCHEMA_VERSION}, match={version_match}"
            )
            health_info = {
                "version": current_version,
                "expected_version": CURRENT_SCHEMA_VERSION,
                "version_match": version_match,
            }
            if version_match:
                flogger.debug("Schema version health check passed")
            else:
                flogger.warning(
                    f"Schema health check: version mismatch "
                    f"(current={current_version}, expected={CURRENT_SCHEMA_VERSION})"
                )
            return health_info
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error retrieving schema health info: {e}")
            return {"status": "error", "error": str(e)}


async def initialize_schema(db_manager) -> SchemaManager:
    """
    Create a SchemaManager and run initialization.
    Returns the initialized manager.
    """
    flogger.trace("initialize_schema() called")
    flogger.debug("Creating new SchemaManager instance")
    schema_manager = SchemaManager(db_manager)
    flogger.debug("Running SchemaManager.initialize_database()")
    await schema_manager.initialize_database()
    flogger.debug("SchemaManager initialization complete")
    return schema_manager
