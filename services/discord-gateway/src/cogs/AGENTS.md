# AGENTS.md - discord-gateway/src/cogs

This file provides detailed guidance for AI agents working on Discord cogs in this directory.

---

## A.35/A.37 Pattern: /inventory Choice + /equip Autocomplete (2026-04-22)

### /inventory item_type Choice (A.35, superseded)

`inventoryCog.inventory` uses `@app_commands.choices(item_type=[...])` with
5 **concrete** value choices: `ship`, `primary_weapon`, `secondary_weapon`,
`turret_weapon`, `module` (display names "Ship", "Primary Weapon", etc.).
The original A.35 generic aliases (`weapon`, `turret`) are gone.

`/inventory` also passes `include_ships=true` to BOTH bot-core calls
(`GET /inventory/player/{id}` and `.../summary`) so INACTIVE ships display as
inventory entries and count in the Ships summary line. The autocomplete cache
(`autocomplete_state._refresh_inventory`) deliberately does NOT pass it, so
equip/sell autocomplete stays ships-free.

### /equip autocomplete filter (A.37)

`equip_autocomplete` in `inventoryCog.py` is a thin delegate to
`player_equippable_autocomplete` (from `utils/autocomplete_helpers.py`) — zero-HTTP,
reads `autocomplete_state` caches. The helper filters by
`_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES` (concrete item types:
`{"primary_weapon", "turret_weapon", "module", "secondary_weapon"}`) and excludes
items already equipped on the active ship.

`unequip_autocomplete` is cache-based too (player_cache + ships_cache), scans all
four loadout slots (`weapons`, `modules`, `turrets`, `secondary_weapons`), and
prepends an `all` sentinel choice for bulk-unequip (B.90).

#### Inventory model — CRITICAL for autocomplete correctness

`player_inventories.quantity` is **cargo-only** — it represents unequipped copies only.
Equipped items live solely in `player_ships.{weapons/modules/turrets/secondary_weapons}` JSON arrays.
They are **not** reflected in `player_inventories`.

The equip autocomplete filters using `qty > already_equipped_on_active_ship` where:
- `qty` = `player_inventories.quantity` (cargo copies)
- `already_equipped` = count of item in the **active ship's** slot arrays only

An item appears in the dropdown if the player has at least one unequipped cargo copy that is not already accounted for by the active ship's loadout. The filter counts the active ship only (not all ships) — this is intentional for the common case. The server-side B.41 guard in `LoadoutConsistencyService` enforces the full cross-ship check.

**Do NOT** conflate cargo quantity with total ownership. A player can have `quantity=1` in cargo AND the same item equipped on a ship simultaneously — those are two separate copies.

### /sell follows the same server-side resolution pattern (A.42/A.42b/A.42c, 2026-04-22)

`shopCog.sell` no longer has `item_type` or `target_tier` parameters on the slash command:

- **A.42**: `_SELL_TYPE_MAP` was **removed** — it caused a vocab-downgrade bug by mapping concrete types (e.g. `primary_weapon`) back to generic aliases (`weapon`) before POSTing to the API. The backend rejects generic aliases on write paths with `InvalidItemTypeError → HTTP 422`.
- **A.42b**: No `item_type` param. The cog POSTs `{player_id, item_name, quantity}` only. The server resolves concrete type from the player's inventory row by item_name.
- **A.42c**: No `target_tier` param. Items always land in the player's current tier shop (server reads `player.tier`), consistent with `/buy` tier-gating.

`sell_item_autocomplete` no longer reads `interaction.namespace.item_type` — there is no item_type filter on `/sell`. The autocomplete simply shows all items in the player's inventory with `"Name (Type)"` labels.

`_ITEM_TYPE_LABELS` remains on the cog (for displaying the resolved type in the success embed), but only contains concrete type labels (`primary_weapon`, `turret_weapon`, etc.) — no generic aliases (`weapon`, `turret`).

There is no `_resolve_sell_item_type` helper method on the cog — type resolution was moved entirely to the server.

