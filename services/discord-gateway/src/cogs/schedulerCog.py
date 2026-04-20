import json
import os
from contextlib import suppress

import discord
import httpx
from cogs.adminCog import is_admin
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
        flogger.debug("SchedulerCog initialized")

    async def cog_unload(self):
        """Called when the cog is unloaded. Always close the HTTP client."""
        await self.http_client.aclose()

    # ------------------------------------------------------------------
    # Autocomplete — live fetch from API on each keystroke
    # ------------------------------------------------------------------

    async def job_id_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Live autocomplete for job IDs — fetches the current job list on each keystroke."""
        try:
            resp = await self.http_client.get(f"{api_base}/jobs", timeout=5)
            resp.raise_for_status()
            jobs = resp.json()
            norm_current = normalize_for_search(current)
            choices = []
            for job in jobs:
                job_id = job.get("id", "")
                trigger = job.get("trigger", "")
                # Build a readable label: "<short_id> (<trigger>)"
                label = f"{job_id[:32]} ({trigger[:40]})" if trigger else job_id[:72]
                if norm_current in normalize_for_search(label):
                    choices.append(app_commands.Choice(name=label[:100], value=job_id))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # ------------------------------------------------------------------
    # /scheduler_list — List all scheduled jobs
    # ------------------------------------------------------------------

    @app_commands.command(name="scheduler_list", description="[ADMIN] List all scheduled jobs")
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    async def scheduler_list(self, interaction: discord.Interaction):
        """List all currently scheduled APScheduler jobs relevant to this guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        flogger.debug(f"/scheduler_list invoked: guild={interaction.guild_id} user={interaction.user.id}")

        try:
            resp = await self.http_client.get(f"{api_base}/jobs", params={"guild_id": interaction.guild_id}, timeout=10)
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
                    value=(f"**Type:** {job_type}\n**Trigger:** {trigger}\n**Next Run:** {next_run_str}"),
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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/scheduler_list error: guild={interaction.guild_id} user={interaction.user.id} error={e}")
            await interaction.followup.send("⚠️ An error occurred while listing jobs.", ephemeral=True)

    @scheduler_list.error
    async def scheduler_list_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /scheduler_list", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /scheduler_view — View details of a specific job
    # ------------------------------------------------------------------

    @app_commands.command(name="scheduler_view", description="[ADMIN] View details of a specific scheduled job")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(job_id="The ID of the job to view")
    @app_commands.autocomplete(job_id=job_id_autocomplete)
    @is_admin()
    async def scheduler_view(self, interaction: discord.Interaction, job_id: str):
        """View full details of a single scheduled job by its ID."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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
            embed.add_field(name="Trigger", value=trigger, inline=False)
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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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
    @is_admin()
    async def scheduler_update(self, interaction: discord.Interaction, job_id: str, payload_json: str):
        """Update the payload/args of an existing scheduled job."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        flogger.debug(
            f"/scheduler_update invoked: guild={interaction.guild_id} user={interaction.user.id} job_id={job_id}"
        )

        # Parse the JSON payload supplied by the user
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as e:
            await interaction.followup.send(
                f'❌ Invalid JSON payload: `{e}`\n\nExample: `{{"job_type": "bounty_spawn"}}`',
                ephemeral=True,
            )
            return

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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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

    # ------------------------------------------------------------------
    # /scheduler_delete — Delete a specific job
    # ------------------------------------------------------------------

    @app_commands.command(name="scheduler_delete", description="[ADMIN] Delete a specific scheduled job")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(job_id="The ID of the job to delete")
    @app_commands.autocomplete(job_id=job_id_autocomplete)
    @is_admin()
    async def scheduler_delete(self, interaction: discord.Interaction, job_id: str):
        """Delete a single scheduled job by its ID."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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

    # ------------------------------------------------------------------
    # /admin_reset_scheduler — Wipe all jobs and re-register defaults
    # ------------------------------------------------------------------

    @app_commands.command(
        name="admin_reset_scheduler",
        description="[ADMIN] Wipe all scheduled jobs and re-register the 3 default recurring jobs",
    )
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    async def admin_reset_scheduler(self, interaction: discord.Interaction):
        """Remove all scheduled jobs and re-register the default recurring jobs."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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

    # ------------------------------------------------------------------
    # /admin_clear_scheduler — Delete all one-time jobs for this guild
    # ------------------------------------------------------------------

    @app_commands.command(
        name="admin_clear_scheduler",
        description="[ADMIN] Delete all one-time scheduled jobs scoped to this guild",
    )
    @app_commands.default_permissions(administrator=True)
    @is_admin()
    async def admin_clear_scheduler(self, interaction: discord.Interaction):
        """Delete all one-time jobs associated with the invoking guild."""
        await interaction.response.defer(thinking=True, ephemeral=True)
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
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
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


async def setup(bot: commands.Bot):
    flogger.debug("Setting up SchedulerCog...")
    await bot.add_cog(SchedulerCog(bot))
    flogger.info("SchedulerCog loaded")
