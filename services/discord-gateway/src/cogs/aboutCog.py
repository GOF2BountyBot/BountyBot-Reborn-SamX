import asyncio
import io
import os
import time

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from cogs._shared.embed_pagination import DEFAULT_LIST_CAP, add_continuation_fields
from cogs._shared.http_error_handler import report_api_error
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import fuzzy_filter, normalize_for_search
from utils.embed_converter import EmbedConverter  # <- grid-builder for 2-col layout

# Set up logger
flogger = bblogger.get_logger("discord-gateway-AboutCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"aboutCog loading with BOT_API_BASE_URL: {api_base}")

# Commodity price/raw fields are rendered explicitly in the commodity branch, so they
# must be suppressed from the generic "Additional Info" extra_atts dump to avoid
# duplicate fields and raw wiki markup leaking into the embed.

# TTL for the icon-URL validation success cache (seconds).
_ICON_CACHE_TTL_S = 3600

_COMMODITY_EXTRA_SKIP = {
    "price_source",
    "price_range_min_credits",
    "price_range_max_credits",
    "price_range_min_system",
    "price_range_max_system",
    "highest_non_loma_price",
    "highest_non_loma_system",
    "raw_infobox",
}


def is_developer():
    # Example role check, uncomment and configure as needed
    # return app_commands.checks.has_role("developer")
    return True


class AboutCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Static catalog caches — TTL=None (never expires; only reloaded on /reload_autocomplete)
        self._categories_cache: AutocompleteCache[str, list[str]] = AutocompleteCache(
            ttl_seconds=None,
            name="about-categories",
        )
        self._objects_cache: AutocompleteCache[str, list[dict]] = AutocompleteCache(
            ttl_seconds=None,
            name="about-objects",
        )
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        # Maps icon URL → monotonic timestamp of last successful HEAD validation.
        # Only successful responses are cached; failures are never stored so that
        # transient rate-limit errors self-heal on the next /about command.
        self._icon_ok_cache: dict[str, float] = {}

        # Schedule preload once bot is ready
        bot.loop.create_task(self._preload_data())

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _preload_data(self):
        """Preload all categories and objects at startup for responsiveness.

        Uses 5-attempt exponential-backoff retry (5s, 10s, 20s, 40s, 60s) for the
        categories fetch, matching the pattern in adminCog._preload_static_catalogs.
        On terminal failure leaves _categories and _objects_by_category empty so the
        cog degrades gracefully (commands still work, autocomplete returns nothing).
        """
        await self.bot.wait_until_ready()

        # --- Step 1: fetch categories with retry ---
        categories: list[str] = []
        for attempt in range(5):
            try:
                flogger.info(f"Starting preload of about data (attempt {attempt + 1}/5)...")
                resp = await self.http_client.get(f"{api_base}/about/categories", timeout=5)
                resp.raise_for_status()
                categories = resp.json()
                flogger.debug(f"Preloaded categories: {categories}")
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                wait = min(5 * (2**attempt), 60)
                flogger.warning(
                    f"_preload_data: failed to fetch categories "
                    f"(attempt {attempt + 1}/5): {type(exc).__name__}: {exc}, retrying in {wait}s"
                )
                await asyncio.sleep(wait)
        else:
            flogger.error(
                "_preload_data: terminal failure fetching categories after 5 attempts; autocomplete will be empty"
            )
            self._categories_cache.set("all", [])
            return

        self._categories_cache.set("all", categories)

        # --- Step 2: fetch objects per category (each independently, no retry needed here) ---
        for category in categories:
            try:
                resp = await self.http_client.get(f"{api_base}/about/categories/{category}/objects", timeout=10)
                resp.raise_for_status()
                objects = resp.json()
                self._objects_cache.set(category, objects)
                flogger.debug(f"Preloaded {len(objects)} objects for category {category}")
            except httpx.TimeoutException as e:
                flogger.warning(f"Timeout preloading objects for category {category}: {e}")
                self._objects_cache.set(category, [])
            except httpx.HTTPStatusError as e:
                flogger.warning(f"HTTP error preloading objects for category {category}: {e.response.status_code}")
                self._objects_cache.set(category, [])
            except httpx.RequestError as e:
                flogger.warning(f"Request error preloading objects for category {category}: {e}")
                self._objects_cache.set(category, [])
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Failed to preload objects for category {category}: {e}")
                self._objects_cache.set(category, [])

        total_objects = sum(len(self._objects_cache.peek(cat) or []) for cat in categories)
        flogger.info(f"Preload complete: {len(categories)} categories, {total_objects} total objects")

    async def category_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for category selection"""
        norm_current = normalize_for_search(current)
        categories = self._categories_cache.peek("all") or []
        choices = [
            app_commands.Choice(name=cat.replace("_", " ").title(), value=cat)
            for cat in categories
            if norm_current in normalize_for_search(cat)
        ]
        return choices[:25]

    async def system_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for system name selection using preloaded data."""
        systems = self._objects_cache.peek("system") or []
        names = [obj["name"] for obj in systems if obj.get("name")]
        return [app_commands.Choice(name=name, value=name) for name in fuzzy_filter(current, names)]

    async def object_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for object selection based on selected category"""
        category = getattr(interaction.namespace, "category", None)
        if not category:
            return []
        objects = self._objects_cache.peek(category)
        if objects is None:
            return []
        names = [obj["name"] for obj in objects if obj.get("name")]
        return [app_commands.Choice(name=name, value=name) for name in fuzzy_filter(current, names)]

    @app_commands.command(
        name="about", description="Get detailed information about game objects (modules, weapons, etc.)"
    )
    @app_commands.describe(category="Select the category of object", name="Select the specific object name")
    @app_commands.autocomplete(category=category_autocomplete, name=object_autocomplete)
    async def about(self, interaction: discord.Interaction, category: str, name: str):
        """Main about command that displays detailed object information"""
        await interaction.response.defer(thinking=True, ephemeral=True)
        flogger.debug(
            f"/about invoked: guild={interaction.guild_id} user={interaction.user.id} category={category} name={name}"
        )

        # ── Resolve alias to canonical name if needed ──────────────────────────────
        resolved_name = name
        _cat_objects = self._objects_cache.peek(category) or []
        for obj in _cat_objects:
            if name == obj.get("name") or name in obj.get("aliases", []):
                resolved_name = obj["name"]
                break
        try:
            # Get object by name from the API
            resp = await self.http_client.get(f"{api_base}/about/object/name/{resolved_name}", timeout=10)
            resp.raise_for_status()
            obj_data = resp.json()

            # Create rich embed with object information
            embed = await self._create_object_embed(obj_data)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/about success: guild={interaction.guild_id} user={interaction.user.id}"
                f" category={category} name={resolved_name}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send(
                    f"❌ Object '{name}' not found in category '{category}'.", ephemeral=True
                )
            else:
                flogger.error(
                    f"/about API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" category={category} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/about error: guild={interaction.guild_id} user={interaction.user.id}"
                f" category={category} name={name} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching object information.", ephemeral=True)

    async def _validate_icon_with_cache(self, url: str) -> bool:
        """Validate an icon URL with retry and success-only caching.

        Returns True if the URL is reachable (HTTP 200).  False on ultimate failure.

        Cache behaviour:
        - Successful validations are cached for _ICON_CACHE_TTL_S seconds using
          time.monotonic() so the result is immune to wall-clock jumps.
        - Failures are NEVER cached; a transient rate-limit therefore self-heals on
          the next /about invocation without any manual intervention.

        Retry behaviour:
        - Up to 2 HEAD attempts; a 1-second async sleep separates them so that a
          single momentary rate-limit burst still yields a valid thumbnail on retry.
        """
        now = time.monotonic()
        cached_ts = self._icon_ok_cache.get(url)
        if cached_ts is not None and now - cached_ts < _ICON_CACHE_TTL_S:
            return True

        for attempt in range(2):
            try:
                head_resp = await self.http_client.head(url, timeout=5)
                if head_resp.status_code == 200:
                    self._icon_ok_cache[url] = time.monotonic()
                    return True
                flogger.debug(f"Icon URL returned {head_resp.status_code} (attempt {attempt + 1}/2): {url}")
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.debug(f"Failed to validate icon URL {url} (attempt {attempt + 1}/2): {e}")

            if attempt == 0:
                await asyncio.sleep(1)

        # Both attempts failed — do NOT cache; return fail-closed
        return False

    async def _create_object_embed(self, obj_data: dict) -> discord.Embed:
        """Create a rich embed with object information"""
        name = obj_data.get("name", "Unknown")
        category = obj_data.get("category", "Unknown")

        # Create embed with appropriate color based on category
        color_map = {
            "module": discord.Color.blue(),
            "primary_weapon": discord.Color.red(),
            "secondary_weapon": discord.Color.orange(),
            "turret_weapon": discord.Color.purple(),
            "ship": discord.Color.green(),
            "criminal": discord.Color.dark_red(),
            "system": discord.Color.gold(),
            "commodity": discord.Color.teal(),
        }
        color = color_map.get(category, discord.Color.default())

        # Create title with emoji if available
        title = name
        if obj_data.get("emoji"):
            title = f"{obj_data['emoji']} {name}"

        embed = discord.Embed(
            title=title, description=f"**Category:** {category.replace('_', ' ').title()}", color=color
        )

        # ← Generic thumbnail: check icon URL resolves before applying
        icon_url = obj_data.get("icon")
        if icon_url and await self._validate_icon_with_cache(icon_url):
            embed.set_thumbnail(url=icon_url)

        # Add basic information
        if obj_data.get("type"):
            embed.add_field(name="Type", value=obj_data["type"], inline=True)

        if obj_data.get("tech_level") is not None:
            embed.add_field(name="Tech Level", value=str(obj_data["tech_level"]), inline=True)

        if obj_data.get("value") is not None:
            embed.add_field(name="Value", value=f"{obj_data['value']:,} credits", inline=True)

        # Add category-specific information
        if category == "module":
            if obj_data.get("max_equipped") is not None:
                embed.add_field(name="Max Equipped", value=str(obj_data["max_equipped"]), inline=True)
            # §14 / T11: PrimaryWeaponMod breakdown — all three fields required per spec
            dmg_pct = obj_data.get("damage_pct")
            fr_pct = obj_data.get("fire_rate_pct")
            dps_mult = obj_data.get("dps_multiplier")
            if dmg_pct is not None or fr_pct is not None or dps_mult is not None:
                if dmg_pct is not None:
                    sign = "+" if dmg_pct >= 0 else ""
                    embed.add_field(name="Damage modifier", value=f"{sign}{dmg_pct}%", inline=True)
                if fr_pct is not None:
                    sign = "+" if fr_pct >= 0 else ""
                    embed.add_field(name="Fire rate modifier", value=f"{sign}{fr_pct}%", inline=True)
                if dps_mult is not None:
                    embed.add_field(name="Net DPS shift", value=f"x{dps_mult:.2f}", inline=True)

        elif category == "primary_weapon":
            if obj_data.get("dps") is not None:
                embed.add_field(name="DPS", value=f"{obj_data['dps']:.1f}", inline=True)
            # §14 / T11: EMP damage — show instead of (misleading) "Damage: 0" for pure-EMP blasters
            emp_dmg = obj_data.get("emp_damage")
            if emp_dmg is not None and emp_dmg > 0:
                embed.add_field(name="EMP damage", value=str(emp_dmg), inline=True)
            # D-002: per-shot breakdown fields
            dps_shot = obj_data.get("damage_per_shot")
            if dps_shot is not None and dps_shot > 0:
                embed.add_field(name="Damage per shot", value=str(dps_shot), inline=True)
            ls_ms = obj_data.get("loading_speed_ms")
            if ls_ms is not None:
                embed.add_field(name="Loading speed", value=f"{ls_ms} ms", inline=True)
            weapon_subtype = obj_data.get("subtype")
            if weapon_subtype:
                embed.add_field(name="Weapon type", value=weapon_subtype.replace("-", " ").title(), inline=True)

        elif category == "secondary_weapon":
            # §14 / T11: Cluster-missile burst fields — show before generic damage to keep context clear
            burst = obj_data.get("burst_count")
            if burst is not None:
                per_shot = obj_data.get("damage")
                embed.add_field(name="Burst count", value=str(burst), inline=True)
                if per_shot is not None:
                    embed.add_field(name="Total damage on full hit", value=str(burst * per_shot), inline=True)
                # per-sub-munition damage shown after totals for comparison
                if per_shot is not None:
                    embed.add_field(name="Damage (per sub-munition)", value=str(per_shot), inline=True)
            # §14 / T11: Nuke fields — direct hit + effective radius + self-damage warning
            elif obj_data.get("nuke_effective_magnitude_m") is not None:
                nuke_dmg = obj_data.get("nuke_direct_damage")
                eff_mag = obj_data["nuke_effective_magnitude_m"]
                self_factor = obj_data.get("nuke_self_damage_factor", 0.25)
                if nuke_dmg is not None:
                    embed.add_field(name="Direct hit damage", value=str(nuke_dmg), inline=True)
                embed.add_field(name="Effective blast radius", value=f"{eff_mag} m", inline=True)
                if nuke_dmg is not None:
                    self_dmg = round(nuke_dmg * self_factor)
                    embed.add_field(name="Self-damage at point-blank", value=f"~{self_dmg} hp", inline=True)
            else:
                # Standard missile/mine damage (non-cluster, non-nuke).
                # §14 / T11: pure-EMP secondaries (damage=0, emp_damage>0) must NOT show
                # a misleading "Damage: 0" field — mirror the primary-weapon EMP path.
                dmg_val = obj_data.get("damage")
                sec_emp_check = obj_data.get("emp_damage")
                is_pure_emp = dmg_val == 0 and sec_emp_check is not None and sec_emp_check > 0
                if dmg_val is not None and not is_pure_emp:
                    embed.add_field(name="Damage", value=str(dmg_val), inline=True)
            # §14 / T11: EMP damage for secondary weapons (Mamba EMP missile, Neétha EMP mine)
            sec_emp = obj_data.get("emp_damage")
            if sec_emp is not None and sec_emp > 0:
                embed.add_field(name="EMP damage", value=str(sec_emp), inline=True)
            ls = obj_data.get("loading_speed")
            if ls is not None:
                embed.add_field(name="Loading Speed", value=f"{ls} ms", inline=True)
            # D-004: weapon subtype for secondary weapons
            sec_subtype = obj_data.get("subtype")
            if sec_subtype:
                embed.add_field(name="Weapon type", value=sec_subtype.replace("-", " ").title(), inline=True)

        elif category == "turret_weapon":
            if obj_data.get("dps") is not None:
                embed.add_field(name="DPS", value=f"{obj_data['dps']:.1f}", inline=True)
            # D-002: per-shot breakdown fields
            dps_shot = obj_data.get("damage_per_shot")
            if dps_shot is not None and dps_shot > 0:
                embed.add_field(name="Damage per shot", value=str(dps_shot), inline=True)
            ls_ms = obj_data.get("loading_speed_ms")
            if ls_ms is not None:
                embed.add_field(name="Loading speed", value=f"{ls_ms} ms", inline=True)
            # D-003: firing mode — shown on all turrets (including plasma-collectors)
            automatic = obj_data.get("automatic")
            if automatic is not None:
                embed.add_field(name="Firing mode", value="Automatic" if automatic else "Manual", inline=True)
            weapon_subtype = obj_data.get("subtype")
            if weapon_subtype:
                embed.add_field(name="Weapon type", value=weapon_subtype.replace("-", " ").title(), inline=True)

        elif category == "ship":
            # Hull & capacity
            if obj_data.get("armour") is not None:
                embed.add_field(name="Armour", value=str(obj_data["armour"]), inline=True)
            if obj_data.get("cargo") is not None:
                embed.add_field(name="Cargo", value=f"{obj_data['cargo']} t", inline=True)

            # Performance
            if obj_data.get("handling") is not None:
                embed.add_field(name="Handling", value=str(obj_data["handling"]), inline=True)
            if obj_data.get("shop_spawn_rate") is not None:
                rate = obj_data["shop_spawn_rate"]
                embed.add_field(name="Shop Spawn Rate", value=f"{rate:.2f}", inline=True)

            # Loadout limits
            embed.add_field(name="Max Modules", value=str(obj_data.get("max_modules", "-")), inline=True)
            embed.add_field(name="Max Primaries", value=str(obj_data.get("max_primaries", "-")), inline=True)
            embed.add_field(name="Max Secondaries", value=str(obj_data.get("max_secondaries", "-")), inline=True)
            embed.add_field(name="Max Turrets", value=str(obj_data.get("max_turrets", "-")), inline=True)

            # Extras
            if obj_data.get("manufacturer"):
                embed.add_field(name="Manufacturer", value=obj_data["manufacturer"], inline=True)
            if obj_data.get("skinnable"):
                embed.add_field(name="Skinnable", value="Yes", inline=True)
            if obj_data.get("builtin_modules"):
                bm = ", ".join(obj_data["builtin_modules"])
                embed.add_field(name="Built-in Modules", value=bm, inline=True)
            if obj_data.get("compatible_skins"):
                names = list(obj_data["compatible_skins"].keys())
                # build pairs of two
                pairs = [names[i : i + 2] for i in range(0, len(names), 2)]
                # compute max width of left column
                max_left = max(len(p[0]) for p in pairs)
                lines: list[str] = []
                for left, *rest in pairs:
                    right = rest[0] if rest else ""
                    if right:
                        # pad left to max_left for perfect mono alignment
                        lines.append(f"{left.ljust(max_left)}    {right}")
                    else:
                        lines.append(left)
                # wrap in a code block so spaces are honored
                grid = "```" + "\n".join(lines) + "```"
                embed.add_field(name=f"Compatible Skins ({len(names)})", value=grid, inline=False)

        elif category == "system":
            coords = obj_data.get("coordinates")
            if coords:
                embed.add_field(name="Coordinates", value=", ".join(str(c) for c in coords), inline=True)
            if obj_data.get("faction"):
                embed.add_field(name="Faction", value=str(obj_data["faction"]), inline=True)

        elif category == "criminal" and obj_data.get("faction"):
            embed.add_field(name="Faction", value=str(obj_data["faction"]), inline=True)

        elif category == "commodity":
            if obj_data.get("subcategory"):
                embed.add_field(
                    name="Subcategory", value=str(obj_data["subcategory"]).replace("_", " ").title(), inline=True
                )
            pmin = obj_data.get("price_range_min_credits")
            pmax = obj_data.get("price_range_max_credits")
            if pmin is not None and pmax is not None:
                embed.add_field(name="Price Range", value=f"{pmin:,} – {pmax:,} cr", inline=True)
            if obj_data.get("price_range_min_system"):
                embed.add_field(name="Lowest @", value=str(obj_data["price_range_min_system"]), inline=True)
            if obj_data.get("price_range_max_system"):
                embed.add_field(name="Highest @", value=str(obj_data["price_range_max_system"]), inline=True)
            if obj_data.get("highest_non_loma_price") is not None:
                _sys = obj_data.get("highest_non_loma_system") or "?"
                embed.add_field(
                    name="Best non-Loma", value=f"{obj_data['highest_non_loma_price']:,} cr @ {_sys}", inline=True
                )
            if obj_data.get("price_source"):
                embed.add_field(
                    name="Price Basis", value=str(obj_data["price_source"]).replace("_", " ").title(), inline=True
                )

        # Add aliases if available
        if obj_data.get("aliases"):
            aliases_text = ", ".join(obj_data["aliases"])
            if len(aliases_text) > 1024:
                aliases_text = aliases_text[:1021] + "..."
            embed.add_field(name="Aliases", value=aliases_text, inline=False)

        # Add built-in indicator
        if obj_data.get("built_in"):
            embed.add_field(name="Built-in", value="Yes", inline=True)

        # Add wiki link if available
        if obj_data.get("wiki"):
            embed.add_field(name="Wiki", value=f"[More Info]({obj_data['wiki']})", inline=False)

        # Mechanics / lore text — extracted from extra_atts and shown separately
        # so it doesn't get buried in the generic attribute dump.
        extra_atts = obj_data.get("extra_atts") or {}
        mechanics = extra_atts.get("mechanics_text")
        if mechanics and isinstance(mechanics, str) and mechanics.strip():
            trunc = mechanics[:500] + "…" if len(mechanics) > 500 else mechanics
            embed.add_field(name="Lore / Mechanics", value=trunc, inline=False)

        # §14 / T11: suppress outer extra_atts keys that are already rendered explicitly
        # (dpsMultiplier is a scalar at the outer level and would appear in the generic dump)
        _T11_EXTRA_SUPPRESS = {"dpsMultiplier"}

        # D-005: suppress "loading speed" from the generic dump for secondary weapons —
        # it is already rendered as the dedicated "Loading Speed: <n> ms" field above.
        # SQL-confirmed outer key: `loading speed` (lowercase, single space, no underscore/suffix).
        _SECONDARY_EXTRA_SKIP = {"loading speed"}

        # Add remaining extra attributes (skip mechanics_text — shown above)
        if extra_atts:
            extra_text = ""
            for key, value in extra_atts.items():
                if key == "mechanics_text":
                    continue
                if key in _T11_EXTRA_SUPPRESS:
                    continue
                if category == "commodity" and key in _COMMODITY_EXTRA_SKIP:
                    continue
                if category == "secondary_weapon" and key in _SECONDARY_EXTRA_SKIP:
                    continue
                if isinstance(value, (int, float, str, bool)):
                    extra_text += f"**{key.replace('_', ' ').title()}:** {value}\n"
            if extra_text:
                if len(extra_text) > 1024:
                    extra_text = extra_text[:1021] + "..."
                embed.add_field(name="Additional Info", value=extra_text, inline=False)

        # Add footer
        embed.set_footer(text=f"ID: {obj_data.get('id', 'N/A')}")

        # ─── FORCE 2-COLUMN LAYOUT FOR MODULES, WEAPONS & SHIPS ─────────────
        if category in ("ship", "module", "primary_weapon", "secondary_weapon", "turret_weapon", "commodity"):
            payload = EmbedConverter.embed_to_payload(embed)
            embed = EmbedConverter.payload_to_grid_embed(payload, fields_per_row=2)

        return embed

    @app_commands.command(name="list_category", description="List all objects in a specific category")
    @app_commands.describe(
        category="Select the category to list",
        tech_level="Filter by tech level (1-5)",
        manufacturer="Filter by manufacturer name",
    )
    @app_commands.autocomplete(category=category_autocomplete)
    async def list_category(
        self,
        interaction: discord.Interaction,
        category: str,
        tech_level: int | None = None,
        manufacturer: str | None = None,
    ):
        """List all objects in a specific category, with optional filters"""
        await interaction.response.defer(thinking=True, ephemeral=True)
        flogger.debug(
            f"/list_category invoked: guild={interaction.guild_id} user={interaction.user.id}"
            f" category={category} tech_level={tech_level} manufacturer={manufacturer}"
        )

        try:
            objects = self._objects_cache.peek(category)
            if objects is None:
                await interaction.followup.send(f"❌ Category '{category}' not found.", ephemeral=True)
                return
            if not objects:
                await interaction.followup.send(f"📭 No objects found in category '{category}'.", ephemeral=True)
                return

            # Apply optional client-side filters
            filtered = objects
            if tech_level is not None:
                filtered = [o for o in filtered if o.get("tech_level") == tech_level]
            if manufacturer is not None:
                manufacturer_lower = manufacturer.lower()
                filtered = [o for o in filtered if manufacturer_lower in str(o.get("manufacturer", "")).lower()]

            if not filtered:
                filter_desc = []
                if tech_level is not None:
                    filter_desc.append(f"tech level {tech_level}")
                if manufacturer is not None:
                    filter_desc.append(f"manufacturer '{manufacturer}'")
                await interaction.followup.send(
                    f"📭 No objects found in category '{category}' matching {' and '.join(filter_desc)}.",
                    ephemeral=True,
                )
                return

            # Build description with filter summary
            description = f"Found {len(filtered)} object(s)"
            if len(filtered) < len(objects):
                description += f" (filtered from {len(objects)})"
            filter_parts = []
            if tech_level is not None:
                filter_parts.append(f"Tech Level: {tech_level}")
            if manufacturer is not None:
                filter_parts.append(f"Manufacturer: {manufacturer}")
            if filter_parts:
                description += "\n🔍 " + " | ".join(filter_parts)

            # Create embed with list of objects
            embed = discord.Embed(
                title=f"{category.replace('_', ' ').title()} Objects",
                description=description,
                color=discord.Color.blue(),
            )

            # Pre-format each line (emoji + name).  The pagination helper
            # handles the 1024-char field-split and the 100-item cap so that
            # A.26 (duplicate "Objects" headers) and A.27 (silent truncation)
            # are both prevented by construction.
            total_count = len(filtered)
            lines = []
            for obj in filtered[:DEFAULT_LIST_CAP]:
                name = obj.get("name", "Unknown")
                emoji = obj.get("emoji", "")
                lines.append(f"{emoji} {name}" if emoji else name)

            add_continuation_fields(
                embed,
                header_name="Objects",
                lines=lines,
                cap=DEFAULT_LIST_CAP,
            )

            if total_count > DEFAULT_LIST_CAP:
                embed.set_footer(text=f"Showing first {DEFAULT_LIST_CAP} of {total_count} objects")

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/list_category success: guild={interaction.guild_id} user={interaction.user.id}"
                f" category={category} count={len(filtered)}"
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/list_category error: guild={interaction.guild_id} user={interaction.user.id}"
                f" category={category} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while listing objects.", ephemeral=True)

    @app_commands.command(name="make-route", description="Find the shortest route between two star systems")
    @app_commands.describe(
        start="Starting star system",
        end="Destination star system",
    )
    @app_commands.autocomplete(start=system_autocomplete, end=system_autocomplete)
    async def make_route(self, interaction: discord.Interaction, start: str, end: str):
        """Display the shortest hop-by-hop route between two star systems."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        flogger.debug(
            f"/make-route invoked: guild={interaction.guild_id} user={interaction.user.id} start={start} end={end}"
        )

        try:
            resp = await self.http_client.get(
                f"{api_base}/systems/route",
                params={"start": start, "end": end},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            route: list[str] = data.get("route", [])
            hops: int = data.get("hops", 0)

            # Build numbered route list
            route_lines = "\n".join(f"{i + 1}. {system}" for i, system in enumerate(route))

            embed = discord.Embed(
                title=f"Route: {start} → {end}",
                description=route_lines or "No route data",
                color=discord.Color.gold(),
            )
            embed.add_field(name="Total Hops", value=str(hops), inline=True)
            embed.set_footer(text=f"Shortest path via A* ({len(route)} system(s))")

            # Attempt to fetch the route map image and attach it to the embed.
            # This is a best-effort enhancement — a failure does not block the response.
            map_file: discord.File | None = None
            try:
                map_resp = await self.http_client.get(
                    f"{api_base}/systems/route/map",
                    params={"start": start, "end": end},
                    timeout=10,
                )
                map_resp.raise_for_status()
                map_file = discord.File(io.BytesIO(map_resp.content), filename="route_map.png")
                embed.set_image(url="attachment://route_map.png")
                flogger.debug(
                    f"/make-route map fetched: guild={interaction.guild_id} user={interaction.user.id}"
                    f" start={start} end={end}"
                )
            except Exception as map_err:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/make-route map unavailable (non-fatal): guild={interaction.guild_id}"
                    f" user={interaction.user.id} start={start} end={end} error={map_err}"
                )

            if map_file is not None:
                await interaction.followup.send(embed=embed, file=map_file, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)

            flogger.info(
                f"/make-route success: guild={interaction.guild_id} user={interaction.user.id}"
                f" start={start} end={end} hops={hops}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send(
                    f"❌ No route found between **{start}** and **{end}**.",
                    ephemeral=True,
                )
            elif e.response.status_code == 400:
                await interaction.followup.send(
                    f"❌ Route from **{start}** to **{end}** exceeds the maximum length (50 hops).",
                    ephemeral=True,
                )
            else:
                flogger.error(
                    f"/make-route API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" start={start} end={end} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/make-route error: guild={interaction.guild_id} user={interaction.user.id}"
                f" start={start} end={end} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while finding the route.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up AboutCog...")
    await bot.add_cog(AboutCog(bot))
    flogger.info("AboutCog loaded")