### /inventory summary display (DEF-A42-001 fix, 2026-04-22)

Post-A.36, `GET /api/v1/inventory/player/{id}/summary` returns **concrete type keys**
(`primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`, `ship`, `total_items`).
Generic alias keys (`weapon`, `turret`) no longer appear in the response.

`inventoryCog.py` (around line 395) aggregates concrete types into 4 display buckets:

| Display Bucket | Source |
|---|---|
| Ships | `summary.get("ship", 0)` |
| Weapons | `summary.get("primary_weapon", 0) + summary.get("secondary_weapon", 0)` |
| Modules | `summary.get("module", 0)` |
| Turrets | `summary.get("turret_weapon", 0)` |

Use `.get(key, 0)` everywhere when reading summary keys — defensive against stale
or partial API responses. Do NOT read `summary["weapon"]` or `summary["turret"]`
— these keys no longer exist in the response.

### Surface gating principle

Cogs must gate equippable item types using `_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES`
from `utils/autocomplete_helpers.py`. This constant mirrors
`GameConstants.CURRENTLY_ENABLED_TYPES` (minus "ship") in bot-core.
Both must be updated together when new item types are enabled.

As of CI-5/CI-16, `secondary_weapon` is included; the current value is
`frozenset({"primary_weapon", "turret_weapon", "module", "secondary_weapon"})`.

### Canonical env var for bot-core URL

`BOT_API_BASE_URL` is the **single canonical environment variable** for the bot-core
API base URL across ALL cogs AND the lifespan in `bot.py`. The dev stack sets this to
`http://bot-core:18000/api/v1` via `.env.dev`.

**Never use `BOT_CORE_URL`** — that variable is not set anywhere in the stack and will
cause autocomplete warm jobs and cache refreshes to silently fail (CI-19 root cause).

The pattern used everywhere:
```python
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
```

---

## Overview

This directory contains all **Discord bot cogs** — modular collections of slash commands and event listeners. Cogs are the primary way users interact with the BountyBot game through Discord.

Each cog:
- Inherits from `commands.Cog`
- Registers slash commands via `@app_commands.command()` decorators
- Calls bot-core (and sometimes blender-service) via `httpx.AsyncClient`
- Exports an `async def setup(bot)` function at module level

---

## Auto-Discovery and Loading

`GatewayBot.setup_hook()` in `bot.py` auto-loads cogs:

```python
for fn in os.listdir("src/cogs"):
    if fn.endswith(".py") and not any(x in fn for x in ("template", "disabled", "test")):
        await self.load_extension(f"cogs.{fn[:-3]}")
```

**Rules:**
- Any `*.py` file in `src/cogs/` is loaded **unless** the filename contains `template`, `disabled`, or `test`
- `templateCog.py` and `testCog.py` are **never loaded** in production or test runs
- No manual registration in `bot.py` is needed — just create the file and the `setup()` function
- **Subdirectories are NOT walked** — the loader only inspects entries ending in `.py` from `os.listdir("src/cogs")`. The `_shared/` subdirectory is therefore never loaded as a cog (see below).

---

## `_shared/` — Cog-Adjacent Helpers

The `_shared/` subdirectory holds helper modules that are **game-layer** (consumed by cogs, not by `utils/`) but are **not themselves cogs**. Examples: `loadout_embed.py` (Discord embed builder for loadouts) and `embed_pagination.py` (continuation-field helper for `/list_category`).

Why here and not under `utils/`?
- `utils/` is reserved for game-agnostic helpers — if the game layer is replaced, `utils/` stays.
- Everything in `cogs/` (including `_shared/`) is part of the game layer and is removed together.
- The leading underscore is a Python convention signalling "private-but-colocated" and, together with the auto-loader's `.py` extension filter, prevents accidental cog loading.

## Alias Commands: `/register` ↔ `/profile`

