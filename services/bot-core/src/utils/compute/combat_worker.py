"""DB-free, picklable, process-pool worker leaf for combat resolution.

IMPORT DISCIPLINE — READ BEFORE ADDING ANY IMPORT
==================================================
This module is a *leaf* designed to be imported cheaply by a forkserver child
process.  It MUST NOT import any of the following at module top-level (or
transitively via module-level imports):

  - ``fastapi``, ``main`` (the FastAPI app / create_app)          → starts ASGI machinery
  - ``sqlalchemy``, ``asyncpg``                                    → opens DB engine pools
  - ``persist.*``  (models, database manager, repositories)        → ORM / engine setup
  - ``services.*`` (any service module)                            → pulls in ORM transitively
  - ``utils.executors.*``                                          → pulls in services/ORM
  - any module that calls ``db_manager.initialize()`` at import    → connects to DB

When a forkserver worker imports this file it does so in a *fresh* interpreter
with *no* inherited file-descriptors.  Any module that opens a DB connection or
binds a socket at import time would either fail or silently exhaust the pool.

Allowed top-level imports: pure stdlib only.  In P2 the DB-free
``services.combat_models`` / ``services.combat_resolver`` leaf (which itself
must obey the same import discipline) will also be allowed here.

P2 PLACEHOLDER
==============
``run_fight`` will be added here in P2.  Signature (provisional)::

    def run_fight(payload: dict) -> dict:
        ...

It will be a plain (non-async) callable so it can be submitted to
``concurrent.futures.ProcessPoolExecutor`` without pickling coroutines.
"""

# ---------------------------------------------------------------------------
# Stdlib-only imports — keep this list minimal and import-discipline-clean.
# ---------------------------------------------------------------------------
import os as _os

# ---------------------------------------------------------------------------
# P2 PLACEHOLDER: run_fight implementation goes here.
# ---------------------------------------------------------------------------
# def run_fight(payload: dict) -> dict:
#     """Execute a single combat simulation in an isolated worker process.
#
#     This function will be implemented in P2.  It must remain DB-free and
#     must not import ORM / service modules at call time either (use only the
#     DB-free combat_models / resolver leaf imported at module level above).
#
#     Parameters
#     ----------
#     payload:
#         A plain dict produced by the caller describing the fight (ship
#         loadouts, resolver parameters, etc.).
#
#     Returns
#     -------
#     dict
#         A plain dict of fight results, picklable for transport back to the
#         parent process.
#     """
#     raise NotImplementedError("run_fight is implemented in P2")

__all__: list[str] = []
