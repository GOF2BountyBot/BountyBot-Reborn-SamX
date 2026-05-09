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

    Transaction-discipline contract (B.34 remediation, AC-4):
    -----------------------------------------------------
    log_action accepts a ``commit: bool = True`` keyword.

    - ``commit=True`` (default, backward-compatible): the audit row commits
      independently. Use this from routes that do NOT wrap their body in
      ``async with db.begin():`` (i.e. legacy bare-session routes).

    - ``commit=False``: the audit row is flushed but not committed. Use this
      when the route owns a wrapping ``async with db.begin():`` block — the
      audit row joins the primary transaction and persists atomically with
      it (or rolls back together if the primary work fails). On flush failure
      a soft-fallback rollback is attempted but the swallow-failure
      semantics still apply: the caller does not see the audit error.
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
        *,
        commit: bool = True,
    ) -> None:
        """Record an admin action to the audit log.

        On any failure the exception is caught and logged; the audit entry is
        silently dropped so the caller's response is unaffected.

        Args:
            commit: When False, flush the audit row without committing — for
                use inside a wrapped ``async with db.begin():`` block where
                the outer transaction commits the whole unit of work.
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
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.info(
                f"AUDIT: user={user_id} action={action} resource={resource_type}:{resource_id} status={status}"
            )
        except Exception as exc:
            flogger.error(f"Failed to write audit log: {exc}")
            # Don't let audit failures surface to the caller. Only attempt
            # rollback when we own the transaction; inside a wrapped block
            # the caller's surrounding db.begin() will roll back on its own
            # exception path.
            if commit:
                with contextlib.suppress(Exception):
                    await db.rollback()
