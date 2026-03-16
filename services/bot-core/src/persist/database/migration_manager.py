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
from sqlalchemy import create_engine, pool

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
        if sync_url.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "MigrationManager requires a synchronous URL (postgresql://…). "
                "Use MigrationManager.from_async_url() to convert an asyncpg URL."
            )
        self._sync_url = sync_url

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
        return cls(_build_sync_url_from_env())

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
        return cls(_async_to_sync_url(async_url))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_alembic_config(self) -> Config:
        """Build an :class:`alembic.config.Config` with the correct paths and URL.

        The URL is injected via :meth:`~alembic.config.Config.set_main_option`
        so that ``env.py`` will see it through ``config.get_main_option``.
        """
        cfg = Config(_INI_PATH)
        cfg.set_main_option("sqlalchemy.url", self._sync_url)
        return cfg

    def _get_script_directory(self) -> ScriptDirectory:
        """Return an Alembic :class:`~alembic.script.ScriptDirectory` instance."""
        cfg = self._get_alembic_config()
        return ScriptDirectory.from_config(cfg)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_current(self) -> None:
        """Apply all pending migrations (``alembic upgrade head``).

        Safe to call on every startup — if the database is already at the
        latest revision this is a no-op.
        """
        cfg = self._get_alembic_config()
        command.upgrade(cfg, "head")

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
        cfg = self._get_alembic_config()
        command.revision(cfg, message=message, autogenerate=True)

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
        current = self.get_current_revision()
        script_dir = self._get_script_directory()

        pending: list[str] = []
        for rev in script_dir.walk_revisions():
            if current is None or rev.revision != current:
                pending.append(rev.revision)
            else:
                break  # Stop once we reach the current revision
        return pending

    def get_current_revision(self) -> str | None:
        """Return the current database revision, or ``None`` if unversioned.

        Queries the ``alembic_version`` table in the database to find the
        revision currently stamped there.
        """
        engine = create_engine(self._sync_url, poolclass=pool.NullPool)
        try:
            with engine.connect() as conn:
                context = MigrationContext.configure(conn)
                return context.get_current_revision()
        finally:
            engine.dispose()

    def get_head_revision(self) -> str | None:
        """Return the latest (head) revision ID from the versions directory.

        Returns ``None`` if there are no migration scripts at all.
        """
        script_dir = self._get_script_directory()
        heads = script_dir.get_heads()
        if not heads:
            return None
        return heads[0]

    def downgrade(self, target: str = "-1") -> None:
        """Roll back one or more migrations.

        Parameters
        ----------
        target:
            Alembic revision target.  Use ``"-1"`` (default) to roll back
            one step, ``"base"`` to roll all the way back, or a specific
            revision ID.
        """
        cfg = self._get_alembic_config()
        command.downgrade(cfg, target)

    def history(self) -> list[str]:
        """Return a human-readable list of migration history lines.

        Each entry is a formatted string as Alembic would print to the
        terminal (e.g. ``"abc123 -> def456 (head), add player reputation"``).

        Returns
        -------
        list[str]
            One entry per revision in the history (newest first).
        """
        cfg = self._get_alembic_config()
        buf = io.StringIO()

        # Temporarily redirect Alembic output to our buffer.
        cfg.stdout = buf
        command.history(cfg)
        output = buf.getvalue()

        lines = [line for line in output.splitlines() if line.strip()]
        return lines
