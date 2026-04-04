# BountyBot Channel & Announcement Redesign — Implementation Plan

**Version:** 1.0  
**Date:** 2026-04-04  
**Author:** Architect Agent  
**Status:** READY FOR IMPLEMENTATION

---

## 1. Executive Summary

### What's Changing

The BountyBot Discord infrastructure is being redesigned from a minimal 3-channel setup (bounty-board, shop, general) to a comprehensive 7-channel system with role-based access control, per-division bounty boards, rich bounty announcement embeds, live message editing, and Discord-hosted route map images.

### Why

1. **Per-division bounty boards** — Players need separate channels for Bronze, Silver, and Gold bounties to reduce noise and enable division-specific notifications.
2. **Role-based access** — The `@Bounty Hunter` role gates all gameplay channels, creating a clear player/non-player boundary.
3. **Rich announcements** — Bounty announcements need faction-colored embeds with criminal portraits, loadout details, route maps, and role mentions — matching the original GOF2BountyBot's feature set.
4. **Message lifecycle** — Bounty announcements must be live-edited when players check systems and deleted when bounties expire/complete.
5. **Image hosting via Discord** — Route map PNGs are uploaded to a hidden bot-only channel so Discord CDN hosts them, eliminating the need for self-hosted image servers.

### Scope

- **GuildConfig model**: Replace 4 generic channel columns with 7 specific channel columns + 1 role column
- **guild_setup.py**: Complete redesign — 7 channels, 2 roles, permission overwrites
- **adminCog.py**: Update `/admin_setup` and `/admin_uninstall` for new structure
- **playerCog.py**: Add `@Bounty Hunter` role assignment on `/profile`
- **New `/unregister` command**: Remove `@Bounty Hunter` role
- **bounty_spawn_executor.py**: Route to per-division channels, rich embeds, route map upload
- **shop_refresh_executor.py**: Post to `#shop` with role mention
- **bounty_expire_executor.py**: Delete announcement messages on expiry
- **Bounty check flow**: Edit announcement messages when systems are checked
- **New bot-core endpoints**: File upload proxy, bounty announcement CRUD

---

## 2. Implementation Segments

### SEG-01: GuildConfig Data Model Migration

**Description:** Replace the existing 4 channel ID columns with 7 channel-specific columns and add `bounty_hunter_role_id`. This is the foundation for all other segments.

**Files to modify:**
- `services/bot-core/src/persist/models/guild_config.py` — Replace `bounty_channel_id` and `general_channel_id` with 7 new columns; add `bounty_hunter_role_id`
- `services/bot-core/src/persist/database/revisions/versions/0003_redesign_channel_structure.py` — NEW migration
- `services/bot-core/src/api/schemas/config_schema.py` — Update `GuildConfigResponse` and `UpdateConfigRequest` fields
- `services/bot-core/src/api/schemas/admin_schema.py` — Update `InitializeGuildRequest` and `GuildInitializationResponse`
- `services/bot-core/src/api/routers/config.py` — Update all `GuildConfigResponse(...)` constructions
- `services/bot-core/src/api/routers/admin.py` — Update `initialize_guild()` config_data dict
- `services/bot-core/src/persist/repositories/config_repository.py` — Update `get_config_summary()`

**Data Model Changes (exact columns):**

| Action | Column Name | Type | Nullable | Notes |
|--------|-------------|------|----------|-------|
| KEEP | `category_id` | BigInteger | Yes | Already exists |
| REMOVE | `bounty_channel_id` | BigInteger | Yes | Replaced by 3 per-division columns |
| REMOVE | `general_channel_id` | BigInteger | Yes | Replaced by 2 specific columns |
| KEEP | `shop_channel_id` | BigInteger | Yes | Already exists |
| ADD | `bronze_bounty_channel_id` | BigInteger | Yes | #bronze-bounty-board |
| ADD | `silver_bounty_channel_id` | BigInteger | Yes | #silver-bounty-board |
| ADD | `gold_bounty_channel_id` | BigInteger | Yes | #gold-bounty-board |
| ADD | `hunting_channel_id` | BigInteger | Yes | #bounty-hunting (main gameplay) |
| ADD | `discussion_channel_id` | BigInteger | Yes | #bounty-discussions (no slash cmds) |
| ADD | `image_channel_id` | BigInteger | Yes | #bot-images (hidden, bot-only) |
| ADD | `bounty_hunter_role_id` | BigInteger | Yes | @Bounty Hunter role snowflake |

