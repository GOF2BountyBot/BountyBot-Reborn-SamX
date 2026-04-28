# B.31 Recon — `/admin_config action:Reset to Defaults` returns 500; user sees raw bot-core URL

**Recon date**: 2026-04-28  
**Method**: Read-only source code investigation (HEAD)  
**Defect phase**: 12.5  
**Sub-defects**: 12.5a (🟠 high) + 12.5b (🔵 low)

---

## Summary

Two independent defects surfaced in a single user interaction:

- **12.5a**: `POST /api/v1/config/guild/{gid}/reset` always fails with a NOT NULL constraint violation when `guild_shops` has rows for the guild. The root cause is a missing `cascade="all, delete-orphan"` on the `GuildConfig.shops` SQLAlchemy relationship, causing SQLAlchemy to attempt `UPDATE guild_shops SET guild_id = NULL` before deleting the parent row. The NOT NULL constraint on `guild_shops.guild_id` rejects this.
- **12.5b**: All 53 `httpx.HTTPStatusError` catch blocks across 9 cogs use `f"❌ API Error: {e}"` which includes the raw internal URL (`http://bot-core:8000/...`) via `HTTPStatusError.__str__()`. Cross-cutting information leak.

---

## 12.5a — Detailed Analysis

### Call Chain (HEAD)

```
adminCog.py:593
  self.http_client.post(f"{api_base}/config/guild/{interaction.guild_id}/reset", timeout=10)
  → POST /api/v1/config/guild/{guild_id}/reset

config.py:188-227
  async def reset_guild_config(guild_id, config_service):
      async with get_db_session() as db:
          config = await config_service.reset_to_defaults(db, guild_id)

config_service.py:90-100
  async def reset_to_defaults(self, db, guild_id):
      await self.config_repo.reset_to_defaults(db, guild_id)
      return await self.config_repo.get_config_summary(db, guild_id)

config_repository.py:186-202
  async def reset_to_defaults(self, db, guild_id):
      existing_config = await self.get_by_guild_id(db, guild_id)  # loads GuildConfig only
      if existing_config:
          await self.remove(db, existing_config)                   # <-- FAILS HERE
      config = await self.create_default_config(db, guild_id)
      return config

config_repository.py:95-104
  async def remove(self, db, obj):
      await db.delete(obj)
      await db.commit()   # <-- triggers flush which attempts SET NULL on guild_shops.guild_id
```

### FK / Relationship Definitions

**`guild_config.py:91`** — GuildConfig model:
```python
shops: Mapped[list["GuildShop"]] = relationship("GuildShop", back_populates="guild_config")
# NO cascade, NO passive_deletes
```

**`guild_shop.py:21-23`** — GuildShop model:
```python
guild_id: Mapped[int] = mapped_column(
    BigInteger, ForeignKey(f"{TableNames.GuildConfigs.value}.guild_id"), nullable=False
)
# ForeignKey targets guild_configs.guild_id (the guild_id column, NOT guild_configs.id)
# nullable=False
# No ondelete= argument → DB-level NO ACTION (default)
```

**`guild_shop.py:34`** — GuildShop → GuildConfig back-reference:
```python
guild_config: Mapped["GuildConfig"] = relationship("GuildConfig", back_populates="shops", foreign_keys=[guild_id])
# No cascade on child side (not expected here)
```

### SQLAlchemy Cascade Behavior

| Setting | Value | Effect |
|---|---|---|
| `GuildConfig.shops.cascade` | not set → `"save-update, merge"` | Does NOT cascade delete to shops |
| `GuildConfig.shops.passive_deletes` | not set → `False` | SQLAlchemy manages FK nullification itself (does NOT let DB handle it) |
| DB FK `ON DELETE` | not set → `NO ACTION` | DB would RESTRICT deletion if rows still reference parent |

**Sequence during `await db.commit()` after `db.delete(existing_config)`**:

1. SQLAlchemy unit-of-work detects `GuildConfig` instance marked for deletion
2. Sees `shops` relationship with `passive_deletes=False` and no cascade delete
3. To maintain relational integrity at the ORM level, SQLAlchemy issues:
   ```sql
   UPDATE guild_shops SET guild_id = NULL WHERE guild_id = <value>
   ```
4. PostgreSQL rejects: `null value in column "guild_id" of relation "guild_shops" violates not-null constraint`
5. `asyncpg.exceptions.NotNullViolationError` → `sqlalchemy...IntegrityError`
6. `get_db_session()` context manager catches, rolls back session

**Why NOT a FK violation error?**  
If SQLAlchemy tried to DELETE the parent first, PostgreSQL would say "still referenced by table guild_shops". Instead, we see a NOT NULL violation, which confirms SQLAlchemy is attempting the SET NULL **before** the DELETE — this is the canonical SQLAlchemy behavior with `passive_deletes=False`.

### Migration / Schema Evidence

`0001_initial_schema.py` uses `Base.metadata.sorted_tables` to create tables from ORM model definitions. The DB-level FK is created exactly as the ORM defines it:
```sql
CONSTRAINT guild_shops_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES guild_configs(guild_id)
-- no ON DELETE clause
```

No separate migration exists for `guild_shops` FK beyond the initial schema.

### Trigger Condition

The bug fires whenever `guild_shops` has ANY rows for the guild at reset time. Since shops are auto-generated during `/admin_setup` and refreshed on a 6-hour schedule, the shop will be populated in any normal post-setup guild. The bug is **not intermittent** — it fires on 100% of `Reset to Defaults` invocations in a configured guild.

### Safe Path Comparison

