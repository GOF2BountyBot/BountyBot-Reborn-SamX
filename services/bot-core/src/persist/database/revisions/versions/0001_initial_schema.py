"""Initial schema — create all tables from current ORM metadata.

Revision ID: 0001
Revises: (none)
Create Date: 2026-03-15

FLATTENED 2026-04-22: Revisions 0002-0007 have been collapsed into this single
revision.  This migration now creates the complete end-state schema (including
player_ships.secondary_weapons JSON column added in A.38).  Because upgrade()
is driven by Base.metadata.sorted_tables it automatically includes all columns
present in the current ORM models — no separate ADD COLUMN steps needed.

REQUIRES FRESH DATABASE: If your alembic_version table contains a revision
other than None/empty, stamp it manually before startup:
    UPDATE alembic_version SET version_num = '0001';
Or wipe the database and let MigrationManager.ensure_current() apply this
migration from scratch.
"""

import os
import sys

from alembic import op

# Alembic revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

# ---------------------------------------------------------------------------
# Ensure the src directory is on sys.path before any app imports are attempted.
# env.py already does this, but the migration file may be loaded independently
# (e.g. when Alembic discovers revisions without running the full env.py path).
# ---------------------------------------------------------------------------
_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

# Import all models at module level so Alembic's autogenerate can detect them
# and so `Base.metadata.sorted_tables` is fully populated when upgrade() runs.
from persist.models.base import Base


def upgrade() -> None:
    """Create all ORM-defined tables that do not already exist.

    Using ``table.create(bind)`` (rather than ``Base.metadata.create_all``)
    lets us skip tables that were already created by a previous migration or
    by a manual DDL script, preventing duplicate-table errors on partial
    deployments.
    """
    from sqlalchemy import inspect as sa_inspect  # local import avoids re-export confusion

    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # sorted_tables respects FK dependencies (parents before children)
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            table.create(bind)


def downgrade() -> None:
    """Drop all ORM-defined tables in reverse dependency order."""
    bind = op.get_bind()

    # Drop in reverse topological order (children before parents)
    for table in reversed(Base.metadata.sorted_tables):
        table.drop(bind, checkfirst=True)
