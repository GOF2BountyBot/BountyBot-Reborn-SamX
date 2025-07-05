import os
import discord
from typing import List, Optional, Dict, Any
from discord import app_commands
from discord.ext import commands
import shared.logging as logging
import requests
import json

# Set up logger
logger = logging.get_logger("discord-gateway-AboutCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
logger.debug(f"aboutCog loading with BOT_API_BASE_URL: {api_base}")

def is_developer():
    # Example role check, uncomment and configure as needed
    # return app_commands.checks.has_role("developer")
    return True

class AboutCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._categories: List[str] = []
        self._objects_by_category: Dict[str, List[Dict]] = {}

        # Schedule preload once bot is ready
        bot.loop.create_task(self._preload_data())

    async def _preload_data(self):
        """Preload all categories and objects at startup for responsiveness"""
        await self.bot.wait_until_ready()
        try:
            logger.info("Starting preload of about data...")

            # Load categories
            resp = requests.get(f"{api_base}/about/categories", timeout=5)
            resp.raise_for_status()
            self._categories = resp.json()
            logger.debug(f"Preloaded categories: {self._categories}")

            # Load objects for each category
            for category in self._categories:
                try:
                    resp = requests.get(f"{api_base}/about/categories/{category}/objects", timeout=10)
                    resp.raise_for_status()
                    objects = resp.json()
                    self._objects_by_category[category] = objects
                    logger.debug(f"Preloaded {len(objects)} objects for category {category}")
                except Exception as e:
                    logger.warning(f"Failed to preload objects for category {category}: {e}")
                    self._objects_by_category[category] = []

            logger.info(f"Preload complete: {len(self._categories)} categories, "
                       f"{sum(len(objs) for objs in self._objects_by_category.values())} total objects")

        except Exception as e:
            logger.warning(f"Failed to preload about data: {e}")
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
        # Get the category from the interaction
        category = None
        if hasattr(interaction.namespace, 'category'):
            category = interaction.namespace.category

        if not category or category not in self._objects_by_category:
            return []

        objects = self._objects_by_category[category]
        choices = []

        for obj in objects:
            name = obj.get('name', '')
            if current.lower() in name.lower():
                choices.append(app_commands.Choice(name=name, value=name))

                # Also check aliases
                for alias in obj.get('aliases', []):
                    if current.lower() in alias.lower() and len(choices) < 25:
                        # Display alias without emoji
                        choices.append(app_commands.Choice(name=f"{alias} (alias)", value=alias))

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

        try:
            # Get object by name from the API
            resp = requests.get(f"{api_base}/about/object/name/{name}", timeout=10)
            resp.raise_for_status()
            obj_data = resp.json()

            # Create rich embed with object information
            embed = await self._create_object_embed(obj_data)

            await interaction.followup.send(embed=embed)

        except requests.HTTPError as e:
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
            logger.error(f"Error in about command: {e}")
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
            for obj in objects[:50]:  # Limit to first 50 objects
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
            logger.error(f"Error in list_category command: {e}")
            await interaction.followup.send(
                f"⚠️ An error occurred while listing objects.", 
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    logger.debug("Setting up AboutCog...")
    await bot.add_cog(AboutCog(bot))
    logger.info("AboutCog loaded")