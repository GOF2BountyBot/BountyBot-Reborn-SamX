"""
Fully automatic database migration manager for BountyBot.

Wraps Alembic to provide zero-friction schema management. Developers only
need to modify SQLAlchemy models — migration generation and application
happen automatically.

Usage in production (via main.py startup)::

    migration_mgr = MigrationManager.from_async_url(db_manager._connection_string)
    migration_mgr.ensure_current()   # apply all pending migrations

Usage for developers (generate a new revision)::

    migration_mgr = MigrationManager.from_env()
    migration_mgr.auto_generate("add player reputation field")
"""

from __future__ import annotations

import io
import os

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from shared import bblogger
from sqlalchemy import create_engine, pool

flogger = bblogger.get_logger("migration-manager")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INI_PATH = os.path.join(os.path.dirname(__file__), "alembic.ini")


def _build_sync_url_from_env() -> str:
    """Build a synchronous (psycopg2-compatible) PostgreSQL URL from env vars.

    Uses the same defaults as the existing ``env.py`` and ``run_migration.py``
    helpers so that all callers share a single source of truth.
    """
    host = os.getenv("POSTGRES_HOST", "bounty_db")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "bountydb")
    user = os.getenv("POSTGRES_USER", "bounty")
    pw = os.getenv("POSTGRES_PASSWORD", "bounty")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def _async_to_sync_url(async_url: str) -> str:
    """Convert an asyncpg URL to a synchronous psycopg2 URL.

    Replaces the ``postgresql+asyncpg://`` scheme with ``postgresql://`` so
    that Alembic (which requires a synchronous DBAPI) can use it.
    """
    return async_url.replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# MigrationManager
# ---------------------------------------------------------------------------


