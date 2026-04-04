# Task 0001: Complete Channel/Role/Announcement Redesign

## Attempt 1 [2026-04-04]
Iteration: 1
Scope: Architecture design for channel structure, role management, permission system, bounty announcements, and executor routing across bot-core and discord-gateway services.
Research: Analyzed 15 source files across bot-core models, schemas, services, executors, and discord-gateway cogs/utils. Reviewed Discord permission model, existing embed schema, EmojiService capabilities, MapRenderer limitations.
Decisions: See full design below.
Rationale: Design maximizes reuse of existing patterns (EmbedPayload, guild_setup utility, config storage) while introducing minimal breaking changes to the DB schema.
Guidance: See Acceptance Criteria and Implementation Plan sections.
Trade-offs: See Trade-offs section.
Risks: See Risks section.
Handoff Count: 1 of 8
Loop Count: 0 of 3
Tool Signatures: [read:guild_config.py (1/3), read:bounty.py (1/3), read:criminal.py (1/3), read:admin_schema.py (1/3), read:config_schema.py (1/3), read:bounty_schema.py (1/3), read:bounty_spawn_executor.py (1/3), read:shop_refresh_executor.py (1/3), read:guild_setup.py (1/3), read:adminCog.py (1/3), read:playerCog.py (1/3), read:setupCog.py (1/3), read:channels.py (1/3), read:message_schemas.py (1/3), read:bounty_service.py (1/3), read:map_renderer.py (1/3), read:emoji_service.py (1/3), read:game_constants.py (1/3), read:tablenames.py (1/3)]

---

## Current State Analysis

### What Exists Today

**GuildConfig model** (`guild_config.py`):
- `admin_role_id: BigInteger, nullable`
- `category_id: BigInteger, nullable`
- `bounty_channel_id: BigInteger, nullable` (single channel for ALL bounty announcements)
- `shop_channel_id: BigInteger, nullable`
- `general_channel_id: BigInteger, nullable`

**guild_setup.py** creates 3 channels under "BountyBot" category:
- `bounty-board` -> `bounty_channel_id`
- `shop` -> `shop_channel_id`
- `general` -> `general_channel_id`
- Category permissions: @everyone can read but not send; bot can read+send

**Bounty divisions**: `_BOUNTY_DIVISIONS = ["Bronze", "Silver", "Gold"]` (no Platinum)
- `GameConstants.DIVISION_NAMES = ["bronze", "silver", "gold"]`

**Announcements**: Single `bounty_channel_id` for all divisions; simple embed with generic title "New Bounty!"

**No @Bounty Hunter role** concept exists anywhere.

**MessageCreateRequest schema**: Only supports `content: EmbedPayload` - no plain text alongside embed for role mentions/pings.

---

## Acceptance Criteria

### AC-1: GuildConfig Schema Expansion
- **AC-1.1**: GuildConfig model stores separate channel IDs for each bounty board: `bronze_bounty_channel_id`, `silver_bounty_channel_id`, `gold_bounty_channel_id`, `platinum_bounty_channel_id` (all BigInteger, nullable)
- **AC-1.2**: GuildConfig model stores `bounty_hunting_channel_id` and `bounty_discussions_channel_id` (both BigInteger, nullable)
- **AC-1.3**: GuildConfig model stores `bounty_hunter_role_id` (BigInteger, nullable)
- **AC-1.4**: The columns `bounty_channel_id` and `general_channel_id` are removed from GuildConfig
- **AC-1.5**: An Alembic migration (0003) adds the new columns and removes the old ones, with `server_default=None` for all new nullable columns
- **AC-1.6**: All API schemas (`GuildConfigResponse`, `UpdateConfigRequest`, `InitializeGuildRequest`, `GuildInitializationResponse`) reflect the new column names

### AC-2: Channel Structure
- **AC-2.1**: When `/admin_setup` is executed, the system creates 7 text channels under the "BountyBot" category: `bronze-bounty-board`, `silver-bounty-board`, `gold-bounty-board`, `platinum-bounty-board`, `shop`, `bounty-hunting`, `bounty-discussions`
- **AC-2.2**: All 7 channel IDs plus the category ID are stored in GuildConfig after setup
- **AC-2.3**: Channel creation is idempotent — existing channels with matching names under the BountyBot category are reused

