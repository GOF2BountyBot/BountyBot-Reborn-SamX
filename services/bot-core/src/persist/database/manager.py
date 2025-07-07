"""
Database connection and management for BountyBot.

This module provides database connectivity, connection management, and basic
database operations. It's designed to be a foundation for future repository
pattern implementation while keeping things simple for the current iteration.

Key Features:
- PostgreSQL async connection management with connection pooling
- Environment-based configuration
- Async database operations
- Health check support
- Future-ready for repository pattern integration
"""

import os
import time
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

from sqlalchemy import text, MetaData, inspect
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

import shared.logging as logging

logger = logging.get_logger("bot-database-manager")


class DatabaseManager:
    """
    Central database management class for BountyBot.

    Handles async connection management, health checks, and provides a foundation
    for future repository pattern implementation.
    """

    def __init__(self):
        """Initialize the database manager with environment configuration."""
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._connection_string: Optional[str] = None
        self._metadata = MetaData()
        self._load_config()

    def _load_config(self) -> None:
        """Load database configuration from environment variables."""
        db_host = os.getenv("POSTGRES_HOST", "bounty_db")
        db_port = os.getenv("POSTGRES_PORT", "5432")
        db_name = os.getenv("POSTGRES_DB", "bountydb")
        db_user = os.getenv("POSTGRES_USER", "bounty")
        db_password = os.getenv("POSTGRES_PASSWORD", "bounty")

        pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
        max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
        pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "3600"))
        echo_flag = os.getenv("DB_ECHO", "false").lower() == "true"

        # Use asyncpg dialect
        self._connection_string = (
            f"postgresql+asyncpg://{db_user}:{db_password}@"
            f"{db_host}:{db_port}/{db_name}"
        )
        self._pool_config = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
            "pool_recycle": pool_recycle,
            "pool_pre_ping": True,
            "echo": echo_flag,
        }
        logger.info(f"Database configuration loaded: {db_host}:{db_port}/{db_name}")

    async def initialize(self) -> None:
        """
        Initialize the async database engine and session factory.
        Call this during application startup.
        """
        if self._engine is not None:
            logger.warning("Database manager already initialized")
            return

        try:
            logger.info("Initializing async database connection...")
            self._engine = create_async_engine(
                self._connection_string,
                future=True,
                **self._pool_config
            )
            self._session_factory = sessionmaker(
                bind=self._engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            await self._test_connection()
            logger.info("Database manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database manager: {e}")
            raise

    async def _test_connection(self) -> None:
        """Test the database connection with retry logic."""
        max_retries = 5
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                async with self.get_connection() as conn:
                    result = await conn.execute(text("SELECT 1 as test"))
                    if result.scalar() == 1:
                        logger.info("Database connectivity test passed")
                        return
                    raise RuntimeError("Unexpected test result from database")
            except OperationalError as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"DB connection attempt {attempt+1} failed: {e}. "
                        f"Retrying in {retry_delay}s..."
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error("Database connection failed after retries")
                    raise
            except Exception:
                logger.error("Unexpected error during DB connectivity test")
                raise

    @asynccontextmanager
    async def get_connection(self):
        """
        Async context manager for a raw connection.
        Usage:
            async with db_manager.get_connection() as conn:
                await conn.execute(text("..."))
        """
        if self._engine is None:
            raise RuntimeError("Database manager not initialized. Call initialize() first.")
        async with self._engine.connect() as conn:
            yield conn

    @asynccontextmanager
    async def get_session(self) -> AsyncSession:
        """
        Async context manager for a session.
        Usage:
            async with db_manager.get_session() as session:
                await session.execute(...)
                await session.commit()
        """
        if self._session_factory is None:
            raise RuntimeError("Database manager not initialized. Call initialize() first.")
        async with self._session_factory() as session:
            try:
                yield session
            except:
                await session.rollback()
                raise

    async def execute_sql(
        self,
        sql_statement: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Execute a raw SQL statement in a transaction.
        Returns the Result object.
        """
        try:
            async with self.get_connection() as conn:
                async with conn.begin():
                    result = await conn.execute(text(sql_statement), parameters or {})
                return result
        except SQLAlchemyError as e:
            logger.error(f"SQL execution failed: {e}")
            raise

    async def table_exists(
        self,
        table_name: str,
        schema: Optional[str] = None
    ) -> bool:
        """
        Check if a table exists in the database.
        """
        if self._engine is None:
            return False
        try:
            # Use the underlying sync engine for inspection
            inspector = inspect(self._engine.sync_engine)
            return inspector.has_table(table_name, schema=schema)
        except SQLAlchemyError as e:
            logger.error(f"Error checking table existence: {e}")
            return False

    async def get_health_info(self) -> Dict[str, Any]:
        """
        Get database health information for health checks.
        """
        health_info = {
            "status": "unknown",
            "connection_pool": {},
            "connectivity": False,
            "error": None
        }

        try:
            if self._engine is None:
                health_info.update(status="not_initialized",
                                   error="Database manager not initialized")
                return health_info

            async with self.get_connection() as conn:
                await conn.execute(text("SELECT 1"))
                health_info["connectivity"] = True

            pool = self._engine.pool
            health_info["connection_pool"] = {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
                "status": pool.status()
            }
            health_info["status"] = "healthy"
        except Exception as e:
            health_info.update(status="unhealthy", error=str(e))
            logger.error(f"Database health check failed: {e}")

        return health_info

    async def shutdown(self) -> None:
        """
        Shutdown the database manager and cleanup resources.
        Call this during application shutdown.
        """
        logger.info("Shutting down database manager...")
        if self._engine:
            # Dispose the underlying pool
            self._engine.sync_engine.dispose()
            self._engine = None
        self._session_factory = None
        logger.info("Database manager shutdown complete")

    @property
    def engine(self) -> Optional[AsyncEngine]:
        """Get the AsyncEngine (for advanced usage)."""
        return self._engine

    @property
    def metadata(self) -> MetaData:
        """Get the SQLAlchemy metadata object."""
        return self._metadata


# Global database manager instance
db_manager = DatabaseManager()

# Convenience functions for common operations
def get_db_connection():
    """Convenience function to get a database connection."""
    return db_manager.get_connection()

def get_db_session():
    """Convenience function to get a database session."""
    return db_manager.get_session()

async def execute_sql(sql: str, params: Optional[Dict[str, Any]] = None):
    """Convenience function to execute SQL."""
    return await db_manager.execute_sql(sql, params)

async def table_exists(table_name: str, schema: Optional[str] = None) -> bool:
    """Convenience function to check if a table exists."""
    return await db_manager.table_exists(table_name, schema)