The `/register` slash command in `playerCog.py` is a **full behavioural alias** for `/profile` — identical embed output, identical side effects (player upsert, role assignment). Both commands delegate to a single private handler `_display_profile(interaction)`. The only difference between the two public commands is the slash-command name users see in Discord, and each wrapper logs its own name for usage-frequency analysis. When adding other aliases in the future, follow the same pattern: keep the work in a shared `_display_*` method and let each `@app_commands.command(...)` wrapper do only `flogger.info(...)` + delegate.

---

## Cog File Template

Use `templateCog.py` as the reference implementation. The canonical pattern:

```python
import os

import discord
import httpx
from cogs._shared.http_error_handler import report_api_error
from cogs.adminCog import is_admin      # import if you need admin protection
from discord import app_commands
from discord.ext import commands
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-MyCog")
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"myCog loading with api_base: {api_base}")


class MyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        flogger.debug("MyCog initialized")

    async def cog_unload(self):
        """Called when the cog is unloaded. ALWAYS close the HTTP client."""
        await self.http_client.aclose()

    @app_commands.command(name="my_command", description="Description shown in Discord")
    @app_commands.describe(param="Description of param")
    async def my_command(self, interaction: discord.Interaction, param: str):
        """Docstring for the command."""
        await interaction.response.defer(thinking=True)
        flogger.debug(f"/my_command invoked: guild={interaction.guild_id} user={interaction.user.id}")

        try:
            resp = await self.http_client.get(f"{api_base}/some/endpoint", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            await interaction.followup.send(f"Result: {data}")
            flogger.info(f"/my_command success: guild={interaction.guild_id} user={interaction.user.id}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Not found.", ephemeral=True)
            else:
                flogger.error(f"/my_command API error: status={e.response.status_code}")
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/my_command error: {e}")
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    @my_command.error
    async def my_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /my_command", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up MyCog...")
    await bot.add_cog(MyCog(bot))
    flogger.info("MyCog loaded")
```

---

## HTTP Client Pattern

### Setup

Every cog that calls bot-core or blender-service must:
1. Create `self.http_client` in `__init__` with a 10-second timeout
2. Close it in `cog_unload`

```python
self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
```

For blender-service (longer operations):
```python
self.blender_client = httpx.AsyncClient(
    base_url=BLENDER_API_BASE_URL,
    timeout=httpx.Timeout(60.0, connect=10.0),
)
```

### Environment Variables

```python
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
BLENDER_API_BASE_URL = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")
```

### Standard Error Handling

Always handle these cases explicitly:
- `httpx.HTTPStatusError` with `status_code == 404` → command-specific "not found" message
  (e.g. `"❌ Job \`{job_id}\` not found."`)
- `httpx.HTTPStatusError` with `status_code == 400` → extract `detail` from JSON body
  if the command has domain-specific 400 semantics; otherwise let the helper handle it
- `httpx.HTTPStatusError` "everything-else" branch → call `report_api_error(interaction, e)`
  (B.31b, Package F). The helper produces a sanitized status-aware embed: it strips
  internal URLs / MDN links, picks a friendly canned message keyed by status code,
  appends FastAPI `detail` when present, and is race-safe via
  `contextlib.suppress(discord.HTTPException)`. Optional kwargs: `action_label="..."`
  (short verb-phrase shown in the embed title) and `detail_override={status: msg}`
  to specialize the canned message for a specific status code.
- Generic `Exception` → generic warning, log full error. Do **not** route the bare
  `except Exception` block through `report_api_error` — those branches typically
  already have command-specific phrasing.

---

## Slash Command Patterns

### Defer for async operations
```python
await interaction.response.defer(thinking=True)       # public "thinking..." indicator
await interaction.response.defer(thinking=True, ephemeral=True)  # private "thinking..."
```
Always defer before any `await` calls to bot-core.

### Followup responses
```python
await interaction.followup.send(embed=embed)             # public embed response
await interaction.followup.send("message", ephemeral=True)  # private error message
```

### Response without defer
```python
await interaction.response.send_message("message", ephemeral=True)
```
Only for immediate responses (no async operations needed first).

