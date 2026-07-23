# AGENTS.md - api/schemas

Pydantic v2 request/response schema modules for bot-core.

---

## Pydantic v2 Conventions

All schemas in this directory use **Pydantic v2** exclusively. The following conventions are mandatory:

### Model Config

```python
from pydantic import BaseModel, ConfigDict


class MyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # ← Pydantic v2
    # NOT:  class Config: orm_mode = True  (Pydantic v1 — BANNED)
```

`from_attributes=True` allows constructing the schema directly from ORM model instances.

### Serialization

```python
data = my_schema_instance.model_dump()  # ← Pydantic v2
# NOT:  my_schema_instance.dict()             (Pydantic v1 — BANNED)

data = my_schema_instance.model_dump_json()  # JSON string
```

### Validation

```python
obj = MyRequest.model_validate(raw_dict)  # ← Pydantic v2
# NOT:  MyRequest.parse_obj(raw_dict)          (Pydantic v1 — BANNED)
```

### Field Validation

```python
from pydantic import Field


# Real examples from players_schema.py:
class UpdateCreditsRequest(BaseModel):
    credits: int = Field(ge=0, description="Credits must be non-negative")
    update_lifetime: bool = Field(default=True, description="Whether to update lifetime credits")


class UpdateTierRequest(BaseModel):
    tier: str = Field(
        pattern="^(Bronze|Silver|Gold|Platinum)$", description="Must be Bronze, Silver, Gold, or Platinum"
    )
```

---

## Schema Organization Conventions

Each schema module is named `<router_name>_schema.py` and mirrors its corresponding router, with two exceptions: `loadout_schema.py` (shared `LoadoutResponse` used by both `/players/{id}/loadout` and `/bounties/{id}/loadout`) has no router of its own, and `data.py` / the `announcements/` routers have no dedicated schema module.

### Naming Pattern

- **Request models**: usually `<Action><Resource>Request` (e.g., `CreatePlayerRequest`, `UpdateCreditsRequest`); a few modules deviate (`BountyCreateRequest`, `DuelRequestCreate`)
- **Response models**: `<Resource>Response` (e.g., `PlayerResponse`, `ShipResponse`)
- **Result models**: `<Action>Response` (e.g., `PrestigeResponse`, `TransferCreditsResponse`)
- **List responses**: returned as `list[XxxResponse]` directly (there are no `XxxListResponse` wrappers)

### Request vs Response

- **Request schemas** represent the JSON body of incoming POST/PUT requests. They should be strict with validation (`Field(ge=0)`, `pattern=...`).
- **Response schemas** represent the JSON body returned. Those populated from ORM objects carry `model_config = ConfigDict(from_attributes=True)` (not every response model does — e.g. `admin_schema.py`, `health_schema.py`, and `shops_schema.py` build their responses field-by-field and use plain `BaseModel`).

---

## All 15 Schema Modules

