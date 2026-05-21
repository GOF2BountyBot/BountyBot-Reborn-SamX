import json
import os
from contextlib import suppress

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from cogs._shared.http_error_handler import report_api_error
from cogs.adminCog import _check_is_super_admin
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import normalize_for_search

# Set up logger
flogger = bblogger.get_logger("discord-gateway-SchedulerCog")

# Base URL of the bot-core API
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"schedulerCog loading with api_base: {api_base}")


class SchedulerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        # Job cache: keyed by sentinel "all" string, TTL=600s (10 min dead-man switch)
        # Healthy refresh cycle (every 2 min) resets TTL so this never fires.
        self._job_cache: AutocompleteCache[str, list[dict]] = AutocompleteCache(
            ttl_seconds=600.0,
            refresh_fn=self._fetch_jobs,
            name="schedulerCog-jobs",
        )
        flogger.debug("SchedulerCog initialized")

    async def cog_unload(self):
        """Called when the cog is unloaded. Always close the HTTP client."""
        await self.http_client.aclose()

    async def _fetch_jobs(self, key: str) -> list[dict]:
        """Fetch all scheduled jobs from bot-core. Called by _job_cache on miss/expiry.

        Args:
            key: Sentinel key — always "all" for the full job list.

        Returns:
            List of job dicts from GET /api/v1/jobs.

        Phase 7: Pre-computes ``_norm`` on each job dict at fill time so the
        hot-path autocomplete scan never calls ``normalize_for_search`` per job.
        """
        _ = key  # only one key: "all"
        try:
            resp = await self.http_client.get(f"{api_base}/jobs", timeout=5)
            if resp.status_code != 200:
                return []
            jobs = resp.json()
            # Pre-compute _norm at fill time — hot path uses pre-computed value.
            for job in jobs:
                job_id = job.get("id", "")
                trigger = job.get("trigger", "")
                label = f"{job_id[:32]} ({trigger[:40]})" if trigger else job_id[:72]
                job["_norm"] = normalize_for_search(label)
            return jobs
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # ------------------------------------------------------------------
    # Autocomplete — zero-HTTP from _job_cache (Phase 6)
    # ------------------------------------------------------------------

    async def job_id_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Zero-HTTP autocomplete for job IDs.

        Phase 6: Reads from _job_cache with peek() — no HTTP call per keystroke.
        On cold miss, schedules a background refresh and returns [] immediately.
        """
        try:
            jobs = self._job_cache.peek("all")
            if jobs is None:
                self._job_cache.schedule_refresh("all")
                return []
            norm_current = normalize_for_search(current)
            choices = []
            for job in jobs:
                job_id = job.get("id", "")
                trigger = job.get("trigger", "")
                # Build a readable label: "<short_id> (<trigger>)"
                label = f"{job_id[:32]} ({trigger[:40]})" if trigger else job_id[:72]
                # Phase 7: use pre-computed _norm; fall back to on-the-fly for older cache entries.
                norm_label = job.get("_norm") or normalize_for_search(label)
                if norm_current in norm_label:
                    choices.append(app_commands.Choice(name=label[:100], value=job_id))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # ------------------------------------------------------------------
    # /scheduler_list — List all scheduled jobs
    # ------------------------------------------------------------------

    @app_commands.command(name="scheduler_list", description="[ADMIN] List all scheduled jobs")
    @app_commands.default_permissions(administrator=True)
    # Cross-1: defer fires BEFORE the admin check so the 3-second Discord budget
    # is not consumed by the Bot-Admin HTTP call.  Inline post-defer pattern matches
    # AdminCog's B.25 fix.
    async def scheduler_list(self, interaction: discord.Interaction):
        """List all currently scheduled APScheduler jobs relevant to this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send("❌ This command requires super-admin privileges.", ephemeral=True)
            return
        flogger.debug(f"/scheduler_list invoked: guild={interaction.guild_id} user={interaction.user.id}")

        try:
            # Peek cache first — avoids HTTP on every invocation when cache is warm
            jobs = self._job_cache.peek("all")
            if jobs is None:
                resp = await self.http_client.get(
                    f"{api_base}/jobs", params={"guild_id": interaction.guild_id}, timeout=10
                )
                resp.raise_for_status()
                jobs = resp.json()

            if not jobs:
                embed = discord.Embed(
                    title="🗓️ Scheduled Jobs",
                    description="No scheduled jobs found.",
                    color=discord.Color.light_grey(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            embed = discord.Embed(
                title="🗓️ Scheduled Jobs",
                description=f"**{len(jobs)}** scheduled job(s)",
                color=discord.Color.blue(),
            )

            for job in jobs:
                job_id = job.get("id", "unknown")
                trigger = job.get("trigger", "N/A")
                next_run = job.get("next_run_time")
                args = job.get("args", [])

                next_run_str = next_run[:19] if next_run else "N/A (paused)"
                # Extract job_type from args payload if available
                job_type = "unknown"
                if len(args) >= 2 and isinstance(args[1], dict):
                    job_type = args[1].get("job_type", "unknown")

                embed.add_field(
                    name=f"📌 {job_id[:50]}",
                    value=(f"**Type:** {job_type}\n**Trigger:** `{trigger}`\n**Next Run:** {next_run_str}"),
                    inline=False,
                )

            embed.set_footer(text="Use /scheduler_view <job_id> for full details")
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/scheduler_list success: guild={interaction.guild_id} user={interaction.user.id} count={len(jobs)}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                await interaction.followup.send(
                    "⚠️ Scheduler is unavailable. The service may still be starting up.", ephemeral=True
                )
            else:
                flogger.error(
                    f"/scheduler_list API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/scheduler_list error: guild={interaction.guild_id} user={interaction.user.id} error={e}")
            await interaction.followup.send("⚠️ An error occurred while listing jobs.", ephemeral=True)

    @scheduler_list.error
    async def scheduler_list_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /scheduler_list", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
        else:
            with suppress(Exception):
                await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /scheduler_view — View details of a specific job
    # ------------------------------------------------------------------

    @app_commands.command(name="scheduler_view", description="[ADMIN] View details of a specific scheduled job")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(job_id="The ID of the job to view")
    @app_commands.autocomplete(job_id=job_id_autocomplete)
    # Cross-1: post-defer inline admin check (see scheduler_list for rationale)
    async def scheduler_view(self, interaction: discord.Interaction, job_id: str):
        """View full details of a single scheduled job by its ID."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send("❌ This command requires super-admin privileges.", ephemeral=True)
            return
        flogger.debug(
            f"/scheduler_view invoked: guild={interaction.guild_id} user={interaction.user.id} job_id={job_id}"
        )

        try:
            resp = await self.http_client.get(f"{api_base}/jobs/{job_id}", timeout=10)
            resp.raise_for_status()
            job = resp.json()

            next_run = job.get("next_run_time")
            next_run_str = next_run[:19] if next_run else "N/A (paused)"
            trigger = job.get("trigger", "N/A")
            args = job.get("args", [])

            # Extract payload details from args
            job_type = "unknown"
            payload_str = "N/A"
            if len(args) >= 2 and isinstance(args[1], dict):
                payload = args[1]
                job_type = payload.get("job_type", "unknown")
                payload_str = "\n".join(f"• **{k}**: {v}" for k, v in payload.items())
            elif args:
                payload_str = str(args)

            embed = discord.Embed(
                title="🔍 Job Details",
                description=f"**ID:** `{job.get('id', 'unknown')}`",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Job Type", value=job_type, inline=True)
            embed.add_field(name="Next Run", value=next_run_str, inline=True)
            embed.add_field(name="Trigger", value=f"`{trigger}`", inline=False)
            if payload_str and payload_str != "N/A":
                embed.add_field(name="Payload", value=payload_str[:1024], inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/scheduler_view success: guild={interaction.guild_id} user={interaction.user.id} job_id={job_id}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send(f"❌ Job `{job_id}` not found.", ephemeral=True)
            elif e.response.status_code == 503:
                await interaction.followup.send(
                    "⚠️ Scheduler is unavailable. The service may still be starting up.", ephemeral=True
                )
            else:
                flogger.error(
                    f"/scheduler_view API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" job_id={job_id} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/scheduler_view error: guild={interaction.guild_id} user={interaction.user.id}"
                f" job_id={job_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching job details.", ephemeral=True)

    @scheduler_view.error
    async def scheduler_view_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /scheduler_view", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
        else:
            with suppress(Exception):
                await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /scheduler_update — Update a job's payload
    # ------------------------------------------------------------------

    @app_commands.command(name="scheduler_update", description="[ADMIN] Update a scheduled job's payload")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        job_id="The ID of the job to update",
        payload_json='New payload as a JSON string (e.g. {"job_type": "bounty_spawn"})',
    )
    @app_commands.autocomplete(job_id=job_id_autocomplete)
    # Cross-1: post-defer inline admin check.  JSON validation still runs before
    # defer (B.28 fix) because it is synchronous and avoids wasting the interaction
    # token on a validation error.  The Bot-Admin HTTP check is deferred.
    async def scheduler_update(self, interaction: discord.Interaction, job_id: str, payload_json: str):
        """Update the payload/args of an existing scheduled job."""
        # B.28 fix: validate JSON synchronously BEFORE defer — no async work needed for validation
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as e:
            await interaction.response.send_message(
                f'❌ Invalid JSON payload: `{e}`\n\nExample: `{{"job_type": "bounty_spawn"}}`',
                ephemeral=True,
            )
            return

        # Only defer after validation passes — async I/O (including admin check) follows
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send("❌ This command requires super-admin privileges.", ephemeral=True)
            return
        flogger.debug(
            f"/scheduler_update invoked: guild={interaction.guild_id} user={interaction.user.id} job_id={job_id}"
        )

        try:
            resp = await self.http_client.put(
                f"{api_base}/jobs/{job_id}",
                json={"payload": payload},
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()

            embed = discord.Embed(
                title="✅ Job Updated",
                description=f"Successfully updated job `{result.get('job_id', job_id)}`.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Job ID", value=f"`{result.get('job_id', job_id)}`", inline=True)
            embed.add_field(name="Status", value=result.get("status", "updated"), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/scheduler_update success: guild={interaction.guild_id} user={interaction.user.id} job_id={job_id}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send(f"❌ Job `{job_id}` not found.", ephemeral=True)
            elif e.response.status_code == 400:
                detail = ""
                with suppress(Exception):
                    detail = e.response.json().get("detail", "")
                await interaction.followup.send(f"❌ Bad request: {detail or e}", ephemeral=True)
            elif e.response.status_code == 503:
                await interaction.followup.send(
                    "⚠️ Scheduler is unavailable. The service may still be starting up.", ephemeral=True
                )
            else:
                flogger.error(
                    f"/scheduler_update API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" job_id={job_id} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/scheduler_update error: guild={interaction.guild_id} user={interaction.user.id}"
                f" job_id={job_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while updating the job.", ephemeral=True)

    @scheduler_update.error
    async def scheduler_update_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /scheduler_update", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
        else:
            with suppress(Exception):
                await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /scheduler_delete — Delete a specific job
    # ------------------------------------------------------------------

    @app_commands.command(name="scheduler_delete", description="[ADMIN] Delete a specific scheduled job")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(job_id="The ID of the job to delete")
    @app_commands.autocomplete(job_id=job_id_autocomplete)
    # Cross-1: post-defer inline admin check (see scheduler_list for rationale)
    async def scheduler_delete(self, interaction: discord.Interaction, job_id: str):
        """Delete a single scheduled job by its ID."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send("❌ This command requires super-admin privileges.", ephemeral=True)
            return
        flogger.debug(
            f"/scheduler_delete invoked: guild={interaction.guild_id} user={interaction.user.id} job_id={job_id}"
        )

        try:
            resp = await self.http_client.delete(f"{api_base}/jobs/{job_id}", timeout=10)
            resp.raise_for_status()
            result = resp.json()

            embed = discord.Embed(
                title="🗑️ Job Deleted",
                description=f"Successfully deleted job `{result.get('job_id', job_id)}`.",
                color=discord.Color.orange(),
            )
            embed.add_field(name="Job ID", value=f"`{result.get('job_id', job_id)}`", inline=True)
            embed.add_field(name="Status", value=result.get("status", "deleted"), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/scheduler_delete success: guild={interaction.guild_id} user={interaction.user.id} job_id={job_id}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send(f"❌ Job `{job_id}` not found.", ephemeral=True)
            elif e.response.status_code == 503:
                await interaction.followup.send(
                    "⚠️ Scheduler is unavailable. The service may still be starting up.", ephemeral=True
                )
            else:
                flogger.error(
                    f"/scheduler_delete API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" job_id={job_id} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/scheduler_delete error: guild={interaction.guild_id} user={interaction.user.id}"
                f" job_id={job_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while deleting the job.", ephemeral=True)

    @scheduler_delete.error
    async def scheduler_delete_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /scheduler_delete", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
        else:
            with suppress(Exception):
                await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /admin_reset_scheduler — Wipe all jobs and re-register defaults
    # ------------------------------------------------------------------

    @app_commands.command(
        name="admin_reset_scheduler",
        description="[ADMIN] Wipe all scheduled jobs and re-register the 3 default recurring jobs",
    )
    @app_commands.default_permissions(administrator=True)
    # Cross-1: post-defer inline admin check (see scheduler_list for rationale)
    async def admin_reset_scheduler(self, interaction: discord.Interaction):
        """Remove all scheduled jobs and re-register the default recurring jobs."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send("❌ This command requires super-admin privileges.", ephemeral=True)
            return
        flogger.debug(f"/admin_reset_scheduler invoked: guild={interaction.guild_id} user={interaction.user.id}")

        try:
            resp = await self.http_client.post(f"{api_base}/reset", timeout=10)
            resp.raise_for_status()
            result = resp.json()

            jobs_registered = result.get("jobs_registered", 0)
            embed = discord.Embed(
                title="🔄 Scheduler Reset",
                description=f"All jobs wiped and **{jobs_registered}** default job(s) re-registered.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Status", value=result.get("status", "reset"), inline=True)
            embed.add_field(name="Jobs Registered", value=str(jobs_registered), inline=True)
            embed.set_footer(text="Default jobs: bounty_spawn_default, shop_refresh_default, temperature_decay_default")

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/admin_reset_scheduler success: guild={interaction.guild_id} user={interaction.user.id}"
                f" jobs_registered={jobs_registered}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                await interaction.followup.send(
                    "⚠️ Scheduler is unavailable. The service may still be starting up.", ephemeral=True
                )
            else:
                flogger.error(
                    f"/admin_reset_scheduler API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/admin_reset_scheduler error: guild={interaction.guild_id} user={interaction.user.id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while resetting the scheduler.", ephemeral=True)

    @admin_reset_scheduler.error
    async def admin_reset_scheduler_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_reset_scheduler", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
        else:
            with suppress(Exception):
                await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /admin_clear_scheduler — Delete all one-time jobs for this guild
    # ------------------------------------------------------------------

    @app_commands.command(
        name="admin_clear_scheduler",
        description="[ADMIN] Delete all one-time scheduled jobs scoped to this guild",
    )
    @app_commands.default_permissions(administrator=True)
    # Cross-1: post-defer inline admin check (see scheduler_list for rationale)
    async def admin_clear_scheduler(self, interaction: discord.Interaction):
        """Delete all one-time jobs associated with the invoking guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_super_admin(interaction):
            await interaction.followup.send("❌ This command requires super-admin privileges.", ephemeral=True)
            return
        flogger.debug(f"/admin_clear_scheduler invoked: guild={interaction.guild_id} user={interaction.user.id}")

        try:
            resp = await self.http_client.delete(f"{api_base}/jobs/guild/{interaction.guild_id}", timeout=10)
            resp.raise_for_status()
            result = resp.json()

            removed = result.get("removed_count", 0)
            embed = discord.Embed(
                title="🧹 Guild Jobs Cleared",
                description=f"Removed **{removed}** one-time job(s) for this guild.",
                color=discord.Color.orange(),
            )
            embed.add_field(name="Status", value=result.get("status", "guild_jobs_deleted"), inline=True)
            embed.add_field(name="Jobs Removed", value=str(removed), inline=True)
            embed.add_field(name="Guild ID", value=str(interaction.guild_id), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/admin_clear_scheduler success: guild={interaction.guild_id} user={interaction.user.id}"
                f" removed={removed}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                await interaction.followup.send(
                    "⚠️ Scheduler is unavailable. The service may still be starting up.", ephemeral=True
                )
            else:
                flogger.error(
                    f"/admin_clear_scheduler API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/admin_clear_scheduler error: guild={interaction.guild_id} user={interaction.user.id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while clearing guild jobs.", ephemeral=True)

    @admin_clear_scheduler.error
    async def admin_clear_scheduler_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_clear_scheduler", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
        else:
            with suppress(Exception):
                await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up SchedulerCog...")
    await bot.add_cog(SchedulerCog(bot))
    flogger.info("SchedulerCog loaded")
