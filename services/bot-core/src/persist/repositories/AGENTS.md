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

## Mutation Pattern: ORM-Tracked Setattr (NOT Core UPDATE)

**Rule**: Single-row column updates in repository methods MUST use ORM-tracked
attribute assignment (`setattr` / direct attribute write) followed by
`flush()` / `commit()`. Do **not** use `db.execute(update(Model).where(...).values(...))`
followed by `get_by_id()` for single-row updates.

### Why

`db.execute(update(...))` is a Core-level statement that bypasses SQLAlchemy's
unit-of-work / identity-map tracking. After such a statement, any ORM instances
already loaded for the affected row are silently expired. A subsequent attribute
read on the caller's reference triggers a re-fetch from the DB — returning the
POST-update value, NOT the pre-update value the caller may have been holding.

This produced the **doubled-credit bug** in `shop_service.sell_item` /
`sell_ship` (April 2026): after `update_credits()` re-fetched the player, the
service re-read `player.credits + total_sell_value` and applied the addition twice.

### Correct pattern (single-row update)

```python
async def update_credits(self, db: AsyncSession, player_id: int, new_credits: int,
                        *, commit: bool = True) -> Player:
    try:
        player = await self.get_by_id(db, player_id)
        if player is None:
            raise ValueError(f"Player {player_id} not found")
        player.credits = new_credits           # ORM-tracked mutation
        if commit:
            await db.commit()
        else:
            await db.flush()
        return player
    except ValueError:
        raise
    except Exception as e:
        flogger.error(f"Error updating credits for player {player_id}: {e}")
        if commit:
            await db.rollback()
        raise
```

The inherited FOR UPDATE row lock (when the caller pre-loaded with
`get_by_id_for_update`) carries through automatically — the internal
`get_by_id` is an identity-map hit that returns the SAME instance.

### Documented exceptions (bulk operations)

Bulk multi-row updates may use Core UPDATE **only** with
`execution_options(synchronize_session="fetch")` so SQLAlchemy correctly expires
any identity-mapped rows. `synchronize_session="evaluate"` is BANNED (compatibility
issues with complex WHERE clauses).

Current documented exceptions:

- **`bounty_repository.clear_active_by_guild`** — bulk-clears N active bounties
  to status='cleared'. Uses Core UPDATE + `synchronize_session="fetch"`. Returns
  IDs only, never returns model objects.
- **`player_ship_repository.set_active_ship`** (deactivate-all step) — bulk-deactivates
  N PlayerShip rows for a player. Uses Core UPDATE + `synchronize_session="fetch"`.
  The activate-target step is a single-row ORM mutation on the already-loaded
  ship instance.
- **`bounty_repository.delete_terminal_older_than`** — bulk-DELETEs terminal-status
  bounty rows older than the retention window. Uses Core DELETE +
  `synchronize_session="fetch"`. Returns row count only.
- **`duel_repository.delete_terminal_older_than`** — bulk-DELETEs terminal-status
  duel rows older than the retention window. Same pattern.
- **`admin_audit_log_repository.delete_older_than`** — bulk-DELETEs audit-log
  rows older than the retention window. Same pattern.

When adding a new bulk-update exception, document it in the method docstring
AND in this AGENTS.md.

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

## All 20 Repositories