**Migration SQL (upgrade):**
```sql
ALTER TABLE guild_configs ADD COLUMN bronze_bounty_channel_id BIGINT;
ALTER TABLE guild_configs ADD COLUMN silver_bounty_channel_id BIGINT;
ALTER TABLE guild_configs ADD COLUMN gold_bounty_channel_id BIGINT;
ALTER TABLE guild_configs ADD COLUMN hunting_channel_id BIGINT;
ALTER TABLE guild_configs ADD COLUMN discussion_channel_id BIGINT;
ALTER TABLE guild_configs ADD COLUMN image_channel_id BIGINT;
ALTER TABLE guild_configs ADD COLUMN bounty_hunter_role_id BIGINT;
-- Migrate existing data: copy bounty_channel_id to bronze_bounty_channel_id
UPDATE guild_configs SET bronze_bounty_channel_id = bounty_channel_id WHERE bounty_channel_id IS NOT NULL;
-- Copy general_channel_id to hunting_channel_id (closest match)
UPDATE guild_configs SET hunting_channel_id = general_channel_id WHERE general_channel_id IS NOT NULL;
ALTER TABLE guild_configs DROP COLUMN bounty_channel_id;
ALTER TABLE guild_configs DROP COLUMN general_channel_id;
```

**Migration SQL (downgrade):**
```sql
ALTER TABLE guild_configs ADD COLUMN bounty_channel_id BIGINT;
ALTER TABLE guild_configs ADD COLUMN general_channel_id BIGINT;
UPDATE guild_configs SET bounty_channel_id = bronze_bounty_channel_id;
UPDATE guild_configs SET general_channel_id = hunting_channel_id;
ALTER TABLE guild_configs DROP COLUMN bronze_bounty_channel_id;
ALTER TABLE guild_configs DROP COLUMN silver_bounty_channel_id;
ALTER TABLE guild_configs DROP COLUMN gold_bounty_channel_id;
ALTER TABLE guild_configs DROP COLUMN hunting_channel_id;
ALTER TABLE guild_configs DROP COLUMN discussion_channel_id;
ALTER TABLE guild_configs DROP COLUMN image_channel_id;
ALTER TABLE guild_configs DROP COLUMN bounty_hunter_role_id;
```

**Schema Changes:**

`GuildConfigResponse` — Replace:
```
bounty_channel_id: int | None = None
general_channel_id: int | None = None
```
With:
```
bronze_bounty_channel_id: int | None = None
silver_bounty_channel_id: int | None = None
gold_bounty_channel_id: int | None = None
hunting_channel_id: int | None = None
discussion_channel_id: int | None = None
image_channel_id: int | None = None
bounty_hunter_role_id: int | None = None
```

Same changes for `UpdateConfigRequest` and `InitializeGuildRequest`.

`GuildInitializationResponse` — Add `bounty_hunter_role_id: int | None = None`.

**Dependencies:** None (first segment)  
**Testing strategy:** Run all 2246 existing tests to verify no regressions; verify migration up/down manually  
**Estimated complexity:** M  
**Recommended agent:** @developer

---

### SEG-02: Guild Setup Utility Redesign

**Description:** Completely redesign `ensure_bountybot_infrastructure()` to create 7 channels with proper permission overwrites, the `@Bounty Hunter` role, and return all channel/role IDs.

**Files to modify:**
- `services/discord-gateway/src/utils/guild_setup.py` — Complete rewrite

**Current state:** Creates 3 channels (bounty-board, shop, general) under BountyBot category with basic overwrites.

**New behavior:**
1. Find-or-create `@Bounty Hunter` role (no special permissions, mentionable)
2. Find-or-create `BountyBot` category with `@everyone` DENY view_channel
3. Find-or-create 7 text channels under category with permission overwrites per the Permission Matrix (Section 7)
4. Return dict with all 9 keys: `category_id`, `bronze_bounty_channel_id`, `silver_bounty_channel_id`, `gold_bounty_channel_id`, `shop_channel_id`, `hunting_channel_id`, `discussion_channel_id`, `image_channel_id`, `bounty_hunter_role_id`
5. All operations idempotent (find existing by name, case-insensitive)
6. Permission failures return None for that specific channel/role (non-fatal)

**Channel-to-key mapping:**

| Channel Name | Dict Key | Read-only? |
|-------------|----------|------------|
| `bronze-bounty-board` | `bronze_bounty_channel_id` | Yes (for @Bounty Hunter) |
| `silver-bounty-board` | `silver_bounty_channel_id` | Yes |
| `gold-bounty-board` | `gold_bounty_channel_id` | Yes |
| `shop` | `shop_channel_id` | Yes |
| `bounty-hunting` | `hunting_channel_id` | No (full access for @Bounty Hunter) |
| `bounty-discussions` | `discussion_channel_id` | No send, but DENY slash commands |
| `bot-images` | `image_channel_id` | Hidden from all users |

**Dependencies:** SEG-01 (GuildConfig model must have the new columns)  
**Testing strategy:** 10+ tests covering happy path, existing infrastructure reuse, permission failures, partial failures  
**Estimated complexity:** L  
**Recommended agent:** @developer

---

### SEG-03: AdminCog Setup & Uninstall Updates

**Description:** Update `/admin_setup` to call the redesigned guild_setup utility and pass all 9 IDs to bot-core. Update `/admin_uninstall` to delete all 7 channels, the category, and the `@Bounty Hunter` role.

