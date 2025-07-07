import sys
import os
from logging.config import fileConfig

from sqlalchemy import pool
from alembic import context

# Add project root (bot-core/src) to sys.path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from persist.database.manager import db_manager
# from persist.models.base import Base
from persist.models import *

import shared.bblogger as bblogger

flogger = bblogger.get_logger("bot-alembic-env")

# Alembic Config object, provides access to values in alembic.ini
config = context.config

# Configure logging from alembic.ini config file
fileConfig(config.config_file_name)

# Set target metadata for 'autogenerate' support
target_metadata = Base.metadata

def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    flogger.info("Running offline migration...")
    url = str(db_manager._connection_string)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""
    flogger.info("Running live/online migration...")
    try:
        flogger.info("🗄️ Initializing database connection...")
        db_manager.initialize()
        
    except Exception as e:
        flogger.error(f"❌ Database initialization failed: {e}")
        flogger.error("🛑 Application startup aborted due to database issues")
        raise  # This will prevent the application from starting

    connectable = db_manager._engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # Optional: detect type changes on autogenerate
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()