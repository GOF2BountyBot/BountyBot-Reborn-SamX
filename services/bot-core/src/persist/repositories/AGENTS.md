# AGENTS.md - persist/repositories

Data access layer for bot-core. All 19 repository classes live here.

---

## Repository Pattern Overview

All repositories implement the `IRepository[T]` abstract protocol from `persist/interfaces/repository_interface.py`. Most extend `GenericRepository[T]`, which provides default implementations. Some (like `PlayerRepository`, `BountyRepository`) extend `IRepository[T]` directly with custom logic.

```
IRepository[T]  (abstract, in persist/interfaces/)
└── GenericRepository[T]  (default implementations)
    └── ShipRepository, ModuleRepository, CriminalRepository, ...

IRepository[T]  (direct implementation)
└── PlayerRepository, BountyRepository, UserRepository, ...
```

---

## IRepository[T] Interface

Defined in `persist/interfaces/repository_interface.py`:

```python
class IRepository(ABC, Generic[T]):
    async def get_by_id(self, db: AsyncSession, obj_id: int) -> T | None: ...
    async def get_by_name(self, db: AsyncSession, name: str) -> T | None: ...
    async def list_all(self, db: AsyncSession) -> list[T]: ...
    async def add(self, db: AsyncSession, obj: T) -> T: ...
    async def create_or_update(self, db: AsyncSession, raw: dict) -> T: ...
    async def remove(self, db: AsyncSession, obj: T) -> None: ...
```

---

## GenericRepository[T] — Default Implementations

`generic_repository.py` provides ready-to-use implementations:

| Method | Implementation |
|---|---|
| `add(db, obj)` | `db.add(obj)` → `commit()` → `refresh(obj)` → return |
| `get_by_id(db, id)` | `db.get(model, id)` |
| `get_by_name(db, name)` | `select(model).filter_by(name=name)` |
| `get_by_alias(db, alias)` | `select(model).where(model.aliases.any(alias))` — PostgreSQL ARRAY contains |
| `list_all(db)` | `select(model)` → `scalars().all()` |
| `remove(db, obj)` | `db.delete(obj)` → `commit()` |
| `create_or_update(db, raw)` | **Not implemented** — subclasses must override |

---

## Error Handling Pattern

Every write operation (add, update, remove, create_or_update) must follow this pattern:

```python
async def add(self, db: AsyncSession, obj: MyModel) -> MyModel:
    try:
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        flogger.info(f"Added {obj.id}")
        return obj
    except Exception as e:
        flogger.error(f"Error adding: {e}")
        await db.rollback()
        raise
```

**Key rules**:
- Always `await db.rollback()` in the `except` block for write operations
- Always `raise` after logging — never swallow repository exceptions
- Log with entity IDs for traceability (`flogger.error(f"Error getting player by ID {obj_id}: {e}")`)
- Read operations (`get_by_id`, `list_all`) do not need rollback but should still catch/log/raise

---

## Session Management

Repositories do **not** own sessions. Sessions are always passed in from the caller (router or executor):

```python
# In a router:
async with get_db_session() as db:
    player = await player_repo.get_by_id(db, player_id)

# In an executor:
async with db_manager.get_session() as db:
    bounties = await bounty_repo.get_active_by_guild(db, guild_id)
```

`get_db_session()` is a FastAPI dependency alias for `db_manager.get_session()`.

---

## Instantiation Pattern

Repositories are instantiated in service `__init__()` methods with no arguments:

```python
class PlayerService:
    def __init__(self):
        self.player_repo = PlayerRepository()
        self.user_repo = UserRepository()
        self.config_repo = ConfigRepository()
```

This is constructor injection. Repositories hold no state other than the model class reference (in `GenericRepository._model`).

---

## All 19 Repositories