**Files to modify:**
- `services/discord-gateway/src/cogs/adminCog.py` — Update `admin_setup` and `admin_uninstall` commands

**admin_setup changes:**
- Call updated `ensure_bountybot_infrastructure(guild)` 
- Pass all 9 channel/role IDs in `init_payload` to `POST /admin/guilds/initialize`
- Show all 7 channel mentions + role mention in confirmation embed
- When optional params (admin_role, starting_credits) are not provided, show an informative message (not an error)

**admin_uninstall changes:**
- Before calling bot-core API, delete all BountyBot channels by reading config from `GET /config/guild/{guild_id}`
- Delete channels by ID (bronze_bounty, silver_bounty, gold_bounty, shop, hunting, discussion, image)
- Delete the BountyBot category by category_id
- Delete `@Bounty Hunter` role by bounty_hunter_role_id
- Then call bot-core uninstall API to clean DB
- All Discord deletions are best-effort (non-fatal if any fail)

**Dependencies:** SEG-01, SEG-02  
**Testing strategy:** Update existing adminCog tests; add tests for new channel/role mention display; test uninstall cleanup  
**Estimated complexity:** M  
**Recommended agent:** @developer

---

### SEG-04: Role Assignment on /profile and /unregister Command

**Description:** When a player runs `/profile` for the first time (creating their player record), add the `@Bounty Hunter` role to them. New `/unregister` command removes the role.

**Files to modify:**
- `services/discord-gateway/src/cogs/playerCog.py` — Add role assignment after successful player creation in `/profile`
- `services/discord-gateway/src/cogs/playerCog.py` — Add new `/unregister` slash command

**/profile role assignment:**
- After `POST /players/` succeeds, fetch the guild's `bounty_hunter_role_id` from `GET /config/guild/{guild_id}`
- If role ID exists and user doesn't already have the role, add it via `member.add_roles(role)`
- Log success/failure; non-fatal if role assignment fails

**/unregister command:**
- Fetch `bounty_hunter_role_id` from config
- Remove role from user via `member.remove_roles(role)`
- Send confirmation message
- Does NOT delete player data (soft removal — keeps stats, just removes role access)
- If player wants data deleted, they should contact admin

**Dependencies:** SEG-01 (bounty_hunter_role_id in config), SEG-02 (role created during setup)  
**Testing strategy:** Test role assignment on profile, role removal on unregister, handling of missing role ID, already-has-role case  
**Estimated complexity:** S  
**Recommended agent:** @developer

---

### SEG-05: Rich Bounty Announcement Embed Builder

**Description:** Create a bounty announcement message builder in bot-core that produces the rich embed format specified in Section 6. This builder is used by the bounty_spawn_executor.

**Files to create/modify:**
- `services/bot-core/src/message_builders/builders/bounty_announcement.py` — NEW builder
- `services/bot-core/src/message_builders/factory.py` — Register new builder
- `services/bot-core/src/api/schemas/discord_message_schema.py` — Extend `EmbedPayloadDict` if needed for image_url

**The builder accepts:**
- Bounty object (criminal_name, criminal_faction, division, tech_level, reward, route, end_time, checked)
- Criminal icon URL (from Criminal model `icon` field)
- Criminal ship data (weapons, modules from criminal_ship JSON)
- Route map CDN URL (uploaded separately)
- bounty_hunter_role_id (for the role mention)

**The builder produces** an embed payload matching the Bounty Announcement Embed Specification (Section 6).

**Dependencies:** SEG-01 (for bounty_hunter_role_id)  
**Testing strategy:** Unit test builder output matches expected embed structure for each faction color; test with/without optional fields  
**Estimated complexity:** M  
**Recommended agent:** @developer

---

### SEG-06: Route Map Image Upload to Discord

**Description:** Implement the Discord-as-image-host pattern. When a bounty is spawned, render the route map PNG, upload it to the hidden `#bot-images` channel, and capture the CDN URL for use in the bounty announcement embed.

**Files to modify:**
- `services/bot-core/src/utils/executors/bounty_spawn_executor.py` — Add image upload step before announcement
- `services/discord-gateway/src/api/routers/channels.py` — Add or verify file upload support in `POST /channels/{channel_id}/messages`

**Flow:**
1. After spawning bounty, call `GET /bounties/{id}/map` to get PNG bytes
2. POST the PNG to discord-gateway `POST /channels/{image_channel_id}/messages` as a file attachment
3. Extract the CDN URL from the returned message (attachment URL)
4. Include CDN URL as `image_url` in the bounty announcement embed

**Key concern:** The current `POST /channels/{channel_id}/messages` endpoint only supports embed payloads, not file attachments. A new endpoint or parameter is needed:
- Option A: Add file upload support to the existing channel messages endpoint
- Option B: Create a dedicated `POST /channels/{channel_id}/upload` endpoint