### Error handlers
```python
@my_command.error
async def my_command_error(self, interaction, error):
    flogger.exception("Error in /my_command", exc_info=error)
    if not interaction.response.is_done():
        await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
```
The guard `if not interaction.response.is_done()` prevents double-response errors.

---

## Autocomplete Pattern

Cogs that need autocomplete preload data on startup using `bot.loop.create_task`:

```python
class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._items: list[str] = []
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        bot.loop.create_task(self._preload_data())   # schedule preload after bot is ready

    async def _preload_data(self):
        await self.bot.wait_until_ready()
        try:
            resp = await self.http_client.get(f"{api_base}/items", timeout=10)
            resp.raise_for_status()
            self._items = [item["name"] for item in resp.json()]
        except Exception:  # broad catch is intentional for preload
            self._items = []  # degrade gracefully

    async def item_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=name, value=name)
            for name in self._items
            if current.lower() in name.lower()
        ][:25]  # Discord limit: 25 choices

    @app_commands.command(name="use_item")
    @app_commands.autocomplete(item=item_autocomplete)
    async def use_item(self, interaction: discord.Interaction, item: str):
        ...
```

**Preload error handling:** Always catch specific exceptions (`TimeoutException`, `HTTPStatusError`, `RequestError`) before the broad `Exception` fallback, to provide better logging.

---

## Admin Permission Pattern

Use the `is_admin()` decorator from `adminCog.py` to gate commands:

```python
from cogs.adminCog import is_admin, _check_is_admin

# Method 1: Decorator (standard for fully admin-gated commands)
@app_commands.command(name="admin_action")
@is_admin()
async def admin_action(self, interaction: discord.Interaction):
    ...

# Method 2: Runtime check (for conditional admin checks within a command)
if user and user != interaction.user:
    if not await _check_is_admin(interaction):
        await interaction.followup.send("❌ Admin only.", ephemeral=True)
        return
```

**Admin check order:**
1. `DEVELOPERS` env var (comma-separated Discord user IDs)
2. Discord built-in Administrator permission
3. Configured Bot Admin role from `GET /api/v1/config/guild/{guild_id}`

---

## Discord UI Views

For interactive components (buttons, selects), define `discord.ui.View` subclasses. Example from `skinsCog.py`:

```python
class ConfirmView(discord.ui.View):
    def __init__(self, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.result: bool | None = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button  # unused, but required by callback signature
        self.result = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = False
        self.stop()
        await interaction.response.defer()
```

Send the view and wait for the response:
```python
view = ConfirmView(timeout=60)
await interaction.followup.send("Are you sure?", view=view)
await view.wait()
if view.result is None:  # timed out
    ...
```

---

## Event Listeners

For non-command event handling, use `@commands.Cog.listener()`:

```python
class SetupCog(commands.Cog):
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Called when bot is added to a new guild."""
        ...

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Called when bot is removed from a guild."""
        ...
```

---

## Player Resolution Pattern

Many cogs need to resolve a Discord user to a bot-core player ID. The standard pattern:

```python
async def _get_player_id(self, user_id: int, guild_id: int) -> int | None:
    """Helper to get player ID from Discord user ID."""
    try:
        user_data = {
            "discord_id": user_id,
            "guild_id": guild_id,
            "discord_username": None,  # None = don't update username; pass real username only from /profile
        }
        resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception:  # pylint: disable=broad-exception-caught
        return None
```

**Key semantics:**

- `discord_username: None` — preserves the existing username stored by `/profile`; it does **not** overwrite it. Only `playerCog`'s `/profile` command should pass the real username via `str(interaction.user)`.
- **No implicit auto-create** — the guild must already be configured via `/admin_setup`. If the guild is not configured, `POST /api/v1/players/` returns a `400 "Guild not configured"` error. Cogs should catch this with the `_is_guild_not_configured(error)` helper (see shopCog/bountyCog for the pattern):