class MigrationManager:
    """Central controller for all Alembic migration operations.

    Consolidates URL-building logic that was previously duplicated across
    ``env.py``, ``run_migration.py``, and ``main.py``.

    Parameters
    ----------
    sync_url:
        A synchronous (psycopg2-compatible) PostgreSQL connection URL.
        Must start with ``postgresql://`` — asyncpg URLs are **not** accepted
        here; use :meth:`from_async_url` to convert them automatically.
    """

    def __init__(self, sync_url: str) -> None:
        flogger.trace("__init__: Initializing MigrationManager")
        if sync_url.startswith("postgresql+asyncpg://"):
            flogger.error("__init__: Attempted to use asyncpg URL (postgresql+asyncpg://) instead of sync URL")
            raise ValueError(
                "MigrationManager requires a synchronous URL (postgresql://…). "
                "Use MigrationManager.from_async_url() to convert an asyncpg URL."
            )
        self._sync_url = sync_url
        flogger.trace("__init__: MigrationManager initialized successfully")

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> MigrationManager:
        """Build a :class:`MigrationManager` from environment variables.

        Reads ``POSTGRES_HOST``, ``POSTGRES_PORT``, ``POSTGRES_DB``,
        ``POSTGRES_USER``, and ``POSTGRES_PASSWORD``.  Falls back to the
        same defaults used elsewhere in the codebase.
        """
        flogger.debug("from_env: Building MigrationManager from environment variables")
        sync_url = _build_sync_url_from_env()
        # Extract password from URL for masking
        _user = os.getenv("POSTGRES_USER", "bounty")
        pw = os.getenv("POSTGRES_PASSWORD", "bounty")
        masked_url = sync_url.replace(pw, "***") if pw else sync_url
        flogger.debug(f"from_env: Connection URL: {masked_url}")
        return cls(sync_url)

    @classmethod
    def from_async_url(cls, async_url: str) -> MigrationManager:
        """Build a :class:`MigrationManager` from an asyncpg URL.

        Automatically strips the ``+asyncpg`` dialect specifier so that
        Alembic can use the resulting URL with its synchronous engine.

        Parameters
        ----------
        async_url:
            An asyncpg-flavoured URL such as
            ``postgresql+asyncpg://user:pw@host:5432/dbname``.
        """
        flogger.debug("from_async_url: Converting asyncpg URL to sync psycopg2 URL")
        # Mask the password in the URL for logging
        masked_async_url = async_url
        if "@" in async_url:
            # Extract and mask password
            scheme_and_creds, host_part = async_url.rsplit("@", 1)
            if ":" in scheme_and_creds:
                scheme_and_user = scheme_and_creds.rsplit(":", 1)[0]
                masked_async_url = f"{scheme_and_user}:***@{host_part}"
        flogger.debug(f"from_async_url: Input URL: {masked_async_url}")
        sync_url = _async_to_sync_url(async_url)
        masked_sync_url = sync_url
        if "@" in sync_url:
            scheme_and_creds, host_part = sync_url.rsplit("@", 1)
            if ":" in scheme_and_creds:
                scheme_and_user = scheme_and_creds.rsplit(":", 1)[0]
                masked_sync_url = f"{scheme_and_user}:***@{host_part}"
        flogger.debug(f"from_async_url: Converted to sync URL: {masked_sync_url}")
        return cls(sync_url)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_alembic_config(self) -> Config:
        """Build an :class:`alembic.config.Config` with the correct paths and URL.

        The URL is injected via :meth:`~alembic.config.Config.set_main_option`
        so that ``env.py`` will see it through ``config.get_main_option``.
        """
        flogger.trace("_get_alembic_config: Building Alembic configuration")
        cfg = Config(_INI_PATH)
        cfg.set_main_option("sqlalchemy.url", self._sync_url)
        flogger.trace(f"_get_alembic_config: Alembic config built with INI path: {_INI_PATH}")
        return cfg

    def _get_script_directory(self) -> ScriptDirectory:
        """Return an Alembic :class:`~alembic.script.ScriptDirectory` instance."""
        flogger.trace("_get_script_directory: Loading Alembic script directory")
        cfg = self._get_alembic_config()
        script_dir = ScriptDirectory.from_config(cfg)
        flogger.trace("_get_script_directory: Script directory loaded successfully")
        return script_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_current(self) -> None:
        """Apply all pending migrations (``alembic upgrade head``).

        Safe to call on every startup — if the database is already at the
        latest revision this is a no-op.
        """
        flogger.info("ensure_current: Checking for pending migrations...")
        try:
            current_rev = self.get_current_revision()
            head_rev = self.get_head_revision()
            if current_rev == head_rev:
                flogger.info(f"ensure_current: Database already at head revision: {head_rev}")
            else:
                flogger.info(f"ensure_current: Current revision: {current_rev}, head revision: {head_rev}")
                cfg = self._get_alembic_config()
                command.upgrade(cfg, "head")
                flogger.info("ensure_current: All pending migrations applied successfully")
        except Exception as e:
            flogger.error(f"ensure_current: Migration failed with error: {e}", exc_info=True)
            raise

    def auto_generate(self, message: str) -> None:
        """Generate a new auto-detected revision script.

        Runs ``alembic revision --autogenerate -m <message>``.  Alembic
        compares the current SQLAlchemy model metadata against the database
        schema and writes a new versioned migration file.

        Parameters
        ----------
        message:
            A short human-readable description for the migration (e.g.
            ``"add player reputation field"``).
        """
        flogger.info(f"auto_generate: Generating new revision with message: '{message}'")
        try:
            cfg = self._get_alembic_config()
            command.revision(cfg, message=message, autogenerate=True)
            flogger.info(f"auto_generate: Revision script generated successfully for: '{message}'")
        except Exception as e:
            flogger.error(f"auto_generate: Failed to generate revision for '{message}': {e}", exc_info=True)
            raise

    def detect_pending(self) -> list[str]:
        """Return the list of pending (unapplied) migration revision IDs.

        Compares the current database revision against the head of the
        migration history and returns any revisions that have not yet been
        applied.

        Returns
        -------
        list[str]
            Revision IDs that are pending.  An empty list means the database
            is already at *head*.
        """
        flogger.trace("detect_pending: Detecting pending migrations")
        try:
            current = self.get_current_revision()
            flogger.debug(f"detect_pending: Current database revision: {current}")
            script_dir = self._get_script_directory()

            pending: list[str] = []
            for rev in script_dir.walk_revisions():
                if current is None or rev.revision != current:
                    pending.append(rev.revision)
                else:
                    break  # Stop once we reach the current revision
            flogger.debug(f"detect_pending: Found {len(pending)} pending migration(s)")
            if pending:
                flogger.debug(f"detect_pending: Pending revisions: {pending}")
            return pending
        except Exception as e:
            flogger.error(f"detect_pending: Failed to detect pending migrations: {e}", exc_info=True)
            raise

    def get_current_revision(self) -> str | None:
        """Return the current database revision, or ``None`` if unversioned.

        Queries the ``alembic_version`` table in the database to find the
        revision currently stamped there.
        """
        flogger.trace("get_current_revision: Querying current database revision")
        engine = create_engine(self._sync_url, poolclass=pool.NullPool)
        try:
            with engine.connect() as conn:
                context = MigrationContext.configure(conn)
                current = context.get_current_revision()
                current_str = current if current else "None (database not versioned)"
                flogger.debug(f"get_current_revision: Current revision is {current_str}")
                return current
        except Exception as e:
            flogger.error(f"get_current_revision: Failed to query current revision: {e}", exc_info=True)
            raise
        finally:
            engine.dispose()

    def get_head_revision(self) -> str | None:
        """Return the latest (head) revision ID from the versions directory.

        Returns ``None`` if there are no migration scripts at all.
        """
        flogger.trace("get_head_revision: Retrieving head revision from script directory")
        try:
            script_dir = self._get_script_directory()
            heads = script_dir.get_heads()
            if not heads:
                flogger.debug("get_head_revision: No migration scripts found (no heads)")
                return None
            head = heads[0]
            flogger.debug(f"get_head_revision: Head revision is {head}")
            return head
        except Exception as e:
            flogger.error(f"get_head_revision: Failed to retrieve head revision: {e}", exc_info=True)
            raise

    def downgrade(self, target: str = "-1") -> None:
        """Roll back one or more migrations.

        Parameters
        ----------
        target:
            Alembic revision target.  Use ``"-1"`` (default) to roll back
            one step, ``"base"`` to roll all the way back, or a specific
            revision ID.
        """
        flogger.warning(f"downgrade: Rolling back migrations to target: {target} (DOWNGRADE is risky!)")
        try:
            cfg = self._get_alembic_config()
            command.downgrade(cfg, target)
            flogger.info(f"downgrade: Successfully downgraded to target: {target}")
        except Exception as e:
            flogger.error(f"downgrade: Downgrade to '{target}' failed: {e}", exc_info=True)
            raise

    def history(self) -> list[str]:
        """Return a human-readable list of migration history lines.

        Each entry is a formatted string as Alembic would print to the
        terminal (e.g. ``"abc123 -> def456 (head), add player reputation"``).

        Returns
        -------
        list[str]
            One entry per revision in the history (newest first).
        """
        flogger.trace("history: Retrieving migration history")
        try:
            cfg = self._get_alembic_config()
            buf = io.StringIO()

            # Temporarily redirect Alembic output to our buffer.
            cfg.stdout = buf
            command.history(cfg)
            output = buf.getvalue()

            lines = [line for line in output.splitlines() if line.strip()]
            flogger.debug(f"history: Retrieved {len(lines)} history line(s)")
            if lines:
                flogger.debug(f"history: Most recent migration: {lines[0] if lines else 'None'}")
            return lines
        except Exception as e:
            flogger.error(f"history: Failed to retrieve migration history: {e}", exc_info=True)
            raise
