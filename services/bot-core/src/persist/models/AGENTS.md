# AGENTS.md - persist/models

SQLAlchemy ORM models for bot-core. All 22 model classes (plus `Base`) live in this directory.

---

## Model Inheritance Hierarchy

```
Base  (persist/models/base.py — DeclarativeBase)
│
├── Item  [table: item]          ← polymorphic root, discriminator: `type` column
│   ├── Commodity  [table: commodity]
│   ├── Module  [table: module]
│   └── Weapon  [table: weapon]  ← concrete intermediate (identity: 'weapon')
│       ├── PrimaryWeapon  [table: primary_weapon]
│       ├── SecondaryWeapon  [table: secondary_weapon]
│       └── TurretWeapon  [table: turret_weapon]
│
├── AdminAuditLog   [table: admin_audit_logs]
├── Bounty          [table: bounty]
├── CombatLog       [table: combat_log]
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

## Joined-Table Inheritance Pattern

The `Item` / `Weapon` / weapon-leaf hierarchy uses SQLAlchemy's **joined-table inheritance**:

- `Item` is the root — all purchasable items share the `item` table columns: `id`, `name`, `aliases`, `built_in`, `emoji`, `icon`, `value`, `wiki`, `type`
- `Weapon` extends `Item` with its own `weapon` table: `id` (FK to item.id), `tech_level`, `extra_atts` (JSONB)
- `PrimaryWeapon`, `SecondaryWeapon`, `TurretWeapon` each have their own table with `id` (FK to weapon.id) and weapon-type-specific columns
- `Module` and `Commodity` extend `Item` directly with their own `module` / `commodity` tables

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

Note: `Criminal`, `Ship`, and `System` also declare `__mapper_args__` with a
`polymorphic_identity` even though they inherit `Base` directly (no
discriminator column is involved for them).

---

## Column Naming Conventions

- **Primary keys**: Always `id: Mapped[int]` with `autoincrement=True`
- **Foreign keys**: Named `<referenced_model_lowercase>_id` (e.g., `user_id`, `player_id`)
- **Discord IDs**: Always `BigInteger` (Discord snowflakes exceed 32-bit int range)
- **Timestamps**: `DateTime(timezone=True)` — always timezone-aware; use `lambda: datetime.now(UTC)` for defaults
- **JSON fields**: use the portable JSONB pattern (revisions 0016/0017, P4-T8/T9):
  `_JSONB = JSON().with_variant(JSONB(), "postgresql")` — Postgres stores JSONB,
  the SQLite test suite falls back to JSON. Declared per-module at the top of each
  model file that needs it. (`Ship.compatible_skins` is the one remaining plain-`JSON` column.)
- **PostgreSQL arrays**: `mapped_column(ARRAY(String))` for `list[str]` fields (used in Item.aliases, Ship.aliases, Ship.assets, etc.)
- **String lengths**: Use `String(N)` when a max length is meaningful; use `String` (unbounded) for names/descriptions
- **Table names**: Always via `TableNames` enum — `TableNames.Players.value` returns `"players"`. Never hardcode strings. (Known exception: `AdminAuditLog` hardcodes `"admin_audit_logs"` and is absent from the enum.)

---

## Relationship Patterns

### One-to-Many (User → Players)

```python
# In User:
players: Mapped[list["Player"]] = relationship(
    "Player", back_populates="user", cascade="all, delete-orphan"
)

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
| `admin_audit_log.py` | `AdminAuditLog` | `admin_audit_logs` | id, timestamp, user_id (BigInt), guild_id (nullable = system-wide), action, resource_type, resource_id, details (Text holding JSON), status (default "success") |
| `bounty.py` | `Bounty` | `bounty` | id, guild_id, division, criminal_name, criminal_faction, route (JSONB), answer, reward, reward_per_sys, checked (JSONB), issue_time, end_time, tech_level, criminal_ship (JSONB — loadout blob; also carries a `cargo` key `{item_type, item_name, quantity}` = the criminal's single rolled PvC loot item, T4), status, escape_count, win_user_id, respawn_time, criminal_current_hull/armour/shield + criminal_last_damage_at (Phase-2 damage state, rev 0009), created_at, updated_at |
| `combat_log.py` | `CombatLog` | `combat_log` | id, guild_id, context, combatant1/2_name, combatant1/2_user_id (nullable = NPC; both indexed), winner_name, is_stalemate, data (JSONB event-tick timeline + summary), created_at (retention key) |
| `commodity.py` | `Commodity` | `commodity` | id (FK→item.id), tech_level, subcategory, extra_atts (JSONB; price-range fields exposed as read-only properties) |
| `criminal.py` | `Criminal` | `criminal` | id, name, aliases (ARRAY), built_in, faction, icon, is_player, wiki |
| `discord_message.py` | `DiscordMessage` | `discord_message` | id (UUIDType), guild_id, channel_id, message_id, message_type, embed_payload (Text), reference_id, created_at, updated_at; unique (guild_id, channel_id, message_id) |
| `duel_request.py` | `DuelRequest` | `duel_requests` | id, guild_id, challenger_id, target_id, stakes, status, created_at, expires_at |
| `guild_config.py` | `GuildConfig` | `guild_configs` | id, guild_id (unique), admin_role_id, per-division channel/role IDs (bronze/silver/gold/platinum bounty channels + roles, shop/hunting/discussion/image channels, shop_announcements_role_id), shop count/quantity ranges (JSONB), tech_level_probabilities, sale_price_factor, starting_credits, xp_thresholds, division_temperatures, bounty_max_per_tier/expiry/spawn-interval, plus ~50 nullable per-guild game-constant overrides (B.49 + combat Phase-1 + criminal loadout-balance; NULL = use `GameConstants` default), created_at, updated_at; `shops` relationship (cascade="all, delete-orphan"). **Criminal loadout-balance overrides (rev 0020/0021, 2026-06-18):** `long_range_threshold_m` (Int), `criminal_long_range_pct` (Float), `primary_tl_band_weights` (JSONB), `criminal_{cloak,booster,emergency,weaponmod}_chance_by_division` (JSONB per-division %), `criminal_exclude_emp_weapons` (Boolean; NULL = use `GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS`=True). **PvC loot knobs (rev 0022, `0022_loot_config_knobs`):** 19 nullable scalar columns mirroring `GameConstants.LOOT_*` — `loot_chance_tractor_t1..t4` + `loot_chance_no_tractor` (Int), `loot_band{1,2,3}_select_pct` (Int), `loot_band1_tl_window` (Int), `loot_band{1,2,3}_qty_{min,mode,max}` (Int), `loot_commodity_sell_fraction` (Float); NULL = use the `GameConstants` default. (`LOOT_DROP_CHANCE` is a fixed constant — no column.) |
| `guild_shop.py` | `GuildShop` | `guild_shops` | id, guild_id (FK→guild_configs.guild_id), tier, tech_level, item_type, item_name, quantity, price, last_restocked, refresh_interval_hours. `tech_level` stores each row's ITEM's real tech level (per-item drawn TL on refresh; catalog/value-derived TL on sell-back) — NOT a single batch-wide shop TL |
| `item.py` | `Item` | `item` | id, name, aliases (ARRAY), built_in, emoji, icon, value, wiki, type |
| `weapon.py` | `Weapon` | `weapon` | id (FK→item.id), tech_level, extra_atts (JSONB) |
| `module.py` | `Module` | `module` | id (FK→item.id), tech_level, max_equipped, extra_atts (JSONB); module subtype is the discriminator stored in `Item.type` (e.g. `ArmourModule`, `ShieldModule`) — there is no separate `module_type` column |
| `primary_weapon.py` | `PrimaryWeapon` | `primary_weapon` | id (FK→weapon.id), dps |
| `secondary_weapon.py` | `SecondaryWeapon` | `secondary_weapon` | id (FK→weapon.id), damage, loading_speed |
| `turret_weapon.py` | `TurretWeapon` | `turret_weapon` | id (FK→weapon.id), dps, automatic (Boolean, default False) |
| `player.py` | `Player` | `players` | id, user_id (FK→users.id), guild_id, credits, lifetime_credits, systems_checked, bounty_wins, xp, tier, prestige_count, duel_wins/losses/credits_won/lost, display_name, xp_surplus, guild_transfer_cooldown, classic_mode, bounty_cooldown_end, tier_change_cooldown_end, active_ship_id (FK→player_ships.id, use_alter), total_fights/total_nukes_fired/total_module_activations (lifetime combat counters, rev 0011), current_hull/armour/shield + last_damage_at (Phase-2 damage state, rev 0009), created_at, updated_at |
| `player_inventory.py` | `PlayerInventory` | `player_inventories` | id, player_id (FK→players.id), item_type, item_name, quantity, acquired_at; unique (player_id, item_type, item_name) (CI-18, rev 0015) |
| `player_ship.py` | `PlayerShip` | `player_ships` | id, player_id (FK→players.id), ship_name, nickname, is_active, weapons/modules/turrets/secondary_weapons (JSONB arrays of item names), secondary_ammo (JSONB dict {weapon_name: remaining_rounds}, CI-16), created_at. `manual_turret_mode` was DROPPED in rev 0018 — turret/primary switching is range-driven in the combat engine, not a per-ship flag |
| `schema_version.py` | `SchemaVersion` | `schema` | version (String(50), PK), applied_at, description |
| `ship.py` | `Ship` | `ship` | id, name, aliases (ARRAY), armour, built_in, cargo, compatible_skins (JSON), emoji, icon, manufacturer, handling, shop_spawn_rate, skinnable, max_modules, max_primaries, max_secondaries, max_turrets, builtin_modules (ARRAY), texture_regions, save_due, model, norm_spec, value, wiki, assets (ARRAY), extra_atts (JSONB, rev 0009) |
| `system.py` | `System` | `system` | id, name, aliases (ARRAY), coordinates (ARRAY Integer), faction, neighbours (ARRAY String), security, wiki |
| `user.py` | `User` | `users` | id (BigInteger, Discord snowflake), discord_username, display_name, created_at, updated_at |

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
- **JSON vs ARRAY** — use `ARRAY(String)` for simple string lists (PostgreSQL-native); use the portable `_JSONB` variant type for dicts or heterogeneous structures.
- **JSONB columns must be reassigned, never mutated in place** — SQLAlchemy does not track in-place dict/list mutation; writes are silently lost (see the `secondary_ammo` comment in `player_ship.py`). Build a new dict/list and assign it.
- **`polymorphic_identity`** — every Item-derived subclass must declare `__mapper_args__` with `'polymorphic_identity'`.

---

*Last updated: 2026-06-20 (PvC loot: GuildConfig +19 loot knob columns rev 0022;
Bounty.criminal_ship `cargo` key). Prior: 2026-06-18 (added GuildConfig criminal
loadout-balance overrides, revs 0020/0021). Prior: 2026-06-11 true-to-source audit — added CombatLog +
Commodity; corrected Criminal/DiscordMessage/GuildConfig/GuildShop/SecondaryWeapon/
TurretWeapon/PlayerInventory/PlayerShip/SchemaVersion/Ship/System/User column
lists; recorded manual_turret_mode drop (rev 0018) and JSONB migration
(revs 0016/0017).*