**Dependencies:** SEG-01 (image_channel_id in config), SEG-02 (image channel created)  
**Testing strategy:** Test PNG upload, CDN URL extraction, fallback when image_channel_id is None  
**Estimated complexity:** L  
**Recommended agent:** @developer

---

### SEG-07: Per-Division Bounty Announcement Routing

**Description:** Update the bounty_spawn_executor to route announcements to the correct per-division bounty board channel and use the rich embed format.

**Files to modify:**
- `services/bot-core/src/utils/executors/bounty_spawn_executor.py` — Replace single `bounty_channel_id` routing with per-division routing

**Current state:** Announces to a single `bounty_channel_id` with a basic embed.

**New behavior:**
1. Map division to channel: `bronze` → `bronze_bounty_channel_id`, `silver` → `silver_bounty_channel_id`, `gold` → `gold_bounty_channel_id`
2. Use the rich embed builder from SEG-05
3. Include the route map image URL from SEG-06
4. Include `<@&bounty_hunter_role_id>` as message content (triggers role mention notification)
5. After posting, persist the Discord message ID in `DiscordMessage` table (for later editing/deletion)

**Dependencies:** SEG-01, SEG-05, SEG-06  
**Testing strategy:** Test routing to correct channel per division; test role mention content; test DiscordMessage persistence  
**Estimated complexity:** M  
**Recommended agent:** @developer

---

### SEG-08: Bounty Announcement Live-Edit on /check

**Description:** When a player uses `/check` and a system is marked as checked, edit the bounty's announcement message to show updated checked/unchecked systems.

**Files to modify:**
- `services/bot-core/src/services/bounty_service.py` — After updating `checked` dict, trigger announcement edit
- `services/bot-core/src/api/routers/bounties.py` — Add endpoint or extend `POST /bounties/check` response with message edit data
- `services/bot-core/src/api/routers/discord_message.py` — Ensure update endpoint works for this flow

**Edit format for the "Route" field in the embed:**
- Unchecked systems: plain text (e.g., `Pan`)
- Checked-incorrect systems: ~~strikethrough~~ (e.g., `~~Pescal Ansen~~`)  
- Criminal-spotted system: **bold** (e.g., `**Mido**`) — only shown when `CORRECT` result

**Flow:**
1. `BountyService.check_bounty()` updates the `checked` dict
2. Look up the DiscordMessage record for this bounty
3. Rebuild the embed with updated checked systems formatting
4. Call `PUT /discord-message` to update the Discord message via gateway
5. The gateway edits the original announcement message in Discord

**Dependencies:** SEG-05 (embed builder), SEG-07 (announcement persisted in DiscordMessage)  
**Testing strategy:** Test embed edit with various check states; test when no DiscordMessage record exists (skip edit gracefully)  
**Estimated complexity:** L  
**Recommended agent:** @developer

---

### SEG-09: Bounty Announcement Deletion on Expire/Complete

**Description:** When a bounty expires or is completed (captured), delete the announcement message from Discord.

**Files to modify:**
- `services/bot-core/src/utils/executors/bounty_expire_executor.py` — After expiring bounty, delete the Discord announcement
- `services/bot-core/src/services/bounty_service.py` — After completing bounty (in `distribute_rewards`), trigger announcement deletion

**Flow:**
1. Look up DiscordMessage record for the bounty (by guild_id + message_type="bounty_announcement" + bounty_id reference)
2. Call discord-gateway `DELETE /channels/{channel_id}/messages/{message_id}` (or similar) to delete from Discord
3. Delete the DiscordMessage record from the database
4. Non-fatal if deletion fails (message may have been manually deleted)

**Dependencies:** SEG-07 (announcement message persisted)  
**Testing strategy:** Test deletion on expire, deletion on complete, graceful handling when message already deleted  
**Estimated complexity:** M  
**Recommended agent:** @developer

---

### SEG-10: Shop Refresh Announcement Enhancement

**Description:** Update shop refresh announcements to post to `#shop` channel with `@Bounty Hunter` role mention.

**Files to modify:**
- `services/bot-core/src/utils/executors/shop_refresh_executor.py` — Add role mention, enhance embed

**Current state:** Posts basic embed to `shop_channel_id`. 

**New behavior:**
- Keep posting to `shop_channel_id` (unchanged column)
- Add `<@&bounty_hunter_role_id>` as message content alongside the embed
- Fetch `bounty_hunter_role_id` from config alongside `shop_channel_id`
- Enhance embed with more detail (items available per tier, etc.)

**Dependencies:** SEG-01 (bounty_hunter_role_id in config)  
**Testing strategy:** Test role mention in message content; test when bounty_hunter_role_id is None (skip mention)  
**Estimated complexity:** S  
**Recommended agent:** @developer

---

### SEG-11: DiscordMessage Linkage to Bounty

