# Design: Manual Tier Promotion + XP Threshold Admin Command

**Author**: Architect Agent
**Date**: 2026-04-05
**Status**: READY FOR IMPLEMENTATION

---

## Executive Summary

Two features to enhance the BountyBot tier progression system:

1. **Manual Tier Promotion**: Players opt-in to tier advancement instead of being auto-promoted when XP crosses thresholds. XP accumulates freely; tier only changes when the player explicitly promotes.
2. **XP Threshold Admin Command**: Discord slash command for guild admins to configure per-guild XP thresholds for tier advancement.

---

## Current Architecture Analysis

### Two Parallel Systems (Important Context)

The codebase has two XP-based classification systems that serve different purposes:

| System | Path | Output | Used By |
|--------|------|--------|---------|
| **XP Thresholds** | `guild_config.xp_thresholds` → `_calculate_tier_from_xp()` | Tier name (Bronze/Silver/Gold/Platinum) | `bounty_service` (bounty access), `shop_service` (shop access), `prestige` (eligibility) |
| **Level + Division** | `XP_LEVEL_BOUNDARIES` → `calculate_user_level()` → `DivisionService` | Level 0-10, Division (bronze/silver/gold) | `add_xp()` return data, prestige level check |

**Key finding**: These systems are independent. `player.tier` is stored in the database and drives game access. The level/division system is purely informational for the `add_xp()` return payload.

### Two XP Mutation Paths

| Method | Called By | Touches Tier? | Used In Gameplay? |
|--------|-----------|---------------|-------------------|
| `add_xp()` (line 366) | Bounty reward distribution | **NO** — does not modify tier | Yes — primary XP earning path |
| `update_player_xp()` (line 149) | Admin "Set XP", `PUT /players/{id}/xp` | **YES** — auto-advances tier (lines 164-169) | No — admin-only |

The auto-advancement only occurs on the admin path. Normal gameplay already does not auto-promote. This design formalizes and completes that pattern.

### Downstream Consumers of `player.tier`

| Consumer | Location | How tier is used |
|----------|----------|-----------------|
| Bounty system | `bounty_service.py:655` | `division = player.tier.lower()` — determines which bounties player can check |
| Shop system | `shop_service.py:679-684` | `_can_access_tier(player.tier, shop_item.tier)` — tier hierarchy gate |
| Profile display | `playerCog.py:52,56` | Embed color + "Tier" field |
| Prestige guard | `playerCog.py:212` | `player_data["tier"] != "Platinum"` — must be Platinum to prestige |
| Leaderboard | `playerCog.py:170` | Displayed next to player name |

All consumers read `player.tier` directly. None will break — they'll simply see the tier the player has chosen to promote to.

---

## Feature 1: Manual Tier Promotion

### Behavioral Change

| Aspect | Before | After |
|--------|--------|-------|
| XP crosses threshold | Tier auto-advances (in `update_player_xp()` only) | Tier stays; player sees eligibility indicator |
| How tier changes | Automatically via admin XP set | Player runs `/promote` command |
| Admin sets XP | Tier auto-recalculated | Tier unchanged; admin can set tier separately |
| Prestige | Requires Platinum tier + level 10 | Same (player must manually promote to Platinum first) |

### Tier Ordering

```
Bronze (1) → Silver (2) → Gold (3) → Platinum (4)
```

Already defined in `Player.tier_level` property (player.py:76-80).

### Promotion Rules

1. **Step-by-step only**: A Bronze player must promote to Silver before Gold, regardless of XP
2. **No demotion**: Once promoted, tier is permanent (until prestige reset)
3. **XP preserved**: Promotion does not reset or modify XP
4. **Guild-specific thresholds**: Promotion eligibility uses the guild's configured `xp_thresholds`
5. **Platinum is ceiling**: Cannot promote beyond Platinum; Platinum leads to prestige

### Service Layer Changes

#### Remove auto-advancement from `update_player_xp()`

**File**: `services/bot-core/src/services/player_service.py`

Remove lines 164-170 (the tier auto-advancement block). After this change, `update_player_xp()` only sets `player.xp` and commits — no tier side effects.

The existing `_calculate_tier_from_xp()` method stays — it's reused by the new promotion logic.

#### New method: `get_promotion_status()`