| File | Key Request Models | Key Response Models |
|---|---|---|
| `about_schema.py` | — | `ItemResponse` (base) + `ModuleResponse`, `WeaponResponse`, `PrimaryWeaponResponse`, `SecondaryWeaponResponse`, `TurretWeaponResponse`, `ShipResponse`, `CriminalResponse`, `SystemResponse`, `CommodityResponse` |
| `admin_schema.py` | `InitializeGuildRequest`, `UpdatePlayerCreditsRequest`, `UpdatePlayerXPRequest`, `AddInventoryItemRequest`, `RemoveInventoryItemRequest`, `RefreshShopRequest`, `UpdateShopConfigRequest`, `AdminGiveItemRequest`, `AdminRemoveItemRequest`, `AdminGiveShipRequest`, `AdminRemoveShipRequest`, `TransferShipRequest` | `GuildInitializationResponse`, `SystemHealthResponse` |
| `bounty_schema.py` | `BountyCreateRequest`, `BountyCheckRequest`, `CombatBonusRequest` | `BountyResponse`, `BountyPublicResponse`, `BountyCheckOutcome`, `BountyCheckResponse`, `CombatBonusResponse`, `ClearBountiesResponse`, `AdminSpawnResponse`. **PvC loot (T6):** `BountyCheckOutcome` (+ the legacy `BountyCheckResponse` single-bounty mirror) carries a nullable `loot` payload `{item_name, qty_looted, qty_total, outcome, tractor_emoji, ...}`, `outcome ∈ {looted, partial, failed, cargo_full}` (the `LootResult.outcome` `Literal`; the no-loot/no-beam case is sent as a null `loot` field — internal `none` never reaches the wire), and over-cap fields (`cargo_current`/`cargo_max`) backing the T7 `OVER_CAP` rejection. The bounty read payloads expose the criminal's `loot_cargo` for the pre-fight advertise line (T4b). |
| `combat_log_schema.py` | — | `CombatLogListItem`, `KeyEvent`, `CombatantSummary`, `CombatLogDetail` |
| `config_schema.py` | `UpdateConfigRequest`, `UpdateShopConfigRequest`, `UpdateXPThresholdsRequest`, `UpdateBountyConfigRequest`, `ResetGameConstantsRequest` | `GameConstantsOverridesMixin` (shared base), `GuildConfigResponse`, `ConfigValidationResponse`, `BountyConfigResponse`, `BountyConfigStatusResponse` |
| `discord_message_schema.py` | `DiscordMessageRequest` | `DiscordMessageResponse`, `EmbedPayloadDict` |
| `duel_schema.py` | `DuelRequestCreate` | `DuelRequestResponse`, `DuelResultResponse` |
| `health_schema.py` | — | `HealthResponse`, `SimpleHealthResponse` |
| `inventory_schema.py` | `AddItemRequest`, `RemoveItemRequest`, `TransferItemRequest` | `InventoryItemResponse`, `InventorySummaryResponse`, `ItemTransactionResponse`. **PvC loot (T1):** `TransferItemRequest.item_type` `Literal` includes `"commodity"` so `/give` of a commodity does not 422. (Note: `AddItemRequest`/`RemoveItemRequest` + the admin give/remove schemas still omit `"commodity"` — see CODE BUG note below; no live loot-path impact since loot writes bypass HTTP.) |
| `loadout_schema.py` | — | `LoadoutResponse` + parts: `EffectItem`, `LoadoutWeaponItem`, `LoadoutModuleItem`, `CargoItem`, `ShipStats` |
| `players_schema.py` | `CreatePlayerRequest`, `UpdateCreditsRequest`, `UpdateXPRequest`, `UpdateTierRequest`, `TransferCreditsRequest` | `PlayerResponse`, `PlayerStatisticsResponse`, `TransferCreditsResponse`, `PrestigeResponse`, `PromotionStatusResponse`, `PromoteResponse`, `DemoteResponse`, `TierChangeCooldownResponse` |
| `scheduler_schema.py` | `OneTimeJob`, `RecurringJob`, `UpdateJob` | `JobInfo` |
| `ships_schema.py` | `CreateShipRequest`, `UpdateLoadoutRequest`, `UpdateNicknameRequest`, `EquipItemRequest`, `UnequipItemRequest`, `EquipCheckRequest`, `TransferShipRequest` | `ShipResponse`, `ShipLoadoutSummaryResponse`, `EquipCheckResponse`, `TransferShipResponse` |
| `shops_schema.py` | `PurchaseRequest`, `SellRequest`, `RefreshShopRequest`, `ShipPurchaseRequest`, `ShipSellRequest` | `ShopItemResponse`, `ShopSummaryResponse`, `TransactionResponse` |
| `users_schema.py` | `CreateUserRequest`, `UpdateUserRequest` | `UserResponse` |

---

## Example: Schema Module Excerpt

```python
# api/schemas/players_schema.py (abridged — see the file for the full field list)
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    guild_id: int
    credits: int
    lifetime_credits: int
    xp: int
    tier: str
    prestige_count: int
    active_ship_id: int | None
    display_name: str | None = None
    guild_transfer_cooldown: datetime | None = None
    created_at: str
    updated_at: str
    # ... plus bounty/duel stat counters, xp_surplus, classic_mode, bounty_cooldown_end


class CreatePlayerRequest(BaseModel):
    discord_id: int
    guild_id: int
    discord_username: str | None = None
    display_name: str | None = None


class TransferCreditsRequest(BaseModel):
    source_player_id: int
    target_player_id: int
    amount: int = Field(gt=0)


class TransferCreditsResponse(BaseModel):
    source_player_id: int
    target_player_id: int
    amount: int
    source_remaining_credits: int
    target_new_credits: int


class PrestigeResponse(BaseModel):
    # B.48: level_before/division_before were renamed to tier_before/xp_before
    # alongside the deletion of the level/division progression system.
    player_id: int
    prestige_count: int
    tier_before: str
    xp_before: int
```

---

## Guidance for AI Agents

- **Never import Pydantic v1 APIs** (`from pydantic import validator`, `class Config`, `.dict()`, `.parse_obj()`)
- **Always add `model_config = ConfigDict(from_attributes=True)`** to response schemas that will be populated from ORM objects
- **Use `Field()` for validation** — `ge`, `gt`, `le`, `lt`, `pattern`, `min_length`, `max_length`
- **Datetime fields**: `created_at`/`updated_at` are returned as `str` (`.isoformat()`); some newer fields (e.g. `PlayerResponse.guild_transfer_cooldown`, `bounty_cooldown_end`) are typed `datetime | None` — match the convention of the surrounding module
- **Optional fields**: Use `field: SomeType | None = None` (Python 3.10+ union syntax)
- **BigInteger Discord IDs**: Map to `int` in Python (Pydantic handles large ints correctly)

---

*Last updated: 2026-06-20 (PvC loot: `loot` payload on `BountyCheckOutcome`,
`commodity` in `TransferItemRequest.item_type`).*