```python
def _is_guild_not_configured(exc: httpx.HTTPStatusError) -> bool:
    """Return True if the HTTPStatusError is a 'guild not configured' 400 response."""
    if exc.response.status_code != 400:
        return False
    try:
        detail = exc.response.json().get("detail", "")
        return "not configured" in detail.lower() or "admin_setup" in detail.lower()
    except Exception:  # pylint: disable=broad-exception-caught
        return False
```

Usage in a command's error handler:

```python
except httpx.HTTPStatusError as e:
    if _is_guild_not_configured(e):
        await interaction.followup.send(
            "❌ This server has not been set up yet. Ask an admin to run `/admin_setup` first.",
            ephemeral=True,
        )
        return
    raise
```

---

## Cog-Specific Notes

### aboutCog.py
- Preloads ALL categories and ALL objects per category into memory on startup
- Two-level autocomplete: `category_autocomplete` then `object_autocomplete` (context-aware)
- `make-route` uses bot-core's A* routing via `GET /api/v1/systems/route`
- `EmbedConverter.payload_to_grid_embed()` forces 2-column layout for detailed embeds

### adminCog.py
- All commands require `@is_admin()` — non-admins get a 403-style error
- `admin_uninstall` requires confirming via the shared `ConfirmView` button (no confirmation string)
- `render_config` and `render_cache_clear` call **blender-service** (not bot-core)
- Per-command `@<command>.error` handlers exist for `admin_setup`, `admin_player`, `admin_cooldown_reset`, the give/remove item/ship commands, the `admin_config_constants*` commands, and `admin_duel`

### bountyCog.py
- `bounty_autocomplete` reads from the per-cog `_bounty_cache` via `peek()` — no HTTP per keystroke; star systems come from `_systems_cache`
- `/check` handles result values `correct` / `incorrect` / `already_checked` / `on_cooldown`, plus per-bounty `outcomes` (multi-bounty consolidated embed when 2+, B.12)
- Cooldown is enforced by bot-core (returns HTTP 429); `/check` handles `resp.status_code == 429` before `raise_for_status()`

### combatLogCog.py
- `/combat-log` autocomplete lists only the invoking user's fights (per-cog `_combatlog_cache`, key `(guild_id, user_id)`)
- `/combat-log` takes an optional `public: bool = False` — when true the embed is posted publicly; errors are always ephemeral
- `/admin_combat_log <user> <battle>` is the admin variant (runtime `_check_is_admin`); always ephemeral, no `public` option; battle autocomplete returns a "Select a user first" sentinel until `user` is filled
- **6000-char embed cap (combat-log fix, 2026-06-18):** the detail embed packs event lines greedily into ≤1024-char "🎯 Key Events" field chunks AND tracks the running `len(embed)` against Discord's **6000-char aggregate** limit — once adding another event field would exceed it, remaining events are dropped and an honest trailer `…(+N more event(s) omitted)` is appended. Prevents the HTTP 400 (50035) that a long timeline (summary ≤1024 + 6×1024 event chunks ≈ 7200) used to trigger. Per-line detail strings are bounded to 80 chars.
- **Re-enter-range detector keyed by `(side, weapon)` (combat-log fix, 2026-06-18):** lives in the bot-core resolver's log formatter, not this cog, but surfaces here in the rendered "enters range / re-enters range" phrasing. Keying the per-weapon firing-gap detection by `(side, weapon)` (rather than globally) preserves each ship's own cadence and killed a false "re-enters range" flood that previously spammed the detail view.

### devCog.py
- All three slash commands are gated to `_check_is_super_admin` (DEVELOPERS env var only) at runtime, with `@app_commands.default_permissions(administrator=True)` for Discord-side visibility
- `load_data` with `category="All"` iterates every category and summarizes results
- `reload_autocomplete` clears `autocomplete_state` and per-cog `AutocompleteCache`s (clear-and-self-heal); only `DevCog._preload_categories` and `AdminCog._preload_render_settings` are still explicit preload calls
- Also defines prefix commands `snooze`, `wake`, `botstatus` (developer-gated)

