# AGENTS.md - discord-gateway/src/utils

This file provides detailed guidance for AI agents working on utility modules in this directory.

---

## New Autocomplete Helpers (A.37, 2026-04-22)

`autocomplete_helpers.py` now exports two new helpers:

- **`player_equippable_autocomplete`** — items in player inventory that can be equipped
  (item_type in `_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES`, not already on active ship)
- **`player_equipped_autocomplete`** — items currently equipped on the active ship
  (all slots: weapons, modules, turrets, secondary_weapons)

**Surface gating constant** `_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES` mirrors
`GameConstants.CURRENTLY_ENABLED_TYPES` (minus "ship"). Currently:
`{"primary_weapon", "turret_weapon", "module"}` — secondary_weapon excluded.

When secondary weapons ship: update BOTH this constant AND bot-core's
`GameConstants.CURRENTLY_ENABLED_TYPES` in the same PR.

---

## Overview

This directory contains **9 utility modules** shared across cogs and REST routers. They provide:
- Bidirectional conversion between Discord objects and JSON/API payloads
- Discord exception → HTTP exception mapping
- Permission evaluation and flag registry
- Embed formatting utilities
- Command validation and cooldown management
- Autocomplete normalization and shared autocomplete helpers

These modules contain **no business logic** — they are pure conversion and validation utilities.

---

## Module Reference

| File | Key Classes/Functions | Consumer |
|------|-----------------------|----------|
| `autocomplete_utils.py` | `normalize_for_search()` | Cogs — accent/apostrophe/hyphen-insensitive string normalization for autocomplete filters |
| `autocomplete_helpers.py` | `resolve_player_id()`, `player_ships_autocomplete()`, `player_inventory_autocomplete()`, `player_equippable_autocomplete()`, `player_equipped_autocomplete()` | Cogs — shared autocomplete logic for player-scoped ships and inventory items |
| `autocomplete_state.py` | `init()`, `player_cache`, `inventory_cache`, `ships_cache`, `get_player()`, `get_player_id()`, `set_player()`, `set_inventory()`, `set_ships()`, `invalidate_player()`, `invalidate_inventory()`, `invalidate_ships()`, `clear_all()`, `NormalizedChoice` | All cogs — shared module-level cache state; must be initialized once from `bot.py` lifespan |
| `autocomplete_warm.py` | `warm_autocomplete_caches()`, APScheduler job registration | `bot.py` — startup warm + recurring APScheduler re-warm jobs for `player_cache`, `inventory_cache`, `ships_cache` |
| `command_utils.py` | `CommandValidator`, `CommandHandler`, `get_command_handler()` | `bot.py`, prefix commands |
| `discord_converters.py` | `GuildConverter`, `ChannelConverter`, `RoleConverter`, `UserConverter`, `MessageConverter`, `PermissionConverter` | REST routers |
| `discord_helpers.py` | `resolve_bot()`, `get_entity_or_404()`, `handle_discord_exception()`, `normalize_emoji()`, `tag_to_dict()`, `tags_to_edit_payload()` | REST routers |
| `embed_converter.py` | `EmbedConverter` | Cogs, REST routers, `discord_converters.py` |
| `permission_utils.py` | `PERMISSION_FLAGS`, `calculate_effective_permissions()`, `check_permission()`, `evaluate_user_guild_permissions()`, etc. | REST routers |

---

## `autocomplete_helpers.py`

### Purpose
Centralises autocomplete logic shared by multiple cogs (player-scoped ship lookup
and inventory lookup). Previously duplicated across `shipsCog.setactive_autocomplete`
and would have been duplicated again for `/ship`/`/nickname`/`/item`; now a
single source of truth.

### Key properties
- **Silent degradation.** Every helper returns `[]` on any error — Discord
  autocomplete has no user-visible error surface.
- **Short timeout (3s default).** Keystroke-triggered, so snappy responses
  matter more than completeness.
- **No module-level state.** All helpers accept the caller's `httpx.AsyncClient`
  so tests can substitute a mocked client without monkeypatching.

### Functions

