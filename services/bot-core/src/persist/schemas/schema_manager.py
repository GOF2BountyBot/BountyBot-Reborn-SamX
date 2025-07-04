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

import os
from persist.database.manager import db_manager
from persist.models.base import Base
from persist.models.schema_version import SchemaVersion
import shared.logging as logging
from sqlalchemy import inspect

logger = logging.get_logger("bot-schema-manager")

CURRENT_SCHEMA_VERSION = "1.0.0"  # Update as necessary

class SchemaManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def initialize_database(self):
        logger.info("Initializing database...")
        self.create_tables_if_not_exist()
        self._verify_schema_version()

    def create_tables_if_not_exist(self):
        """Creates all tables if they don't already exist."""
        try:
            Base.metadata.create_all(bind=self.db_manager.engine)
            logger.info("Database tables ensured.")
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
            raise

    def _verify_schema_version(self):
        """Checks and initializes schema version info."""
        with self.db_manager.get_session() as session:
            schema_version = session.query(SchemaVersion).first()
            
            if schema_version is None:
                # if no schema version, set the current one
                schema_version = SchemaVersion(
                    version=CURRENT_SCHEMA_VERSION,
                    description="Initial Schema Version"
                )
                session.add(schema_version)
                session.commit()
                logger.info(f"Initialized schema version to {CURRENT_SCHEMA_VERSION}")
            elif schema_version.version != CURRENT_SCHEMA_VERSION:
                # Schema version mismatch here means migrations are needed (suggest using Alembic)
                logger.warning(f"Schema version mismatch detected: "
                               f"DB version = {schema_version.version}, "
                               f"Expected version = {CURRENT_SCHEMA_VERSION}. "
                               f"Consider running migrations.")
            else:
                logger.info(f"Schema version is up-to-date ({CURRENT_SCHEMA_VERSION}).")

    def get_current_version(self):
        """Retrieve the current schema version from the database."""
        with self.db_manager.get_session() as session:
            schema_version = session.query(SchemaVersion).first()
            return schema_version.version if schema_version else None

    
    def get_schema_health_info(self):
        """Retrieve detailed schema health information."""
        try:
            current_version = self.get_current_version()
            version_match = (current_version == CURRENT_SCHEMA_VERSION)
            return {
                "version": current_version,
                "expected_version": CURRENT_SCHEMA_VERSION,
                "version_match": version_match
            }
        except Exception as e:
            logger.error(f"Error retrieving schema health info: {e}")
            return {
                "status": "error",
                "error": str(e)
            }

def initialize_schema(db_manager) -> SchemaManager:
    schema_manager = SchemaManager(db_manager)
    schema_manager.initialize_database()
    return schema_manager