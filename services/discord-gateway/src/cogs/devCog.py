import os

import discord
import httpx
from cogs.adminCog import is_admin
from discord import app_commands
from discord.ext import commands
from httpx import HTTPStatusError as HttpxHTTPStatusError
from httpx import RequestError as HttpxRequestError
from httpx import TimeoutException as HttpxTimeoutException
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-DevCog")
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"devCog loading with api_base: {api_base}")


# TODO:  COme back and make a proper dev check since these are global commands and not limited to a single guild
class DevCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._categories: list[str] = []
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        # schedule preload once
        bot.loop.create_task(self._preload_categories())

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _preload_categories(self):
        await self.bot.wait_until_ready()
        try:
            resp = await self.http_client.get(f"{api_base}/data/categories", timeout=5)
            resp.raise_for_status()
            self._categories = resp.json()
            flogger.debug(f"Preloaded data categories: {self._categories}")
        except HttpxTimeoutException as e:
            flogger.warning(f"Timeout preloading data categories: {e}")
        except HttpxHTTPStatusError as e:
            flogger.warning(f"HTTP error preloading data categories: {e.response.status_code}")
        except HttpxRequestError as e:
            flogger.warning(f"Request error preloading data categories: {e}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"Unexpected error preloading data categories: {e}")

    async def category_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        # include a virtual "All" option
        choices = ["All", *self._categories]
        return [app_commands.Choice(name=cat, value=cat) for cat in choices if current.lower() in cat.lower()][:25]

    @app_commands.command(name="load_data", description="Trigger a JSON → DB load for a given category")
    @is_admin()
    @app_commands.describe(category="Choose a data category")
    @app_commands.autocomplete(category=category_autocomplete)
    async def load_data(self, interaction: discord.Interaction, category: str):
        await interaction.response.defer(thinking=True)
        # virtual "All" path: iterate every category
        if category == "All":
            total_count = 0
            errors: list[str] = []
            summary_lines: list[str] = []

            for cat in self._categories:
                try:
                    resp = await self.http_client.post(f"{api_base}/data/{cat}", timeout=10)
                    resp.raise_for_status()
                    msgs = resp.json() or []
                    count = len(msgs)
                    total_count += count
                    summary_lines.append(f"{cat}: {count} files")
                except Exception as e:  # pylint: disable=broad-exception-caught
                    errors.append(f"{cat}: {e}")

            header = f"✅ Loaded ALL categories. Total files: {total_count}"
            if errors:
                header += f", Errors in {len(errors)} categories"
            body = "\n".join(summary_lines + (["Errors:", *errors] if errors else []))
            max_len = 500
            if len(body) > max_len:
                body = body[:max_len] + "... (truncated)"

            await interaction.followup.send(f"{header}\n```{body}```")
            return

        # single-category path
        try:
            resp = await self.http_client.post(f"{api_base}/data/{category}", timeout=10)
            resp.raise_for_status()
            msgs = resp.json() or []
            count = len(msgs)
            await interaction.followup.send(
                f"✅ Data load complete for **{category}**: {count} file{'s' if count != 1 else ''} processed."
            )
        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            err_str = str(e)
            max_len = 500
            if len(err_str) > max_len:
                err_str = err_str[:max_len] + "... (truncated)"
            await interaction.followup.send(f"⚠️ Unexpected error: {err_str}", ephemeral=True)

    @app_commands.command(name="reload_autocomplete", description="Force-reload all autocomplete data in other cogs")
    @is_admin()
    async def reload_autocomplete(self, interaction: discord.Interaction):
        """Call each cog's preload method so you don't have to restart."""
        await interaction.response.defer(thinking=True)
        reloaded = []
        failed = []

        # list of (cog_name, method_name, friendly_name)
        targets = [
            ("AboutCog", "_preload_data", "about data"),
            ("DevCog", "_preload_categories", "dev categories"),
            ("SkinsCog", "_preload_ship_skins", "ship skins"),
            # add more cogs here if needed
        ]

        for cog_name, method_name, label in targets:
            cog = self.bot.get_cog(cog_name)
            if not cog:
                failed.append(f"{label}: cog not found")
                continue

            method = getattr(cog, method_name, None)
            if not method:
                failed.append(f"{label}: no method {method_name}()")
                continue

            try:
                # call the preload method; most are async
                await method()
                reloaded.append(label)
            except Exception as e:  # pylint: disable=broad-exception-caught
                failed.append(f"{label}: {e}")

        msg = []
        if reloaded:
            msg.append(f"✅ Reloaded: {', '.join(reloaded)}")
        if failed:
            msg.append(f"⚠️ Failed: {', '.join(failed)}")
        await interaction.followup.send("\n".join(msg), ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up DevCog...")
    await bot.add_cog(DevCog(bot))
    flogger.info("DevCog loaded")
