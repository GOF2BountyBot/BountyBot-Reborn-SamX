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
from typing import Dict, Any, Optional, List
from sqlalchemy import text, Table, Column, String, DateTime, MetaData
from sqlalchemy.exc import SQLAlchemyError, ProgrammingError
from datetime import datetime
import shared.logging as logging

logger = logging.get_logger("bot-schema-manager")

class SchemaManager:
    """
    Manages database schema versioning and migrations.
    
    This class provides a foundation for schema management that can be
    easily extended or replaced with Alembic in the future.
    """
    
    # Current schema version - update this when adding new migrations
    CURRENT_SCHEMA_VERSION = "1.0.0"
    
    def __init__(self, db_manager):
        """
        Initialize schema manager with database manager reference.
        
        Args:
            db_manager: DatabaseManager instance
        """
        self.db_manager = db_manager
        self._schema_migrations = self._load_migrations()
        
    def _load_migrations(self) -> Dict[str, callable]:
        """
        Load schema migration functions.
        
        Returns a dictionary mapping version strings to migration functions.
        In the future, this could be replaced with Alembic migration discovery.
        """
        return {
            "1.0.0": self._migrate_to_1_0_0,
            # Add future migrations here:
            # "1.1.0": self._migrate_to_1_1_0,
            # "2.0.0": self._migrate_to_2_0_0,
        }
        
    def initialize_database(self) -> None:
        """
        Initialize the database schema.
        
        This method should be called during application startup to ensure
        the database is properly initialized and up-to-date.
        """
        logger.info("Starting database schema initialization...")
        
        try:
            # Check if database exists and is accessible
            if not self._database_accessible():
                raise RuntimeError("Database is not accessible")
                
            # Get current schema version
            current_version = self._get_current_schema_version()
            
            if current_version is None:
                # Database exists but no schema version table - initialize
                logger.info("No schema version found, initializing database...")
                self._initialize_fresh_database()
            elif current_version != self.CURRENT_SCHEMA_VERSION:
                # Database exists but schema is outdated - migrate
                logger.info(f"Schema version mismatch. Current: {current_version}, "
                          f"Required: {self.CURRENT_SCHEMA_VERSION}")
                self._migrate_schema(current_version, self.CURRENT_SCHEMA_VERSION)
            else:
                # Database is up-to-date
                logger.info(f"Database schema is up-to-date (version: {current_version})")
                
        except Exception as e:
            logger.error(f"Database schema initialization failed: {e}")
            raise
            
    def _database_accessible(self) -> bool:
        """
        Check if the database is accessible.
        
        Returns:
            True if database is accessible, False otherwise
        """
        try:
            with self.db_manager.get_connection() as conn:
                conn.execute(text("SELECT 1"))
                return True
        except Exception as e:
            logger.error(f"Database accessibility check failed: {e}")
            return False
            
    def _get_current_schema_version(self) -> Optional[str]:
        """
        Get the current schema version from the database.
        
        Returns:
            Current schema version string, or None if schema table doesn't exist
        """
        try:
            # Check if schema table exists
            if not self.db_manager.table_exists("schema"):
                return None
                
            # Query current version
            with self.db_manager.get_connection() as conn:
                result = conn.execute(text("SELECT version FROM schema LIMIT 1"))
                row = result.fetchone()
                return row[0] if row else None
                
        except Exception as e:
            logger.warning(f"Could not retrieve schema version: {e}")
            return None
            
    def _create_schema_table(self) -> None:
        """Create the schema version tracking table."""
        logger.info("Creating schema version table...")
        
        create_schema_table_sql = """
        CREATE TABLE schema (
            version VARCHAR(50) NOT NULL,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        )
        """
        
        try:
            with self.db_manager.get_connection() as conn:
                conn.execute(text(create_schema_table_sql))
                conn.commit()
                logger.info("Schema version table created successfully")
        except Exception as e:
            logger.error(f"Failed to create schema table: {e}")
            raise
            
    def _update_schema_version(self, version: str, description: str = "") -> None:
        """
        Update the schema version in the database.
        
        Args:
            version: New schema version
            description: Optional description of the schema changes
        """
        try:
            with self.db_manager.get_connection() as conn:
                # Clear existing version records
                conn.execute(text("DELETE FROM schema"))
                
                # Insert new version record
                conn.execute(
                    text("INSERT INTO schema (version, description) VALUES (:version, :description)"),
                    {"version": version, "description": description}
                )
                conn.commit()
                
                logger.info(f"Schema version updated to: {version}")
                
        except Exception as e:
            logger.error(f"Failed to update schema version: {e}")
            raise
            
    def _initialize_fresh_database(self) -> None:
        """Initialize a fresh database with the latest schema."""
        logger.info("Initializing fresh database...")
        
        try:
            # Create schema version table
            self._create_schema_table()
            
            # Apply the latest schema migration
            latest_version = self.CURRENT_SCHEMA_VERSION
            if latest_version in self._schema_migrations:
                self._schema_migrations[latest_version]()
                
            # Update schema version
            self._update_schema_version(
                latest_version, 
                f"Initial database setup with schema version {latest_version}"
            )
            
            logger.info("Fresh database initialization completed")
            
        except Exception as e:
            logger.error(f"Fresh database initialization failed: {e}")
            raise
            
    def _migrate_schema(self, from_version: str, to_version: str) -> None:
        """
        Migrate schema from one version to another.
        
        Args:
            from_version: Current schema version
            to_version: Target schema version
        """
        logger.info(f"Migrating schema from {from_version} to {to_version}")
        
        try:
            # Get ordered list of migrations to apply
            migrations_to_apply = self._get_migration_path(from_version, to_version)
            
            # Apply each migration in order
            for version in migrations_to_apply:
                if version in self._schema_migrations:
                    logger.info(f"Applying migration to version {version}")
                    self._schema_migrations[version]()
                    self._update_schema_version(
                        version, 
                        f"Migrated from {from_version} to {version}"
                    )
                else:
                    raise RuntimeError(f"No migration found for version {version}")
                    
            logger.info(f"Schema migration completed: {from_version} -> {to_version}")
            
        except Exception as e:
            logger.error(f"Schema migration failed: {e}")
            raise
            
    def _get_migration_path(self, from_version: str, to_version: str) -> List[str]:
        """
        Get the ordered list of migrations to apply.
        
        Args:
            from_version: Starting version
            to_version: Target version
            
        Returns:
            List of version strings representing the migration path
        """
        # For now, implement simple version comparison
        # In a more complex system, this would use proper version parsing
        available_versions = sorted(self._schema_migrations.keys())
        
        try:
            from_index = available_versions.index(from_version) if from_version in available_versions else -1
            to_index = available_versions.index(to_version)
            
            if from_index >= to_index:
                return []  # No migration needed or downgrade (not supported)
                
            return available_versions[from_index + 1:to_index + 1]
            
        except ValueError:
            raise RuntimeError(f"Invalid migration path: {from_version} -> {to_version}")
            
    def get_schema_health_info(self) -> Dict[str, Any]:
        """
        Get schema health information for health checks.
        
        Returns:
            Dictionary containing schema health metrics
        """
        health_info = {
            "status": "unknown",
            "current_version": None,
            "expected_version": self.CURRENT_SCHEMA_VERSION,
            "schema_table_exists": False,
            "version_match": False,
            "error": None
        }
        
        try:
            # Check if schema table exists
            health_info["schema_table_exists"] = self.db_manager.table_exists("schema")
            
            if health_info["schema_table_exists"]:
                # Get current version
                current_version = self._get_current_schema_version()
                health_info["current_version"] = current_version
                
                # Check version match
                health_info["version_match"] = (current_version == self.CURRENT_SCHEMA_VERSION)
                
                if health_info["version_match"]:
                    health_info["status"] = "healthy"
                else:
                    health_info["status"] = "version_mismatch"
                    health_info["error"] = f"Schema version mismatch: {current_version} != {self.CURRENT_SCHEMA_VERSION}"
            else:
                health_info["status"] = "schema_table_missing"
                health_info["error"] = "Schema version table does not exist"
                
        except Exception as e:
            health_info["status"] = "error"
            health_info["error"] = str(e)
            logger.error(f"Schema health check failed: {e}")
            
        return health_info
        
    # Schema Migration Functions
    # ==========================
    
    def _migrate_to_1_0_0(self) -> None:
        """
        Initial schema migration - creates basic BountyBot tables.
        
        This migration creates the foundational tables for the BountyBot application.
        Future migrations will build upon this base schema.
        """
        logger.info("Applying migration to schema version 1.0.0...")
        
        # Example initial tables - adjust according to your needs
        migrations_sql = [
            # Example: Users table for basic user management
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                discord_id BIGINT UNIQUE NOT NULL,
                username VARCHAR(100) NOT NULL,
                display_name VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
            """,
            
            # Example: Guilds table for Discord server management
            """
            CREATE TABLE IF NOT EXISTS guilds (
                id SERIAL PRIMARY KEY,
                discord_id BIGINT UNIQUE NOT NULL,
                name VARCHAR(255) NOT NULL,
                prefix VARCHAR(10) DEFAULT '!',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
            """,
            
            # Create indexes for performance
            "CREATE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id)",
            "CREATE INDEX IF NOT EXISTS idx_guilds_discord_id ON guilds(discord_id)",
            
            # Add any other foundational tables here
        ]
        
        try:
            with self.db_manager.get_connection() as conn:
                for sql in migrations_sql:
                    conn.execute(text(sql))
                conn.commit()
                
            logger.info("Schema migration to 1.0.0 completed successfully")
            
        except Exception as e:
            logger.error(f"Migration to 1.0.0 failed: {e}")
            raise
            
    # Future migration methods would be added here:
    # def _migrate_to_1_1_0(self) -> None:
    #     """Migration to version 1.1.0 - add bounty tables."""
    #     pass
    #     
    # def _migrate_to_2_0_0(self) -> None:
    #     """Migration to version 2.0.0 - major schema refactor."""
    #     pass

def initialize_schema(db_manager) -> SchemaManager:
    """
    Convenience function to initialize schema management.
    
    Args:
        db_manager: DatabaseManager instance
        
    Returns:
        Initialized SchemaManager instance
    """
    schema_manager = SchemaManager(db_manager)
    schema_manager.initialize_database()
    return schema_manager
