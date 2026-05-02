"""Temperature decay executor — decays division temperatures for all active guilds.

Invoked by APScheduler via the JobExecutor dispatch.  The executor:
  1. Queries all guild configs via ConfigRepository.list_all().
  2. For each guild, reads the current per-division temperatures from
     ``GuildConfig.division_temperatures``.
  3. Applies TemperatureService.decay_temperature() to each division's value
     (multiplies by 2/3, floors at 1.0, rounds to one decimal place).
  4. Persists the decayed temperatures back via
     ConfigRepository.update_division_temperatures().
  5. Returns a summary dict with per-guild decay results.

Imports of service/repository classes are deferred to function scope so that
the module can be safely imported in test environments without a live database
or all ORM dependencies being present.
"""

import traceback
from datetime import UTC, datetime

from shared.bblogger import get_logger

flogger = get_logger("temperature-decay-executor")

# ---------------------------------------------------------------------------
# Supported bounty divisions
# B.48: hardcoded list now (previously matched GameConstants.DIVISION_NAMES,
# which only had 3 entries — bronze/silver/gold — and was deleted alongside
# the level/division progression system).
# ---------------------------------------------------------------------------
_BOUNTY_DIVISIONS: list[str] = ["bronze", "silver", "gold", "platinum"]

# Default temperature applied to a division that has no stored value yet.
_DEFAULT_TEMPERATURE: float = 1.0


async def execute_temperature_decay_job(job_id: str, payload: dict) -> dict:
    """Execute a temperature decay job.

    Decays the activity temperature for every division of every configured
    guild by a factor of 2/3, floored at 1.0.  The decayed values are
    persisted back to the database so they survive service restarts.

    Payload fields
    --------------
    guild_id : int, optional
        When provided, only that guild is processed.  When omitted all
        configured guilds are processed (bulk mode).
    division : str, optional
        When provided, only that division is decayed for the given *guild_id*.
        When omitted all three divisions are decayed.

    Returns
    -------
    dict
        Summary of the decay operation with per-guild results::

            {
                "status": "success",
                "guilds_processed": 2,
                "total_decays": 6,
                "results": {
                    123456: {
                        "bronze": {"before": 5.0, "after": 3.3},
                        "silver": {"before": 1.0, "after": 1.0},
                        "gold":   {"before": 2.0, "after": 1.3},
                    },
                    ...
                },
            }
    """
    # Deferred imports — avoids transitive ORM dependencies at module load time.
    from persist.database.manager import db_manager
    from persist.repositories.config_repository import ConfigRepository
    from services.temperature_service import TemperatureService

    start_ts = datetime.now(UTC)
    flogger.info(f"TemperatureDecayJob[{job_id}] START")
    flogger.trace(f"TemperatureDecayJob[{job_id}] payload: {payload}")

    guild_id: int | None = payload.get("guild_id")
    division_filter: str | None = payload.get("division", "").lower() or None

    # Determine which divisions to process.
    divisions_to_decay = [division_filter] if division_filter else list(_BOUNTY_DIVISIONS)

    total_decays = 0
    guild_results: dict = {}

    try:
        async with db_manager.get_session() as db:
            config_repo = ConfigRepository()

            # ------------------------------------------------------------------
            # Determine which guilds to process
            # ------------------------------------------------------------------
            if guild_id:
                # Single-guild mode — wrap result in a one-element list so the
                # loop below works uniformly.
                config = await config_repo.get_by_guild_id(db, guild_id)
                if config is None:
                    flogger.warning(f"TemperatureDecayJob[{job_id}] guild={guild_id} not found, nothing to do")
                    return {
                        "status": "success",
                        "guilds_processed": 0,
                        "total_decays": 0,
                        "results": {},
                    }
                guild_configs = [config]
            else:
                # Bulk mode — enumerate all configured guilds.
                guild_configs = await config_repo.list_all(db)
                if not guild_configs:
                    flogger.info(f"TemperatureDecayJob[{job_id}] no guilds configured, nothing to do")
                    return {
                        "status": "success",
                        "guilds_processed": 0,
                        "total_decays": 0,
                        "results": {},
                    }

            # ------------------------------------------------------------------
            # Process each guild
            # ------------------------------------------------------------------
            for config in guild_configs:
                gid: int = config.guild_id
                flogger.debug(f"TemperatureDecayJob[{job_id}] processing guild={gid}")

                # Read stored temperatures (default to 1.0 per division if None
                # or missing).
                stored: dict[str, float] = config.division_temperatures or {}

                division_decay_results: dict[str, dict] = {}
                updated_temperatures: dict[str, float] = dict(stored)

                for div in divisions_to_decay:
                    before: float = float(stored.get(div, _DEFAULT_TEMPERATURE))
                    after: float = TemperatureService.decay_temperature(before)
                    updated_temperatures[div] = after

                    division_decay_results[div] = {"before": before, "after": after}
                    total_decays += 1

                    flogger.debug(f"TemperatureDecayJob[{job_id}] guild={gid} div={div}: {before} → {after}")

                # Persist decayed temperatures.
                flogger.debug(f"TemperatureDecayJob[{job_id}] guild={gid} calling update_division_temperatures()")
                await config_repo.update_division_temperatures(db, gid, updated_temperatures)
                flogger.info(f"TemperatureDecayJob[{job_id}] guild={gid}: persisted decayed temperatures")

                guild_results[gid] = division_decay_results

        end_ts = datetime.now(UTC)
        duration = (end_ts - start_ts).total_seconds()
        flogger.info(
            f"TemperatureDecayJob[{job_id}] completed: {total_decays} division(s) "
            f"decayed across {len(guild_results)} guild(s) in {duration:.2f}s"
        )
        return {
            "status": "success",
            "guilds_processed": len(guild_results),
            "total_decays": total_decays,
            "results": guild_results,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"TemperatureDecayJob[{job_id}] failed: {e}")
        flogger.trace(traceback.format_exc())
        raise