**Description:** Add a `reference_id` column to the `DiscordMessage` model to link announcements to specific bounties, enabling lookup for editing/deletion.

**Files to modify:**
- `services/bot-core/src/persist/models/discord_message.py` — Add `reference_id` column
- `services/bot-core/src/persist/database/revisions/versions/0004_add_reference_id_to_discord_message.py` — NEW migration
- `services/bot-core/src/api/schemas/discord_message_schema.py` — Add `reference_id` to request/response
- `services/bot-core/src/persist/repositories/discord_message_repository.py` — Add `get_by_guild_type_and_reference()`

**Column definition:**
```
reference_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
```

This stores the bounty ID (or any entity ID) that the message refers to. Combined with `message_type` (e.g., "bounty_announcement"), it enables efficient lookup.

**New index:** `ix_discord_message_reference` on `(guild_id, message_type, reference_id)`.

**Dependencies:** None (can be done in parallel with SEG-01)  
**Testing strategy:** Test create with reference_id, lookup by reference_id  
**Estimated complexity:** S  
**Recommended agent:** @developer

---

## 3. Data Model Changes

### GuildConfig (guild_configs table)

#### Columns Added
| Column | Type | Nullable | Default | Purpose |
|--------|------|----------|---------|---------|
| `bronze_bounty_channel_id` | BigInteger | Yes | None | #bronze-bounty-board channel |
| `silver_bounty_channel_id` | BigInteger | Yes | None | #silver-bounty-board channel |
| `gold_bounty_channel_id` | BigInteger | Yes | None | #gold-bounty-board channel |
| `hunting_channel_id` | BigInteger | Yes | None | #bounty-hunting channel |
| `discussion_channel_id` | BigInteger | Yes | None | #bounty-discussions channel |
| `image_channel_id` | BigInteger | Yes | None | #bot-images channel (hidden) |
| `bounty_hunter_role_id` | BigInteger | Yes | None | @Bounty Hunter role |

#### Columns Removed
| Column | Reason |
|--------|--------|
| `bounty_channel_id` | Replaced by 3 per-division columns |
| `general_channel_id` | Replaced by hunting_channel_id and discussion_channel_id |

#### Migration Path
- Migration 0003: Adds 7 new columns, copies data from old columns, drops old columns
- `bounty_channel_id` data → `bronze_bounty_channel_id` (best-effort migration)
- `general_channel_id` data → `hunting_channel_id` (best-effort migration)

### DiscordMessage (discord_message table)

#### Columns Added
| Column | Type | Nullable | Purpose |
|--------|------|----------|---------|
| `reference_id` | BigInteger | Yes | Links to bounty ID or other entity |

#### Indexes Added
| Index Name | Columns | Purpose |
|-----------|---------|---------|
| `ix_discord_message_reference` | `(guild_id, message_type, reference_id)` | Fast lookup for edit/delete |

---

## 4. Schema Changes

### config_schema.py

**GuildConfigResponse** — Remove `bounty_channel_id`, `general_channel_id`. Add:
```
bronze_bounty_channel_id: int | None = None
silver_bounty_channel_id: int | None = None
gold_bounty_channel_id: int | None = None
hunting_channel_id: int | None = None
discussion_channel_id: int | None = None
image_channel_id: int | None = None
bounty_hunter_role_id: int | None = None
```

**UpdateConfigRequest** — Same field replacement as above.

### admin_schema.py

**InitializeGuildRequest** — Remove `bounty_channel_id`, `general_channel_id`. Add same 7 fields as above.

**GuildInitializationResponse** — Add `bounty_hunter_role_id: int | None = None`.

### discord_message_schema.py

**DiscordMessageRequest** — Add `reference_id: int | None = None`.

**DiscordMessageResponse** — Add `reference_id: int | None = None`.

---

## 5. API Endpoint Changes

### Modified Endpoints

| Endpoint | Change | Details |
|----------|--------|---------|
| `POST /admin/guilds/initialize` | Request body updated | Accepts 7 new channel IDs + bounty_hunter_role_id |
| `GET /config/guild/{guild_id}` | Response updated | Returns 7 channel IDs + bounty_hunter_role_id |
| `PUT /config/guild/{guild_id}` | Request/response updated | Same field changes |
| `POST /discord-message` | Request updated | Accepts optional `reference_id` |
| `PUT /discord-message` | Request updated | Accepts optional `reference_id` |

### New Endpoints

| Endpoint | Method | Service | Purpose |
|----------|--------|---------|---------|
| `/channels/{channel_id}/upload` | POST | discord-gateway | Upload file attachment to channel, return CDN URL |
| `/discord-message/guild/{guild_id}/type/{message_type}/reference/{reference_id}` | GET | bot-core | Look up DiscordMessage by guild + type + reference |
| `/discord-message/guild/{guild_id}/type/{message_type}/reference/{reference_id}` | DELETE | bot-core | Delete DiscordMessage by reference (also deletes from Discord) |

