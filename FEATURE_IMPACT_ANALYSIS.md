# Comprehensive Feature Impact Analysis Report

## Executive Summary

Two new features have been scoped for the BountyBot-Reborn-SamX Discord bot:

1. **Feature 1: Shop Refresh Channel Announcement** — Post per-tier shop inventory when the scheduler refreshes shops
2. **Feature 2: Bounty Announcement Layout Change** — Reorganize bounty embed field order

**Findings**:
- Both features are **highly supported** by existing architecture
- **No database changes** required for either feature
- **No new models or migrations** needed
- **Minimal architectural refactoring** required
- Features are **independent** and can be implemented in parallel
- Total estimated implementation: **~3 hours** (Feature 1: ~2h, Feature 2: ~1h)

---

## FEATURE 1: Shop Refresh Channel Announcement

### Requirement
When the scheduled shop refresh job runs, after refreshing the shop inventory for each tier, post a new embed to the guild's configured shop channel showing:
- One embed post **per tier** that was refreshed
- Bold header noting the **tier name**
- Embed color **based on the tier** (with no major refactoring)
- The **store inventory** at the time of the refresh (list of items for sale)

### Current Architecture

#### Entry Point: Scheduler Executor
**File**: `/proj/services/bot-core/src/utils/executors/shop_refresh_executor.py`

**Function**: `execute_shop_refresh_job(job_id: str, payload: dict) -> dict`

**Current Flow** (simplified):
1. Called by APScheduler every 6 hours (cron job `shop_refresh_default`)
2. For each guild in the system:
   - Calls `ShopService.refresh_shop(db, guild_id, tier)` for each tier
   - Returns result dict containing the refreshed items
   - **Currently**: Calls `_announce_shop_refresh()` ONCE with `tier=None` (line 122)
   - **Problem**: Single announcement doesn't reflect per-tier posting requirement

**Key code section** (lines 107-123):
```python
for config in guild_configs:
    gid = config.guild_id
    tier_results: dict = {}
    for t in _SHOP_TIERS:  # Bronze, Silver, Gold, Platinum
        tier_results[t] = await shop_service.refresh_shop(db, gid, t, force_tech_level)
    bulk_results[gid] = tier_results

    # ── Announce shop refresh to discord-gateway ───────────
    shop_channel_id = getattr(config, "shop_channel_id", None)
    _shop_ann_id = getattr(config, "shop_announcements_role_id", None)
    _bh_role_id = getattr(config, "bounty_hunter_role_id", None)
    mention_role_id = _shop_ann_id if isinstance(_shop_ann_id, int) else _bh_role_id
    await _announce_shop_refresh(job_id, gid, shop_channel_id, mention_role_id, tier=None)
```

#### Shop Refresh Service
**File**: `/proj/services/bot-core/src/services/shop_service.py`

**Key Method**: `refresh_shop(db: AsyncSession, guild_id: int, tier: str, force_tech_level: int | None = None)`

**Returns**: 
```python
{
    "status": "success",
    "tier": "Bronze",
    "items": [GuildShop(...), GuildShop(...), ...],  # List of refreshed items
    "tech_level": 5
}
```

**GuildShop Model Structure** (from `/proj/services/bot-core/src/persist/models/guild_shop.py`):
- `guild_id: int` — FK to guild config
- `tier: str` — "Bronze", "Silver", "Gold", or "Platinum"
- `tech_level: int` — TL 1-9
- `item_type: str` — "ship", "primary_weapon", "secondary_weapon", "turret_weapon", or "module"
- `item_name: str` — Name of the item
- `quantity: int` — Stack size available
- `price: int` — Cost in credits

#### Shop Announcement Module
**File**: `/proj/services/bot-core/src/utils/shop_announcement.py`

**Function**: `announce_shop_refresh(caller_label: str, guild_id: int, channel_id: int | None, bounty_hunter_role_id: int | None = None, tier: str | None = None) -> None`