```python
from utils.autocomplete_helpers import (
    resolve_player_id,
    player_ships_autocomplete,
    player_inventory_autocomplete,
)

# Upsert-style player ID lookup (returns None on any failure)
player_id = await resolve_player_id(
    http_client, api_base, user_id, guild_id, timeout=3.0
)

# Player-scoped ship choices; value=str(id), label uses 🟢 for active ship
choices = await player_ships_autocomplete(
    http_client, api_base, interaction, current,
    exclude_active=False,  # set True in flows that forbid active ship
)

# Player-scoped inventory choices; label formatted 'Name (Type) xN' (qty >1)
choices = await player_inventory_autocomplete(
    http_client, api_base, interaction, current,
    item_type_filter="weapon",  # optional; scope to a specific item_type
)
```

### Consumers
- `shipsCog`: `/ship`, `/nickname`, `/setactive` (all delegate to `player_ships_autocomplete`)
- `inventoryCog`: `/item` (delegates to `player_inventory_autocomplete`)

Migration of `/equip`/`/unequip`/`/give` autocompletes to this helper is
deferred — keep their existing inline implementations unless a targeted
refactor is scoped.

---

## `command_utils.py`

### Purpose
Centralized permission checking and cooldown management for **prefix commands** (i.e., `commands.command()` style). Most slash commands use the simpler `@is_admin()` decorator from `adminCog.py`; this module is used primarily by `GatewayBot.execute_command_with_validation()`.

### Classes

#### `CommandValidator`
```python
validator = CommandValidator()

# Register a command with its permission requirements
validator.register_command("my_command", "Description", {
    "admin_only": True,    # requires is_admin() to pass
    "dev_only": False,     # requires user in DEVELOPER_IDS env var
    "required_roles": ["Moderator"]  # requires specific role names
})

# Check permissions (returns True/False)
allowed = validator.validate_permissions("my_command", user, guild)

# Check cooldown (returns False if still cooling down)
can_execute = validator.check_cooldown("my_command", user.id, cooldown_seconds=5)

# Check if user is a developer (reads DEVELOPER_IDS env var)
is_dev = validator.is_developer(user)
```

#### `CommandHandler`
```python
handler = CommandHandler(bot)

# Execute a command with full validation
success = await handler.execute_command(
    ctx,
    command_name="my_command",
    handler=my_handler_coroutine,
    permissions={"admin_only": True},
    cooldown_seconds=5
)
```

#### `get_command_handler(bot)` — Singleton Factory
```python
from utils.command_utils import get_command_handler

handler = get_command_handler(bot)  # returns existing or creates new CommandHandler
```

`GatewayBot.__init__()` calls this to attach a command handler to the bot instance:
```python
self.command_handler = get_command_handler(self)
```

### When to Use
- Prefix commands (`@commands.command()`) that need permission/cooldown control
- Slash commands already have their own per-command decorators (`@is_admin()`, `@app_commands.checks.*`)

---

## `discord_converters.py`

### Purpose
Bidirectional conversion between live Discord objects (from `discord.py`) and Pydantic schemas (from `api/schemas/`). Used exclusively by REST routers to serialize Discord objects for API responses.

**Design principle:** Converters are generic — they contain no game-specific business logic, only structural conversion.

### Converter Classes

#### `GuildConverter`
```python
from utils.discord_converters import GuildConverter

# Convert a discord.Guild to the Guild schema
payload: Guild = GuildConverter.guild_to_summary(guild)
# guild_to_detail is an alias for guild_to_summary (same schema covers both)
```

#### `ChannelConverter`
```python
from utils.discord_converters import ChannelConverter

# Summary (minimal fields)
summary: Channel = ChannelConverter.channel_to_summary(channel)

# Detail (full fields including topic, nsfw, bitrate, etc.)
detail: Channel = ChannelConverter.channel_to_detail(channel)

# Category
category: Category = ChannelConverter.category_to_detail(category_channel)

# Thread
thread: Thread = ChannelConverter.thread_to_summary(thread)
# thread_to_detail is an alias

# Forum tag (returns dict, not a Pydantic model)
tag_dict: dict = ChannelConverter.forum_tag_to_payload(forum_tag, channel_id=123)
```

#### `RoleConverter`
```python
from utils.discord_converters import RoleConverter

role_payload: Role = RoleConverter.role_to_payload(role)
# Includes: id, guild_id, name, color, hoist, position, permissions (bitfield), managed, mentionable
```