### AC-3: Role Structure
- **AC-3.1**: When `/admin_setup` is executed, the system creates a `@Bounty Hunter` role (if it does not already exist) and stores its ID as `bounty_hunter_role_id` in GuildConfig
- **AC-3.2**: If `admin_role` param is not provided to `/admin_setup`, the system creates `@BountyBot Admins` role (existing behavior preserved)
- **AC-3.3**: When `/admin_setup` is executed with missing optional params, the system returns an informative success message (not an error)

### AC-4: Channel Permissions
- **AC-4.1**: `@everyone` is denied `view_channel` on the BountyBot category (private category)
- **AC-4.2**: `@Bounty Hunter` role is granted `view_channel` on the BountyBot category
- **AC-4.3**: On `bronze-bounty-board`, `silver-bounty-board`, `gold-bounty-board`, `platinum-bounty-board`, and `shop` channels: `@Bounty Hunter` is denied `send_messages` (read-only)
- **AC-4.4**: On `bounty-hunting` channel: `@Bounty Hunter` is granted `send_messages` and `use_application_commands`
- **AC-4.5**: On `bounty-discussions` channel: `@Bounty Hunter` is granted `send_messages` and denied `use_application_commands`
- **AC-4.6**: `@BountyBot Admins` role has full access to all BountyBot channels
- **AC-4.7**: The bot user has full access to all BountyBot channels (read, send, manage messages)

### AC-5: Role Assignment
- **AC-5.1**: When a user executes `/profile`, the system assigns the `@Bounty Hunter` role to that user (if the role exists in GuildConfig and the user does not already have it)
- **AC-5.2**: Role assignment failure (missing permissions, role deleted) is logged but does not prevent `/profile` from completing successfully
- **AC-5.3**: Role assignment is idempotent — users who already have the role are not modified

### AC-6: Admin Uninstall Cleanup
- **AC-6.1**: When `/admin_uninstall` is confirmed, the system deletes all BountyBot channels (all 7 channels under the category)
- **AC-6.2**: When `/admin_uninstall` is confirmed, the system deletes the BountyBot category
- **AC-6.3**: When `/admin_uninstall` is confirmed, the system deletes the `@Bounty Hunter` role
- **AC-6.4**: Discord resource deletion failures are logged but do not block the overall uninstall operation
- **AC-6.5**: After Discord cleanup, bot-core database records are cleaned up (existing behavior preserved)

### AC-7: Platinum Division Support
- **AC-7.1**: The bounty spawn executor processes 4 divisions: Bronze, Silver, Gold, Platinum
- **AC-7.2**: GameConstants includes "platinum" in `DIVISION_NAMES` with appropriate division boundaries
- **AC-7.3**: Bounty spawning for Platinum division uses an appropriate tech-level center (e.g., 10)
- **AC-7.4**: `division_temperatures` default in GuildConfig includes "platinum" key

### AC-8: Per-Division Bounty Routing
- **AC-8.1**: When a bounty is spawned in the Bronze division, the announcement is posted to the `bronze_bounty_channel_id` channel
- **AC-8.2**: When a bounty is spawned in the Silver division, the announcement is posted to the `silver_bounty_channel_id` channel
- **AC-8.3**: When a bounty is spawned in the Gold division, the announcement is posted to the `gold_bounty_channel_id` channel
- **AC-8.4**: When a bounty is spawned in the Platinum division, the announcement is posted to the `platinum_bounty_channel_id` channel
- **AC-8.5**: If a division's channel is not configured, the announcement is skipped with a warning log