| File | Class | Model | Key Custom Methods |
|---|---|---|---|
| `generic_repository.py` | `GenericRepository[T]` | Generic | Base: add, get_by_id, get_by_name, get_by_alias, list_all, remove |
| `bounty_repository.py` | `BountyRepository` | `Bounty` | get_active_by_guild, get_active_by_guild_and_division, count_active_by_guild_and_division, create, update, delete |
| `config_repository.py` | `ConfigRepository` | `GuildConfig` | get_by_guild_id, create_or_get_default |
| `criminal_repository.py` | `CriminalRepository` | `Criminal` | get_by_faction, get_by_tech_level_range |
| `discord_message_repository.py` | `DiscordMessageRepository` | `DiscordMessage` | get_by_guild_and_type, delete_by_guild_and_type |
| `duel_repository.py` | `DuelRepository` | `DuelRequest` | get_pending_by_guild, get_by_challenger_and_target, expire_old_duels |
| `inventory_repository.py` | `InventoryRepository` | `PlayerInventory` | get_by_player, get_player_item, get_equipped_items, update_quantity, remove_player_item |
| `item_repository.py` | `ItemRepository` | `Item` | get_by_type |
| `module_repository.py` | `ModuleRepository` | `Module` | get_by_tech_level (subtype queries use `Item.type` STI discriminator; there is no `module_type` column and no `get_by_module_type` method) |
| `player_repository.py` | `PlayerRepository` | `Player` | get_by_user_and_guild, get_players_by_guild, get_players_by_user, update_credits (with FOR UPDATE lock variant), update_xp, update_tier, update_active_ship, count |
| `player_ship_repository.py` | `PlayerShipRepository` | `PlayerShip` | get_by_player, get_by_player_and_ship, update_loadout |
| `primary_weapon_repository.py` | `PrimaryWeaponRepository` | `PrimaryWeapon` | get_by_tech_level, list_by_tech_range |
| `secondary_weapon_repository.py` | `SecondaryWeaponRepository` | `SecondaryWeapon` | get_by_tech_level, list_by_tech_range |
| `ship_repository.py` | `ShipRepository` | `Ship` | get_by_manufacturer, get_skinnable, get_by_value_range |
| `shop_repository.py` | `ShopRepository` | `GuildShop` | get_by_guild, clear_guild_shop, get_item_by_guild_and_name |
| `system_repository.py` | `SystemRepository` | `System` | get_connected_systems, get_all_with_connections |
| `turret_weapon_repository.py` | `TurretWeaponRepository` | `TurretWeapon` | get_by_tech_level |
| `user_repository.py` | `UserRepository` | `User` | get_by_discord_id, get_or_create_user |
| `weapon_repository.py` | `WeaponRepository` | `Weapon` | get_by_tech_level (base weapon queries) |

---

## Special Patterns

### get_by_id_for_update (PlayerRepository)

Used when reading-then-modifying credits to prevent TOCTOU race conditions:

```python
async def get_by_id_for_update(self, db: AsyncSession, obj_id: int) -> Player | None:
    result = await db.execute(
        select(Player).where(Player.id == obj_id).with_for_update()
    )
    return result.scalars().first()
```

Use this inside an explicit `async with db.begin()` transaction block when the caller will later modify and commit.

### update_credits with commit=False

```python
await player_repo.update_credits(db, player_id, new_amount, commit=False)
```

Pass `commit=False` when the update is part of a larger transaction managed by the caller. The method will `flush()` instead of `commit()`.

### create_or_update for game data seeding

Game data repositories implement `create_or_update(db, raw: dict)` to support idempotent seeding from JSON files. The method:
1. Tries to fetch the existing record by name
2. If found: updates changed fields
3. If not found: creates a new record

---

## InventoryRepository Notes (post-A.36)

`get_inventory_summary(db, player_id)` returns a dict with **concrete type keys**:

```python
{
    "ship": int,
    "primary_weapon": int,
    "secondary_weapon": int,
    "turret_weapon": int,
    "module": int,
    "total_items": int,
}
```

Post-A.36, `player_inventories.item_type` stores only concrete types — generic aliases
(`"weapon"`, `"turret"`) are never persisted. The summary dict was updated to match
(DEF-A42-001 fix, 2026-04-22).

Callers that display a human-readable summary should aggregate concrete types into
display buckets on their side. The Discord cog uses these 4 display buckets:

| Display Bucket | Concrete Types Summed |
|---|---|
| Ships | `summary["ship"]` |
| Weapons | `summary["primary_weapon"] + summary["secondary_weapon"]` |
| Modules | `summary["module"]` |
| Turrets | `summary["turret_weapon"]` |

---

## How to Add a New Repository

1. **Create the file** `persist/repositories/<name>_repository.py`:

   ```python
   from shared import bblogger
   from sqlalchemy.ext.asyncio import AsyncSession
   from persist.models.my_model import MyModel
   from persist.repositories.generic_repository import GenericRepository

   flogger = bblogger.get_logger("my-model-repository")

   class MyModelRepository(GenericRepository[MyModel]):
       def __init__(self):
           super().__init__(MyModel)

       async def create_or_update(self, db: AsyncSession, raw: dict) -> MyModel:
           try:
               existing = await self.get_by_name(db, raw["name"])
               if existing:
                   for key, value in raw.items():
                       if hasattr(existing, key) and key != "id":
                           setattr(existing, key, value)
                   await db.commit()
                   await db.refresh(existing)
                   return existing
               else:
                   obj = MyModel(**raw)
                   return await self.add(db, obj)
           except Exception as e:
               flogger.error(f"Error upserting MyModel: {e}")
               await db.rollback()
               raise

       # Add domain-specific methods as needed
   ```

2. **No registration needed** — instantiate directly in service `__init__()` methods.

3. **Add tests** in `tests/repositories/test_<name>_repository.py`.

---

*Last updated: 2026-03-16*
