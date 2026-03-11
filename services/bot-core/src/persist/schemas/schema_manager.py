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

from persist.models.base import Base
from persist.models.schema_version import SchemaVersion

flogger = bblogger.get_logger("bot-schema-manager")

CURRENT_SCHEMA_VERSION = "1.0.0"  # Update as necessary

class SchemaManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    async def initialize_database(self):
        flogger.info("Initializing database...")
        await self.create_tables_if_not_exist()
        await self._verify_schema_version()

    async def create_tables_if_not_exist(self):
        """Creates all tables if they don't already exist."""
        try:
            async with self.db_manager.engine.begin() as conn:
                # run_sync wraps the sync create_all call
                await conn.run_sync(Base.metadata.create_all)
            flogger.info("Database tables ensured.")
        except Exception as e:
            flogger.error(f"Error creating tables: {e}")
            raise

    async def _verify_schema_version(self):
        """Checks and initializes schema version info."""
        async with self.db_manager.get_session() as session:
            result = await session.execute(select(SchemaVersion))
            schema_version = result.scalars().first()

            if schema_version is None:
                # if no schema version, set the current one
                schema_version = SchemaVersion(
                    version=CURRENT_SCHEMA_VERSION,
                    description="Initial Schema Version"
                )
                session.add(schema_version)
                await session.commit()
                flogger.info(f"Initialized schema version to {CURRENT_SCHEMA_VERSION}")
            elif schema_version.version != CURRENT_SCHEMA_VERSION:
                # Schema version mismatch here means migrations are needed (suggest using Alembic)
                flogger.warning(
                    f"Schema version mismatch detected: "
                    f"DB version = {schema_version.version}, "
                    f"Expected version = {CURRENT_SCHEMA_VERSION}. "
                    f"Consider running migrations."
                )
            else:
                flogger.info(f"Schema version is up-to-date ({CURRENT_SCHEMA_VERSION}).")

    async def get_current_version(self) -> str | None:
        """Retrieve the current schema version from the database."""
        async with self.db_manager.get_session() as session:
            result = await session.execute(select(SchemaVersion))
            schema_version = result.scalars().first()
            return schema_version.version if schema_version else None

    async def get_schema_health_info(self) -> dict:
        """Retrieve detailed schema health information."""
        try:
            current_version = await self.get_current_version()
            version_match = current_version == CURRENT_SCHEMA_VERSION
            return {
                "version": current_version,
                "expected_version": CURRENT_SCHEMA_VERSION,
                "version_match": version_match
            }
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error retrieving schema health info: {e}")
            return {
                "status": "error",
                "error": str(e)
            }

async def initialize_schema(db_manager) -> SchemaManager:
    """
    Create a SchemaManager and run initialization.
    Returns the initialized manager.
    """
    schema_manager = SchemaManager(db_manager)
    await schema_manager.initialize_database()
    return schema_manager
