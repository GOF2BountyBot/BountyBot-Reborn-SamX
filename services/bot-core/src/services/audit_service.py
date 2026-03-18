"""Lightweight audit logging service for admin operations."""

import contextlib
import json
from typing import Any

from persist.models.admin_audit_log import AdminAuditLog
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("audit-service")


class AuditService:
    """Records admin mutations to the audit log table.

    All methods are static: no state is held. Callers pass an already-open
    AsyncSession.  Audit failures are swallowed so the primary operation is
    never blocked.
    """

    @staticmethod
    async def log_action(
        db: AsyncSession,
        user_id: int,
        action: str,
        guild_id: int | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        status: str = "success",
    ) -> None:
        """Record an admin action to the audit log.

        On any failure the exception is caught and logged; the audit entry is
        silently dropped so the caller's response is unaffected.
        """
        try:
            entry = AdminAuditLog(
                user_id=user_id,
                guild_id=guild_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=json.dumps(details) if details is not None else None,
                status=status,
            )
            db.add(entry)
            await db.commit()
            flogger.info(
                f"AUDIT: user={user_id} action={action} resource={resource_type}:{resource_id} status={status}"
            )
        except Exception as exc:
            flogger.error(f"Failed to write audit log: {exc}")
            # Don't let audit failures surface to the caller.
            with contextlib.suppress(Exception):
                await db.rollback()