`config_service.uninstall_guild` (used by `/admin_uninstall`) is NOT affected because it explicitly clears shops before deleting config:
```python
await self.shop_repo.clear_all_guild_shops(db, guild_id)   # clears shops first
config_deleted = await self.config_repo.delete_guild_config(db, guild_id)   # then deletes config
```
This ordering is the correct pattern that the reset path should also follow (or use cascade).

---

## 12.5b — Detailed Analysis

### Code Path

`adminCog.py:592-602` — `admin_config` handler, `action == "reset"` branch:

```python
elif action == "reset":
    resp = await self.http_client.post(f"{api_base}/config/guild/{interaction.guild_id}/reset", timeout=10)
    resp.raise_for_status()  # raises httpx.HTTPStatusError on 500
    await interaction.followup.send(
        "✅ Guild configuration has been reset to default values", ephemeral=True
    )

# ... (lines 601-602)
except httpx.HTTPStatusError as e:
    await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
```

`httpx.HTTPStatusError.__str__()` produces:
```
Server error '500 Internal Server Error' for url 'http://bot-core:8000/api/v1/config/guild/1490693399307616276/reset'
```

Newer httpx versions also append an MDN documentation hyperlink for the HTTP status code. Both appear in the ephemeral embed shown to the user.

### Information Disclosed

The raw URL exposes:
- Internal container hostname: `bot-core`
- Internal container port: `8000`
- API versioning: `/api/v1`
- Complete operation path: `/config/guild/{guild_id}/reset`
- Guild ID (already known to the user invoking the command, so not secret)

### Cross-Cutting Scope

The same `f"❌ API Error: {e}"` pattern appears across **all 9 cogs** with `httpx.HTTPStatusError` handlers:

| Cog | Occurrences | Commands affected |
|---|---|---|
| `adminCog.py` | 22 | All 11 admin commands |
| `inventoryCog.py` | 6 | `/inventory`, `/equip`, `/unequip`, etc. |
| `schedulerCog.py` | 6 | All scheduler commands |
| `playerCog.py` | 5 | `/profile`, `/leaderboard`, `/prestige`, etc. |
| `shopCog.py` | 4 | `/shop`, `/buy`, `/sell`, `/shops` |
| `bountyCog.py` | 4 | `/check`, `/bounties`, `/route`, etc. |
| `shipsCog.py` | 4 | `/ships`, `/ship`, `/setactive`, `/nickname` |
| `duelCog.py` | 3 | `/duel-challenge`, `/duel-accept`, `/duel-reject` |
| `aboutCog.py` | 2 | `/about`, `/list_category` |
| **Total** | **56** | |

All uses are `ephemeral=True` — the URL is visible only to the user who triggered the command. This limits real-world impact but is still a design smell.

---

## Recommended Fixes

### 12.5a — Surgical fix

**Option A** (preferred, no migration required): Add cascade to the ORM relationship.

`guild_config.py:91`, change:
```python
shops: Mapped[list["GuildShop"]] = relationship("GuildShop", back_populates="guild_config")
```
to:
```python
shops: Mapped[list["GuildShop"]] = relationship(
    "GuildShop", back_populates="guild_config", cascade="all, delete-orphan"
)
```

Effect: When `db.delete(guild_config_instance)` is called, SQLAlchemy will `DELETE FROM guild_shops WHERE guild_id = <value>` before `DELETE FROM guild_configs WHERE ...`. No migration needed — this is ORM behavior, not schema change.

**Option B** (migration required): Add DB-level `ON DELETE CASCADE`.

Change `GuildShop.guild_id` FK:
```python
guild_id: Mapped[int] = mapped_column(
    BigInteger, ForeignKey(f"{TableNames.GuildConfigs.value}.guild_id", ondelete="CASCADE"), nullable=False
)
```
AND add `passive_deletes=True` to `GuildConfig.shops` relationship.

Requires a new Alembic migration to drop+recreate the FK constraint with `ON DELETE CASCADE`.

**Option A is preferred** — single-line ORM change, no migration, consistent with existing codebase patterns.

### 12.5b — Theme-bundle fix

Replace all 56 occurrences of:
```python
except httpx.HTTPStatusError as e:
    await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
```
with a sanitized variant that:
- Shows status code and phrase (useful to the user) but NOT the raw URL
- Distinguishes 4xx (client error, user can act) from 5xx (server error, user cannot act)

Example:
```python
except httpx.HTTPStatusError as e:
    status = e.response.status_code
    if status == 404:
        await interaction.followup.send("❌ Not found.", ephemeral=True)
    elif status == 400:
        detail = e.response.json().get("detail", "Bad request.")
        await interaction.followup.send(f"❌ {detail}", ephemeral=True)
    elif 400 <= status < 500:
        await interaction.followup.send(f"❌ Request error ({status}).", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ Server error ({status}) — please try again.", ephemeral=True)
```

A shared helper function (e.g., `utils/error_utils.py`) would avoid duplicating this logic 56 times. Add a `flogger.error(f"API error {status}: {e.request.url.path}")` inside the handler to preserve internal diagnostics without exposing the URL to the user.

---

## Related Defects

- **B.25** — Discord 3-second interaction timeout on admin commands. Not related to 12.5a/12.5b but shares the same `adminCog` surface area.
- **B.30** — Silent destructive write on `PUT /api/v1/jobs/{job_id}`. Same class: user-visible error handling gaps.
- **B.27** — `/scheduler_view` with nonexistent job_id shows raw Discord "This interaction failed". Related error-handling gap pattern.

---

*Last updated: 2026-04-28 — read-only recon by developer*