### AC-9: Rich Bounty Announcement Format
- **AC-9.1**: Bounty announcements use the criminal's name as the embed title
- **AC-9.2**: Bounty announcements use a faction-specific color (different color per faction: Nivelian, Vossk, Terran, Midorian, with a default fallback)
- **AC-9.3**: Bounty announcements include fields for: difficulty (tech level), reward pool, bounty expiry (Discord relative timestamp `<t:unix:R>` format)
- **AC-9.4**: Bounty announcements include the criminal's loadout information (ship name, weapon names, module names)
- **AC-9.5**: Bounty announcements include the route as comma-separated system names
- **AC-9.6**: Bounty announcements include the criminal's faction name in the footer

### AC-10: Role Mentions in Announcements
- **AC-10.1**: The MessageCreateRequest schema in discord-gateway supports an optional plain text field alongside the embed payload
- **AC-10.2**: Bounty announcements include a `@Bounty Hunter` role mention (using `<@&role_id>` format) that triggers Discord notifications
- **AC-10.3**: Shop refresh announcements include a `@Bounty Hunter` role mention that triggers Discord notifications
- **AC-10.4**: The channels router handler sends the plain text content alongside the embed when both are provided

### AC-11: Shop Announcement Routing
- **AC-11.1**: Shop refresh announcements continue to post to the `shop_channel_id` channel (no change to routing)
- **AC-11.2**: Shop refresh announcements include the `@Bounty Hunter` role mention

---

## Design Guidance

### 1. GuildConfig Model Changes

**Remove columns:**
- `bounty_channel_id`
- `general_channel_id`

**Add columns (all BigInteger, nullable=True):**
- `bronze_bounty_channel_id`
- `silver_bounty_channel_id`
- `gold_bounty_channel_id`
- `platinum_bounty_channel_id`
- `bounty_hunting_channel_id`
- `bounty_discussions_channel_id`
- `bounty_hunter_role_id`

**Update `division_temperatures` default** to include platinum:
```
{"bronze": 1.0, "silver": 1.0, "gold": 1.0, "platinum": 1.0}
```

### 2. Migration 0003

File: `0003_channel_role_redesign.py`

Upgrade:
1. Add 7 new columns (all nullable BigInteger)
2. Migrate data: copy `bounty_channel_id` to `bronze_bounty_channel_id` (preserve existing data)
3. Migrate data: copy `general_channel_id` to `bounty_hunting_channel_id`
4. Drop `bounty_channel_id` and `general_channel_id`

Downgrade:
1. Add back `bounty_channel_id` and `general_channel_id`
2. Copy data back from `bronze_bounty_channel_id` and `bounty_hunting_channel_id`
3. Drop the 7 new columns

### 3. guild_setup.py Redesign

The function signature changes to accept optional role references:

New channel map:
```
_CHANNEL_KEY_MAP = {
    "bronze-bounty-board": "bronze_bounty_channel_id",
    "silver-bounty-board": "silver_bounty_channel_id", 
    "gold-bounty-board": "gold_bounty_channel_id",
    "platinum-bounty-board": "platinum_bounty_channel_id",
    "shop": "shop_channel_id",
    "bounty-hunting": "bounty_hunting_channel_id",
    "bounty-discussions": "bounty_discussions_channel_id",
}
```

New result dict includes `bounty_hunter_role_id`.

Permission overwrites architecture:
- **Category level**: `@everyone` DENY view_channel; `@Bounty Hunter` ALLOW view_channel; `@BountyBot Admins` ALLOW all; bot ALLOW all
- **Read-only channels** (4 bounty boards + shop): `@Bounty Hunter` DENY send_messages
- **bounty-hunting**: `@Bounty Hunter` ALLOW send_messages + use_application_commands
- **bounty-discussions**: `@Bounty Hunter` ALLOW send_messages, DENY use_application_commands

The function creates the @Bounty Hunter role if it doesn't exist.

### 4. adminCog.py Changes

**admin_setup:**
- Call updated `ensure_bountybot_infrastructure(guild)` which now returns all 7 channel IDs + bounty_hunter_role_id
- Pass all new IDs in the init_payload to bot-core
- Update confirmation embed to show all new channels