#### `UserConverter`
```python
from utils.discord_converters import UserConverter

user_payload: User = UserConverter.user_to_payload(user)
# Includes: id, username, discriminator, avatar (URL), bot, system, public_flags

member_payload: Member = UserConverter.member_to_payload(member)
# Includes: User nested, guild_id, nick, roles (list of IDs), joined_at, permissions (bitfield)
```

#### `MessageConverter`
```python
from utils.discord_converters import MessageConverter

# Full message (prefers embed content if present)
message_payload: Message = MessageConverter.message_to_payload(message)

# Summary (text-focused fallback: plain text → embed description → embed title → fields)
summary: MessageSummary = MessageConverter.message_to_summary(message)
```

#### `PermissionConverter`
```python
from utils.discord_converters import PermissionConverter

overwrite_payload: PermissionOverwrite = PermissionConverter.overwrite_to_payload(
    target,       # discord.Role or discord.Member
    overwrite,    # discord.PermissionOverwrite
    channel_id=123
)
# Result: { id, channel_id, target_id, type ("role"|"member"), allow (int), deny (int) }
```

### Robustness Pattern
All converters use `getattr(obj, "attr", default)` defensively to avoid `AttributeError` on partially initialized or mocked objects. This is critical for test isolation.

---

## `discord_helpers.py`

### Purpose
Utility functions used by REST routers for Discord entity resolution, exception mapping, and normalization.

### Key Functions

#### `resolve_bot(request) → commands.Bot`
Gets the live bot from FastAPI app state. **Always call this first in any route handler.**
```python
bot = await resolve_bot(request)
# Raises HTTP 503 if bot not ready
# Raises HTTP 500 if bot not a valid Bot instance
# Waits up to 15 seconds for bot.wait_until_ready()
```

#### `get_entity_or_404(get_func, fetch_func, entity_id, entity_type)`
Cache-first entity resolution with automatic 404 on not found.
```python
guild = await get_entity_or_404(
    bot.get_guild,   # synchronous cache lookup (fast)
    bot.fetch_guild, # async Discord API call (slow, used as fallback)
    guild_id,
    "guild"          # entity type name for error messages
)
```

#### `handle_discord_exception(operation, exc)`
Maps Discord exceptions to HTTP exceptions with detailed logging.
```python
try:
    await guild.create_role(name="Role")
except Exception as exc:
    await handle_discord_exception("create role in guild 123", exc)
# discord.NotFound → HTTP 404
# discord.Forbidden → HTTP 403
# discord.HTTPException(4xx) → HTTP 4xx
# discord.HTTPException(5xx) → HTTP 502
# other Exception → HTTP 500
```

#### `normalize_emoji(val) → str`
Normalizes various emoji input formats into a canonical unicode emoji string.
```python
normalize_emoji("📌")          # → "📌"
normalize_emoji("1f4cc")       # → "📌" (hex codepoint)
normalize_emoji("U+1F4CC")     # → "📌" (with prefix)
normalize_emoji("1f3f7fe0f")   # → "🏷️" (concatenated hex)
normalize_emoji("<:name:123>") # → "<:name:123>" (custom emoji)
normalize_emoji(":name:")       # → "name" (short form)
```

#### `tag_to_dict(tag, channel_id=None) → dict`
Normalizes a ForumTag-like object (Discord object, dict, mock, etc.) to a consistent dict:
```python
result = tag_to_dict(forum_tag)
# → {"id": 123, "channel_id": 456, "name": "bug", "emoji": "🐛"}
```
Handles Discord objects, dicts, mock objects, and any mapping via a defensive extraction strategy.

#### `tags_to_edit_payload(tags_iterable, updates=None) → list`
Builds the `available_tags` payload for `ForumChannel.edit()`.
```python
payload = tags_to_edit_payload(
    existing_tags,
    updates={123: {"name": "New Name", "emoji": "🆕"}}
)
```

#### Validation Helpers
```python
from utils.discord_helpers import validate_guild_channel_relationship, validate_channel_type

# Raises HTTP 400 if channel doesn't belong to guild
validate_guild_channel_relationship(channel, guild_id)

# Raises HTTP 400 if channel type is not in expected_types list
validate_channel_type(channel, ["text", "voice"], channel_id)
```

---

