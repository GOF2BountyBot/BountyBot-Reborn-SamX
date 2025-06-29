"""
Database schema management and versioning for BountyBot.

This module handles all schema-related operations including:
- Schema version tracking
- Migration management
- Schema validation
"""

import os
from typing import Optional, List, Dict, Any
from sqlalchemy import MetaData, Table, Column, Integer, String, DateTime, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
import shared.logging as logging

logger = logging.get_logger("schema-manager")

class SchemaManager:
    """
    Manages database schema versioning and migrations.
    
    Provides centralized schema management with:
    - Version tracking
    - Migration execution
    - Schema validation
    - Rollback capabilities
    """

    def __init__(self):
        self.current_schema_version = "1.0.0"
        self.metadata = MetaData()
        self._define_schema_table()
        self._migration_registry = {}
        self._register_migrations()

    def _define_schema_table(self) -> None:
        """Define the schema version tracking table."""
        self.schema_table = Table(
            'schema_version',
            self.metadata,
            Column('id', Integer, primary_key=True),
            Column('version', String(50), nullable=False, unique=True),
            Column('applied_at', DateTime, nullable=False, server_default=text('NOW()')),
            Column('description', String(255)),
            Column('checksum', String(64)),  # For validation
            Column('execution_time_ms', Integer),
            Column('applied_by', String(100))
        )

    def _register_migrations(self) -> None:
        """Register all available migrations."""
        # This would be populated with actual migration functions
        self._migration_registry = {
            "1.0.0": {
                "description": "Initial schema version",
                "migration_func": self._migrate_to_1_0_0,
                "rollback_func": None
            }
            # Add more migrations as needed
        }

    async def initialize(self, engine) -> None:
        """
        Initialize schema management.
        
        Args:
            engine: SQLAlchemy engine for database operations
        """
        try:
            await self._ensure_schema_table(engine)
            await self._check_and_update_schema(engine)
            logger.info("Schema management initialized successfully")
        except Exception as e:
            logger.error(f"Schema management initialization failed: {e}")
            raise

    async def _ensure_schema_table(self, engine) -> None:
        """Ensure the schema versioning table exists."""
        try:
            # Define schema table structure
            metadata = MetaData()
            schema_table = Table(
                'schema_version',
                metadata,
                Column('id', Integer, primary_key=True),
                Column('version', String(50), nullable=False),
                Column('applied_at', DateTime, nullable=False, server_default=text('NOW()')),
                Column('description', String(255))
            )

            # Use async connection for consistency
            async with engine.begin() as conn:
                # Check if table exists
                result = await conn.execute(text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = 'schema_version'
                    );
                """))
                table_exists_row = result.fetchone()
                table_exists = table_exists_row[0]

                if not table_exists:
                    logger.info("Creating schema_version table...")
                    # For DDL operations, use sync connection
                    await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, tables=[schema_table]))
                    
                    # Insert initial version
                    await conn.execute(text("""
                        INSERT INTO schema_version (version, description)
                        VALUES (:version, :description)
                    """), {
                        "version": self.current_schema_version,
                        "description": "Initial schema version"
                    })
                    
                    logger.info(f"Schema version table created with version {self.current_schema_version}")
                else:
                    logger.info("Schema version table already exists")
        except Exception as e:
            logger.error(f"Failed to ensure schema table: {e}")
            raise


    async def _check_and_update_schema(self) -> None:
        """Check current schema version and apply updates if necessary."""
        try:
            # Use async connection for consistency
            async with self.async_engine.begin() as conn:
                # Get current schema version
                result = await conn.execute(text("""
                    SELECT version FROM schema_version
                    ORDER BY applied_at DESC LIMIT 1
                """))
                current_version = result.fetchone()
                
                if current_version:
                    db_version = current_version[0]
                    logger.info(f"Current database schema version: {db_version}")
                    if db_version != self.current_schema_version:
                        logger.info(f"Schema update needed: {db_version} -> {self.current_schema_version}")
                        await self._apply_schema_updates(db_version)
                    else:
                        logger.info("Schema is up to date")
                else:
                    logger.warning("No schema version found, this should not happen")
        except Exception as e:
            logger.error(f"Schema version check failed: {e}")
            raise


    async def get_current_version(self, conn) -> str:
        """
        Get the current schema version from the database.
        
        Args:
            conn: Database connection
            
        Returns:
            str: Current schema version
        """
        try:
            result = await conn.execute(text("""
                SELECT version FROM schema_version
                ORDER BY applied_at DESC LIMIT 1
            """))
            row = result.fetchone()
            return row.version if row else "0.0.0"
        except Exception as e:
            logger.error(f"Failed to get current schema version: {e}")
            return "0.0.0"

    async def _apply_migrations(self, engine, from_version: str, to_version: str) -> None:
        """
        Apply schema migrations from current version to target version.
        
        Args:
            engine: Database engine
            from_version: Current schema version
            to_version: Target schema version
        """
        migrations_to_apply = self._get_migration_path(from_version, to_version)
        
        for migration_version in migrations_to_apply:
            start_time = datetime.utcnow()
            try:
                migration_info = self._migration_registry[migration_version]
                logger.info(f"Applying migration to version {migration_version}: {migration_info['description']}")
                
                # Execute migration
                await migration_info['migration_func'](engine)
                
                # Record migration
                execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                await self._record_migration(engine, migration_version, migration_info, execution_time)
                
                logger.info(f"Migration to {migration_version} completed successfully")
                
            except Exception as e:
                logger.error(f"Migration to {migration_version} failed: {e}")
                # Attempt rollback if available
                if migration_info.get('rollback_func'):
                    try:
                        await migration_info['rollback_func'](engine)
                        logger.info(f"Rollback for {migration_version} completed")
                    except Exception as rollback_error:
                        logger.error(f"Rollback failed: {rollback_error}")
                raise

    def _get_migration_path(self, from_version: str, to_version: str) -> List[str]:
        """
        Determine the migration path from current to target version.
        
        Args:
            from_version: Current version
            to_version: Target version
            
        Returns:
            List of migration versions to apply
        """
        # Simple implementation - in production, this would handle complex version trees
        available_versions = sorted(self._migration_registry.keys())
        
        try:
            from_index = available_versions.index(from_version)
            to_index = available_versions.index(to_version)
            
            if to_index > from_index:
                return available_versions[from_index + 1:to_index + 1]
            else:
                return []  # Downgrade not supported in this simple implementation
                
        except ValueError:
            logger.error(f"Invalid version range: {from_version} -> {to_version}")
            return []

    async def _record_migration(self, engine, version: str, migration_info: Dict, execution_time: int) -> None:
        """Record a successful migration in the database."""
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO schema_version (version, description, execution_time_ms, applied_by)
                    VALUES (:version, :description, :execution_time, :applied_by)
                """), {
                    "version": version,
                    "description": migration_info['description'],
                    "execution_time": execution_time,
                    "applied_by": "system"
                })
        except Exception as e:
            logger.error(f"Failed to record migration: {e}")
            raise

    async def _migrate_to_1_0_0(self, engine) -> None:
        """Migration function for version 1.0.0."""
        # This would contain the actual migration logic
        logger.info("Executing migration to version 1.0.0")
        # Example: Create initial tables, indexes, etc.
        pass

    async def get_migration_history(self, engine) -> List[Dict[str, Any]]:
        """
        Get the complete migration history.
        
        Returns:
            List of migration records
        """
        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT version, description, applied_at, execution_time_ms, applied_by
                    FROM schema_version
                    ORDER BY applied_at DESC
                """))
                
                return [
                    {
                        "version": row.version,
                        "description": row.description,
                        "applied_at": row.applied_at.isoformat(),
                        "execution_time_ms": row.execution_time_ms,
                        "applied_by": row.applied_by
                    }
                    for row in result.fetchall()
                ]
        except Exception as e:
            logger.error(f"Failed to get migration history: {e}")
            return []
