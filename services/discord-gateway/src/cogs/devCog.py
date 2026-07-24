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

    def _ensure_categories_loaded(self) -> None:
        """Size-guard self-heal: if the in-code category list is empty (e.g. after a
        failed preload), kick off a deduped background reload so the next keystroke is
        warm. Background (not inline) to respect the 3s autocomplete deadline.
        """
        try:
            if self._categories:
                return
            existing = getattr(self, "_categories_preload_task", None)
            if existing is not None and not existing.done():
                return
            self._categories_preload_task = asyncio.create_task(
                self._preload_categories(), name="dev-categories-selfheal-preload"
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.warning(f"_ensure_categories_loaded: failed to schedule self-heal: {type(exc).__name__}: {exc}")

    async def category_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        # include a virtual "All" option
        self._ensure_categories_loaded()  # size-guard self-heal (background; degrade-then-warm)
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
            # A data load mutates DB game data; ALWAYS refresh caches so nothing is stale.
            await self._followup_reload(interaction)
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
            # A data load mutates DB game data; ALWAYS refresh caches so nothing is stale.
            await self._followup_reload(interaction)
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
        # Phase 3: backend-sourced static AutocompleteCaches (about/bounty-systems/
        # admin catalogs/ship-skins) are now CLEARED in cache_targets below and
        # self-heal via their refresh_fn / size-guard — so their bulk preloads are no
        # longer driven from here (clear-and-self-heal is the uniform mechanism). Only
        # the plain in-code lists that are NOT AutocompleteCaches keep an explicit
        # preload: DevCog._categories and AdminCog._render_settings.
        method_targets = [
            ("DevCog", "_preload_categories", "dev categories"),
            ("AdminCog", "_preload_render_settings", "render settings"),
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
        # Phase 3: the D-010 carve-out is GONE. Every backend-sourced static catalog
        # now has a refresh_fn (or a handler-level size-guard), so clear() applies
        # UNIFORMLY — a cleared key self-heals lazily on the next autocomplete keystroke.
        # _systems_cache and the about/admin static catalogs are cleared here like any
        # other cache; the next /check / /about / /admin_* keystroke cold-fills them.
        cache_targets = [
            ("ShopCog", "_shop_cache", "shop cache"),
            ("BountyCog", "_bounty_cache", "bounty cache"),
            ("BountyCog", "_systems_cache", "bounty systems cache"),
            ("AboutCog", "_categories_cache", "about categories cache"),
            ("AboutCog", "_objects_cache", "about objects cache"),
            ("AdminCog", "_item_catalog", "admin item catalog"),
            ("AdminCog", "_ship_catalog", "admin ship catalog"),
            ("AdminCog", "_admin_pending_duel_cache", "admin pending-duel cache"),
            ("SkinsCog", "_ship_skins", "ship skins cache"),
            ("DuelCog", "_pending_duel_cache", "duel pending cache"),
            ("DuelCog", "_outgoing_duel_cache", "duel outgoing cache"),
            ("SchedulerCog", "_job_cache", "scheduler job cache"),
            ("CombatLogCog", "_combatlog_cache", "combat-log cache"),
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
        hot and current the moment the command completes. Covers per-guild dynamic caches
        AND static game-data caches (systems/items/ships catalogs + the bot-core system
        graph) — see _reload_all_caches.

        Gated to DEVELOPERS env var only.
        """
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send(
                "❌ This command requires super-admin (developer) privileges.", ephemeral=True
            )
            return

        lines = await self._reload_all_caches()
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    async def _reload_all_caches(self) -> list[str]:
        """Clear + re-warm every cache: static game data AND per-guild dynamic state.

        Static game-data caches (systems/items/ships autocomplete catalogs and the
        bot-core system graph) mirror DB rows that /load_data mutates, so they go
        stale after a reload. This rebuilds them alongside the dynamic per-guild
        caches, which is why /load_data runs it on completion — a data reload never
        needs a bot restart. Returns human-readable status lines.
        """
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
        flogger.info(f"cache reload: starting full reload across {guild_count} guild(s)")

        # Step 0 — STATIC game-data caches. Clear the static autocomplete catalogs
        # (they self-heal lazily on the next keystroke) and force the bot-core system
        # graph — a load-once cache — to rebuild from the freshly-loaded DB rows.
        for cog_name, attr in [
            ("BountyCog", "_systems_cache"),
            ("AdminCog", "_item_catalog"),
            ("AdminCog", "_ship_catalog"),
            ("AboutCog", "_categories_cache"),
            ("AboutCog", "_objects_cache"),
            ("SkinsCog", "_ship_skins"),
        ]:
            cog = self.bot.get_cog(cog_name)
            if cog and hasattr(cog, attr):
                with contextlib.suppress(Exception):
                    getattr(cog, attr).clear()

        try:
            resp = await self.http_client.post(f"{api_base}/systems/reload-graph", timeout=20)
            resp.raise_for_status()
            n = (resp.json() or {}).get("systems", "?")
            graph_line = f"System graph rebuilt: ✅ ({n} systems)"
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"cache reload: system-graph rebuild failed: {e}")
            graph_line = "System graph rebuilt: ⚠️ failed"

        # Step 1 — clear dynamic shared + per-cog caches
        try:
            autocomplete_state.clear_all()
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.warning("cache reload: autocomplete_state.clear_all() failed (non-fatal)")

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
            graph_line,
            "Static autocomplete caches (systems/items/ships): ✅ cleared",
            f"Shop + bounty warm: {'✅' if not wave0_errors else f'⚠️ {wave0_errors} error(s)'}",
            f"Player + loadout warm: {'✅' if not wave1_errors else f'⚠️ {wave1_errors} error(s)'}",
            f"Duel cache warm: {'✅' if not wave1b_errors else f'⚠️ {wave1b_errors} error(s)'}",
            f"Job cache: {'✅' if jobs_ok else '⚠️ failed'}",
        ]
        flogger.info(f"cache reload: done in {elapsed:.1f}s — {guild_count} guild(s)")
        return lines

    async def _followup_reload(self, interaction: discord.Interaction) -> None:
        """Run a full cache reload after a data load and report it as a follow-up.

        Failures are surfaced but never mask the (already-sent) load-success reply.
        """
        try:
            lines = await self._reload_all_caches()
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"load_data: post-load cache reload failed: {e}")
            await interaction.followup.send(f"⚠️ Data loaded, but cache reload failed: {e}", ephemeral=True)
            return
        await interaction.followup.send("🔄 **Cache refresh after load**\n" + "\n".join(lines), ephemeral=True)

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
        """Hide this bot's slash commands in the current guild only (owner only).

        Pushes an empty command list to Discord scoped to ``ctx.guild`` via
        the HTTP bulk-upsert endpoint. Other guilds and global commands are
        untouched, and the in-memory command tree stays intact so ``wake``
        can re-sync it.
        """
        if not self._is_developer(ctx.author.id):
            return
        if ctx.guild is None:
            await ctx.send("⚠ `snooze` must be run inside a guild.", delete_after=15)
            return
        app_id = self.bot.application_id
        await self.bot.http.bulk_upsert_guild_commands(app_id, ctx.guild.id, [])
        flogger.info(
            f"snooze: commands cleared in guild {ctx.guild.name} ({ctx.guild.id}) by {ctx.author} ({ctx.author.id})"
        )
        await ctx.send(
            f"💤 **{self.bot.user.name}** commands cleared in **{ctx.guild.name}**.",
            delete_after=30,
        )

    @commands.command(name="wake")
    async def wake(self, ctx: commands.Context):
        """Re-sync this bot's slash commands in the current guild only (owner only).

        Reloads every loaded extension first so the in-memory command tree
        is rebuilt from cog definitions, then copies the global tree into
        ``ctx.guild`` and syncs that guild only. This makes wake idempotent
        and self-healing: it can recover from any prior state where the
        tree was cleared or partially populated, without disturbing
        registrations in other guilds.
        """
        if not self._is_developer(ctx.author.id):
            return
        if ctx.guild is None:
            await ctx.send("⚠ `wake` must be run inside a guild.", delete_after=15)
            return
        flogger.info(f"wake: invoked in guild {ctx.guild.name} ({ctx.guild.id}) by {ctx.author} ({ctx.author.id})")
        for ext_name in list(self.bot.extensions.keys()):
            try:
                await self.bot.reload_extension(ext_name)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                flogger.error(f"wake: failed to reload extension {ext_name}: {exc}")
        try:
            self.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await self.bot.tree.sync(guild=discord.Object(id=ctx.guild.id))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.exception(f"wake: sync failed for guild {ctx.guild.id}: {exc}")
            await ctx.send(f"❌ wake failed: `{type(exc).__name__}: {exc}`", delete_after=60)
            return
        flogger.info(
            f"wake: synced {len(synced)} commands in guild {ctx.guild.name} ({ctx.guild.id}) "
            f"by {ctx.author} ({ctx.author.id})"
        )
        await ctx.send(
            f"✅ **{self.bot.user.name}** synced {len(synced)} command(s) in **{ctx.guild.name}**.",
            delete_after=30,
        )

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
