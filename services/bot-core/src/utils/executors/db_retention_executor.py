"""DB data retention executor — daily cleanup of stale rows.

Runs daily via APScheduler.  Three independent passes, each in its own
database session so a failure on one target table does not abort the
others:

1. ``bounty`` — delete rows in terminal status (completed, expired,
   cleared) whose ``updated_at`` is older than
   ``GameConstants.BOUNTY_RETENTION_HOURS`` hours (default 24h).
2. ``duel_requests`` — delete rows in terminal status (completed,
   expired, cancelled, rejected, declined) whose ``created_at`` is older
   than ``GameConstants.DUEL_RETENTION_HOURS`` hours (default 24h).
3. ``admin_audit_logs`` — delete rows whose ``timestamp`` is older than
   ``GameConstants.AUDIT_RETENTION_DAYS`` days (default 30d).

Rationale
---------
Per-player aggregate stats (``bounty_wins``, ``systems_checked``,
``lifetime_credits``, ``duel_wins``/``losses``/``credits_won``/``credits_lost``)
are kept on the ``players`` table, so terminal-state rows in ``bounty``
and ``duel_requests`` have no game-relevant value.  Audit history is
preserved out-of-band by the scheduled ``pg_backup_default`` job.

This executor never fails fatally — all exceptions are caught, logged,
and the executor still returns ``{"status": "success", ...}`` with
per-target counts (possibly zero) so APScheduler does not retry.

Imports of repositories / database manager are deferred to function
scope so the module is safely importable in test environments without a
live database (matches the project-wide executor pattern).
"""

from datetime import UTC, datetime, timedelta

from shared.bblogger import get_logger

flogger = get_logger("db-retention-executor")


async def execute_db_retention_job(job_id: str, payload: dict) -> dict:
    """Run the three retention passes.  Always returns success.

    Args:
        job_id: APScheduler job identifier (used for log correlation).
        payload: Job payload (unused; reserved for future per-call overrides).

    Returns:
        Dict with keys ``status``, ``bounties_deleted``, ``duels_deleted``,
        ``audit_logs_deleted`` and ``errors`` (list of strings — empty on
        full success).
    """
    # Deferred imports — see module docstring.
    from persist.database.manager import db_manager
    from persist.repositories.admin_audit_log_repository import AdminAuditLogRepository
    from persist.repositories.bounty_repository import BountyRepository
    from persist.repositories.duel_repository import DuelRepository
    from services.game_constants import GameConstants

    flogger.info(f"DBRetention[{job_id}] START")
    now = datetime.now(UTC)

    bounty_cutoff = now - timedelta(hours=GameConstants.BOUNTY_RETENTION_HOURS)
    duel_cutoff = now - timedelta(hours=GameConstants.DUEL_RETENTION_HOURS)
    audit_cutoff = now - timedelta(days=GameConstants.AUDIT_RETENTION_DAYS)

    bounties_deleted = 0
    duels_deleted = 0
    audit_logs_deleted = 0
    errors: list[str] = []

    # ----- Pass 1: bounties --------------------------------------------------
    try:
        async with db_manager.get_session() as db:
            bounty_repo = BountyRepository()
            bounties_deleted = await bounty_repo.delete_terminal_older_than(db, bounty_cutoff)
        flogger.info(
            f"DBRetention[{job_id}] bounty pass: deleted={bounties_deleted} cutoff={bounty_cutoff.isoformat()} "
            f"(retention={GameConstants.BOUNTY_RETENTION_HOURS}h)"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        msg = f"bounty pass failed: {type(e).__name__}: {e}"
        flogger.warning(f"DBRetention[{job_id}] {msg}")
        errors.append(msg)

    # ----- Pass 2: duels -----------------------------------------------------
    try:
        async with db_manager.get_session() as db:
            duel_repo = DuelRepository()
            duels_deleted = await duel_repo.delete_terminal_older_than(db, duel_cutoff)
        flogger.info(
            f"DBRetention[{job_id}] duel pass: deleted={duels_deleted} cutoff={duel_cutoff.isoformat()} "
            f"(retention={GameConstants.DUEL_RETENTION_HOURS}h)"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        msg = f"duel pass failed: {type(e).__name__}: {e}"
        flogger.warning(f"DBRetention[{job_id}] {msg}")
        errors.append(msg)

    # ----- Pass 3: audit logs ------------------------------------------------
    try:
        async with db_manager.get_session() as db:
            audit_repo = AdminAuditLogRepository()
            audit_logs_deleted = await audit_repo.delete_older_than(db, audit_cutoff)
        flogger.info(
            f"DBRetention[{job_id}] audit pass: deleted={audit_logs_deleted} cutoff={audit_cutoff.isoformat()} "
            f"(retention={GameConstants.AUDIT_RETENTION_DAYS}d)"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        msg = f"audit pass failed: {type(e).__name__}: {e}"
        flogger.warning(f"DBRetention[{job_id}] {msg}")
        errors.append(msg)

    flogger.info(
        f"DBRetention[{job_id}] DONE bounties={bounties_deleted} duels={duels_deleted} "
        f"audit={audit_logs_deleted} errors={len(errors)}"
    )

    return {
        "status": "success",
        "bounties_deleted": bounties_deleted,
        "duels_deleted": duels_deleted,
        "audit_logs_deleted": audit_logs_deleted,
        "errors": errors,
    }
