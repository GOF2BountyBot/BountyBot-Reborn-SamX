"""Helper to run Alembic commands from the bot-core src directory.

All database URL construction is delegated to :class:`MigrationManager`,
which is the single source of truth for sync URL building.

Usage (from inside the container, with venv active)::

    cd /app/src
    python -m persist.database.run_migration upgrade
    python -m persist.database.run_migration downgrade --target -1
    python -m persist.database.run_migration revision -m "add new column"
    python -m persist.database.run_migration current
    python -m persist.database.run_migration history
"""

import os
import sys

# Ensure the src directory is on sys.path so that `persist.*` resolves correctly
# when this module is invoked directly (e.g. `python run_migration.py`) rather
# than as a package (e.g. `python -m persist.database.run_migration`).
_src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _src_dir)

# Also add the services/ directory so that `shared` is importable when running
# outside of Docker (e.g. in CI).  Inside Docker the Dockerfile copies
# services/shared/ directly into src/, so this path is a harmless no-op there.
_services_dir = os.path.abspath(os.path.join(_src_dir, "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

from alembic import command

from persist.database.migration_manager import MigrationManager

# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------


def get_alembic_config():
    """Return an Alembic Config built from environment variables.

    .. deprecated::
        Prefer :class:`~persist.database.migration_manager.MigrationManager`
        directly.  This function is retained for backward compatibility only
        (e.g. Dockerfile entrypoint scripts that might call it explicitly).
    """
    return MigrationManager.from_env()._get_alembic_config()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="BountyBot Alembic migration runner",
    )
    parser.add_argument(
        "action",
        choices=["upgrade", "downgrade", "revision", "current", "history"],
        help="Alembic command to run",
    )
    parser.add_argument(
        "--message",
        "-m",
        help="Migration message (used with 'revision')",
    )
    parser.add_argument(
        "--target",
        default="head",
        help="Target revision (default: head). Use '-1' to downgrade one step.",
    )
    args = parser.parse_args()

    mgr = MigrationManager.from_env()

    if args.action == "upgrade":
        mgr.ensure_current() if args.target == "head" else command.upgrade(mgr._get_alembic_config(), args.target)
    elif args.action == "downgrade":
        mgr.downgrade(args.target)
    elif args.action == "revision":
        mgr.auto_generate(args.message or "")
    elif args.action == "current":
        command.current(mgr._get_alembic_config())
    elif args.action == "history":
        lines = mgr.history()
        for line in lines:
            print(line)