### New Endpoint: File Upload

**`POST /channels/{channel_id}/upload`** (discord-gateway)

Request: Multipart form data with `file` field (PNG bytes)
Response:
```json
{
  "status": "created",
  "data": {
    "message_id": 123456789,
    "attachment_url": "https://cdn.discordapp.com/attachments/..."
  }
}
```

---

## 6. Bounty Announcement Embed Specification

### Embed Structure

```
┌─────────────────────────────────────┐
│ [THUMBNAIL: Criminal icon URL]      │
│                                     │
│ TITLE: {criminal_name}              │
│ COLOR: {faction_color}              │
│                                     │
│ FIELDS (inline):                    │
│   Difficulty: T{tech_level}         │
│   Reward Pool: {reward:,} cr        │
│   Bounty Ends: <t:{unix}:R>        │
│                                     │
│ FIELD (not inline):                 │
│   Loadout:                          │
│   {ship_emoji} {ship_name}          │
│     HP: {armour} | DPS: {total_dps} │
│   {weapon_emojis + names}           │
│   {module_emojis + names}           │
│   Use `/criminal-loadout {name}`    │
│                                     │
│ FIELD (not inline):                 │
│   Route:                            │
│   {system1}, {system2}, {system3}...│
│                                     │
│ FIELD (not inline):                 │
│   > ~~Already checked systems~~     │
│   > **Criminal spotted here**       │
│                                     │
│ IMAGE: {route_map_cdn_url}          │
│                                     │
│ FOOTER: {faction_name}              │
│         [FOOTER_ICON: faction_icon] │
└─────────────────────────────────────┘
```

**Text content (outside embed):** `<@&{bounty_hunter_role_id}>`

### Faction Colors

| Faction | Color Name | Hex | Integer |
|---------|-----------|-----|---------|
| Terran | Gold | `#F1C40F` | 15844367 |
| Vossk | Dark Green | `#1ABC9C` | 1752220 |
| Midorian | Dark Red | `#992D22` | 10038562 |
| Nivelian | Dark Blue | `#206694` | 2123412 |
| Neutral / Unknown | Purple | `#9B59B6` | 10181046 |

### Field Details

**Difficulty + Reward + Expiry** (inline fields):
- `Difficulty`: `T{tech_level}` (e.g., "T5")
- `Reward Pool`: `{reward:,} credits` (e.g., "50,000 credits")
- `Bounty Ends`: `<t:{end_time_unix}:R>` (Discord relative timestamp, e.g., "in 3 days")

**Loadout** (non-inline field):
- Ship line: `{ship_emoji} **{ship_name}** — HP: {armour} | DPS: {total_dps}`
- Weapon lines: `{weapon_emoji} {weapon_name}` for each equipped weapon
- Module lines: `{module_emoji} {module_name}` for each equipped module
- Hint: `Use \`/criminal-loadout {criminal_name}\` for full details`
- If emoji is unavailable for an item, use the item name without emoji
- DPS is sum of all weapon DPS values from `criminal_ship.weapons`

**Route** (non-inline field):
- Comma-separated system names from `bounty.route`
- Initial state: all plain text
- After checks: ~~strikethrough~~ for checked-incorrect, **bold** for criminal-found

**Checked Systems** (non-inline field, updated via live-edit):
- Initial: `> *No systems checked yet*`
- After checks: `> ~~{checked_system_1}~~ ~~{checked_system_2}~~\n> **{criminal_spotted_system}**`

### Emoji Resolution

Game objects (ships, weapons, modules) have two emoji-related fields:
- `emoji`: Pre-formatted Discord emoji string (e.g., `<:cronus:723705945074434200>`)
- `icon`: HTTP URL to icon image

**Resolution order:**
1. Use `emoji` field if non-null (already formatted for Discord)
2. Fall back to empty string if `emoji` is null (no emoji displayed)

The `icon` field is used for thumbnail/footer images, not inline emoji.

### Criminal Icon Resolution

The Criminal model has an `icon` field (nullable string, HTTP URL). This is used as the embed thumbnail. If null, no thumbnail is set.

---

## 7. Permission Matrix

### Category-Level Permissions

| Target | Permission | Value |
|--------|-----------|-------|
| @everyone | view_channel | DENY |
| @Bounty Hunter | view_channel | ALLOW |
| @BountyBot Admins | view_channel | ALLOW |
| Bot user (guild.me) | view_channel | ALLOW |
| Bot user (guild.me) | send_messages | ALLOW |
| Bot user (guild.me) | manage_messages | ALLOW |

### Channel-Level Permission Overwrites

