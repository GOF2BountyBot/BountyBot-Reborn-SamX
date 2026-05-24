"""Admin audit log repository for the BountyBot system.

Provides minimal data-access methods for the ``admin_audit_logs`` table.
Inserts are performed directly by ``AuditService.log_action`` (which writes
the ORM object to the supplied session) rather than via this repository,
so the repository's surface is intentionally small: counts and deletion
for data retention.
"""

from datetime import datetime

from shared import bblogger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.models.admin_audit_log import AdminAuditLog

flogger = bblogger.get_logger("admin-audit-log-repository")


class AdminAuditLogRepository:
    """Minimal repository for AdminAuditLog table operations.

    This does NOT implement ``IRepository[T]`` — the audit log is append-only
    by design (writes go through ``AuditService.log_action``), so the standard
    CRUD surface would be misleading. Only operations that are part of the
    DB-retention pipeline are exposed here.
    """

    async def count(self, db: AsyncSession) -> int:
        """Return total number of audit log rows."""
        try:
            result = await db.execute(select(func.count()).select_from(AdminAuditLog))  # pylint: disable=not-callable
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting audit log rows: {e}")
            raise

    async def delete_older_than(
        self,
        db: AsyncSession,
        cutoff: datetime,
        *,
        commit: bool = True,
    ) -> int:
        """Delete audit-log rows whose ``timestamp`` is older than ``cutoff``.

        Audit history is preserved out-of-band via the scheduled ``pg_backup``
        job, so in-database retention can be relatively short (default 30 days,
        configurable via ``BOUNTYBOT_AUDIT_RETENTION_DAYS``).

        Args:
            db: Async database session.
            cutoff: Rows with ``timestamp < cutoff`` are eligible for deletion.
            commit: When False, flush without committing (caller owns transaction).

        Returns:
            Count of deleted rows.
        """
        try:
            result = await db.execute(
                delete(AdminAuditLog)
                .where(AdminAuditLog.timestamp < cutoff)
                .execution_options(synchronize_session="fetch")
            )
            if commit:
                await db.commit()
            else:
                await db.flush()
            count = result.rowcount or 0
            flogger.info(f"Deleted {count} audit log row(s) older than {cutoff.isoformat()}")
            return count
        except Exception as e:
            flogger.error(f"Error deleting audit log rows older than {cutoff.isoformat()}: {e}")
            if commit:
                await db.rollback()
            raise
