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

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from typing import Any

import orjson
from shared import bblogger
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

flogger = bblogger.get_logger("bot-database-manager")

# ---------------------------------------------------------------------------
# orjson codec for SQLAlchemy JSON/JSONB columns (P4-T2).
#
# OPT_NAIVE_UTC: naive datetimes (no tzinfo) are treated as UTC and serialized
# to RFC-3339 format ("YYYY-MM-DDTHH:MM:SS+00:00").  All JSON columns in this
# codebase store only string-keyed dicts — OPT_NON_STR_KEYS is intentionally
# absent (fail-fast on any future int-keyed write; see P4-T3 audit).
#
# json_serializer must return str (SQLAlchemy contract); orjson.dumps returns
# bytes, so .decode() is required.
# ---------------------------------------------------------------------------
_ORJSON_OPTS: int = orjson.OPT_NAIVE_UTC

_json_serializer = lambda o: orjson.dumps(o, option=_ORJSON_OPTS).decode()  # noqa: E731
_json_deserializer = orjson.loads


class DatabaseManager:
    """
    Central database management class for BountyBot.

    Handles async connection management, health checks, and provides a foundation
    for future repository pattern implementation.
    """

    def __init__(self):
        """Initialize the database manager with environment configuration."""
        self._engine: AsyncEngine | None = None
        self._session_factory: sessionmaker | None = None
        self._connection_string: str | None = None
        self._metadata = MetaData()
        self._load_config()

    # Pool sizing formula (single-worker rationale — Decision D10):
    #   pool_size + max_overflow >= (2 * AUTOCOMPLETE_WARM_CONCURRENCY) + live_headroom
    #   With AUTOCOMPLETE_WARM_CONCURRENCY default 16 → up to 32 in-flight warm GETs,
    #   and live_headroom >= 10, the minimum is 42.
    #   We run ONE uvicorn worker (P0-T1); there is ONE pool, not 4×(pool+overflow).
    #   Defaults: pool_size=40 / max_overflow=20 → total 60.
    #   Must stay safely under Postgres max_connections (100 on this deployment).
    #   Override via DB_POOL_SIZE / DB_MAX_OVERFLOW env vars.
    _POSTGRES_MAX_CONNECTIONS_FLOOR = 100  # Observed via SHOW max_connections; keep total well below.

    def _load_config(self) -> None:
        """Load database configuration from environment variables."""
        db_host = os.getenv("POSTGRES_HOST", "bounty_db")
        db_port = os.getenv("POSTGRES_PORT", "5432")
        db_name = os.getenv("POSTGRES_DB", "bountydb")
        db_user = os.getenv("POSTGRES_USER", "bounty")
        db_password = os.getenv("POSTGRES_PASSWORD", "bounty")

        pool_size = int(os.getenv("DB_POOL_SIZE", "40"))
        max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
        pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "3600"))
        echo_flag = os.getenv("DB_ECHO", "false").lower() == "true"

        total_pool = pool_size + max_overflow
        ceiling = self._POSTGRES_MAX_CONNECTIONS_FLOOR
        if total_pool >= ceiling:
            # Hard error: exceeding max_connections causes connection failures for all services.
            raise ValueError(
                f"DB pool too large: pool_size={pool_size} + max_overflow={max_overflow} = {total_pool} "
                f">= Postgres max_connections={ceiling}. "
                f"Reduce DB_POOL_SIZE or DB_MAX_OVERFLOW."
            )
        if total_pool > ceiling * 0.75:
            flogger.warning(
                f"DB pool approaching Postgres max_connections ceiling: "
                f"pool_size={pool_size} + max_overflow={max_overflow} = {total_pool} "
                f"(ceiling={ceiling}, used {total_pool / ceiling:.0%}). "
                f"Consider reducing DB_POOL_SIZE or DB_MAX_OVERFLOW."
            )
        flogger.info(
            f"DB pool sizing: pool_size={pool_size} + max_overflow={max_overflow} = {total_pool} "
            f"(Postgres max_connections ceiling={ceiling}, headroom={ceiling - total_pool})"
        )

        flogger.debug(
            f"Pool configuration: size={pool_size}, max_overflow={max_overflow}, "
            f"timeout={pool_timeout}s, recycle={pool_recycle}s, echo={echo_flag}"
        )

        # Use asyncpg dialect
        self._connection_string = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        self._pool_config = {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_timeout": pool_timeout,
            "pool_recycle": pool_recycle,
            "pool_pre_ping": True,
            "echo": echo_flag,
        }
        flogger.info(f"Database configuration loaded: {db_host}:{db_port}/{db_name}")

    async def initialize(self) -> None:
        """
        Initialize the async database engine and session factory.
        Call this during application startup.
        """
        if self._engine is not None:
            flogger.warning("Database manager already initialized")
            return

        try:
            flogger.info("Initializing async database connection...")
            flogger.debug("Creating AsyncEngine with asyncpg dialect and connection pool")
            self._engine = create_async_engine(
                self._connection_string,
                future=True,
                json_serializer=_json_serializer,
                json_deserializer=_json_deserializer,
                **self._pool_config,
            )
            flogger.debug("AsyncEngine created successfully")
            flogger.debug("Creating async_sessionmaker")
            self._session_factory = sessionmaker(bind=self._engine, class_=AsyncSession, expire_on_commit=False)
            flogger.debug("async_sessionmaker created successfully")
            await self._test_connection()
            flogger.info("Database manager initialized successfully")
        except Exception as e:
            flogger.error(f"Failed to initialize database manager: {e}")
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
                        flogger.info("Database connectivity test passed")
                        return
                    raise RuntimeError("Unexpected test result from database")
            except OperationalError as e:
                if attempt < max_retries - 1:
                    flogger.warning(f"DB connection attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    flogger.error("Database connection failed after retries")
                    raise
            except Exception:
                flogger.error("Unexpected error during DB connectivity test")
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
        flogger.debug("Acquiring connection from pool")
        async with self._engine.connect() as conn:
            flogger.debug("Connection acquired from pool")
            try:
                yield conn
            finally:
                flogger.debug("Releasing connection back to pool")

    @asynccontextmanager
    async def get_session(self) -> AsyncSession:
        """
        Async context manager for a session.
        Usage:
            async with db_manager.get_session() as session:
                await session.execute(...)
                await session.commit()

        Defense-in-depth (AC-7, B.34 remediation): on clean exit, any
        pending transaction is committed automatically. This is the third
        layer of defense against the B.34 silent-rollback class:

        - Layer 1 (static): tests/test_transaction_discipline.py — fails
          CI when a route calls a flush-only service method without
          wrapping or committing.
        - Layer 2 (runtime): @requires_transaction on choke-point methods.
        - Layer 3 (this method): even if a caller forgets to wrap or
          commit, work is preserved at the session boundary instead of
          being silently rolled back on clean exit.

        Behaviour:
          - On exception: rollback (existing behaviour, unchanged).
          - On clean exit with active transaction: commit.
          - On clean exit without active transaction (e.g. caller already
            committed via db.commit() or via async with db.begin()): no-op.

        Read-only callers that mutated ORM instances and intentionally do
        not want them flushed must call ``await session.rollback()`` before
        exiting. No such callsites exist in bot-core as of this commit
        (callsite audit performed for the AC-7 commit; documented in
        commit message).
        """
        if self._session_factory is None:
            raise RuntimeError("Database manager not initialized. Call initialize() first.")
        flogger.debug("Creating new database session")
        async with self._session_factory() as session:
            flogger.debug("Database session created")
            try:
                yield session
            except Exception as e:
                flogger.error(f"Session error — rolling back transaction: {type(e).__name__}")
                await session.rollback()
                raise
            else:
                # AC-7: clean exit auto-commit. If the caller already
                # committed (db.commit() or db.begin() block exit), the
                # session is no longer in a transaction and this is a
                # no-op. If the caller forgot, this preserves their work.
                #
                # ─── AC-7 CALLSITE AUDIT (recorded for re-verification) ───
                # When AC-7 landed (commit 1a6d63e), an exhaustive grep was
                # performed for every `async with get_db_session() as ...`
                # and `async with db_manager.get_session() as ...` in
                # services/bot-core/src/. Empirical finding: ZERO callers
                # load mutable ORM instances and intentionally exit without
                # committing. Every read-only path is GET-style (list_all,
                # get_by_id, count_*, etc.) where the implicit no-commit
                # close was incidental — auto-commit is a no-op for those
                # paths because no DML was issued.
                #
                # Re-run criteria — the audit MUST be re-performed if ANY
                # of the following becomes true:
                #   (a) A new `async with get_db_session() as ...` block
                #       is added that performs ORM attribute mutation
                #       (e.g. `player.credits = ...`) but intentionally
                #       relies on session-close-discards-changes semantics
                #       to avoid persisting them.
                #   (b) A new pattern is introduced that loads ORM
                #       instances, mutates them for in-memory computation,
                #       and expects the changes NOT to persist.
                #   (c) `expire_on_commit` is changed from False (currently
                #       False at line 92) — that would change the
                #       attribute-refresh semantics of this commit.
                #
                # If any of (a)–(c) is true, re-run the audit and add an
                # explicit `await session.rollback()` before context exit
                # at the affected callsite. The contract is: this method
                # commits pending work; callers that want discard-on-close
                # MUST opt out explicitly.
                # ──────────────────────────────────────────────────────────
                try:
                    if session.in_transaction():
                        await session.commit()
                        flogger.debug("Session auto-committed pending transaction on clean exit")
                except Exception as commit_exc:
                    # If the auto-commit itself fails, ensure a clean
                    # rollback so the session can be returned to the pool.
                    flogger.error(f"Auto-commit on clean exit failed — rolling back: {type(commit_exc).__name__}")
                    with contextlib.suppress(Exception):
                        await session.rollback()
                    raise
            finally:
                flogger.debug("Session released")

    async def execute_sql(self, sql_statement: str, parameters: dict[str, Any] | None = None) -> Any:
        """
        Execute a raw SQL statement in a transaction.
        Returns the Result object.
        """
        try:
            flogger.debug(f"Executing SQL statement: {sql_statement[:100]}...")
            async with self.get_connection() as conn:
                async with conn.begin():
                    flogger.debug("SQL transaction started")
                    result = await conn.execute(text(sql_statement), parameters or {})
                flogger.debug("SQL transaction committed successfully")
                return result
        except SQLAlchemyError as e:
            flogger.error(f"SQL execution failed: {e}")
            raise

    async def table_exists(self, table_name: str, schema: str | None = None) -> bool:
        """
        Check if a table exists in the database.
        """
        if self._engine is None:
            flogger.debug(f"Engine not initialized, cannot check if table '{table_name}' exists")
            return False
        try:
            flogger.debug(f"Checking if table '{table_name}' exists in schema '{schema}'")
            # The inspection API is synchronous — it must run through the async
            # connection's greenlet bridge (TRUEUP-P5: inspecting .sync_engine
            # directly raises MissingGreenlet, which the except below swallowed
            # into an unconditional False).
            async with self._engine.connect() as conn:
                exists = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(table_name, schema=schema))
            flogger.debug(f"Table '{table_name}' exists: {exists}")
            return exists
        except SQLAlchemyError as e:
            flogger.error(f"Error checking table existence for '{table_name}': {e}")
            return False

    async def get_health_info(self) -> dict[str, Any]:
        """
        Get database health information for health checks.
        """
        health_info = {"status": "unknown", "connection_pool": {}, "connectivity": False, "error": None}

        try:
            if self._engine is None:
                flogger.debug("Health check: engine not initialized")
                health_info.update(status="not_initialized", error="Database manager not initialized")
                return health_info

            flogger.debug("Health check: testing connectivity")
            async with self.get_connection() as conn:
                await conn.execute(text("SELECT 1"))
                health_info["connectivity"] = True
            flogger.debug("Health check: connectivity test passed")

            pool = self._engine.pool
            pool_stats = {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
                "status": pool.status(),
            }
            health_info["connection_pool"] = pool_stats
            flogger.debug(
                f"Health check: pool stats — size={pool_stats['size']}, "
                f"checked_in={pool_stats['checked_in']}, checked_out={pool_stats['checked_out']}, "
                f"overflow={pool_stats['overflow']}"
            )
            health_info["status"] = "healthy"
        except Exception as e:  # pylint: disable=broad-exception-caught
            health_info.update(status="unhealthy", error=str(e))
            flogger.error(f"Database health check failed: {e}")

        return health_info

    async def shutdown(self) -> None:
        """
        Shutdown the database manager and cleanup resources.
        Call this during application shutdown.
        """
        flogger.info("Shutting down database manager...")
        if self._engine:
            flogger.debug("Disposing connection pool and underlying sync engine")
            # Dispose the underlying pool
            self._engine.sync_engine.dispose()
            flogger.debug("Connection pool disposed")
            self._engine = None
        self._session_factory = None
        flogger.debug("Session factory cleared")
        flogger.info("Database manager shutdown complete")

    @property
    def engine(self) -> AsyncEngine | None:
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


async def execute_sql(sql: str, params: dict[str, Any] | None = None):
    """Convenience function to execute SQL."""
    return await db_manager.execute_sql(sql, params)


async def table_exists(table_name: str, schema: str | None = None) -> bool:
    """Convenience function to check if a table exists."""
    return await db_manager.table_exists(table_name, schema)