### duelCog.py
- `pending_duel_autocomplete` reads from the per-cog `_pending_duel_cache` via `peek()` (player_id resolved from `autocomplete_state.player_cache`)
- `/duel-accept` resolves combat immediately and returns winner/loser + credit transfer details
- `/duel-cancel` lets the challenger withdraw an outgoing challenge (`_outgoing_duel_cache` autocomplete)

### healthCog.py
- Both `/ping` and `/health` are admin-only — `/ping` via `@is_admin()`, `/health` via a runtime `_check_is_admin` call after deferring
- `/health` calls bot-core's `/api/v1/health` endpoint and formats the response into an embed

### inventoryCog.py
- `/inventory` with `user=<other user>` requires admin permission (runtime check via `_check_is_admin`)
- `/inventory` passes `include_ships=true` to the inventory + summary endpoints so inactive ships are listed and counted
- `/equip` and `/unequip` resolve the player's **active ship** first, then modify its loadout; `WeaponSwapView`/`UniqueModuleSwapView` handle full-slot and unique-module conflicts
- `/give` transfers credits, an item, or an (inactive) ship to another player

### playerCog.py
- `/profile` uses `POST /api/v1/players/` upsert to create the player if they don't exist; `/register` is a full alias (see above)
- `/prestige` requires Platinum tier and confirmation via the shared `ConfirmView` button
- `/promote` and `/demote` are two-step `ConfirmView` flows subject to the tier-change cooldown (24h default, guild-overridable)

### setupCog.py
- **No slash commands** — only listens to guild events
- `on_guild_join`: sends a welcome embed directing admins to run `/admin_setup` (no channel creation here — roles/category/channels are created by `/admin_setup` via `utils/guild_setup.ensure_bountybot_infrastructure`)
- `on_guild_remove`: best-effort `DELETE /admin/guilds/{id}/cleanup` call (non-fatal if it fails)

### skinsCog.py
- Uses `bot.wait_for("message", ...)` to collect texture uploads interactively (120s timeout)
- Two httpx clients: `self.http_client` (bot-core) and `self.blender_client` (blender-service)
- `SquareCheckView` asks crop/stretch/cancel when uploaded image is non-square; `RegionModeView`/`RegionOptionView` handle region-overlay choices
- `FormatDownloadView` offers AEI format conversion after render (ETC1 for Android, DXT5 for PC)
- `_preload_ship_skins` fetches render info for ALL ships on startup

---

## Testing Cogs

### Test File Location
`tests/cogs/test_<cogFileName>.py`

### Test Structure Pattern

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord

class TestMyCog:
    @pytest.fixture
    def bot(self):
        bot = MagicMock()
        bot.loop = MagicMock()
        bot.loop.create_task = MagicMock()
        return bot

    @pytest.fixture
    def cog(self, bot):
        from cogs.myCog import MyCog
        return MyCog(bot)

    @pytest.fixture
    def interaction(self):
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        return interaction

    @pytest.mark.asyncio
    async def test_my_command_success(self, cog, interaction):
        # Mock the HTTP response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "data"}
        cog.http_client.get = AsyncMock(return_value=mock_resp)

        await cog.my_command(interaction, param="test")

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
```

### Test Coverage Requirements
- ✅ Happy path (success response)
- ✅ 404 Not Found error
- ✅ Other API errors
- ✅ Network/timeout error
- ✅ Admin permission check (if applicable)
- ✅ Autocomplete function (if applicable)

---

## File Naming Conventions

| Pattern | Auto-loaded? | Purpose |
|---------|-------------|---------|
| `*Cog.py` (e.g., `aboutCog.py`) | ✅ Yes | Production cog |
| `*Cog.py` containing "template" | ❌ No | Template/scaffold only |
| `*Cog.py` containing "test" | ❌ No | Test utilities only |
| `*Cog.py` containing "disabled" | ❌ No | Temporarily disabled |

---

*Last updated: 2026-06-11*
