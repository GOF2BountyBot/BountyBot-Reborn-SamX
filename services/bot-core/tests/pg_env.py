"""Postgres test-database resolution shared by the live-DB test modules.

The concurrency-locking and migration test modules run against a REAL Postgres
with the application schema at alembic head plus the import_data/ seed rows:

- Locally that is the dev stack (bountydev-db at the bountydev-net bridge IP —
  re-check via `sudo docker inspect bountydev-db` after a stack rebuild;
  host-published localhost:15432 is unreachable from this dev container).
- In CI (publish.yml) it is the job's postgres service container, which the
  workflow migrates to head and seeds before pytest runs, exporting the
  POSTGRES_* variables consumed here.

``pg_skip_reason()`` is the module-level guard: it reports why the database is
unusable (unreachable, or reachable but not migrated) so the consuming modules
skip with a precise reason instead of failing 54 tests one connection timeout
at a time. A bare reachability check is NOT enough — on a GitHub runner the
dev-stack fallback IP can coincidentally be another postgres with identical
credentials, so the guard also requires the migrated schema to be present.
"""

import os
from functools import cache

from sqlalchemy import create_engine, text

_HOST = os.environ.get("POSTGRES_HOST", "172.18.0.2")
_PORT = os.environ.get("POSTGRES_PORT", "5432")
_USER = os.environ.get("POSTGRES_USER", "bounty")
_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "bounty")
_DB = os.environ.get("POSTGRES_DB", "bountydb")

PG_ASYNC_URL = f"postgresql+asyncpg://{_USER}:{_PASSWORD}@{_HOST}:{_PORT}/{_DB}"
PG_SYNC_URL = f"postgresql+psycopg2://{_USER}:{_PASSWORD}@{_HOST}:{_PORT}/{_DB}"


@cache
def pg_skip_reason() -> str | None:
    """Return why the Postgres test DB is unusable, or None when it is ready.

    Cached so the seven consuming modules share a single connection attempt
    per pytest session.
    """
    try:
        engine = create_engine(PG_SYNC_URL, connect_args={"connect_timeout": 3})
        try:
            with engine.connect() as conn:
                migrated = conn.execute(text("SELECT to_regclass('public.players')")).scalar()
                at_head = conn.execute(
                    text("SELECT count(*) FROM pg_constraint WHERE conname = 'uq_player_inventories_player_item'")
                ).scalar()
            if migrated is None:
                return f"Postgres at {_HOST}:{_PORT} is reachable but has no migrated schema (players table missing)"
            if not at_head:
                return (
                    f"Postgres at {_HOST}:{_PORT} schema is NOT at alembic head "
                    "(uq_player_inventories_player_item missing — schema drift; re-apply migrations)"
                )
        finally:
            engine.dispose()
    except Exception as exc:
        return f"Postgres test DB unreachable at {_HOST}:{_PORT}: {exc.__class__.__name__}: {exc}"
    return None