**File**: `services/bot-core/src/services/player_service.py`

Responsibility:
- Load player by ID
- Load guild config for XP thresholds
- Calculate the highest tier the player's XP qualifies for (using `_calculate_tier_from_xp()`)
- Determine the player's next tier (current tier level + 1)
- Compute can_promote: eligible tier level >= next tier level AND current tier is not Platinum

Return dict:
```
{
    "player_id": int,
    "current_tier": str,          # e.g., "Bronze"
    "current_tier_level": int,    # e.g., 1
    "eligible_tier": str,         # Highest tier XP qualifies for, e.g., "Gold"
    "next_tier": str | None,      # Next tier above current, e.g., "Silver" (None if Platinum)
    "can_promote": bool,          # True if eligible for next tier
    "xp": int,                    # Current XP
    "xp_threshold_for_next": int | None,  # XP needed for next tier (None if Platinum)
    "xp_surplus_for_next": int | None,    # XP beyond next threshold (None if not eligible)
}
```

#### New method: `promote_player()`

**File**: `services/bot-core/src/services/player_service.py`

Responsibility:
- Load player by ID
- Load guild config for XP thresholds
- Calculate eligible tier from current XP
- Determine next tier (current + 1)
- Validate: current tier is not Platinum (ValueError: "Already at maximum tier")
- Validate: eligible tier level >= next tier level (ValueError: "Not eligible for promotion. Need {threshold} XP for {next_tier}, currently have {xp}")
- Set `player.tier = next_tier`
- Commit and refresh
- Return result dict

Return dict:
```
{
    "player_id": int,
    "old_tier": str,
    "new_tier": str,
    "xp": int,
    "eligible_for_next": bool,    # Can promote again immediately?
    "next_tier": str | None,      # Next tier after new tier (None if now Platinum)
}
```

### API Layer Changes

#### New endpoint: `GET /api/v1/players/{player_id}/promotion-status`

**File**: `services/bot-core/src/api/routers/players.py`

- Response model: `PromotionStatusResponse`
- Calls `player_service.get_promotion_status(db, player_id)`
- Error 404 if player not found
- Error 500 on unexpected failure

#### New endpoint: `PUT /api/v1/players/{player_id}/promote`

**File**: `services/bot-core/src/api/routers/players.py`

- No request body (empty PUT — always promotes to next tier)
- Response model: `PromoteResponse`
- Calls `player_service.promote_player(db, player_id)`
- Error 400 if not eligible or at max tier
- Error 404 if player not found
- Error 500 on unexpected failure

#### Modified endpoint: `PUT /api/v1/players/{player_id}/xp`

**File**: `services/bot-core/src/api/routers/players.py`

- No API signature changes
- Behavioral change: response no longer reflects auto-changed tier

### Schema Changes

#### New schema: `PromotionStatusResponse`

**File**: `services/bot-core/src/api/schemas/players_schema.py`

```
PromotionStatusResponse:
    player_id: int
    current_tier: str
    current_tier_level: int
    eligible_tier: str
    next_tier: str | None
    can_promote: bool
    xp: int
    xp_threshold_for_next: int | None
    xp_surplus_for_next: int | None
```

#### New schema: `PromoteResponse`

**File**: `services/bot-core/src/api/schemas/players_schema.py`

```
PromoteResponse:
    player_id: int
    old_tier: str
    new_tier: str
    xp: int
    eligible_for_next: bool
    next_tier: str | None
```

### Discord Gateway Changes

#### New command: `/promote`

**File**: `services/discord-gateway/src/cogs/playerCog.py`

- No parameters
- No admin check (any player can promote themselves)
- Flow:
  1. Defer thinking
  2. POST /players/ to ensure player exists
  3. PUT /players/{id}/promote
  4. On success: embed with old tier → new tier, tier color, and whether further promotion is available
  5. On 400 error: parse detail message, show current tier, XP, and what's needed
  6. Standard error handling

Success embed fields:
- Title: "⬆️ Tier Promoted!"
- Description: "You have advanced from **{old_tier}** to **{new_tier}**!"
- Color: new tier's color
- Fields: "New Tier", "XP", "Next Promotion" (eligible or threshold needed)

Error embed (not eligible):
- Title: "❌ Cannot Promote"
- Description: detail message from API
- Color: current tier's color

