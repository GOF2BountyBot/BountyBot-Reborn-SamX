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
data = my_schema_instance.model_dump()        # ← Pydantic v2
# NOT:  my_schema_instance.dict()             (Pydantic v1 — BANNED)

data = my_schema_instance.model_dump_json()   # JSON string
```

### Validation

```python
obj = MyRequest.model_validate(raw_dict)       # ← Pydantic v2
# NOT:  MyRequest.parse_obj(raw_dict)          (Pydantic v1 — BANNED)
```

### Field Validation

```python
from pydantic import Field, field_validator, model_validator

class UpdateCreditsRequest(BaseModel):
    credits: int = Field(ge=0, description="Must be non-negative")
    tier: str = Field(pattern="^(Bronze|Silver|Gold|Platinum)$")
```

---

## Schema Organization Conventions

Each schema module is named `<router_name>_schema.py` and mirrors its corresponding router.

### Naming Pattern

- **Request models**: `<Action><Resource>Request` (e.g., `CreatePlayerRequest`, `UpdateCreditsRequest`)
- **Response models**: `<Resource>Response` (e.g., `PlayerResponse`, `ShipResponse`)
- **Result models**: `<Action>Response` (e.g., `PrestigeResponse`, `TransferCreditsResponse`)
- **List responses**: Either `list[XxxResponse]` returned directly, or a `XxxListResponse` wrapper

### Request vs Response

- **Request schemas** represent the JSON body of incoming POST/PUT requests. They should be strict with validation (`Field(ge=0)`, `pattern=...`).
- **Response schemas** represent the JSON body returned. They typically have `model_config = ConfigDict(from_attributes=True)` to support direct ORM-to-schema mapping.

---

## All 13 Schema Modules

| File | Key Request Models | Key Response Models |
|---|---|---|
| `about_schema.py` | — | `ShipResponse`, `ModuleResponse`, `PrimaryWeaponResponse`, `SecondaryWeaponResponse`, `TurretWeaponResponse`, `CriminalResponse`, `SystemResponse` |
| `admin_schema.py` | `GuildResetRequest`, `CreditUpdateRequest` | `AuditLogResponse`, `AdminActionResponse` |
| `bounty_schema.py` | `SpawnBountyRequest`, `CheckSystemRequest` | `BountyResponse`, `BountyCheckResponse` |
| `config_schema.py` | `CreateConfigRequest`, `UpdateConfigRequest` | `GuildConfigResponse` |
| `discord_message_schema.py` | `CreateDiscordMessageRequest` | `DiscordMessageResponse` |
| `duel_schema.py` | `DuelChallengeRequest`, `DuelResolutionRequest` | `DuelResponse`, `DuelResultResponse` |
| `health_schema.py` | — | `HealthResponse`, `SimpleHealthResponse` |
| `inventory_schema.py` | `EquipRequest`, `UnequipRequest`, `SellRequest`, `TransferItemRequest` | `InventoryResponse`, `InventoryItemResponse` |
| `players_schema.py` | `CreatePlayerRequest`, `UpdateCreditsRequest`, `UpdateXPRequest`, `UpdateTierRequest`, `TransferCreditsRequest` | `PlayerResponse`, `PlayerStatisticsResponse`, `PrestigeResponse`, `TransferCreditsResponse` |
| `scheduler_schema.py` | `OneTimeJob`, `RecurringJob`, `UpdateJob` | `JobInfo` |
| `ships_schema.py` | — | `ShipResponse`, `ShipListResponse` |
| `shops_schema.py` | `BuyRequest`, `SellToShopRequest` | `ShopResponse`, `ShopItemResponse`, `BuyResponse` |
| `users_schema.py` | `CreateUserRequest` | `UserResponse`, `UserWithPlayersResponse` |

---

## Example: Full Schema Module

```python
# api/schemas/players_schema.py
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    guild_id: int
    credits: int
    tier: str
    xp: int
    created_at: str
    updated_at: str


class CreatePlayerRequest(BaseModel):
    discord_id: int
    guild_id: int
    discord_username: str | None = None


class UpdateCreditsRequest(BaseModel):
    credits: int = Field(ge=0, description="Credits must be non-negative")
    update_lifetime: bool = Field(default=True)


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
- **Datetime fields**: Return as `str` (`.isoformat()`) in responses for consistent serialization; accept `datetime` in requests
- **Optional fields**: Use `field: SomeType | None = None` (Python 3.10+ union syntax)
- **BigInteger Discord IDs**: Map to `int` in Python (Pydantic handles large ints correctly)

---

*Last updated: 2026-03-16*
