"""DB-free compute leaf package for process-pool workers.

This package is intentionally inert — no imports, no auto-importers.
Importing ``compute.combat_worker`` must never trigger utils/__init__
or any other module that pulls in SQLAlchemy, FastAPI, or the ORM stack.
"""