**Current Behavior**:
- Accepts optional `tier` parameter
- If `tier` provided: builds single announcement saying "The {tier} shop has been restocked"
- If `tier` is None: generic "all tiers" message
- Posts to `/api/v1/channels/{channel_id}/messages` endpoint on discord-gateway
- **Hardcoded color**: Blue (3447003 / #3498DB) — NOT tier-dependent
- Single generic field "Tier Refreshed" or "Tiers Refreshed"
- **Missing**: Item inventory listing

**Current embed structure** (lines 94-106):
```python
announcement = {
    "content": {  # embed payload
        "title": "🛒 Shop Refreshed!",
        "description": description,
        "color": 3447003,  # BLUE — hardcoded
        "fields": [
            {"name": field_name, "value": field_value, "inline": False},
        ],
        "footer_text": "Use /shop to browse!",
    },
    "text_content": f"<@&{bounty_hunter_role_id}>" if bounty_hunter_role_id else None,
    "message_type": "default",
}
```

#### Guild Configuration
**File**: `/proj/services/bot-core/src/persist/models/guild_config.py`

**Relevant Fields**:
- `shop_channel_id: Mapped[int | None]` (line 28) — Channel to post shop announcements
- `shop_announcements_role_id: Mapped[int | None]` (line 41) — Role to mention for shop announcements
- `bounty_hunter_role_id: Mapped[int | None]` (line 35) — Fallback role if shop announcement role not set

The executor already fetches and uses these fields (lines 116-122 of executor).

#### Discord Posting Mechanism
**Gateway Endpoint**: `POST /api/v1/channels/{channel_id}/messages`

**Payload Schema**:
```json
{
  "content": { /* EmbedPayload dict */ },
  "text_content": "<@&role_id>" or null,
  "message_type": "default"
}
```

**Implementation Pattern** (from `shop_announcement.py` lines 109-120):
```python
async with httpx.AsyncClient() as client:
    resp = await client.post(
        f"{_GATEWAY_BASE_URL}/channels/{channel_id}/messages",
        json=announcement,
        timeout=10,
    )
resp.raise_for_status()
```

**Error Handling**: Non-fatal — failures are logged but don't block the job (lines 117-120)

### Tier Structure & Color Mapping

#### Current Tiers
Defined in multiple places:
- Executor: `_SHOP_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]` (line 31)
- Service: `VALID_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]` (line 72)

#### Existing Color Mappings in Codebase
**Faction colors** (from `bounty_announcement_payload.py`):
```python
FACTION_COLORS: dict[str, int] = {
    "terran": 15844367,      # #F1C40F (yellow/gold)
    "vossk": 1752220,        # #1ABC9C (teal)
    "midorian": 10038562,    # #992D22 (dark red)
    "nivelian": 2123412,     # #206694 (blue)
}
```

**Captured bounty color**: 3066993 (#2ECC71 - green)

**No tier→color mapping currently exists.**

### Implementation Scope

#### Changes Required
1. **shop_announcement.py**: 
   - Modify `announce_shop_refresh()` to accept and render shop items list
   - Add tier→color mapping (hardcoded or from config)
   - Build inventory display (item names, types, quantities, prices)

2. **shop_refresh_executor.py**:
   - Modify loop at line 113 to call `_announce_shop_refresh()` once per tier
   - Pass tier name and items list from `shop_service.refresh_shop()` result

3. **Possible enhancement**: Add `shop_tier_colors` to `GameConstants` for future flexibility

#### No Changes Needed
- ✅ `shop_service.py` — already provides items per tier
- ✅ `guild_config.py` — shop channel field already exists
- ✅ `guild_shop.py` — model structure adequate
- ✅ Discord gateway endpoints — already support the pattern

#### Database Changes
**None required** — all fields already exist

#### Migration/Schema Changes
**None required**

### Design Decisions

#### Tier Color Assignment
**Three options**:

**Option A: Hardcoded in shop_announcement.py** (RECOMMENDED for MVP)
```python
TIER_COLORS = {
    "Bronze": 10824603,      # #A5634B (brownish)
    "Silver": 11974326,      # #B6B6B6 (light gray)
    "Gold": 16776960,        # #FFFF00 (bright yellow)
    "Platinum": 15921906,    # #F2F2F2 (white-ish)
}
```
- Simplest implementation
- Sufficient for MVP
- Can refactor later if needed

**Option B: GameConstants** (medium flexibility)
- Store `SHOP_TIER_COLORS` on `GameConstants`
- Loadable via `GameConstants.load()` — env-overridable per deployment
- More future-proof

**Option C: GuildConfig JSON** (maximum flexibility)
- New JSON column `shop_tier_colors: dict[str, int]`
- Per-guild customizable
- Requires migration + schema update
- Overkill for initial feature

**Recommendation**: Start with **Option A** (hardcoded). Migrate to **Option B** in follow-up if flexibility needed.

#### Shop Inventory Display Format
**Suggested embed field format**:
```
🛒 Bronze Tier Shop (TL5)

Ships (2)
  • Betty ($50,000)
  • Interceptor ($75,000)

Weapons (3)
  • Flak Gun ($5,000)
  • Pulse Rifle ($8,000)
  • Missile Launcher ($12,000)

Modules (2)
  • Shield Module ($3,000)
  • Armour Module ($2,500)

Turrets (1)
  • Twin Turret ($4,000)
```

Alternatively, simpler format:
```
4 Ships, 3 Weapons, 2 Modules, 1 Turret (TL5)
```

---

## FEATURE 2: Bounty Announcement Layout Change

### Requirement
When posting a bounty announcement embed, reorganize the field order:
- **Current order assumption**: ... route/checked systems ... map at bottom ... Active Ship + ship stats
- **New order**: Map stays at bottom, but move "route" and "checked systems" fields to appear **RIGHT ABOVE** "Active Ship" and ship stats section

### Current Architecture

#### Bounty Announcement Entry Point
**File**: `/proj/services/bot-core/src/utils/executors/bounty_spawn_executor.py`

**Function**: `execute_bounty_spawn_one_job(job_id: str, payload: dict) -> dict`

**Relevant section** (lines 437-449):
```python
announcement = await build_bounty_announcement_request(
    db, bounty,
    criminal_icon=criminal_icon,
    route_map_url=route_map_url,
    bounty_hunter_role_id=mention_role_id,
    captured=False,
)

# Post to gateway
await _announce_bounty_spawn(job_id, gid, channel_id, announcement)
```

#### Payload Builder: The Core of Field Order Control
**File**: `/proj/services/bot-core/src/utils/bounty_announcement_payload.py`

**Function**: `build_bounty_announcement_request(db, bounty, *, criminal_icon, route_map_url, bounty_hunter_role_id, captured)`

**Returns structure**:
```python
{
    "text_content": "<@&role_id>" or None,
    "loadout_response": {...},  # LoadoutResponse-shaped dict
    "metadata": {
        "title": "Criminal Name" or "✅ Criminal Name — CAPTURED",
        "color": 15844367,  # Faction color or green-on-capture
        "footer_text": "Faction name" or None,
        "image_url": "route_map_url_or_empty",
        "captured": false,
        "prefix_fields": [...],   # Rendered BEFORE Active Ship
        "suffix_fields": [...]    # Rendered AFTER loadout sections
    }
}
```

**Key methods**:
1. `_build_prefix_fields(bounty, captured)` (lines 146-159)
2. `_build_suffix_fields(bounty)` (lines 162-170)

#### Current Prefix Fields
**Lines 155-159** (before Active Ship):
```python
return [
    {"name": "Difficulty", "value": f"T{bounty.tech_level}", "inline": True},
    {"name": "Reward Pool", "value": f"{bounty.reward:,} credits", "inline": True},
    {"name": "Bounty Ends", "value": bounty_ends_value, "inline": True},
]
```

#### Current Suffix Fields
**Lines 167-170** (AFTER all loadout sections):
```python
return [
    {"name": "Route", "value": _build_route_value(route, checked), "inline": False},
    {"name": "Checked Systems", "value": _build_checked_systems_value(checked), "inline": False},
]
```

#### Embed Builder: Where Fields Are Rendered
**File**: `/proj/services/discord-gateway/src/cogs/_shared/loadout_embed.py`

**Function**: `build_loadout_embed(response, viewer_is_owner_or_admin, *, title_override, color_override, footer_text, image_url, prefix_fields, suffix_fields, captured)`

**Embed Construction Order** (lines 119-146):
```python
# 0. Prefix fields (Difficulty, Reward Pool, Bounty Ends)
if prefix_fields:
    used = _render_extra_fields(embed, prefix_fields, budget, used)

if not captured:
    # 1. Active Ship
    name, value = _format_active_ship_field(response)
    _add_field_safe(embed, name, value)
    
    # 2. Ship Stats
    name, value = _format_ship_stats_field(response)
    _add_field_safe(embed, name, value)
    
    # 3. Loadout sections (weapons, modules, cargo)
    sections = _apply_truncation_strategy(...)
    for section_header, lines in sections:
        used = _render_section(embed, section_header, lines, budget, used)

# 4. Suffix fields (Route, Checked Systems)
if suffix_fields:
    used = _render_extra_fields(embed, suffix_fields, budget, used)
```

### Current Field Order in Bounty Announcements
1. **Prefix fields** (lines 119-121):
   - Difficulty (TL5) — inline
   - Reward Pool (50,000 credits) — inline
   - Bounty Ends (relative time) — inline

2. **Active Ship** (lines 124-126):
   - Ship name and emoji

3. **Ship Stats** (lines 129-132):
   - Armour, Handling, HP, DPS stats

4. **Loadout Sections** (lines 141-142):
   - Weapons <3/6>
   - Modules <2/4>
   - Cargo Hold (if visible)

5. **Suffix fields** (lines 145-146):
   - Route (with strikethrough/bold markdown)
   - Checked Systems (with blockquote formatting)

6. **Map image** (via `image_url` on line 109)

### Requested New Order
- **Route** and **Checked Systems** fields should move to **BEFORE** "Active Ship"
- Map stays at bottom ✓ (already the case via `image_url`)

### Implementation Approaches

#### Approach A: Move Route/Checked to Prefix Fields (RECOMMENDED)
**Complexity**: Very Low

**Changes**:
1. Modify `_build_prefix_fields()` to include Route and Checked Systems
2. Remove them from `_build_suffix_fields()`
3. Return new order:
   - Route
   - Checked Systems
   - Difficulty
   - Reward Pool
   - Bounty Ends

**New prefix_fields return** (in `bounty_announcement_payload.py`):
```python
def _build_prefix_fields(bounty, captured: bool) -> list[dict]:
    route: list[str] = list(bounty.route or [])
    checked = _project_checked(bounty)
    
    return [
        {"name": "Route", "value": _build_route_value(route, checked), "inline": False},
        {"name": "Checked Systems", "value": _build_checked_systems_value(checked), "inline": False},
        {"name": "Difficulty", "value": f"T{bounty.tech_level}", "inline": True},
        {"name": "Reward Pool", "value": f"{bounty.reward:,} credits", "inline": True},
        {"name": "Bounty Ends", "value": bounty_ends_value, "inline": True},
    ]
```

**New suffix_fields return**:
```python
def _build_suffix_fields(bounty) -> list[dict]:
    return []  # Empty — Route/Checked moved to prefix
```

**Pros**:
- ✅ Single file change (only `bounty_announcement_payload.py`)
- ✅ No gateway changes needed
- ✅ Leverages existing field-ordering infrastructure
- ✅ Minimal risk

**Cons**:
- ⚠ Visual balance changes (Difficulty/Reward move to bottom of prefix)
- ⚠ Requires moving/calling helper functions `_build_route_value()` and `_build_checked_systems_value()` from suffix builder

#### Approach B: Create New "Middle Fields" Between Active Ship and Stats
**Complexity**: Low-Medium

**Changes**:
1. Add new metadata field in payload: `"middle_fields": [...]`
2. Modify gateway embed builder to insert middle_fields between Active Ship and Ship Stats
3. Call new helper in `bounty_announcement_payload.py`

**Pros**:
- ✅ Preserves existing prefix field order (Difficulty/Reward/Ends stay at top)
- ✅ New visual flow: Difficulty/Reward → Active Ship → Checked Systems/Route → Stats → Loadout
- ✅ More structured design (3 field groups instead of 2)

**Cons**:
- ⚠ Requires changes to both bot-core AND gateway
- ⚠ Gateway embed builder code needs to change (lines 119-146 in loadout_embed.py)
- ⚠ More test coverage needed
- ⚠ Slightly higher risk

**Recommendation**: **Approach A** (move to prefix). Simpler, lower risk, achieves requirement.

### Current Field Content Builders

#### Route Field (`_build_route_value`)
**Lines 215-240**:
- Renders route systems with markdown status indicators
- "checked" → ~~strikethrough~~
- "recently_spotted" → **~~strikethrough~~**
- "found" → **bold**
- None → plain

Example: `System1, ~~System2~~, **System3**`

#### Checked Systems Field (`_build_checked_systems_value`)
**Lines 243-268**:
- Groups by status: checked, recently_spotted, found
- Each group wrapped in blockquote (> )
- Newline-separated

Example:
```
> ~~System1~~ ~~System2~~
> **~~System3~~**
> **System4**
```

#### Prefix Fields (`_build_prefix_fields`)
**Lines 146-159**:
- Difficulty: inline
- Reward Pool: inline
- Bounty Ends: inline (relative timestamp or "Captured")

### Data Dependencies
No new data needed — both Route and Checked Systems are already computed from the `Bounty` model:
- `bounty.route` — list of system names
- `bounty.checked` — dict of system → player_id
- `bounty.answer` — the target system

### Edge Cases

#### Captured Bounty
When `captured=True`:
- Active Ship, Ship Stats, and loadout sections are **omitted** (line 123 in loadout_embed.py)
- Prefix and suffix fields are **still rendered** (lines 120-121, 145-146)
- With Approach A (prefix move), Route/Checked would still display even on capture
- This is probably desired (shows where it was found and what was checked)

#### Very Long Route/Checked Systems
Discord limit: 1024 chars per field
- Gateway already handles continuation splits (lines 544-581 in loadout_embed.py)
- Route/Checked can span multiple continuation fields automatically
- No special handling needed

#### Missing Route or Checked Systems
- `_build_route_value()` returns "—" if no route (line 225)
- `_build_checked_systems_value()` returns "> *No systems checked yet*" if none (line 246)
- Both gracefully degrade

### Field Count Ceiling
Discord max 25 fields per embed.

**Typical bounty announcement field count**:
- Prefix fields: 3 (or 5 with Route/Checked)
- Active Ship: 1
- Ship Stats: 1
- Loadout sections: 3-5 (weapons, modules, cargo + continuations)
- Suffix fields: 0-2 (or 0 with Approach A)
- **Total**: 8-14 fields (well under 25)

With Approach A:
- Prefix fields: 5
- Active Ship: 1
- Ship Stats: 1
- Loadout sections: 3-5
- **Total**: 10-12 fields (still well under)

**No field-count issues expected.**

---

## IMPACTED FILES SUMMARY

### Feature 1: Shop Refresh Channel Announcement

| File | Type | Impact | Specific Changes |
|------|------|--------|------------------|
| `/proj/services/bot-core/src/utils/executors/shop_refresh_executor.py` | MODIFY | Change announcement call logic | Loop lines 107-113: Call `_announce_shop_refresh()` once per tier with items list from `tier_results[t]` |
| `/proj/services/bot-core/src/utils/shop_announcement.py` | MODIFY | Enhance embed building | 1. Add tier color mapping; 2. Accept items parameter; 3. Build inventory display fields |
| `/proj/services/bot-core/src/services/shop_service.py` | REFERENCE | No changes | Already provides items via `refresh_shop()` return value |
| `/proj/services/bot-core/src/persist/models/guild_config.py` | REFERENCE | No changes | Field `shop_channel_id` already exists |
| `/proj/services/bot-core/src/persist/models/guild_shop.py` | REFERENCE | No changes | Model structure already adequate |

### Feature 2: Bounty Announcement Layout Change

| File | Type | Impact | Specific Changes (Approach A) |
|------|------|--------|------|
| `/proj/services/bot-core/src/utils/bounty_announcement_payload.py` | MODIFY | Rearrange fields | 1. Move Route/Checked logic to `_build_prefix_fields()` (call `_build_route_value()` and `_build_checked_systems_value()`); 2. Return empty list from `_build_suffix_fields()` |
| `/proj/services/discord-gateway/src/cogs/_shared/loadout_embed.py` | NO CHANGE | Field order already correct | Rendering order already respects prefix/suffix field groups |
| `/proj/services/discord-gateway/src/cogs/bountyCog.py` | NO CHANGE | No cog changes | Cog doesn't build embed; gateway API handles it |

---

## NO CHANGES REQUIRED

- ✅ Database models and migrations
- ✅ Guild configuration schema
- ✅ Discord gateway REST endpoints
- ✅ Bounty model structure
- ✅ Shop model structure
- ✅ Repository interfaces

---

## COUPLING & DEPENDENCIES

### Feature 1 ↔ Feature 2 Coupling
**None** — features are completely independent.
- Feature 1 affects shop refresh announcements only
- Feature 2 affects bounty announcements only
- Can implement in parallel

### Inter-Service Dependencies

#### Feature 1 Dependencies
- bot-core → discord-gateway (HTTP POST to `/api/v1/channels/{id}/messages`)
- bot-core → PostgreSQL (for shop items lookup)
- Non-fatal error handling on gateway call (logged but doesn't block job)

#### Feature 2 Dependencies
- bot-core → discord-gateway (HTTP POST to bounty announcement endpoint)
- bot-core → database (for bounty data)
- No new external dependencies

---

## GOTCHAS & IMPORTANT NOTES

### Feature 1 Gotchas

1. **Tier Name Case-Sensitivity**
   - Executor uses: `["Bronze", "Silver", "Gold", "Platinum"]` (capitalized)
   - Database stores same case
   - String comparisons must preserve case

2. **Per-Tier Shop Refresh Semantics**
   - `refresh_shop()` clears and regenerates ONE tier at a time
   - Must announce AFTER each tier finishes (not after all tiers)
   - Executor loop already handles this; just need to move announce call inside tier loop

3. **Role Mention Placement**
   - Discord doesn't parse `@role` mentions in embed descriptions
   - Must be in `text_content` field (not `description`)
   - Code already does this correctly (line 104 in shop_announcement.py)

4. **Non-Fatal Errors**
   - If announcement fails, job must complete successfully
   - Failures logged but not propagated
   - Executor already handles this pattern (lines 117-120)

5. **No Default Tier Colors Exist**
   - Current code has no tier→color mapping
   - Must be added (recommend hardcoded dict for MVP)
   - Consider fallback color if tier name is unexpected

### Feature 2 Gotchas

1. **Field Order is Critical**
   - Discord renders fields in order received
   - Prefix fields must come before Active Ship/Stats
   - Suffix fields come after ALL loadout sections
   - No "middle" concept exists (yet)

2. **Large Field Values**
   - Route can be long (comma-separated systems)
   - Checked Systems can be long (blockquote-formatted)
   - Discord 1024-char per-field limit enforced
   - Gateway already handles continuation splits automatically

3. **Captured Bounty Special Case**
   - When `captured=True`, loadout detail sections are omitted
   - Prefix/suffix fields still render
   - Route/Checked will appear even on captured bounty
   - Probably desired, but worth noting

4. **Field Count Ceiling**
   - Discord max 25 fields per embed
   - Current bounties use ~8-14 fields
   - Moving Route/Checked to prefix adds 2 fields max
   - Safe margin (10-16 fields << 25 limit)

5. **Prefix Field Reordering**
   - Moving Route/Checked to prefix changes visual flow
   - Route/Checked will appear BEFORE Difficulty/Reward
   - May want to group differently (Route/Checked last in prefix)
   - Exact order is a UX decision, not a functional requirement

---

## IMPLEMENTATION ROADMAP

### Phase 1: Feature 1 (Shop Refresh) — ~2 hours

1. **Step 1**: Design tier colors (5 min)
   - Choose color palette (recommend hardcoded hex values)
   - Document decisions in code comments

2. **Step 2**: Modify `shop_announcement.py` (45 min)
   - Add tier color lookup dict
   - Modify `announce_shop_refresh()` signature to accept items list
   - Build inventory display (format items as embedded field)
   - Handle edge cases (no items, many items)

3. **Step 3**: Modify executor (20 min)
   - Move `_announce_shop_refresh()` call inside tier loop
   - Extract items from `tier_results[tier]`
   - Pass tier and items to announcement function

4. **Step 4**: Testing (30 min)
   - Local testing with mocked shop data
   - Verify per-tier announcements post correctly
   - Verify color renders on Discord
   - Verify role mentions work

### Phase 2: Feature 2 (Bounty Announcement) — ~1 hour

1. **Step 1**: Implement field rearrangement (30 min)
   - Modify `_build_prefix_fields()` to include Route/Checked
   - Remove Route/Checked from `_build_suffix_fields()`
   - Update function calls and return value

2. **Step 2**: Testing (20 min)
   - Verify field order in bounty spawn announcement
   - Verify field order on captured bounty
   - Verify large Route/Checked fields still render
   - Visual inspection on Discord

3. **Step 3**: Edge case validation (10 min)
   - Test with empty route
   - Test with no checked systems
   - Test with very long routes (50+ systems)

### Phase 3: Deployment & Validation — ~30 min

1. Run full test suite
2. Deploy to dev environment
3. Monitor scheduler logs (Feature 1)
4. Spawn test bounty (Feature 2)
5. Verify Discord embeds render correctly

---

## RISK ASSESSMENT

### Feature 1 Risk Level: **LOW**

**Why**:
- ✅ Uses existing patterns (HTTP POST, guild config fields)
- ✅ No database changes
- ✅ No new models or migrations
- ✅ Service already provides required data
- ✅ Non-fatal error handling already in place
- ⚠ Only new element: color mapping (easy to test, easy to adjust)

**Mitigations**:
- Test with real shop data in dev environment
- Color values easily adjustable if wrong
- Fallback color for unexpected tier names

### Feature 2 Risk Level: **VERY LOW**

**Why**:
- ✅ No database changes
- ✅ No new models
- ✅ Pure data rearrangement (no new logic)
- ✅ Existing handlers already support field reordering
- ✅ No impact on other features
- ✅ Field count well within Discord limits

**Mitigations**:
- Test field rendering on Discord client
- Verify field order in edge cases (captured, empty route)

---

## DEPLOYMENT CHECKLIST

### Before Merge
- [ ] Code review completed
- [ ] Unit tests pass (if added)
- [ ] No linting errors (Ruff)
- [ ] Documentation updated (if needed)

### Testing
- [ ] Feature 1: Shop refresh announces per tier
- [ ] Feature 1: Colors render correctly on Discord
- [ ] Feature 1: Role mention works
- [ ] Feature 1: Non-fatal error handling (break announcement, verify job completes)
- [ ] Feature 2: Bounty field order is correct
- [ ] Feature 2: Works with captured bounty
- [ ] Feature 2: Works with empty route/checked systems
- [ ] Feature 2: Works with very long route (50+ systems)

### Monitoring
- [ ] Scheduler logs show correct announcements
- [ ] Discord embeds render without errors
- [ ] No HTTP timeouts or connection failures
- [ ] No database exceptions

---

## SUMMARY TABLE

| Aspect | Feature 1 | Feature 2 |
|--------|-----------|-----------|
| **Requirement Type** | New feature | Layout change |
| **Entry Point** | Shop refresh job | Bounty spawn |
| **Files Modified** | 2 | 1 |
| **Files Added** | 0 | 0 |
| **Database Changes** | None | None |
| **Migrations Needed** | None | None |
| **New Models** | None | None |
| **Gateway Changes** | None | None |
| **Estimated Effort** | ~2 hours | ~1 hour |
| **Risk Level** | LOW | VERY LOW |
| **Can Parallel** | YES | YES |
| **Dependencies** | None | None |
| **Coupling** | None | None |

---

## CONCLUSION

Both features are **well-supported** by existing architecture:

1. **Shop Refresh Feature**: Requires enhancement to existing announcement logic + color mapping. No structural changes. Low risk.

2. **Bounty Announcement Feature**: Requires rearrangement of existing field groups. No new logic. Very low risk.

**Recommendation**: Proceed with implementation. Start with Feature 1 (more changes), then Feature 2 (simpler). Can be implemented in parallel if resources available.