| Channel | @Bounty Hunter | @BountyBot Admins | Bot | @everyone |
|---------|---------------|-------------------|-----|-----------|
| #bronze-bounty-board | view: ALLOW, send: DENY | All ALLOW | All ALLOW | Inherit (DENY view from category) |
| #silver-bounty-board | view: ALLOW, send: DENY | All ALLOW | All ALLOW | Inherit |
| #gold-bounty-board | view: ALLOW, send: DENY | All ALLOW | All ALLOW | Inherit |
| #shop | view: ALLOW, send: DENY | All ALLOW | All ALLOW | Inherit |
| #bounty-hunting | view: ALLOW, send: ALLOW, use_application_commands: ALLOW | All ALLOW | All ALLOW | Inherit |
| #bounty-discussions | view: ALLOW, send: ALLOW, use_application_commands: DENY | All ALLOW | All ALLOW | Inherit |
| #bot-images | view: DENY, send: DENY | view: DENY | view: ALLOW, send: ALLOW, attach_files: ALLOW | Inherit (DENY view from category) |

**Notes:**
- `@BountyBot Admins` is the existing admin_role_id — already handled
- `@everyone` gets DENY view_channel at the category level, making the entire category private
- `@Bounty Hunter` gets selective access per channel
- `#bot-images` is hidden from ALL users (including admins and Bounty Hunters) — only the bot has access

---

## 8. Message Lifecycle Flows

### Flow 1: Bounty Announcement (Create)

```
1. APScheduler triggers bounty_spawn_executor
2. BountyService.spawn_bounty() creates bounty in DB
3. Executor reads config: image_channel_id, {division}_bounty_channel_id, bounty_hunter_role_id
4. Executor calls GET /bounties/{id}/map → PNG bytes
5. Executor uploads PNG to #bot-images via POST /channels/{image_channel_id}/upload → CDN URL
6. Executor builds rich embed using BountyAnnouncementBuilder
7. Executor POSTs embed + role mention to per-division channel via POST /channels/{channel_id}/messages
8. Discord-gateway sends message to Discord, returns message_id
9. Executor persists DiscordMessage record: guild_id, channel_id, message_id, type="bounty_announcement", reference_id=bounty.id
```

### Flow 2: Bounty Announcement (Edit on /check)

```
1. Player runs /check <system> in Discord
2. BountyCog calls POST /bounties/check to bot-core
3. BountyService.check_bounty() updates bounty.checked dict
4. After check, bot-core looks up DiscordMessage where type="bounty_announcement" AND reference_id=bounty.id
5. Bot-core rebuilds the embed with updated checked systems (strikethrough/bold)
6. Bot-core calls PUT /discord-message to update the message via gateway
7. Discord-gateway edits the original message in Discord
8. Response returns to bountyCog → player sees /check result
```

### Flow 3: Bounty Announcement (Delete on Expire/Complete)

```
1. bounty_expire_executor fires at bounty.end_time, OR BountyService completes the bounty
2. Look up DiscordMessage where type="bounty_announcement" AND reference_id=bounty.id
3. Call discord-gateway DELETE to remove message from Discord channel
4. Delete DiscordMessage record from database
5. If message already deleted (404 from Discord) → log and continue (non-fatal)
```

### Flow 4: Shop Refresh Announcement (Create)

```
1. APScheduler triggers shop_refresh_executor
2. ShopService refreshes all tiers for each guild
3. Executor reads config: shop_channel_id, bounty_hunter_role_id
4. Executor builds shop refresh embed
5. Executor POSTs embed + "<@&{bounty_hunter_role_id}>" to #shop channel
6. No DiscordMessage persistence needed (shop announcements are not edited/deleted)
```

---

## 9. Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Migration data loss during column rename | High | Low | Migration preserves data by copying before dropping; downgrade path exists |
| 2 | Discord rate limits during setup (7 channel creates + 1 role) | Medium | Medium | Use idempotent find-or-create pattern; add small delays between API calls if needed |
| 3 | CDN URL expiration for route map images | Medium | Low | Discord CDN URLs are permanent for messages that remain; if message is deleted, URL breaks — but map is only used in the announcement embed which is also deleted |
| 4 | File upload endpoint doesn't exist yet | High | Certain | SEG-06 creates this endpoint; it's a hard dependency for route map images |
| 5 | Emoji resolution fails for some items | Low | Medium | Graceful fallback: display item name without emoji if `emoji` field is null |
| 6 | Large number of existing guilds need migration | Medium | Low | Migration handles existing data; new fields are all nullable so existing guilds continue to work without the new channels |
| 7 | Race condition: bounty expires while /check is being processed | Low | Low | Bounty status is checked in check_bounty; edit/delete operations are idempotent |
| 8 | Bot lacks permissions to create channels/roles in some guilds | Medium | Medium | All operations are wrapped in try/except; missing permissions return None for that specific item |
| 9 | `#bot-images` channel could accumulate many images over time | Low | High | Each guild has its own channel; route map PNGs are small (~50KB); could add periodic cleanup later |
| 10 | Breaking change: `bounty_channel_id` removal affects existing executor tests | High | Certain | Tests must be updated in SEG-01/SEG-07; use migration to preserve data |

---

