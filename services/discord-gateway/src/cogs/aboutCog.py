import io
import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import normalize_for_search
from utils.embed_converter import EmbedConverter  # <- grid-builder for 2-col layout

# Set up logger
flogger = bblogger.get_logger("discord-gateway-AboutCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"aboutCog loading with BOT_API_BASE_URL: {api_base}")


def is_developer():
    # Example role check, uncomment and configure as needed
    # return app_commands.checks.has_role("developer")
    return True


class AboutCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._categories: list[str] = []
        self._objects_by_category: dict[str, list[dict]] = {}
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

        # Schedule preload once bot is ready
        bot.loop.create_task(self._preload_data())

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _preload_data(self):
        """Preload all categories and objects at startup for responsiveness"""
        await self.bot.wait_until_ready()
        try:
            flogger.info("Starting preload of about data...")

            # Load categories
            resp = await self.http_client.get(f"{api_base}/about/categories", timeout=5)
            resp.raise_for_status()
            self._categories = resp.json()
            flogger.debug(f"Preloaded categories: {self._categories}")

            # Load objects for each category
            for category in self._categories:
                try:
                    resp = await self.http_client.get(f"{api_base}/about/categories/{category}/objects", timeout=10)
                    resp.raise_for_status()
                    objects = resp.json()
                    self._objects_by_category[category] = objects
                    flogger.debug(f"Preloaded {len(objects)} objects for category {category}")
                except httpx.TimeoutException as e:
                    flogger.warning(f"Timeout preloading objects for category {category}: {e}")
                    self._objects_by_category[category] = []
                except httpx.HTTPStatusError as e:
                    flogger.warning(f"HTTP error preloading objects for category {category}: {e.response.status_code}")
                    self._objects_by_category[category] = []
                except httpx.RequestError as e:
                    flogger.warning(f"Request error preloading objects for category {category}: {e}")
                    self._objects_by_category[category] = []
                except Exception as e:  # pylint: disable=broad-exception-caught
                    flogger.warning(f"Failed to preload objects for category {category}: {e}")
                    self._objects_by_category[category] = []

            flogger.info(
                f"Preload complete: {len(self._categories)} categories, "
                f"{sum(len(objs) for objs in self._objects_by_category.values())} total objects"
            )

        except httpx.TimeoutException as e:
            flogger.warning(f"Timeout preloading about data: {e}")
            self._categories = []
            self._objects_by_category = {}
        except httpx.HTTPStatusError as e:
            flogger.warning(f"HTTP error preloading about data: {e.response.status_code}")
            self._categories = []
            self._objects_by_category = {}
        except httpx.RequestError as e:
            flogger.warning(f"Request error preloading about data: {e}")
            self._categories = []
            self._objects_by_category = {}
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"Unexpected error preloading about data: {e}")
            # Set defaults so the cog can still function
            self._categories = []
            self._objects_by_category = {}

    async def category_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for category selection"""
        norm_current = normalize_for_search(current)
        choices = [
            app_commands.Choice(name=cat.replace("_", " ").title(), value=cat)
            for cat in self._categories
            if norm_current in normalize_for_search(cat)
        ]
        return choices[:25]

    async def system_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for system name selection using preloaded data."""
        norm_current = normalize_for_search(current)
        systems = self._objects_by_category.get("system", [])
        choices = [
            app_commands.Choice(name=obj["name"], value=obj["name"])
            for obj in systems
            if norm_current in normalize_for_search(obj.get("name", ""))
        ]
        return choices[:25]

    async def object_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for object selection based on selected category"""
        category = getattr(interaction.namespace, "category", None)
        if not category or category not in self._objects_by_category:
            return []

        norm_current = normalize_for_search(current)
        objects = self._objects_by_category[category]
        choices: list[app_commands.Choice[str]] = []
        for obj in objects:
            name = obj.get("name", "")
            if norm_current in normalize_for_search(name):
                choices.append(app_commands.Choice(name=name, value=name))

        return choices[:25]

    @app_commands.command(
        name="about", description="Get detailed information about game objects (modules, weapons, etc.)"
    )
    @app_commands.describe(category="Select the category of object", name="Select the specific object name")
    @app_commands.autocomplete(category=category_autocomplete, name=object_autocomplete)
    async def about(self, interaction: discord.Interaction, category: str, name: str):
        """Main about command that displays detailed object information"""
        await interaction.response.defer(thinking=True)
        flogger.debug(
            f"/about invoked: guild={interaction.guild_id} user={interaction.user.id} category={category} name={name}"
        )

        # ── Resolve alias to canonical name if needed ──────────────────────────────
        resolved_name = name
        if category in self._objects_by_category:
            for obj in self._objects_by_category[category]:
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
            await interaction.followup.send(embed=embed)
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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/about error: guild={interaction.guild_id} user={interaction.user.id}"
                f" category={category} name={name} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching object information.", ephemeral=True)

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
        if icon_url:
            try:
                head_resp = await self.http_client.head(icon_url, timeout=5)
                if head_resp.status_code == 200:
                    embed.set_thumbnail(url=icon_url)
                else:
                    flogger.debug(f"Icon URL returned {head_resp.status_code}: {icon_url}")
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.debug(f"Failed to validate icon URL {icon_url}: {e}")

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

        elif category == "primary_weapon":
            if obj_data.get("dps") is not None:
                embed.add_field(name="DPS", value=f"{obj_data['dps']:.1f}", inline=True)

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

        # Add extra attributes if available
        if obj_data.get("extra_atts"):
            extra_text = ""
            for key, value in obj_data["extra_atts"].items():
                if isinstance(value, (int, float, str, bool)):
                    extra_text += f"**{key.replace('_', ' ').title()}:** {value}\n"
            if extra_text:
                if len(extra_text) > 1024:
                    extra_text = extra_text[:1021] + "..."
                embed.add_field(name="Additional Info", value=extra_text, inline=False)

        # Add footer
        embed.set_footer(text=f"ID: {obj_data.get('id', 'N/A')}")

        # ─── FORCE 2-COLUMN LAYOUT FOR MODULES, WEAPONS & SHIPS ─────────────
        if category in ("ship", "module", "primary_weapon", "secondary_weapon", "turret_weapon"):
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
        await interaction.response.defer(thinking=True)
        flogger.debug(
            f"/list_category invoked: guild={interaction.guild_id} user={interaction.user.id}"
            f" category={category} tech_level={tech_level} manufacturer={manufacturer}"
        )

        try:
            if category not in self._objects_by_category:
                await interaction.followup.send(f"❌ Category '{category}' not found.", ephemeral=True)
                return

            objects = self._objects_by_category[category]
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

            # Group objects into fields to avoid hitting embed limits
            objects_text = ""
            for obj in filtered[:50]:
                name = obj.get("name", "Unknown")
                emoji = obj.get("emoji", "")
                line = f"{emoji} {name}\n" if emoji else f"{name}\n"
                if len(objects_text + line) > 1024:
                    embed.add_field(name="Objects", value=objects_text, inline=False)
                    objects_text = line
                else:
                    objects_text += line

            if objects_text:
                embed.add_field(name="Objects", value=objects_text, inline=False)
            if len(filtered) > 100:
                embed.set_footer(text=f"Showing first 100 of {len(filtered)} objects")

            await interaction.followup.send(embed=embed)
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
        await interaction.response.defer(thinking=True)
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
                await interaction.followup.send(embed=embed, file=map_file)
            else:
                await interaction.followup.send(embed=embed)

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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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