#### Modified command: `/profile`

**File**: `services/discord-gateway/src/cogs/playerCog.py`

After fetching player data and statistics, make an additional call:
- GET /players/{player_id}/promotion-status

Add a field to the profile embed:
- If `can_promote` is true: field name "Promotion", value "⬆️ **Eligible for {next_tier}!** Use `/promote`"
- If `can_promote` is false and `next_tier` is not None: field name "Next Tier", value "{next_tier} ({xp_threshold_for_next:,} XP needed)"
- If `next_tier` is None (Platinum): field name "Tier", value "🏆 Maximum Tier"

The promotion status call should be non-fatal — if it fails, the profile still displays without the promotion indicator.

---

## Feature 2: XP Threshold Admin Command

### Backend Status

**Already fully implemented** — no backend changes needed:

| Component | Status | Location |
|-----------|--------|----------|
| `PUT /config/guild/{guild_id}/xp-thresholds` | ✅ Exists | config.py:283-321 |
| `UpdateXPThresholdsRequest` schema | ✅ Exists | config_schema.py:59-61 |
| `config_service.update_xp_thresholds()` | ✅ Exists | config_service.py:123-145 |
| Validation (ascending, positive) | ✅ Exists | config_service.py:127-136 |
| Display in /admin_config view | ✅ Exists | adminCog.py:487-493 |

### Discord Gateway Changes

#### New command: `/admin_config_xp`

**File**: `services/discord-gateway/src/cogs/adminCog.py`

Follows the established pattern of `/admin_config_bounty` and `/admin_config_shop`.

Parameters:
- `action`: Choice — "View" or "Update"
- `silver`: int | None — XP threshold for Silver tier
- `gold`: int | None — XP threshold for Gold tier
- `platinum`: int | None — XP threshold for Platinum tier

Decorated with `@is_admin()`.

**"View" action:**
1. GET /config/guild/{guild_id}
2. Extract `xp_thresholds` from response
3. Display embed with Silver, Gold, Platinum thresholds

**"Update" action:**
1. Require all three thresholds (silver, gold, platinum) — if any missing, show error
2. Client-side pre-validation: silver < gold < platinum
3. PUT /config/guild/{guild_id}/xp-thresholds with `{"thresholds": {"Silver": silver, "Gold": gold, "Platinum": platinum}}`
4. On success: embed showing old → new thresholds
5. On 400 error: show validation message from API

---

## Edge Cases

### 1. Admin changes thresholds while players have accumulated XP

