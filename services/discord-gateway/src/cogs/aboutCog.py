import os
import discord
from typing import List, Optional, Dict, Any
from discord import app_commands
from discord.ext import commands
import shared.bblogger as bblogger
import httpx
import json
from utils.embed_converter import EmbedConverter    # ← grid‐builder for 2-col layout

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
        self._categories: List[str] = []
        self._objects_by_category: Dict[str, List[Dict]] = {}
        self.http_client = httpx.AsyncClient()

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
                    resp = await self.http_client.get(
                        f"{api_base}/about/categories/{category}/objects",
                        timeout=10
                    )
                    resp.raise_for_status()
                    objects = resp.json()
                    self._objects_by_category[category] = objects
                    flogger.debug(f"Preloaded {len(objects)} objects for category {category}")
                except Exception as e:
                    flogger.warning(f"Failed to preload objects for category {category}: {e}")
                    self._objects_by_category[category] = []

            flogger.info(
                f"Preload complete: {len(self._categories)} categories, "
                f"{sum(len(objs) for objs in self._objects_by_category.values())} total objects"
            )

        except Exception as e:
            flogger.warning(f"Failed to preload about data: {e}")
            # Set defaults so the cog can still function
            self._categories = []
            self._objects_by_category = {}

    async def category_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for category selection"""
        choices = [
            app_commands.Choice(name=cat.replace('_', ' ').title(), value=cat)
            for cat in self._categories
            if current.lower() in cat.lower()
        ]
        return choices[:25]

    async def object_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for object selection based on selected category"""
        category = getattr(interaction.namespace, 'category', None)
        if not category or category not in self._objects_by_category:
            return []

        objects = self._objects_by_category[category]
        choices: List[app_commands.Choice[str]] = []
        for obj in objects:
            name = obj.get('name', '')
            if current.lower() in name.lower():
                choices.append(app_commands.Choice(name=name, value=name))

        return choices[:25]

    @app_commands.command(
        name="about",
        description="Get detailed information about game objects (modules, weapons, etc.)"
    )
    @app_commands.describe(
        category="Select the category of object",
        name="Select the specific object name"
    )
    @app_commands.autocomplete(category=category_autocomplete, name=object_autocomplete)
    async def about(
        self,
        interaction: discord.Interaction,
        category: str,
        name: str
    ):
        """Main about command that displays detailed object information"""
        await interaction.response.defer(thinking=True)

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

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send(
                    f"❌ Object '{name}' not found in category '{category}'.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"❌ API Error: {e}",
                    ephemeral=True
                )
        except Exception as e:
            flogger.error(f"Error in about command: {e}")
            await interaction.followup.send(
                f"⚠️ An error occurred while fetching object information.",
                ephemeral=True
            )

    async def _create_object_embed(self, obj_data: Dict) -> discord.Embed:
        """Create a rich embed with object information"""
        name = obj_data.get('name', 'Unknown')
        category = obj_data.get('category', 'Unknown')

        # Create embed with appropriate color based on category
        color_map = {
            'module': discord.Color.blue(),
            'primary_weapon': discord.Color.red(),
            'secondary_weapon': discord.Color.orange(),
            'turret_weapon': discord.Color.purple(),
            'ship': discord.Color.green(),
            'criminal': discord.Color.dark_red(),
            'system': discord.Color.gold(),
        }
        color = color_map.get(category, discord.Color.default())

        # Create title with emoji if available
        title = name
        if obj_data.get('emoji'):
            title = f"{obj_data['emoji']} {name}"

        embed = discord.Embed(
            title=title,
            description=f"**Category:** {category.replace('_', ' ').title()}",
            color=color
        )

        # ← Generic thumbnail: check icon URL resolves before applying
        icon_url = obj_data.get('icon')
        if icon_url:
            try:
                head_resp = await self.http_client.head(icon_url, timeout=5)
                if head_resp.status_code == 200:
                    embed.set_thumbnail(url=icon_url)
                else:
                    flogger.debug(f"Icon URL returned {head_resp.status_code}: {icon_url}")
            except Exception as e:
                flogger.debug(f"Failed to validate icon URL {icon_url}: {e}")

        # Add basic information
        if obj_data.get('type'):
            embed.add_field(name="Type", value=obj_data['type'], inline=True)

        if obj_data.get('tech_level') is not None:
            embed.add_field(name="Tech Level", value=str(obj_data['tech_level']), inline=True)

        if obj_data.get('value') is not None:
            embed.add_field(name="Value", value=f"{obj_data['value']:,} credits", inline=True)

        # Add category-specific information
        if category == 'module':
            if obj_data.get('max_equipped') is not None:
                embed.add_field(name="Max Equipped", value=str(obj_data['max_equipped']), inline=True)

        elif category == 'primary_weapon':
            if obj_data.get('dps') is not None:
                embed.add_field(name="DPS", value=f"{obj_data['dps']:.1f}", inline=True)

        elif category == 'ship':
            # Hull & capacity
            if obj_data.get('armour') is not None:
                embed.add_field(name="Armour", value=str(obj_data['armour']), inline=True)
            if obj_data.get('cargo') is not None:
                embed.add_field(name="Cargo", value=f"{obj_data['cargo']} t", inline=True)

            # Performance
            if obj_data.get('handling') is not None:
                embed.add_field(name="Handling", value=str(obj_data['handling']), inline=True)
            if obj_data.get('shop_spawn_rate') is not None:
                rate = obj_data['shop_spawn_rate']
                embed.add_field(name="Shop Spawn Rate", value=f"{rate:.2f}", inline=True)

            # Loadout limits
            embed.add_field(name="Max Modules",    value=str(obj_data.get('max_modules', '–')), inline=True)
            embed.add_field(name="Max Primaries",  value=str(obj_data.get('max_primaries', '–')), inline=True)
            embed.add_field(name="Max Secondaries",value=str(obj_data.get('max_secondaries','–')), inline=True)
            embed.add_field(name="Max Turrets",    value=str(obj_data.get('max_turrets', '–')), inline=True)

            # Extras
            if obj_data.get('manufacturer'):
                embed.add_field(name="Manufacturer", value=obj_data['manufacturer'], inline=True)
            if obj_data.get('skinnable'):
                embed.add_field(name="Skinnable", value="Yes", inline=True)
            if obj_data.get('compatible_skins'):
                names = list(obj_data['compatible_skins'].keys())
                # build pairs of two
                pairs = [names[i:i+2] for i in range(0, len(names), 2)]
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
                embed.add_field(
                    name=f"Compatible Skins ({len(names)})",
                    value=grid,
                    inline=False
                )

        elif category == 'system':
            coords = obj_data.get('coordinates')
            if coords:
                embed.add_field(name="Coordinates", value=", ".join(str(c) for c in coords), inline=True)
            if obj_data.get('faction'):
                embed.add_field(name="Faction", value=str(obj_data['faction']), inline=True)

        elif category == 'criminal':
            if obj_data.get('faction'):
                embed.add_field(name="Faction", value=str(obj_data['faction']), inline=True)

        # Add aliases if available
        if obj_data.get('aliases'):
            aliases_text = ", ".join(obj_data['aliases'])
            if len(aliases_text) > 1024:
                aliases_text = aliases_text[:1021] + "..."
            embed.add_field(name="Aliases", value=aliases_text, inline=False)

        # Add built-in indicator
        if obj_data.get('built_in'):
            embed.add_field(name="Built-in", value="Yes", inline=True)

        # Add wiki link if available
        if obj_data.get('wiki'):
            embed.add_field(name="Wiki", value=f"[More Info]({obj_data['wiki']})", inline=False)

        # Add extra attributes if available
        if obj_data.get('extra_atts'):
            extra_text = ""
            for key, value in obj_data['extra_atts'].items():
                if isinstance(value, (int, float, str, bool)):
                    extra_text += f"**{key.replace('_', ' ').title()}:** {value}\n"
            if extra_text:
                if len(extra_text) > 1024:
                    extra_text = extra_text[:1021] + "..."
                embed.add_field(name="Additional Info", value=extra_text, inline=False)

        # Add footer
        embed.set_footer(text=f"ID: {obj_data.get('id', 'N/A')}")

        # ─── FORCE 2-COLUMN LAYOUT FOR MODULES, WEAPONS & SHIPS ─────────────
        if category in ('ship', 'module', 'primary_weapon', 'secondary_weapon', 'turret_weapon'):
            payload = EmbedConverter.embed_to_payload(embed)
            embed   = EmbedConverter.payload_to_grid_embed(payload, fields_per_row=2)

        return embed

    @app_commands.command(
        name="list_category",
        description="List all objects in a specific category"
    )
    @app_commands.describe(category="Select the category to list")
    @app_commands.autocomplete(category=category_autocomplete)
    async def list_category(
        self,
        interaction: discord.Interaction,
        category: str
    ):
        """List all objects in a specific category"""
        await interaction.response.defer(thinking=True)

        try:
            if category not in self._objects_by_category:
                await interaction.followup.send(
                    f"❌ Category '{category}' not found.",
                    ephemeral=True
                )
                return

            objects = self._objects_by_category[category]
            if not objects:
                await interaction.followup.send(
                    f"📭 No objects found in category '{category}'.",
                    ephemeral=True
                )
                return

            # Create embed with list of objects
            embed = discord.Embed(
                title=f"{category.replace('_', ' ').title()} Objects",
                description=f"Found {len(objects)} objects",
                color=discord.Color.blue()
            )

            # Group objects into fields to avoid hitting embed limits
            objects_text = ""
            for obj in objects[:50]:
                name = obj.get('name', 'Unknown')
                emoji = obj.get('emoji', '')
                line = f"{emoji} {name}\n" if emoji else f"{name}\n"
                if len(objects_text + line) > 1024:
                    embed.add_field(name="Objects", value=objects_text, inline=False)
                    objects_text = line
                else:
                    objects_text += line

            if objects_text:
                embed.add_field(name="Objects", value=objects_text, inline=False)
            if len(objects) > 100:
                embed.set_footer(text=f"Showing first 100 of {len(objects)} objects")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            flogger.error(f"Error in list_category command: {e}")
            await interaction.followup.send(
                f"⚠️ An error occurred while listing objects.",
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    flogger.debug("Setting up AboutCog...")
    await bot.add_cog(AboutCog(bot))
    flogger.info("AboutCog loaded")
