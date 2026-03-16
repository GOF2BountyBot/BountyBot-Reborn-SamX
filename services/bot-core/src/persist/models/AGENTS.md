# AGENTS.md - persist/models

SQLAlchemy ORM models for bot-core. All 21 model classes live in this directory.

---

## Model Inheritance Hierarchy

```
Base  (persist/models/base.py — DeclarativeBase)
│
├── Item  [table: item]          ← STI root, discriminator: `type` column
│   ├── Module  [table: module]
│   └── Weapon  [table: weapon]  ← abstract STI intermediate
│       ├── PrimaryWeapon  [table: primary_weapon]
│       ├── SecondaryWeapon  [table: secondary_weapon]
│       └── TurretWeapon  [table: turret_weapon]
│
├── AdminAuditLog   [table: admin_audit_logs]
├── Bounty          [table: bounty]
├── Criminal        [table: criminal]
├── DiscordMessage  [table: discord_message]
├── DuelRequest     [table: duel_requests]
├── GuildConfig     [table: guild_configs]
├── GuildShop       [table: guild_shops]
├── Player          [table: players]
├── PlayerInventory [table: player_inventories]
├── PlayerShip      [table: player_ships]
├── SchemaVersion   [table: schema]
├── Ship            [table: ship]
├── System          [table: system]
└── User            [table: users]
```

---

## Base Class

```python
# base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

All models must inherit from `Base`. No shared columns are defined on `Base` itself — each model declares its own columns.

---

## Single-Table Inheritance (STI) Pattern

The `Item` / `Weapon` / weapon-leaf hierarchy uses SQLAlchemy's **joined-table inheritance**:

- `Item` is the root — all purchasable items share the `item` table columns: `id`, `name`, `aliases`, `built_in`, `emoji`, `icon`, `value`, `wiki`, `type`
- `Weapon` extends `Item` with its own `weapon` table: `id` (FK to item.id), `tech_level`, `extra_atts` (JSON)
- `PrimaryWeapon`, `SecondaryWeapon`, `TurretWeapon` each have their own table with `id` (FK to weapon.id) and weapon-type-specific columns
- `Module` extends `Item` directly with its own `module` table

### Discriminator Column

The `type` column on the `item` table is the polymorphic discriminator:

```python
# In Item:
type: Mapped[str] = mapped_column(String)  # e.g. 'weapon', 'primary_weapon', 'module'

# In PrimaryWeapon:
__mapper_args__ = {
    'polymorphic_identity': 'primary_weapon',
    'concrete': False,
}
```

**Important**: Never insert into an Item-derived table without setting the `type` to the correct `polymorphic_identity` value.

---

## Column Naming Conventions

- **Primary keys**: Always `id: Mapped[int]` with `autoincrement=True`
- **Foreign keys**: Named `<referenced_model_lowercase>_id` (e.g., `user_id`, `player_id`)
- **Discord IDs**: Always `BigInteger` (Discord snowflakes exceed 32-bit int range)
- **Timestamps**: `DateTime(timezone=True)` — always timezone-aware; use `lambda: datetime.now(UTC)` for defaults
- **JSON fields**: `mapped_column(JSON, nullable=True, default=dict)` for dicts; `mapped_column(JSON)` for lists/arrays
- **PostgreSQL arrays**: `mapped_column(ARRAY(String))` for `list[str]` fields (used in Item.aliases, Ship.aliases, Ship.assets, etc.)
- **String lengths**: Use `String(N)` when a max length is meaningful; use `String` (unbounded) for names/descriptions
- **Table names**: Always via `TableNames` enum — `TableNames.Players.value` returns `"players"`. Never hardcode strings.

---

## Relationship Patterns

### One-to-Many (User → Players)

```python
# In User:
players: Mapped[list["Player"]] = relationship("Player", back_populates="user")

