# pylint: disable=no-member  # alembic.context uses dynamic attributes
import os
import sys

# MUST be before any app imports so that `persist.*` and `shared` are resolvable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from logging.config import fileConfig

from alembic import context

# Import all models so their metadata is registered with Base before autogenerate runs.
from persist.models import *  # noqa: F403  # pylint: disable=wildcard-import,unused-wildcard-import
from persist.models.base import Base
from sqlalchemy import engine_from_config, pool

# Alembic Config object — provides access to values in alembic.ini
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata target for 'autogenerate' support
target_metadata = Base.metadata


def get_sync_url() -> str:
    """Return a synchronous (psycopg2-compatible) PostgreSQL URL.

    Priority order:
    1. If ``MigrationManager`` (or any caller) already set ``sqlalchemy.url``
       on the Alembic config object, use that value — it is the single source
       of truth and avoids re-deriving the URL from env vars.
    2. Otherwise fall back to building the URL from env vars directly, which
       preserves backward-compatibility when this file is invoked standalone
       via the ``alembic`` CLI.
    """
    # Check whether the URL was pre-populated (e.g. by MigrationManager).
    pre_set = config.get_main_option("sqlalchemy.url")
    if pre_set:
        return pre_set

    # Fallback: build from environment variables.
    host = os.getenv("POSTGRES_HOST", "bounty_db")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "bountydb")
    user = os.getenv("POSTGRES_USER", "bounty")
    pw = os.getenv("POSTGRES_PASSWORD", "bounty")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Offline mode configures the context with just a URL and does not establish
    a real database connection.  Useful for generating SQL scripts.
    """
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Online mode establishes a real synchronous connection and applies
    migrations directly.  A NullPool is used because Alembic's migration
    commands are short-lived processes that do not benefit from connection
    pooling.
    """
    # Build the configuration section, injecting the URL only if it has not
    # already been set (e.g. by MigrationManager via set_main_option).
    configuration = config.get_section(config.config_ini_section, {})
    if not configuration.get("sqlalchemy.url"):
        configuration["sqlalchemy.url"] = get_sync_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,           # detect column-type changes during autogenerate
            compare_server_default=True,  # detect server_default changes during autogenerate
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
