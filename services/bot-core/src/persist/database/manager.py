"""
Database initialization and connection management for BountyBot.

This module provides robust database connectivity with retry logic,
circuit breaker pattern, and comprehensive health monitoring.
"""

import os
import asyncio
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import shared.logging as logging
from persist.schemas.schema_manager import SchemaManager
from persist.database.circuit_breaker import CircuitBreaker

logger = logging.get_logger("bot-db-manager")

# Base class for all models
Base = declarative_base()

class DatabaseManager:
    """
    Manages database connections with resilience features including:
    - Connection retry logic with exponential backoff
    - Circuit breaker pattern for fault tolerance
    - Comprehensive health monitoring
    - Graceful shutdown handling
    """

    _instance: Optional['DatabaseManager'] = None
    _lock = asyncio.Lock()

    def __new__(cls) -> 'DatabaseManager':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.engine = None
            self.async_engine = None
            self.Session = None
            self.AsyncSessionLocal = None
            self.schema_manager = SchemaManager()
            self.circuit_breaker = CircuitBreaker(
                failure_threshold=5,
                recovery_timeout=30,
                expected_exception=SQLAlchemyError
            )
            self._health_status = {"status": "initializing"}
            self._last_health_check = None
            self._shutdown_event = asyncio.Event()
            self._initialized = True

    def _get_database_config(self) -> Dict[str, str]:
        """Get database configuration from environment variables."""
        return {
            "user": os.getenv("POSTGRES_USER", "bounty"),
            "password": os.getenv("POSTGRES_PASSWORD", "bounty"),
            "host": os.getenv("POSTGRES_HOST", "db"),
            "port": os.getenv("POSTGRES_PORT", "5432"),
            "database": os.getenv("POSTGRES_DB", "bountydb")
        }

    def get_database_url(self) -> str:
        """Constructs the database URL from environment variables."""
        config = self._get_database_config()
        return f"postgresql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}"

    def get_async_database_url(self) -> str:
        """Constructs the async database URL using asyncpg driver."""
        config = self._get_database_config()
        return f"postgresql+asyncpg://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}"

    async def initialize(self, max_retries: int = 5) -> bool:
        """
        Initialize database connections with retry logic.
        
        Args:
            max_retries: Maximum number of connection attempts
            
        Returns:
            bool: True if initialization successful, False otherwise
        """
        async with self._lock:
            if self.engine is not None:
                logger.info("Database already initialized")
                return True

            logger.info("Initializing database connections...")
            
            for attempt in range(max_retries):
                try:
                    await self._create_engines()
                    await self._test_connection()
                    await self.schema_manager.initialize(self.engine)
                    
                    # Start health monitoring
                    asyncio.create_task(self._health_monitor())
                    
                    logger.info("Database initialization completed successfully")
                    self._health_status = {"status": "healthy", "initialized_at": datetime.utcnow().isoformat()}
                    return True
                    
                except Exception as e:
                    wait_time = min(2 ** attempt, 30)  # Exponential backoff, max 30s
                    logger.warning(f"Database initialization attempt {attempt + 1} failed: {e}")
                    
                    if attempt < max_retries - 1:
                        logger.info(f"Retrying in {wait_time} seconds...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error("All database initialization attempts failed")
                        self._health_status = {"status": "failed", "error": str(e)}
                        return False

    async def _create_engines(self) -> None:
        """Create database engines with optimized settings."""
        sync_url = self.get_database_url()
        async_url = self.get_async_database_url()
        
        # Enhanced connection pool settings
        pool_settings = {
            "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
            "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
            "pool_pre_ping": True,
            "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "3600")),
            "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
            "echo": os.getenv("DB_ECHO", "false").lower() == "true"
        }
        
        self.engine = create_engine(sync_url, **pool_settings)
        self.async_engine = create_async_engine(async_url, **pool_settings)
        
        # Create session factories
        self.Session = sessionmaker(bind=self.engine)
        self.AsyncSessionLocal = async_sessionmaker(
            bind=self.async_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    async def _test_connection(self) -> None:
        """Test database connectivity."""
        try:
            # Test sync connection
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                assert result.fetchone()[0] == 1

            # Test async connection
            async with self.async_engine.begin() as conn:
                result = await conn.execute(text("SELECT 1"))
                assert result.fetchone()[0] == 1

            logger.info("Database connectivity test passed")
        except Exception as e:
            logger.error(f"Database connectivity test failed: {e}")
            raise


    async def _health_monitor(self) -> None:
        """Background task to monitor database health."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                health_info = await self.get_db_health()
                self._health_status = health_info
                self._last_health_check = datetime.utcnow()
                
                if health_info["status"] != "healthy":
                    logger.warning(f"Database health check failed: {health_info}")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error: {e}")
                self._health_status = {"status": "unhealthy", "error": str(e)}

    async def get_db_health(self) -> Dict[str, Any]:
        """
        Comprehensive database health check with circuit breaker.
        
        Returns:
            Dict containing detailed database health information
        """
        try:
            return await self.circuit_breaker.call(self._perform_health_check)
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "circuit_breaker_state": self.circuit_breaker.state,
                "timestamp": datetime.utcnow().isoformat()
            }

    async def _perform_health_check(self) -> Dict[str, Any]:
        """Perform the actual health check operations."""
        async with asyncio.timeout(5):
            async with self.async_engine.begin() as conn:
                # Basic connectivity
                result = await conn.execute(text("SELECT 1"))
                assert (result.fetchone())[0] == 1

                # Database information
                db_info = await conn.execute(text("""
                    SELECT version() as pg_version, 
                           current_database() as database_name,
                           current_user as user_name
                """))
                info = db_info.fetchone()

                # Schema version
                schema_version = await self.schema_manager.get_current_version(conn)

                # Connection pool stats
                pool = self.async_engine.pool
                
                return {
                    "status": "healthy",
                    "database_name": info.database_name,
                    "user": info.user_name,
                    "postgresql_version": info.pg_version.split(" ")[1] if info.pg_version else "unknown",
                    "schema_version": schema_version,
                    "pool_stats": {
                        "size": pool.size(),
                        "checked_in": pool.checkedin(),
                        "checked_out": pool.checkedout(),
                        "overflow": pool.overflow()
                    },
                    "circuit_breaker_state": self.circuit_breaker.state,
                    "timestamp": datetime.utcnow().isoformat()
                }

    @asynccontextmanager
    async def get_session(self):
        """
        Resilient async context manager for database sessions.
        Includes automatic retry logic and circuit breaker protection.
        """
        if not self.AsyncSessionLocal:
            raise RuntimeError("Database not initialized")

        async def _get_session_with_retry():
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    return await self.circuit_breaker.call(self._create_session)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(0.1 * (2 ** attempt))  # Exponential backoff
            
        session = await _get_session_with_retry()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def _create_session(self):
        """Create a new database session."""
        return self.AsyncSessionLocal()

    async def close(self) -> None:
        """Graceful shutdown with proper cleanup."""
        logger.info("Initiating database manager shutdown...")
        
        # Signal health monitor to stop
        self._shutdown_event.set()
        
        try:
            # Wait for ongoing operations to complete (with timeout)
            await asyncio.wait_for(self._wait_for_active_sessions(), timeout=30)
            
            if self.async_engine:
                await self.async_engine.dispose()
                logger.info("Async database engine disposed")
                
            if self.engine:
                self.engine.dispose()
                logger.info("Sync database engine disposed")
                
            logger.info("Database manager shutdown completed")
            
        except asyncio.TimeoutError:
            logger.warning("Database shutdown timeout - forcing close")
        except Exception as e:
            logger.error(f"Error during database cleanup: {e}")

    async def _wait_for_active_sessions(self) -> None:
        """Wait for active database sessions to complete."""
        if self.async_engine:
            pool = self.async_engine.pool
            while pool.checkedout() > 0:
                logger.info(f"Waiting for {pool.checkedout()} active sessions to complete...")
                await asyncio.sleep(1)

# Global database manager instance
db_manager = DatabaseManager()

# FastAPI dependency
async def get_db_session():
    """FastAPI dependency for getting database session."""
    async with db_manager.get_session() as session:
        yield session