**admin_uninstall:**
- Before calling bot-core API for DB cleanup:
  1. Fetch guild config from bot-core to get all channel/role IDs
  2. Delete each channel (7 channels) — catch and log errors per channel
  3. Delete the BountyBot category — catch and log
  4. Delete @Bounty Hunter role — catch and log
  5. Then proceed with bot-core DB cleanup (existing behavior)

### 5. playerCog.py Changes

**profile command enhancement:**
- After successful player data retrieval, before sending the embed:
  1. Fetch guild config to get `bounty_hunter_role_id`
  2. If role ID exists, get the role from guild
  3. If user doesn't have the role, add it
  4. Wrap in try/except — role assignment failure is non-fatal

### 6. Bounty Announcement Format

**Faction color map:**
- Nivelian → `0x2ECC71` (green)
- Vossk → `0x1ABC9C` (teal)
- Terran → `0xF1C40F` (gold)
- Midorian → `0x3498DB` (blue)
- Default → `0xE74C3C` (red)

**Embed structure:**
```
Title: "{criminal_name}"
Color: faction_color
Description: ""  (or brief flavor text)
Fields:
  - "Difficulty": "Tech Level {tech_level}" (inline)
  - "Reward Pool": "{reward:,} Credits" (inline)
  - "Bounty Ends": "<t:{unix_timestamp}:R>" (inline)
  - "Ship": "{ship_name}" (inline)
  - "Loadout": weapon1, weapon2, ... / module1, module2, ... (inline)
  - "Route": "System1, System2, System3, ..." (not inline)
Footer: "{faction_name}"
```

**Role mention:** Sent as plain text content alongside the embed: `<@&{bounty_hunter_role_id}>`

### 7. MessageCreateRequest Schema Change

Add optional `text_content` field:
```
text_content: str | None = None  # Plain text sent alongside embed
```

The channels.py handler sends both: `await channel.send(content=text_content, embed=embed)`

### 8. Executor Per-Division Channel Routing

The bounty_spawn_executor needs a mapping function:
```
division_to_channel_key = {
    "bronze": "bronze_bounty_channel_id",
    "silver": "silver_bounty_channel_id",
    "gold": "gold_bounty_channel_id",
    "platinum": "platinum_bounty_channel_id",
}
```

Use `getattr(config, division_to_channel_key[div_lower], None)` to get the channel ID.

### 9. GameConstants Changes

```
DIVISION_NAMES: ["bronze", "silver", "gold", "platinum"]
DIVISION_BOUNDARIES: [(0, 3), (4, 7), (8, 9), (10, 10)]
```

bounty_service.py `division_tl_map`:
```
{"bronze": 2, "silver": 5, "gold": 8, "platinum": 10}
```

---

## Implementation Order (File Change List)

### Phase 1: Data Layer (bot-core)
1. `services/bot-core/src/persist/database/tablenames.py` — no changes needed
2. `services/bot-core/src/persist/models/guild_config.py` — remove bounty_channel_id, general_channel_id; add 7 new columns; update division_temperatures default
3. `services/bot-core/src/persist/database/revisions/versions/0003_channel_role_redesign.py` — new migration
4. `services/bot-core/src/api/schemas/config_schema.py` — update GuildConfigResponse, UpdateConfigRequest
5. `services/bot-core/src/api/schemas/admin_schema.py` — update InitializeGuildRequest, GuildInitializationResponse
6. `services/bot-core/src/services/game_constants.py` — add platinum to DIVISION_NAMES, DIVISION_BOUNDARIES

### Phase 2: Service Layer (bot-core)
7. `services/bot-core/src/services/bounty_service.py` — add platinum to division_tl_map

### Phase 3: Executor Layer (bot-core)
8. `services/bot-core/src/utils/executors/bounty_spawn_executor.py` — add Platinum to divisions; per-tier channel routing; rich embed format; role mention via text_content
9. `services/bot-core/src/utils/executors/shop_refresh_executor.py` — role mention via text_content

### Phase 4: Discord Gateway - Schema
10. `services/discord-gateway/src/api/schemas/message_schemas.py` — add text_content field to MessageCreateRequest