| File | Class | Model | Key Custom Methods |
|---|---|---|---|
| `generic_repository.py` | `GenericRepository[T]` | Generic | Base: add, get_by_id, get_by_name, get_by_alias, list_all, remove |
| `admin_audit_log_repository.py` | `AdminAuditLogRepository` | `AdminAuditLog` | `count`, `delete_older_than` — does NOT implement `IRepository[T]`. Append-only via `AuditService.log_action`; this repo exists only to support the `db_retention_default` executor's retention pass. |
| `bounty_repository.py` | `BountyRepository` | `Bounty` | get_active_by_guild, get_active_by_guild_and_division, count_active_by_guild_and_division, create, update, delete, `delete_terminal_older_than` (bulk DELETE for data retention — uses `synchronize_session="fetch"`) |
| `config_repository.py` | `ConfigRepository` | `GuildConfig` | get_by_guild_id, create_or_get_default |
| `criminal_repository.py` | `CriminalRepository` | `Criminal` | get_by_faction, get_by_tech_level_range |
| `discord_message_repository.py` | `DiscordMessageRepository` | `DiscordMessage` | get_by_guild_and_type, delete_by_guild_and_type |
| `duel_repository.py` | `DuelRepository` | `DuelRequest` | get_pending_by_guild, get_by_challenger_and_target, expire_old_duels, `delete_terminal_older_than` (bulk DELETE for data retention) |
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
        select(Player).where(Player.id == obj_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    return result.scalars().first()
```

Use this inside an explicit `async with db.begin()` transaction block when the
caller will later modify and commit. The `populate_existing=True` is **required**,
not cosmetic — see the **Refresh-under-lock** rule under "Global lock-ordering
rule" below for why.

### Global lock-ordering rule (D5 — deadlock safety)

Any transaction that locks **more than one** row MUST acquire locks in this order:

1. **Aggregate row first** — the `Bounty` row (`/check`) or `Duel` row (`/accept`)
   is locked via `get_by_id_for_update` **before** any `Player` lock the same
   transaction takes. No path may lock a `Player` row *before* the aggregate row
   it also touches (doing so would create an AB-BA cycle against `/check` /
   `/accept`, which lock aggregate-then-player).
2. **Then `Player` row(s) in ascending `player_id` order** — matches
   `transfer_credits` (`player_service.py`). The only multi-player transactions
   are `transfer_credits`, `duel accept`, and `ships.transfer_ship`; all lock
   players in ascending id order.
3. **In any single-player credit/inventory/loadout mutation, the FIRST lock
   acquired MUST be the `Player` row** (`get_by_id_for_update`), taken before any
   unlocked read whose value feeds a read-modify-write (credit balance, cargo
   quantity, slot list, slot caps). "First lock = most restrictive mode that will
   be needed" (PostgreSQL deadlocks guidance).
4. The **loadout lock and the credit lock are the SAME `Player` row** and so
   collapse into one lock class. Re-acquiring the same player's row lock later in
   the same transaction (e.g. the loadout choke-point's `_lock_player` after a
   shop service already locked for credits) is permitted and is an
   **intra-transaction no-op** — a transaction may re-hold its own row lock.

Audited lock-first sites (D5-T1 + D5-T2): the `LoadoutConsistencyService`
choke-point (`equip_one`, `unequip_one`, `evacuate_ship_loadout_to_inventory`,
`reconcile_active_ship_slots`, `activate_ship`, `repair_player`),
`shop_service.{purchase_item, purchase_ship, sell_item, sell_ship}`, and
`ships.transfer_ship` all take the `Player` lock as the first player access.

> Note (D5-T2b, IMPLEMENTED): `bounty_service.distribute_rewards` previously
> mutated each rewarded player's credits from an **unlocked** `get_by_id` read —
> a lost-update gap for concurrent credit ops (NOT a lock-ordering/deadlock
> hazard, since loadout/credit ops never lock a `Bounty` row so no cycle exists).
> D5-T2b closed it: `distribute_rewards` now acquires each rewarded player's row
> `FOR UPDATE` via `get_by_id_for_update` **before** the credit RMW, locking
> players in **ascending `player_id` order** (it iterates `sorted(rewards,
> key=lambda r: r.player_id)`) to preserve rule 2. It runs inside `check_bounty`,
> which already holds the `Bounty` row lock (P2-T10), so the composed order is
> Bounty → Players-ascending (rule 1, aggregate-first) — no Player → Bounty cycle
> is introduced. `get_by_id_for_update`'s `populate_existing=True` (D5-T1) means
> the locked re-fetch refreshes `check_bounty`'s pre-loaded (unlocked) player
> object with the freshly-committed credits, so the increment lands on fresh
> state under the lock. (`_award_combat_bonus`, the bronze 2x bonus, re-reads the
> winner via unlocked `get_by_id` *after* `distribute_rewards` has already locked
> that same row in the same transaction — an identity-map hit on an
> already-locked row — so it is serialised by transitivity and needs no separate
> lock.)

**Refresh-under-lock (D5-T1).** A locked read MUST go through
`get_by_id_for_update`, which emits
`select(...).with_for_update().execution_options(populate_existing=True)`. The
`populate_existing=True` is **required**, not cosmetic: our sessions run with
`expire_on_commit=False` (production default), so an instance already present in
the identity map (e.g. pre-loaded by an earlier unlocked `get_by_id` in the same
transaction — as `shop_service.{sell_ship,purchase_ship}` and
`ships.transfer_ship` do) would be returned **from cache**. Without
`populate_existing`, the `FOR UPDATE` re-read acquires the row lock but the
guard then reads the **stale pre-commit** attributes — the classic "lock looks
correct, tests green" trap. `populate_existing=True` makes the ORM
unconditionally overwrite the in-memory object with the row just fetched under
the lock — *"the corresponding instances in the Session will be fully refreshed –
erasing any existing data within the objects (including pending changes) and
replacing with the data loaded from the result"*
([SQLAlchemy 2.0 — Populate Existing](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing)).
So the lock-holder always evaluates its guards against committed state.

**Transaction boundary (D5-T3).** A PostgreSQL `FOR UPDATE` row lock is held
until the current transaction ends: *"Row-level locks are released at
transaction end or during savepoint rollback"* and `FOR UPDATE` *"prevents them
from being locked, modified or deleted by other transactions until the current
transaction ends"*
([PostgreSQL 13.3.2 — Row-Level Locks](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS)).
Wrapping the lock acquisition in `async with db.begin():` does **not** change
that *duration*, because an explicit `Session.begin()` is not a separate or
nested transaction from the one the session would autobegin on its first DB
statement — *"The `Session.begin()` method and the session's "autobegin" process
use the same sequence of steps to begin the transaction"*
([SQLAlchemy 2.0 — Explicit Begin](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#explicit-begin)).
`db.begin()` is still **mandatory** for any route that calls a flush-only
(`commit=False`) service: it makes the lock acquisition and the flush-only
writes one explicit unit of work that commits/rolls back together (atomicity),
and it is the contract enforced by `tests/test_transaction_discipline.py`
(relying on `get_db_session`'s clean-exit auto-commit instead is not acceptable —
the boundary must be explicit). Within that unit of work, acquire the `Player`
lock (`get_by_id_for_update`) **FIRST**, before any read whose value feeds the
read-modify-write (rule 1 above).

**Bypass routes are closed (D5-T3).** The two routes that used to mutate the
loadout/inventory aggregate *without* the choke-point —
`inventory.consolidate_inventory` (POST `/inventory/player/{id}/consolidate`) and
`ships.update_ship_loadout` (PUT `/ships/{id}/loadout`, admin/maintenance JSON
overwrite) — now both open an explicit `db.begin()` and take the `Player` row
`FOR UPDATE` before any read whose value feeds the read-modify-write (rule 3
above). For `consolidate_inventory` the `get_by_id_for_update` IS the first DB
statement in the block. For `update_ship_loadout` the lock is preceded by one
unlocked `player_ship_repo.get_by_id(ship_id)` read — a non-RMW lookup that only
resolves the ship's immutable `player_id` so the route knows *which* aggregate to
lock; its value never feeds the protected invariant, so it does not violate rule
3 (which governs reads that feed the RMW, not the read that selects the lock
target). They are the canonical **worked examples** of applying this rule
outside the `LoadoutConsistencyService` choke-point; copy their inline comment
pattern when adding any new route that touches the aggregate directly.

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

## `commit: bool = True` Parameter (Package G B.19, B.34 expansion 2026-04-30)

Every repository write method (INSERT/UPDATE/DELETE) accepts a
`commit: bool = True` keyword argument. When `commit=False`, the method
calls `db.flush()` instead of `db.commit()` and does NOT roll back on
exception — the caller (typically a router-level `async with db.begin()`
block) owns the transaction.

The full inventory after the B.34 remediation:

**`GenericRepository[T]`** (base class — all subclasses inherit):
- `add(db, obj, *, commit=True)`
- `remove(db, obj, *, commit=True)`

**`PlayerShipRepository`** (Package G B.19 canonical pattern):
- `set_active_ship`, `add_equipment`, `remove_equipment`,
  `update_loadout`, `update_nickname`, `add`, `create_or_update`, `remove`

**`PlayerRepository`** (B.34 expansion):
- `add`, `create_or_update`, `remove`,
  `update_credits`, `update_xp`, `update_tier`, `update_active_ship`

**`UserRepository`** (B.34 expansion):
- `add`, `create_or_update`, `remove`, `get_or_create_user`

**`InventoryRepository`** (Package G B.19 canonical pattern):
- `add`, `create_or_update`, `remove`,
  `add_item`, `remove_item`, `update_quantity`,
  `clear_player_inventory` (B.34 closeout, 2026-04-30)

**`ShopRepository`** (Package G B.19 canonical pattern):
- `add`, `create_or_update`, `remove`, `update_quantity`,
  `clear_shop_tier`, `clear_all_guild_shops`, `update_prices`
  (B.34 closeout, 2026-04-30)

**`BountyRepository`** (B.34 expansion):
- `add`, `create_or_update`, `remove`, `create`, `update`, `delete`,
  `clear_active_by_guild` (B.34 closeout, 2026-04-30)

**`DuelRepository`** (B.34 expansion):
- `add`, `create_or_update`, `remove`, `create`,
  `update_status`, `delete_expired`

**`ConfigRepository`** (B.34 expansion):
- `add`, `create_or_update`, `remove`,
  `create_default_config`, `reset_to_defaults`, `update_shop_config`,
  `update_admin_role`, `update_starting_credits`, `update_xp_thresholds`,
  `update_division_temperatures`, `delete_guild_config`
  (B.34 closeout, 2026-04-30)

**`DiscordMessageRepository`** (B.34 expansion):
- `create_or_update`, `delete_by_composite_key`,
  `delete_by_guild_type_and_reference`

When NOT to use commit=False: methods that exist explicitly to be
self-committing transaction-owners (e.g. legacy single-row updates that
are called from bare-session routes). The default `commit=True` preserves
backward compatibility — existing callers are unaffected.

### When to use `commit=False`

Whenever a router wraps multiple repository calls in `async with db.begin():`
for atomicity (Package G's invariant I3 contract).  Pre-fix, several routers
used `async with get_db_session() as db:` only and accepted that mid-flow
crashes left the player in an inconsistent state across `player_ships` and
`player_inventories`.  Post-fix, every cross-table flow is wrapped, and
every repo call inside that wrapper passes `commit=False`.

---

## Transaction Discipline Enforcement (B.34, 2026-04-30)

The "every cross-table flow must be wrapped" contract documented above is
enforced by **four defense-in-depth layers**, not by reviewer discipline alone.

### Layer 1 — Static linter (test-time)

`tests/test_transaction_discipline.py` is a pytest-collectable AST analyzer
that fails CI when any router function calls a flush-only service method
without wrapping in `async with db.begin():` or committing explicitly.

How the linter classifies "flush-only":

  Phase 1: walks every `services/*.py` file. A service method is flagged
  flush-only if its body contains either:
    - a Call with literal `commit=False` keyword argument, OR
    - a direct `db.flush()` call AND no `db.commit()` call anywhere.
  Then computes transitive closure: a method that calls (by name) a
  method already in the set is itself in the set.

  Phase 2: walks every router function. A route is in violation if it
  calls a flush-only method without:
    - `async with ... db.begin():` (any nesting), OR
    - `await db.commit()` on the success path, OR
    - a `# noqa: TRANSACTION_DISCIPLINE - <reason>` comment on the
      offending line.

To suppress a false positive (rare, but possible — e.g. dynamic dispatch
the AST cannot reason about), add a comment to the offending line:

```python
await player_service.update_player_credits(...)  # noqa: TRANSACTION_DISCIPLINE - explanation
```

The marker must be exactly `noqa: TRANSACTION_DISCIPLINE`. The trailing
`- explanation` is required documentation for human reviewers; the
linter does not parse it. Production-code suppressions should cite a
specific architectural reason in the same commit.

### Layer 2 — Runtime decorator (call-time)

`services/_transaction_guards.py` provides `@requires_transaction` which
raises `RuntimeError` immediately if invoked outside an active transaction.
Applied to all 6 public methods of `LoadoutConsistencyService` (the
choke-point). This catches dynamic-dispatch bypasses that the static
linter cannot reason about.

### Layer 3 — Session manager auto-commit (exit-time)

`db_manager.get_session()` commits any pending transaction on clean exit.
If a caller forgets to wrap or commit, work is preserved at the session
boundary instead of being silently rolled back. Read-only callers that
mutated ORM instances and intentionally do not want them flushed must
call `await session.rollback()` before exiting (no such callsites exist
in bot-core as of the AC-7 callsite audit).

### Layer 4 — Cross-session integration tests (CI-time)

`tests/integration/test_cross_session_persistence.py` covers all 20
cross-table operations enumerated in
`/proj/recon/B34-remediation-spec.md` §6.1. Each test:

  1. Opens session A, performs the operation.
  2. Closes session A entirely.
  3. Opens FRESH session B from the same engine.
  4. Queries DB through B and asserts what should have persisted, did.

This is the precise idiom that detects the B.34 silent-rollback class —
mock-only tests cannot, because mocked repos return success regardless
of whether commit was called.

### Adding a new cross-table service method

A service method that performs cross-table writes MUST have at least one
integration test in `tests/integration/` following the cross-session-reload
pattern. Mock-only tests (in `tests/services/`) are insufficient for
methods in the WRITES_FLUSH_ONLY set produced by the linter — they can
add coverage but do not substitute for the integration assertion.

---

*Last updated: 2026-06-07 (D5-T4: consolidated the D5 lock-ordering /
refresh-under-lock / transaction-boundary convention into the "Global
lock-ordering rule" section)*