# In Player:
user: Mapped["User"] = relationship("User", back_populates="players")
```

### One-to-Many with Cascade Delete (Player → Inventory)

```python
# In Player:
inventory: Mapped[list["PlayerInventory"]] = relationship(
    "PlayerInventory", back_populates="player", cascade="all, delete-orphan"
)
```

### Self-referential FK with post_update (Player → active_ship)

```python
# Player has both a list of ships AND a pointer to the active one.
# post_update=True resolves the circular FK insert order issue.
ships: Mapped[list["PlayerShip"]] = relationship(
    "PlayerShip", back_populates="player", cascade="all, delete-orphan",
    foreign_keys="PlayerShip.player_id"
)
active_ship: Mapped[Optional["PlayerShip"]] = relationship(
    "PlayerShip", foreign_keys=[active_ship_id], post_update=True
)
```

When a model has two relationships to the same target, **always specify `foreign_keys`** to disambiguate.

---

## All Models — Quick Reference

| File | Class | Table | Key Columns |
|---|---|---|---|
| `base.py` | `Base` | — | Declarative base |
| `admin_audit_log.py` | `AdminAuditLog` | `admin_audit_logs` | id, timestamp, user_id, guild_id, action, resource_type, resource_id, details (Text/JSON), status |
| `bounty.py` | `Bounty` | `bounty` | id, guild_id, division, criminal_name, criminal_faction, route (JSON), answer, reward, reward_per_sys, checked (JSON), issue_time, end_time, tech_level, criminal_ship (JSON), status, escape_count, win_user_id, respawn_time |
| `criminal.py` | `Criminal` | `criminal` | id, name, aliases, faction, ship_name, tech_level, emoji, icon, wiki |
| `discord_message.py` | `DiscordMessage` | `discord_message` | id (UUID), guild_id, channel_id, message_id, message_type, payload (Text) |
| `duel_request.py` | `DuelRequest` | `duel_requests` | id, guild_id, challenger_id, target_id, stakes, status, created_at, expires_at |
| `guild_config.py` | `GuildConfig` | `guild_configs` | id, guild_id (unique), starting_credits, bounty_channel_id, shop_channel_id, announcement_channel_id, other settings |
| `guild_shop.py` | `GuildShop` | `guild_shops` | id, guild_id, item_name, item_type, tier_required, price, quantity |
| `item.py` | `Item` | `item` | id, name, aliases (ARRAY), built_in, emoji, icon, value, wiki, type |
| `weapon.py` | `Weapon` | `weapon` | id (FK→item.id), tech_level, extra_atts (JSON) |
| `module.py` | `Module` | `module` | id (FK→item.id), module_type, tech_level, extra_atts (JSON) |
| `primary_weapon.py` | `PrimaryWeapon` | `primary_weapon` | id (FK→weapon.id), dps |
| `secondary_weapon.py` | `SecondaryWeapon` | `secondary_weapon` | id (FK→weapon.id), dps, ammo |
| `turret_weapon.py` | `TurretWeapon` | `turret_weapon` | id (FK→weapon.id), dps |
| `player.py` | `Player` | `players` | id, user_id (FK→users.id), guild_id, credits, lifetime_credits, xp, tier, prestige_count, duel_wins/losses/credits_won/lost, bounty_wins, systems_checked, xp_surplus, guild_transfer_cooldown, classic_mode, bounty_cooldown_end, active_ship_id (FK→player_ships.id), created_at, updated_at |
| `player_inventory.py` | `PlayerInventory` | `player_inventories` | id, player_id (FK→players.id), item_name, item_type, quantity, location |
| `player_ship.py` | `PlayerShip` | `player_ships` | id, player_id (FK→players.id), ship_name, nickname, primary_weapons (JSON), secondary_weapons (JSON), turret_weapons (JSON), modules (JSON), created_at |
| `schema_version.py` | `SchemaVersion` | `schema` | id, version_num, description, applied_at |
| `ship.py` | `Ship` | `ship` | id, name, aliases (ARRAY), armour, built_in, cargo, compatible_skins (JSON), emoji, icon, manufacturer, handling, shop_spawn_rate, skinnable, max_modules, max_primaries, max_secondaries, max_turrets, builtin_modules (ARRAY), texture_regions, save_due, model, norm_spec, value, wiki, assets (ARRAY) |
| `system.py` | `System` | `system` | id, name, connections (JSON/ARRAY), faction, position_x, position_y |
| `user.py` | `User` | `users` | id (BigInteger, Discord snowflake), discord_username, created_at |

---

## Auto-Import Mechanism

`__init__.py` uses `pkgutil.walk_packages` to auto-import every module in this package:

```python
for _loader, module_name, _is_pkg in pkgutil.walk_packages(__path__):
    importlib.import_module(f"{__name__}.{module_name}")
```

This ensures all SQLAlchemy model classes are registered with the mapper before any session is created. **You do not need to update `__init__.py` when adding a new model** — just create the file and it will be discovered automatically.

---

## How to Add a New Model

1. **Add to `TableNames` enum first** (`persist/database/tablenames.py`):
   ```python
   MyNewModel = "my_new_model"
   ```

2. **Create the model file** `persist/models/my_new_model.py`:
   ```python
   from sqlalchemy import Integer, String
   from sqlalchemy.orm import Mapped, mapped_column
   from persist.database.tablenames import TableNames
   from persist.models.base import Base

   class MyNewModel(Base):
       __tablename__ = TableNames.MyNewModel.value

       id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
       name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
       # ... other columns
   ```

3. **No `__init__.py` update needed** — auto-imported by `pkgutil.walk_packages`.

4. **Generate migration**:
   ```bash
   cd /app/src
   python -m persist.database.run_migration revision -m "add my_new_model table"
   python -m persist.database.run_migration upgrade
   ```

5. **Create the repository** in `persist/repositories/my_new_model_repository.py`.

---

## Important Gotchas

- **Never use `create_all()`** — migrations are managed exclusively by Alembic. `create_all()` was used in the legacy version and is no longer appropriate.
- **BigInteger for Discord IDs** — Discord snowflake IDs exceed 32-bit integer range. Always use `BigInteger`.
- **Timezone-aware datetimes** — always `DateTime(timezone=True)` and `datetime.now(UTC)` (not `datetime.utcnow()`).
- **JSON vs ARRAY** — use `ARRAY(String)` for simple string lists (PostgreSQL-native); use `JSON` for dicts or heterogeneous structures.
- **STI `polymorphic_identity`** — every non-abstract STI subclass must declare `__mapper_args__` with `'polymorphic_identity'`.

---

*Last updated: 2026-03-16*