### Phase 5: Discord Gateway - Router
11. `services/discord-gateway/src/api/routers/channels.py` — handle text_content in create_channel_message

### Phase 6: Discord Gateway - Setup & Cogs
12. `services/discord-gateway/src/utils/guild_setup.py` — complete redesign: 7 channels, role creation, permission overwrites
13. `services/discord-gateway/src/cogs/adminCog.py` — admin_setup: pass new IDs; admin_uninstall: delete Discord resources
14. `services/discord-gateway/src/cogs/playerCog.py` — /profile: add @Bounty Hunter role
15. `services/discord-gateway/src/cogs/setupCog.py` — on_guild_join: use updated guild_setup

### Total: 15 files modified/created

---

## Trade-offs

| Decision | Alternative Considered | Rationale |
|----------|----------------------|-----------|
| 7 separate channel ID columns in GuildConfig | JSON dict mapping division→channel_id | Individual columns are type-safe, queryable, and match existing pattern. JSON loses DB-level type checking. |
| Category-level @everyone DENY view_channel | Per-channel DENY | Category-level is simpler and more maintainable. Channels inherit. |
| Role mention via text_content field addition | Put mention in embed description | Embed text doesn't trigger Discord notifications. Plain text content is the only way to actually ping users. |
| Single migration for add+remove columns | Separate migrations for add then remove | Single migration is cleaner; data migration included for backward compat. |
| Platinum division TL center = 10 | TL center = 9 | Platinum is the highest tier; max tech level aligns with game progression. |
| @Bounty Hunter role assigned on /profile | Assigned on guild join or separate command | /profile is the registration point; assigning there ensures only active players get the role. |

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Migration removes bounty_channel_id — existing guilds lose that reference | Existing bounty announcements stop working until admin_setup is rerun | Migration copies old value to bronze_bounty_channel_id as fallback |
| @Bounty Hunter role manually deleted in Discord | Role assignment fails silently; announcements lose mentions | Graceful handling: log warning, skip mention, don't crash |
| Discord permission overwrites complexity | Incorrect permissions could lock out users or expose channels | Extensive test coverage for permission combinations |
| 7 channels per guild — Discord has 500 channel limit | Unlikely to hit but adds 7 channels per bot install | Acceptable; 7 channels is standard for feature-rich bots |
| text_content field in MessageCreateRequest — backward compat | Old callers don't send it | Field is optional (None default); no breaking change |

---

## Future Work (Out of Scope)

1. **Route map image in bounty announcements** — MapRenderer generates PNG bytes but there's no hosting mechanism. Would need: file attachment support in MessageCreateRequest, or a render-and-host endpoint.
2. **Criminal portrait thumbnails** — Criminal model has `icon` field but values need URL population (currently just identifier strings). Needs asset hosting infrastructure.
3. **Faction icon footer images** — Same as #2; needs hosted URLs for faction logo images.
4. **Loadout emoji integration** — EmojiService resolves game object names to Discord emoji format but requires BOTTOKEN/BOTAPPID which are only in discord-gateway, not bot-core executors. Options: (a) move emoji resolution to gateway side, (b) pass emoji data through API, (c) have executor call gateway emoji endpoint.
5. **Checked-systems live editing** — The reference format shows struck-through checked systems and bold current-location systems. Would require: persisting bounty message IDs in DiscordMessage table, editing messages when /check is called.
6. **Unregister command** — Remove @Bounty Hunter role from individual users. Currently no "unregister" or "delete profile" command exists.
7. **Platinum division temperatures** — division_temperatures default updated but temperature_decay_executor may need adjustment to process 4 divisions.

---

## Handoff Record

- **From**: Architect (Phase 0)
- **To**: Tester (Phase 1)
- **Handoff Count**: 1 → 2
- **What's Done**: Complete architectural analysis and acceptance criteria for channel/role/announcement redesign
- **What's Next**: Tester creates test cases from the 11 acceptance criteria groups (AC-1 through AC-11)
- **Files Read**: 19 source files across bot-core and discord-gateway
- **Files Modified**: 1 (this activity.md)
