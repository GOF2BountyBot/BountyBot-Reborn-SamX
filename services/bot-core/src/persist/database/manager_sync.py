"""
Database connection and management for BountyBot.

This module provides database connectivity, connection management, and basic
database operations. It's designed to be a foundation for future repository
pattern implementation while keeping things simple for the current iteration.

Key Features:
- PostgreSQL connection management with connection pooling
- Environment-based configuration
- Traditional (non-async) database operations
- Health check support
- Future-ready for repository pattern integration
"""

import os
import time
from typing import Optional, Dict, Any
from contextlib import contextmanager
from sqlalchemy import create_engine, text, MetaData, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from sqlalchemy.orm import sessionmaker, Session
import shared.bblogger as bblogger

flogger = bblogger.get_logger("bot-database-manager")

class DatabaseManager:
    """
    Central database management class for BountyBot.
    
    Handles connection management, health checks, and provides a foundation
    for future repository pattern implementation.
    """
    
    def __init__(self):
        """Initialize the database manager with environment configuration."""
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._connection_string: Optional[str] = None
        self._metadata = MetaData()
        
        # Load configuration from environment
        self._load_config()
        
    def _load_config(self) -> None:
        """Load database configuration from environment variables."""
        # Database connection parameters
        db_host = os.getenv("POSTGRES_HOST", "bounty_db")  # Docker service name
        db_port = os.getenv("POSTGRES_PORT", "5432")
        db_name = os.getenv("POSTGRES_DB", "bountydb")
        db_user = os.getenv("POSTGRES_USER", "bounty")
        db_password = os.getenv("POSTGRES_PASSWORD", "bounty")
        
        # Connection pool settings
        pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "3600"))  # 1 hour
        
        # Build connection string
        self._connection_string = (
            f"postgresql+psycopg2://{db_user}:{db_password}@"
            f"{db_host}:{db_port}/{db_name}"
        )
        
        # Store pool configuration
        self._pool_config = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
            "pool_recycle": pool_recycle,
            "pool_pre_ping": True,  # Validates connections before use
            "echo": os.getenv("DB_ECHO", "false").lower() == "true"  # SQL logging
        }
        
        flogger.info(f"Database configuration loaded: {db_host}:{db_port}/{db_name}")
        
    def initialize(self) -> None:
        """
        Initialize the database connection and session factory.
        
        This method should be called during application startup.
        """
        if self._engine is not None:
            flogger.warning("Database manager already initialized")
            return
            
        try:
            flogger.info("Initializing database connection...")
            
            # Create the engine with connection pooling
            self._engine = create_engine(
                self._connection_string,
                **self._pool_config
            )
            
            # Create session factory
            self._session_factory = sessionmaker(
                bind=self._engine,
                expire_on_commit=False  # Keep objects accessible after commit
            )
            
            # Test the connection
            self._test_connection()
            
            flogger.info("Database manager initialized successfully")
            
        except Exception as e:
            flogger.error(f"Failed to initialize database manager: {e}")
            raise
            
    def _test_connection(self) -> None:
        """Test the database connection with retry logic."""
        max_retries = 5
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                with self.get_connection() as conn:
                    # Simple connectivity test
                    result = conn.execute(text("SELECT 1 as test"))
                    test_value = result.scalar()
                    
                    if test_value == 1:
                        flogger.info("Database connectivity test passed")
                        return
                    else:
                        raise RuntimeError("Unexpected test result from database")
                        
            except OperationalError as e:
                if attempt < max_retries - 1:
                    flogger.warning(
                        f"Database connection attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {retry_delay} seconds..."
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    flogger.error(f"Database connection failed after {max_retries} attempts")
                    raise
                    
    @contextmanager
    def get_connection(self):
        """
        Get a database connection using context manager.
        
        Usage:
            with db_manager.get_connection() as conn:
                result = conn.execute(text("SELECT * FROM users"))
        """
        if self._engine is None:
            raise RuntimeError("Database manager not initialized. Call initialize() first.")
            
        connection = self._engine.connect()
        try:
            yield connection
        finally:
            connection.close()
            
    @contextmanager
    def get_session(self) -> Session:
        """
        Get a database session using context manager.
        
        Usage:
            with db_manager.get_session() as session:
                users = session.query(User).all()
                session.commit()
        """
        if self._session_factory is None:
            raise RuntimeError("Database manager not initialized. Call initialize() first.")
            
        session = self._session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            
    def execute_sql(self, sql_statement: str, parameters: Optional[Dict[str, Any]] = None) -> Any:
        """
        Execute a raw SQL statement.
        
        Args:
            sql_statement: SQL statement to execute
            parameters: Optional parameters for the SQL statement
            
        Returns:
            Result of the SQL execution
        """
        try:
            with self.get_connection() as conn:
                if parameters:
                    result = conn.execute(text(sql_statement), parameters)
                else:
                    result = conn.execute(text(sql_statement))
                conn.commit()
                return result
                
        except SQLAlchemyError as e:
            flogger.error(f"SQL execution failed: {e}")
            raise
            
    def table_exists(self, table_name: str, schema: Optional[str] = None) -> bool:
        """
        Check if a table exists in the database.
        
        Args:
            table_name: Name of the table to check
            schema: Optional schema name (defaults to public schema)
            
        Returns:
            True if table exists, False otherwise
        """
        try:
            with self.get_connection() as conn:
                inspector = inspect(conn)
                return inspector.has_table(table_name, schema=schema)
                
        except SQLAlchemyError as e:
            flogger.error(f"Error checking table existence: {e}")
            return False
            
    def get_health_info(self) -> Dict[str, Any]:
        """
        Get database health information for health checks.
        
        Returns:
            Dictionary containing database health metrics
        """
        health_info = {
            "status": "unknown",
            "connection_pool": {},
            "connectivity": False,
            "error": None
        }
        
        try:
            if self._engine is None:
                health_info["status"] = "not_initialized"
                health_info["error"] = "Database manager not initialized"
                return health_info
                
            # Test basic connectivity
            with self.get_connection() as conn:
                conn.execute(text("SELECT 1"))
                health_info["connectivity"] = True
                
            # Get connection pool information
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
            health_info["status"] = "unhealthy"
            health_info["error"] = str(e)
            flogger.error(f"Database health check failed: {e}")
            
        return health_info
        
    def shutdown(self) -> None:
        """
        Shutdown the database manager and cleanup resources.
        
        This method should be called during application shutdown.
        """
        flogger.info("Shutting down database manager...")
        
        if self._engine:
            self._engine.dispose()
            self._engine = None
            
        self._session_factory = None
        flogger.info("Database manager shutdown complete")
        
    @property
    def engine(self) -> Optional[Engine]:
        """Get the SQLAlchemy engine (for advanced usage)."""
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

def execute_sql(sql: str, params: Optional[Dict[str, Any]] = None):
    """Convenience function to execute SQL."""
    return db_manager.execute_sql(sql, params)

def table_exists(table_name: str, schema: Optional[str] = None) -> bool:
    """Convenience function to check if table exists."""
    return db_manager.table_exists(table_name, schema)