**Scenario**: Silver threshold is 1000. Player at Bronze with 1500 XP (eligible but hasn't promoted). Admin raises Silver threshold to 2000.

**Behavior**: Player is no longer eligible for promotion. Player stays at Bronze. `/profile` now shows "Silver (2,000 XP needed)".

**Rationale**: No retroactive changes. The player chose not to promote when eligible.

### 2. Demotion prevention after threshold increase

**Scenario**: Silver threshold was 500. Player promoted to Silver at 600 XP. Admin raises Silver threshold to 1000.

**Behavior**: Player stays at Silver. Their tier is not recalculated or reverted. Promotion is a one-way operation.

**Rationale**: Respects player achievement. Tier is a "high-water mark" — it can only go up (via promote) or reset (via prestige).

### 3. Admin sets XP to 0

**Scenario**: Player is at Gold tier with 10,000 XP. Admin sets XP to 0.

**Behavior**: Player stays at Gold tier with 0 XP. The admin must separately set the tier if they want to demote the player. The `/profile` would show Gold tier with 0 XP (no promotion available since they're already higher than eligible).

**Note**: The admin can use a future tier-set mechanism or player reset to handle this case.

### 4. Rapid double-promote

**Scenario**: Player at Bronze with 20,000 XP (eligible for Platinum). Player hits /promote twice rapidly.

**Behavior**: First promote succeeds (Bronze → Silver). Second promote also succeeds (Silver → Gold). Both are valid operations. The player would need to /promote twice more for Gold → Platinum.

**Note**: Each `promote_player()` call loads the current state from the database, so there's no race condition — just sequential valid promotions.

### 5. Player with enough XP for Platinum at Bronze

**Scenario**: Player has been earning XP for months at Bronze tier. They have 50,000 XP.

**Behavior**: Player must promote three times: /promote (→ Silver), /promote (→ Gold), /promote (→ Platinum). Each time, the success embed shows "eligible for next promotion" so the player knows they can keep going.

---

## Implementation Order

### Phase 1: New Capabilities (bot-core, additive, no breaking changes)

| Step | Files | Description |
|------|-------|-------------|
| 1a | `players_schema.py` | Add `PromotionStatusResponse` and `PromoteResponse` schemas |
| 1b | `player_service.py` | Add `get_promotion_status()` method |
| 1c | `player_service.py` | Add `promote_player()` method |
| 1d | `players.py` router | Add `GET /players/{player_id}/promotion-status` endpoint |
| 1e | `players.py` router | Add `PUT /players/{player_id}/promote` endpoint |
| 1f | Tests | Service tests for `get_promotion_status()` and `promote_player()` |
| 1g | Tests | Router tests for new endpoints |

### Phase 2: Behavioral Change (bot-core, breaking change)

| Step | Files | Description |
|------|-------|-------------|
| 2a | `player_service.py` | Remove auto-tier-advancement from `update_player_xp()` (lines 164-170) |
| 2b | Tests | Update any tests that assert tier changes in `update_player_xp()` |

### Phase 3: Discord Commands (discord-gateway)

| Step | Files | Description |
|------|-------|-------------|
| 3a | `playerCog.py` | Add `/promote` command |
| 3b | `playerCog.py` | Update `/profile` to show promotion eligibility |
| 3c | `adminCog.py` | Add `/admin_config_xp` command |
| 3d | Tests | Cog tests for `/promote`, updated `/profile`, `/admin_config_xp` |

---

## Acceptance Criteria

### Feature 1: Manual Tier Promotion

**AC-1**: When a player earns experience points that cross a tier threshold, the system shall retain the player's current tier without automatically advancing it.

**AC-2**: A player can view their promotion eligibility status within their profile, displaying the next available tier and whether they qualify based on accumulated experience points.

**AC-3**: A player can request promotion to the next tier when their accumulated experience points meet or exceed the guild's configured threshold for that tier.

**AC-4**: A player attempting promotion without sufficient experience points receives an error indicating the required amount and their current amount.

**AC-5**: Promotion advances the player by exactly one tier per request, even when experience points qualify for a higher tier.

**AC-6**: A player at the highest non-prestige tier (Platinum) attempting promotion receives an error indicating they are at the maximum tier.

**AC-7**: A player who has promoted retains all accumulated experience points after promotion (no XP reset on promote).

**AC-8**: After promotion, the bounty system restricts the player to bounties matching their new tier.

**AC-9**: After promotion, the shop system grants the player access to items up to and including their new tier.

**AC-10**: Prestige requires the player to have manually promoted to the highest non-prestige tier before initiating the prestige process.

**AC-11**: An administrator setting a player's experience points via administrative tools shall not automatically change the player's tier.

**AC-12**: Once promoted, a player retains their tier even if an administrator subsequently raises the experience threshold above the player's current experience points (no demotion).

**AC-13**: The promotion status endpoint returns the player's current tier, eligible tier, whether promotion is available, and the experience point threshold for the next tier.

**AC-14**: The promote endpoint returns the previous tier, new tier, current experience points, and whether the player is eligible for further promotion.

### Feature 2: XP Threshold Admin Command

**AC-15**: An administrator can view the current experience point thresholds for all tiers in a guild.

**AC-16**: An administrator can update the experience point thresholds for all tiers in a guild, specifying values for each tier.

**AC-17**: The system rejects threshold updates where tier thresholds are not in strictly ascending order.

**AC-18**: The system rejects threshold updates containing zero or negative values.

**AC-19**: A non-administrator attempting to update experience thresholds receives a permission denied error.

**AC-20**: After updating thresholds, existing players' tiers remain unchanged (no retroactive promotion or demotion).

**AC-21**: The updated thresholds take effect immediately for subsequent promotion eligibility checks.

**AC-22**: The admin command confirms the update by displaying the updated threshold values.

**AC-23**: The configuration validation endpoint reports an error when tier thresholds are not in ascending order.

---

## Guidance for Agents

### Developer Guidance

**For Feature 1 (bot-core service layer):**
- `_calculate_tier_from_xp()` stays as-is — reuse it in both `get_promotion_status()` and `promote_player()`
- Use the `Player.tier_level` property (player.py:76-80) for tier comparison logic. Define a reverse mapping (level → name) for determining `next_tier`: `{1: "Bronze", 2: "Silver", 3: "Gold", 4: "Platinum"}`
- `promote_player()` needs `config_repo.get_by_guild_id()` to fetch XP thresholds — follow the same pattern as `update_player_xp()` (line 165)
- For the `promote_player()` commit, use the same pattern as `prestige_player()` — direct attribute set + commit + refresh
- The `PUT /promote` endpoint should use `PUT` (idempotent in spirit — promoting when already at next tier is a 400, not a side effect)

**For Feature 1 (discord-gateway cog layer):**
- Follow the exact pattern of `/prestige` in `playerCog.py` for the `/promote` command — defer, resolve player, call API, handle success/error
- For the profile enhancement, wrap the promotion-status call in a try/except so profile still works if the status endpoint fails (non-fatal enhancement)
- Use `_get_tier_color()` (already exists at line 320) for embed colors

**For Feature 2:**
- Model the `/admin_config_xp` command after `/admin_config_bounty` (adminCog.py:950-1032) — same view/update action pattern, same `@is_admin()` decorator
- For the "Update" action, require all three thresholds. If any is None, send an error message explaining all three are required. This matches the API's validation which requires Silver, Gold, AND Platinum
- Pre-validate `silver < gold < platinum` in the cog to give immediate feedback before the API call

### Tester Guidance

**Critical test scenarios for Feature 1:**
1. Player earns XP via `add_xp()` — verify tier does NOT change (already the case, but needs explicit test)
2. Admin sets XP via `update_player_xp()` — verify tier does NOT change (behavioral change)
3. `get_promotion_status()` at Bronze with 0 XP — not eligible, shows Silver threshold
4. `get_promotion_status()` at Bronze with 1500 XP (Silver=1000, Gold=5000) — eligible for Silver
5. `promote_player()` at Bronze with 1500 XP — succeeds, tier → Silver
6. `promote_player()` at Bronze with 20000 XP — succeeds to Silver (not Gold, no skipping)
7. `promote_player()` at Bronze with 500 XP — fails with clear error
8. `promote_player()` at Platinum — fails "already at max"
9. `promote_player()` twice in succession (Bronze→Silver, Silver→Gold) with sufficient XP
10. Promotion status after admin raises threshold above player's XP (player still at their tier)
11. `/profile` shows promotion indicator when eligible
12. `/profile` shows threshold needed when not eligible

**Critical test scenarios for Feature 2:**
1. `/admin_config_xp view` — displays current thresholds
2. `/admin_config_xp update` with valid ascending values — success
3. `/admin_config_xp update` with non-ascending values — error
4. `/admin_config_xp update` with negative values — error
5. `/admin_config_xp update` with missing tier — error
6. Non-admin runs command — permission denied
7. After threshold update, promotion eligibility uses new thresholds

---

## Files Changed Summary

### bot-core (service)

| File | Change Type | Description |
|------|-------------|-------------|
| `src/services/player_service.py` | Modified | Remove auto-advancement from `update_player_xp()`; add `get_promotion_status()` and `promote_player()` |
| `src/api/schemas/players_schema.py` | Modified | Add `PromotionStatusResponse` and `PromoteResponse` |
| `src/api/routers/players.py` | Modified | Add `GET /players/{id}/promotion-status` and `PUT /players/{id}/promote` |
| `tests/services/test_player_service.py` | Modified | Tests for new methods; update tests for removed auto-advancement |
| `tests/api/test_players_router.py` | Modified | Tests for new endpoints |

### discord-gateway (UI)

| File | Change Type | Description |
|------|-------------|-------------|
| `src/cogs/playerCog.py` | Modified | Add `/promote` command; update `/profile` for promotion indicator |
| `src/cogs/adminCog.py` | Modified | Add `/admin_config_xp` command |
| `tests/cogs/test_playerCog.py` | Modified | Tests for `/promote` and updated `/profile` |
| `tests/cogs/test_adminCog.py` or new file | Modified | Tests for `/admin_config_xp` |

**Total files**: ~8-10 (no new files, no model changes, no migrations)