## `embed_converter.py`

### Purpose
Bidirectional conversion between `discord.Embed` objects and the `EmbedPayload` Pydantic schema. Used by cogs for rich embed formatting and by `MessageConverter` for message content extraction.

### `EmbedConverter` Class

#### `payload_to_embed(payload) → discord.Embed`
```python
from utils.embed_converter import EmbedConverter
from api.schemas.message_schemas import EmbedPayload, EmbedField

payload = EmbedPayload(
    title="My Title",
    description="My description",
    color=0x00FF00,
    fields=[EmbedField(name="Field 1", value="Value 1", inline=True)],
    footer_text="Footer text",
    thumbnail_url="https://example.com/image.png",
    timestamp=datetime.now()
)
embed = EmbedConverter.payload_to_embed(payload)
```

Accepts:
- `EmbedPayload` instance
- `dict` that can be coerced to `EmbedPayload`
- Any object with `.model_dump()` or `.dict()` method

#### `embed_to_payload(embed) → EmbedPayload`
```python
embed = discord.Embed(title="Title", description="Desc")
payload = EmbedConverter.embed_to_payload(embed)
```

Extracts: `title`, `description`, `color`, `fields`, `footer_text`, `footer_icon_url`, `timestamp`, `thumbnail_url`, `image_url`.

#### `payload_to_grid_embed(payload, fields_per_row) → discord.Embed`
Forces a grid layout by injecting zero-width spacer fields between rows.
```python
# Used in aboutCog.py for ship/module/weapon embeds with 2-column layout
embed = EmbedConverter.payload_to_grid_embed(payload, fields_per_row=2)
```

Discord renders inline fields in groups of 3 per row. By injecting `"\u200B"` (zero-width space) spacer fields, you can force 2-column layout:
- Row of 2 real fields → spacer → next row of 2 real fields → ...

#### `test_round_trip_consistency(payload) → bool`
Verifies `payload → embed → payload` produces identical data. Used in tests.
```python
consistent = EmbedConverter.test_round_trip_consistency(my_payload)
assert consistent
```

### Usage in Cogs
```python
from utils.embed_converter import EmbedConverter

# Build a standard embed, then convert for 2-column layout:
embed = discord.Embed(title="Ship: Niode", color=discord.Color.green())
embed.add_field(name="Armour", value="200", inline=True)
embed.add_field(name="Cargo", value="50 t", inline=True)
embed.add_field(name="Handling", value="8", inline=True)

# Convert to grid
payload = EmbedConverter.embed_to_payload(embed)
grid_embed = EmbedConverter.payload_to_grid_embed(payload, fields_per_row=2)
await interaction.followup.send(embed=grid_embed)
```

---

## `permission_utils.py`

### Purpose
Complete Discord permission flag registry and evaluation logic. Used by REST routers that expose permission management. Contains **no direct Discord interactions** — works with integer bitfields only.

### `PERMISSION_FLAGS`
A dictionary mapping all Discord permission names to their bit values and applicable channel types:
```python
from utils.permission_utils import PERMISSION_FLAGS

# Each entry:
PERMISSION_FLAGS["SEND_MESSAGES"] = {
    "value": 0x0000000000000800,
    "description": "Allows for sending messages in a channel...",
    "channel_types": ["text", "voice", "stage"]
}

# Guild-only permissions have empty channel_types list:
PERMISSION_FLAGS["BAN_MEMBERS"] = {
    "value": 0x0000000000000004,
    "description": "Allows banning members",
    "channel_types": []
}
```

### Permission Check Functions

```python
from utils.permission_utils import (
    check_permission,
    check_permissions,
    has_administrator,
    calculate_effective_permissions,
    permissions_to_dict,
    get_permission_names_by_value,
    combine_permissions,
)

# Single permission check
has_perm = check_permission(permissions_value, "SEND_MESSAGES")  # True/False

# Multiple permissions at once
results = check_permissions(permissions_value, ["SEND_MESSAGES", "READ_MESSAGE_HISTORY"])
# → {"SEND_MESSAGES": True, "READ_MESSAGE_HISTORY": False}

# Administrator check (bypasses all other overwrites)
is_admin = has_administrator(permissions_value)

# Apply overwrites to base permissions
effective = calculate_effective_permissions(
    base_permissions=0x8000,  # base role permissions
    allow_overwrites=0x800,   # channel allow overwrite
    deny_overwrites=0x400,    # channel deny overwrite
)
# Note: Administrator permission bypasses overwrites entirely

# Convert to human-readable dict
perms_dict = permissions_to_dict(permissions_value)
# → {"send_messages": True, "read_message_history": True, "administrator": False, ...}

# Get list of granted permission names
granted = get_permission_names_by_value(permissions_value)
# → ["VIEW_CHANNEL", "SEND_MESSAGES", ...]

# Combine multiple permission values
combined = combine_permissions(0x800, 0x400, 0x40000)
```

