import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-SkinsCog")
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")

class SkinsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient()
        # map ship name → list of skin names
        self._ship_skins: dict[str, list[str]] = {}
        bot.loop.create_task(self._preload_ship_skins())

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _preload_ship_skins(self):
        await self.bot.wait_until_ready()
        try:
            flogger.info("Preloading ship skins…")
            resp = await self.http_client.get(f"{api_base}/about/categories/ship/objects", timeout=10)
            resp.raise_for_status()
            ships = resp.json()
            for sh in ships:
                name = sh.get("name")
                if not name:
                    continue
                try:
                    full = await self.http_client.get(f"{api_base}/about/object/name/{name}", timeout=10)
                    full.raise_for_status()
                    data = full.json()
                    skins = data.get("compatible_skins") or {}
                    self._ship_skins[name] = list(skins.keys())
                except Exception as e:  # pylint: disable=broad-exception-caught
                    flogger.warning(f"Failed to load skins for {name}: {e}")
                    self._ship_skins[name] = []
            flogger.info(f"Finished preloading skins for {len(self._ship_skins)} ships")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Could not preload ship list: {e}")

    async def ship_autocomplete(
        self,
        _interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        txt = current.lower()
        choices = [
            app_commands.Choice(name=name, value=name)
            for name in self._ship_skins
            if txt in name.lower()
        ]
        return choices[:25]

    async def skin_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        # read the already-selected ship from namespace
        ship = getattr(interaction.namespace, "ship", None)
        if not ship:
            return []
        flogger.debug(f"skin_autocomplete: ship={ship!r}, filter={current!r}")
        skins = self._ship_skins.get(ship)
        if skins is None:
            return []
        if not skins:
            return [app_commands.Choice(name="Default", value="Default")]
        txt = current.lower()
        choices = [
            app_commands.Choice(name=s, value=s)
            for s in skins
            if txt in s.lower()
        ]
        if not choices:
            choices = [app_commands.Choice(name="Default", value="Default")]
        return choices[:25]

    @app_commands.command(
        name="ship_skin",
        description="Display a ship skin image (or default icon)"
    )
    @app_commands.describe(
        ship="Select the ship",
        skin="Select the skin (or Default)"
    )
    @app_commands.autocomplete(
        ship=ship_autocomplete,
        skin=skin_autocomplete
    )
    async def ship_skin(
        self,
        interaction: discord.Interaction,
        ship: str,
        skin: str
    ):
        await interaction.response.defer(thinking=True)
        # fetch full ship object to get URLs
        try:
            resp = await self.http_client.get(f"{api_base}/about/object/name/{ship}", timeout=10)
            resp.raise_for_status()
            obj = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return await interaction.followup.send(
                    f"❌ Ship '{ship}' not found.", ephemeral=True
                )
            return await interaction.followup.send(f"❌ API error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error fetching ship '{ship}': {e}")
            return await interaction.followup.send(
                "⚠️ Unexpected error fetching ship data.", ephemeral=True
            )

        # select URL
        if skin == "Default":
            img_url = obj.get("icon")
        else:
            skins_map = obj.get("compatible_skins") or {}
            img_url = skins_map.get(skin)

        if not img_url:
            return await interaction.followup.send(
                f"❌ Skin '{skin}' not found for ship '{ship}'.", ephemeral=True
            )

        embed = discord.Embed(title=f"{ship} — {skin}", color=discord.Color.green())
        embed.set_image(url=img_url)
        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    flogger.debug("Loading SkinsCog…")
    await bot.add_cog(SkinsCog(bot))
    flogger.info("SkinsCog loaded")
