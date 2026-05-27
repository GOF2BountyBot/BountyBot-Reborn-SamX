import asyncio
import contextlib
import os
import time

import discord
import httpx
from cogs.adminCog import _check_is_super_admin
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import normalize_for_search

from utils import autocomplete_state

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
        """Preload data category names at startup for autocomplete (with retries)."""
        await self.bot.wait_until_ready()
        delays = [5, 10, 20, 40, 60]
        for attempt, delay in enumerate(delays, start=1):
            try:
                flogger.info("DevCog: Starting preload of data categories (attempt %d/%d)...", attempt, len(delays))
                resp = await self.http_client.get(f"{api_base}/data/categories", timeout=5)
                resp.raise_for_status()
                self._categories = resp.json()
                flogger.info("DevCog: Preloaded %d data categories", len(self._categories))
                return
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as e:
                flogger.warning(
                    "DevCog: Preload attempt %d/%d failed: %s — retrying in %ds", attempt, len(delays), e, delay
                )
                await asyncio.sleep(delay)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning("DevCog: Unexpected error on preload attempt %d/%d: %s", attempt, len(delays), e)
                await asyncio.sleep(delay)
        flogger.error("DevCog: All preload attempts exhausted. Category autocomplete will be empty.")
        self._categories = []

    async def category_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        # include a virtual "All" option
        norm_current = normalize_for_search(current)
        choices = ["All", *self._categories]
        return [
            app_commands.Choice(name=cat, value=cat) for cat in choices if norm_current in normalize_for_search(cat)
        ][:25]

    @app_commands.command(name="load_data", description="Trigger a JSON → DB load for a given category")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(category="Choose a data category")
    @app_commands.autocomplete(category=category_autocomplete)
    # Cross-1: defer fires BEFORE the admin check so the 3-second Discord budget
    # is not consumed by the Bot-Admin HTTP call.  Inline post-defer pattern matches
    # AdminCog's B.25 fix.
    async def load_data(self, interaction: discord.Interaction, category: str):
        await interaction.response.defer(thinking=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send("❌ This command requires super-admin privileges.", ephemeral=True)
            return
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
    @app_commands.default_permissions(administrator=True)
    # Cross-1: post-defer inline admin check (see load_data for rationale)
    async def reload_autocomplete(self, interaction: discord.Interaction):
        """Call each cog's preload method so you don't have to restart."""
        await interaction.response.defer(thinking=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send("❌ This command requires super-admin privileges.", ephemeral=True)
            return

        # Clear all shared autocomplete caches first so next keystrokes re-warm from scratch
        try:
            autocomplete_state.clear_all()
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.warning("/reload_autocomplete: autocomplete_state.clear_all() failed (non-fatal)")

        reloaded = []
        failed = []

        # Preload-method targets: (cog_name, method_name, friendly_name).
        # Includes previously-missing entries (recon §7.3): BountyCog, AdminCog render settings.
        method_targets = [
            ("AboutCog", "_preload_data", "about data"),
            ("DevCog", "_preload_categories", "dev categories"),
            ("SkinsCog", "_preload_ship_skins", "ship skins"),
            ("BountyCog", "_preload_data", "bounty data"),
            ("AdminCog", "_preload_render_settings", "render settings"),
            ("AdminCog", "_preload_static_catalogs", "admin static catalogs"),
        ]

        for cog_name, method_name, label in method_targets:
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

        # Cache-clear targets: (cog_name, cache_attr_name, friendly_name).
        # clear() is synchronous; no await needed.
        cache_targets = [
            ("ShopCog", "_shop_cache", "shop cache"),
            ("BountyCog", "_bounty_cache", "bounty cache"),
            ("BountyCog", "_systems_cache", "bounty systems cache"),
            ("DuelCog", "_pending_duel_cache", "duel pending cache"),
            ("DuelCog", "_outgoing_duel_cache", "duel outgoing cache"),
            ("SchedulerCog", "_job_cache", "scheduler job cache"),
        ]

        for cog_name, attr_name, label in cache_targets:
            cog = self.bot.get_cog(cog_name)
            if not cog:
                failed.append(f"{label}: cog not found")
                continue

            cache = getattr(cog, attr_name, None)
            if cache is None:
                failed.append(f"{label}: no attribute {attr_name}")
                continue

            try:
                cache.clear()
                reloaded.append(label)
            except Exception as e:  # pylint: disable=broad-exception-caught
                failed.append(f"{label}: {e}")

        msg = []
        if reloaded:
            msg.append(f"✅ Reloaded: {', '.join(reloaded)}")
        if failed:
            msg.append(f"⚠️ Failed: {', '.join(failed)}")
        await interaction.followup.send("\n".join(msg), ephemeral=True)

    @app_commands.command(
        name="force_reload_caches",
        description="[DEV] Force-clear and hot-reload ALL caches for ALL guilds/players from live DB state",
    )
    @app_commands.default_permissions(administrator=True)
    async def force_reload_caches(self, interaction: discord.Interaction):
        """Clear every cache then immediately re-warm from current DB state.

        Unlike /reload_autocomplete (which clears and leaves caches empty for lazy-fill),
        this command actively re-populates every cache before responding, so all data is
        hot and current the moment the command completes.

        Gated to DEVELOPERS env var only.
        """
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send(
                "❌ This command requires super-admin (developer) privileges.", ephemeral=True
            )
            return

        from utils.autocomplete_warm import (
            refresh_jobs_cache,
            warm_guild_bounty_cache,
            warm_guild_duel_caches,
            warm_guild_players,
            warm_guild_shop_cache,
        )

        t_start = time.monotonic()
        guilds = self.bot.guilds
        guild_count = len(guilds)
        flogger.info(f"/force_reload_caches: starting full cache reload across {guild_count} guild(s)")

        # Step 1 — clear everything
        try:
            autocomplete_state.clear_all()
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.warning("/force_reload_caches: clear_all() failed (non-fatal)")

        for cog_name, attr in [
            ("ShopCog", "_shop_cache"),
            ("BountyCog", "_bounty_cache"),
            ("DuelCog", "_pending_duel_cache"),
            ("DuelCog", "_outgoing_duel_cache"),
            ("SchedulerCog", "_job_cache"),
        ]:
            cog = self.bot.get_cog(cog_name)
            if cog and hasattr(cog, attr):
                with contextlib.suppress(Exception):
                    getattr(cog, attr).clear()

        # Step 2 — Wave 0: shop + bounty (all guilds in parallel)
        wave0_results = await asyncio.gather(
            *[warm_guild_shop_cache(self.bot, g.id) for g in guilds],
            *[warm_guild_bounty_cache(self.bot, g.id) for g in guilds],
            return_exceptions=True,
        )
        wave0_errors = sum(1 for r in wave0_results if isinstance(r, Exception))

        # Step 3 — Wave 1: player cache + loadouts (all guilds in parallel)
        # warm_guild_players already runs Stage 2 (loadout warm) internally
        wave1_results = await asyncio.gather(
            *[warm_guild_players(g.id) for g in guilds],
            return_exceptions=True,
        )
        wave1_errors = sum(1 for r in wave1_results if isinstance(r, Exception))

        # Step 4 — Wave 1b: duel caches (depends on player_cache being populated)
        wave1b_results = await asyncio.gather(
            *[warm_guild_duel_caches(self.bot, g.id) for g in guilds],
            return_exceptions=True,
        )
        wave1b_errors = sum(1 for r in wave1b_results if isinstance(r, Exception))

        # Step 5 — job cache
        try:
            await refresh_jobs_cache(self.bot)
            jobs_ok = True
        except Exception:  # pylint: disable=broad-exception-caught
            jobs_ok = False

        elapsed = time.monotonic() - t_start

        lines = [
            f"✅ Full cache reload complete in **{elapsed:.1f}s**",
            f"Guilds: **{guild_count}**",
            f"Shop + bounty warm: {'✅' if not wave0_errors else f'⚠️ {wave0_errors} error(s)'}",
            f"Player + loadout warm: {'✅' if not wave1_errors else f'⚠️ {wave1_errors} error(s)'}",
            f"Duel cache warm: {'✅' if not wave1b_errors else f'⚠️ {wave1b_errors} error(s)'}",
            f"Job cache: {'✅' if jobs_ok else '⚠️ failed'}",
        ]
        flogger.info(f"/force_reload_caches: done in {elapsed:.1f}s — {guild_count} guild(s)")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @force_reload_caches.error
    async def force_reload_caches_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /force_reload_caches", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


    # ── Bot management prefix commands ──────────────────────────────────────

    def _is_developer(self, user_id: int) -> bool:
        devs = {int(uid.strip()) for uid in os.getenv("DEVELOPERS", "").split(",") if uid.strip()}
        return user_id in devs

    @commands.command(name="snooze")
    async def snooze(self, ctx: commands.Context):
        """Hide this bot's slash commands on Discord without touching the in-memory tree (owner only).

        Previously this called ``tree.clear_commands`` which wiped the in-memory
        command registry — leaving ``wake`` nothing to copy back. Now it
        pushes an empty command list directly to Discord via the HTTP bulk-upsert
        endpoints, so the local tree stays intact and ``wake`` can re-sync it.
        """
        if not self._is_developer(ctx.author.id):
            return
        app_id = self.bot.application_id
        for guild in self.bot.guilds:
            await self.bot.http.bulk_upsert_guild_commands(app_id, guild.id, [])
        await self.bot.http.bulk_upsert_global_commands(app_id, [])
        guild_names = ", ".join(g.name for g in self.bot.guilds) or "none"
        flogger.info(f"snooze: commands cleared by {ctx.author} ({ctx.author.id})")
        await ctx.send(f"💤 **{self.bot.user.name}** commands cleared from {len(self.bot.guilds)} guild(s): {guild_names}", delete_after=30)

    @commands.command(name="wake")
    async def wake(self, ctx: commands.Context):
        """Re-sync this bot's slash commands to all guilds (owner only).

        Reloads every loaded extension first so the in-memory command tree
        is rebuilt from cog definitions. This makes wake idempotent and
        self-healing: it can recover from any prior state where the tree
        was cleared or partially populated.
        """
        if not self._is_developer(ctx.author.id):
            return
        for ext_name in list(self.bot.extensions.keys()):
            try:
                await self.bot.reload_extension(ext_name)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                flogger.error(f"wake: failed to reload extension {ext_name}: {exc}")
        for guild in self.bot.guilds:
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=discord.Object(id=guild.id))
        guild_names = ", ".join(g.name for g in self.bot.guilds) or "none"
        flogger.info(f"wake: commands synced by {ctx.author} ({ctx.author.id})")
        await ctx.send(f"✅ **{self.bot.user.name}** commands synced to {len(self.bot.guilds)} guild(s): {guild_names}", delete_after=30)

    @commands.command(name="botstatus")
    async def botstatus(self, ctx: commands.Context):
        """Show this bot's command registration status per guild (owner only)."""
        if not self._is_developer(ctx.author.id):
            return
        lines = [f"**{self.bot.user.name}** (`{self.bot.user.id}`)"]
        for guild in self.bot.guilds:
            cmds = await self.bot.tree.fetch_commands(guild=discord.Object(id=guild.id))
            lines.append(f"• {guild.name}: {len(cmds)} command(s)")
        global_cmds = await self.bot.tree.fetch_commands()
        lines.append(f"• Global: {len(global_cmds)} command(s)")
        flogger.info(f"botstatus: queried by {ctx.author} ({ctx.author.id})")
        await ctx.send("\n".join(lines), delete_after=60)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up DevCog...")
    await bot.add_cog(DevCog(bot))
    flogger.info("DevCog loaded")