### Member/Role Evaluation Functions

```python
from utils.permission_utils import (
    evaluate_user_guild_permissions,
    evaluate_user_channel_permissions,
    evaluate_role_guild_permissions,
    evaluate_role_channel_permissions,
    has_channel_permission,
    has_guild_permission,
)

# Evaluate what permissions a member has in a guild
granted, denied = evaluate_user_guild_permissions(
    member=discord_member,
    _guild=discord_guild,
    requested_permissions=["SEND_MESSAGES", "BAN_MEMBERS"]
)
# granted → {"SEND_MESSAGES": PermissionSource(type="role", role_name="@everyone")}
# denied → {"BAN_MEMBERS"}

# Evaluate effective channel permissions with overwrites
granted, denied = evaluate_user_channel_permissions(
    member=discord_member,
    channel=discord_channel,
    requested_permissions=["SEND_MESSAGES"]
)

# Quick boolean checks
can_send = has_channel_permission(member, channel, "SEND_MESSAGES")  # True/False
can_ban = has_guild_permission(member, "BAN_MEMBERS")  # True/False
```

### `PermissionSource`
Returned by evaluation functions to indicate **how** a permission was granted:
```python
source.type       # "direct", "role", or "everyone"
source.role_name  # role name if type == "role"
source.role_id    # role ID if type == "role"
```

### Permission Listing Functions
```python
from utils.permission_utils import (
    get_all_permissions,
    get_role_permissions,
    get_user_permissions,
    get_channel_permissions,
)

all_perms = get_all_permissions()    # all flags with metadata
role_perms = get_role_permissions()  # all permissions (roles can have any)
user_perms = get_user_permissions()  # channel-applicable permissions for user overwrites
chan_perms = get_channel_permissions()  # text/voice channel permissions
```

### Permission Overwrite Utilities
```python
from utils.permission_utils import create_permission_overwrite, overwrite_to_dict

# Create a discord.PermissionOverwrite from bitfields
overwrite = create_permission_overwrite(allow=0x800, deny=0x400)

# Convert a PermissionOverwrite to a serializable dict
ow_dict = overwrite_to_dict(overwrite)
# → {"allow": 0x800, "deny": 0x400, "permissions": {"send_messages": True, "view_channel": False}}
```

---

## Design Principles

### 1. Converters are purely structural
`discord_converters.py` and `embed_converter.py` only convert data shapes — they contain no business logic, no API calls, no game rules.

### 2. Robustness over strictness
All converters use `getattr(obj, "attr", default)` to handle partially initialized Discord objects, different Discord.py versions, and mock objects in tests. This prevents `AttributeError` from crashing API endpoints.

### 3. Permission utilities are Discord-native
`permission_utils.py` mirrors Discord's own permission model exactly (bitfield arithmetic, overwrite precedence, Administrator bypass). Do not simplify or change the permission evaluation order.

### 4. No circular imports
Utility modules import only from `api/schemas/` — they never import from `cogs/` or `api/routers/`. Cogs and routers import from `utils/` but not vice versa.

---

## Testing Utilities

All utility modules have corresponding test files in `tests/utils/`:

```
tests/utils/
├── test_command_utils.py
├── test_discord_converters.py
├── test_discord_helpers.py
├── test_embed_converter.py
└── test_permission_utils.py
```

For `discord_converters.py` tests, use the `mocks/` directory for pre-built Discord object mocks that mirror real Discord.py object structure.

For `embed_converter.py`, use `EmbedConverter.test_round_trip_consistency()` to verify changes don't break round-trip fidelity.

---

*Last updated: 2026-05-16*
