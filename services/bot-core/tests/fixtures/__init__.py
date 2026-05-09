"""Shared test fixture helpers seeded from real game data.

Import from this package to access pre-built game object stubs:

    from fixtures.game_data import (
        get_seed_ships,
        get_seed_primary_weapons,
        get_seed_secondary_weapons,
        get_seed_turret_weapons,
        get_seed_modules,
        get_seed_criminals,
        get_seed_systems,
    )

Each function returns a list of ``types.SimpleNamespace`` objects whose
attributes mirror the corresponding SQLAlchemy model columns, populated
from the real JSON files in ``import_data/``.
"""

from fixtures.game_data import (
    get_seed_criminals,
    get_seed_modules,
    get_seed_primary_weapons,
    get_seed_secondary_weapons,
    get_seed_ships,
    get_seed_systems,
    get_seed_turret_weapons,
)

__all__ = [
    "get_seed_criminals",
    "get_seed_modules",
    "get_seed_primary_weapons",
    "get_seed_secondary_weapons",
    "get_seed_ships",
    "get_seed_systems",
    "get_seed_turret_weapons",
]