## 10. Future Work Notes

### Platinum Tier
- The current design uses Bronze/Silver/Gold (3 tiers)
- A `#platinum-bounty-board` channel can be added later by:
  1. Adding `platinum_bounty_channel_id` to GuildConfig (new migration)
  2. Adding the channel to `guild_setup.py`
  3. Adding platinum to the division routing in bounty_spawn_executor
- The GuildConfig already has `xp_thresholds` with a Platinum key, so tier progression supports it

### Additional Announcement Types
- Duel challenge/result announcements
- Player prestige announcements  
- Level-up announcements
- These can use the same DiscordMessage + MessageBuilder pattern

### Route Map Enhancement
- The current MapRenderer produces a basic route overlay
- Future: add faction-colored system dots, criminal name overlay, checked system markers directly on the image

### Scheduled Cleanup
- Periodic job to clean up old DiscordMessage records (e.g., for expired bounties)
- Periodic cleanup of old images in #bot-images channel

---

## Appendix A: Segment Dependency Graph

```
SEG-01 (Data Model)
  ├── SEG-02 (Guild Setup) ────── SEG-03 (AdminCog)
  │                                  └── SEG-04 (Role Assignment)
  ├── SEG-05 (Embed Builder)
  │     ├── SEG-07 (Per-Division Routing) ── SEG-08 (Live Edit)
  │     │                                    SEG-09 (Delete on Expire)
  │     └── SEG-06 (Image Upload) ───────── SEG-07
  ├── SEG-10 (Shop Announcement)
  └── SEG-11 (DiscordMessage reference_id) ── SEG-07, SEG-08, SEG-09
```

### Recommended Implementation Order

| Phase | Segments | Rationale |
|-------|----------|-----------|
| 1 | SEG-01, SEG-11 | Data model changes (can be done in parallel) |
| 2 | SEG-02, SEG-05 | Infrastructure + embed builder (can be done in parallel) |
| 3 | SEG-03, SEG-04, SEG-06 | Admin updates, role assignment, image upload |
| 4 | SEG-07, SEG-10 | Per-division routing + shop announcements |
| 5 | SEG-08, SEG-09 | Message lifecycle (edit + delete) |

---

## Appendix B: Files Changed Summary

### New Files
| File | Segment | Description |
|------|---------|-------------|
| `services/bot-core/src/persist/database/revisions/versions/0003_redesign_channel_structure.py` | SEG-01 | Alembic migration |
| `services/bot-core/src/persist/database/revisions/versions/0004_add_reference_id_to_discord_message.py` | SEG-11 | Alembic migration |
| `services/bot-core/src/message_builders/builders/bounty_announcement.py` | SEG-05 | Bounty embed builder |

### Modified Files
| File | Segment(s) | Nature of Change |
|------|-----------|-----------------|
| `services/bot-core/src/persist/models/guild_config.py` | SEG-01 | Column changes |
| `services/bot-core/src/persist/models/discord_message.py` | SEG-11 | Add reference_id |
| `services/bot-core/src/api/schemas/config_schema.py` | SEG-01 | Field changes |
| `services/bot-core/src/api/schemas/admin_schema.py` | SEG-01 | Field changes |
| `services/bot-core/src/api/schemas/discord_message_schema.py` | SEG-11 | Add reference_id |
| `services/bot-core/src/api/routers/config.py` | SEG-01 | Response construction |
| `services/bot-core/src/api/routers/admin.py` | SEG-01, SEG-03 | Config data + uninstall |
| `services/bot-core/src/api/routers/discord_message.py` | SEG-11 | New lookup endpoint |
| `services/bot-core/src/api/routers/bounties.py` | SEG-08 | Check triggers edit |
| `services/bot-core/src/persist/repositories/config_repository.py` | SEG-01 | Summary fields |
| `services/bot-core/src/persist/repositories/discord_message_repository.py` | SEG-11 | New query methods |
| `services/bot-core/src/services/bounty_service.py` | SEG-08, SEG-09 | Trigger edit/delete |
| `services/bot-core/src/utils/executors/bounty_spawn_executor.py` | SEG-06, SEG-07 | Image upload + rich embed + per-division routing |
| `services/bot-core/src/utils/executors/bounty_expire_executor.py` | SEG-09 | Delete announcement |
| `services/bot-core/src/utils/executors/shop_refresh_executor.py` | SEG-10 | Role mention |
| `services/bot-core/src/message_builders/factory.py` | SEG-05 | Register builder |
| `services/discord-gateway/src/utils/guild_setup.py` | SEG-02 | Complete redesign |
| `services/discord-gateway/src/cogs/adminCog.py` | SEG-03 | Setup + uninstall |
| `services/discord-gateway/src/cogs/playerCog.py` | SEG-04 | Role assignment + /unregister |
| `services/discord-gateway/src/api/routers/channels.py` | SEG-06 | File upload endpoint |

---

*End of Implementation Plan*
