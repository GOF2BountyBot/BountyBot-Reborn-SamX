"""
Duel repository for the BountyBot system.

Handles database operations for DuelRequest entities including guild-scoped
queries, CRUD operations, and status management.
"""

from datetime import datetime

from shared import bblogger
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.duel_request import DuelRequest

flogger = bblogger.get_logger("duel-repository")


class DuelRepository(IRepository[DuelRequest]):
    # ------------------------------------------------------------------ #
    # IRepository abstract method implementations                          #
    # ------------------------------------------------------------------ #

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> DuelRequest | None:
        """Get duel request by primary key."""
        try:
            return await db.get(DuelRequest, obj_id)
        except Exception as e:
            flogger.error(f"Error getting duel request by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> DuelRequest | None:
        """Not applicable for duel requests."""
        raise NotImplementedError("Duel requests are not queried by name")

    async def list_all(self, db: AsyncSession) -> list[DuelRequest]:
        """Get all duel requests."""
        try:
            result = await db.execute(select(DuelRequest))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all duel requests: {e}")
            raise

    async def add(self, db: AsyncSession, obj: DuelRequest, *, commit: bool = True) -> DuelRequest:
        """Add a new duel request to the database.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            db.add(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(obj)
            flogger.info(f"Added new duel request: {obj.id} challenger={obj.challenger_id} target={obj.target_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding duel request: {e}")
            if commit:
                await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict, *, commit: bool = True) -> DuelRequest:
        """Create or update a duel request from raw data."""
        raise NotImplementedError("Use create() and update_status() methods directly")

    async def remove(self, db: AsyncSession, obj: DuelRequest, *, commit: bool = True) -> None:
        """Remove a duel request from the database.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            await db.delete(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.info(f"Removed duel request: {obj.id}")
        except Exception as e:
            flogger.error(f"Error removing duel request {obj.id}: {e}")
            if commit:
                await db.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Domain-specific methods                                              #
    # ------------------------------------------------------------------ #

    async def create(self, db: AsyncSession, duel: DuelRequest, *, commit: bool = True) -> DuelRequest:
        """Create a new duel request — alias for add()."""
        return await self.add(db, duel, commit=commit)

    async def get_pending_by_players(
        self,
        db: AsyncSession,
        challenger_id: int,
        target_id: int,
        guild_id: int,
    ) -> DuelRequest | None:
        """Find a pending duel request between two specific players in a guild."""
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.challenger_id == challenger_id,
                        DuelRequest.target_id == target_id,
                        DuelRequest.guild_id == guild_id,
                        DuelRequest.status == "pending",
                    )
                )
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(
                f"Error getting pending duel for challenger={challenger_id} target={target_id} guild={guild_id}: {e}"
            )
            raise

    async def update_status(
        self, db: AsyncSession, duel_id: int, new_status: str, *, commit: bool = True
    ) -> DuelRequest | None:
        """Update the status of a duel request.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            duel = await db.get(DuelRequest, duel_id)
            if duel is None:
                return None
            duel.status = new_status
            try:
                if commit:
                    await db.commit()
                else:
                    await db.flush()
                await db.refresh(duel)
            except Exception:
                if commit:
                    await db.rollback()
                raise
            flogger.info(f"Updated duel request {duel_id} status to {new_status!r}")
            return duel
        except Exception as e:
            flogger.error(f"Error updating duel request {duel_id} status: {e}")
            raise

    async def delete_terminal_older_than(
        self,
        db: AsyncSession,
        cutoff: datetime,
        *,
        terminal_statuses: tuple[str, ...] = ("completed", "expired", "cancelled", "rejected", "declined"),
        commit: bool = True,
    ) -> int:
        """Delete duel rows in a terminal status whose ``created_at`` is older than ``cutoff``.

        Per-player duel aggregate stats (``duel_wins``, ``duel_losses``,
        ``duel_credits_won``, ``duel_credits_lost``) are kept on the
        ``players`` table, so historical duel rows have no game-relevant
        value once they reach a terminal state.

        Filters on ``created_at`` (duels have a short natural lifecycle —
        hours, not days — so created_at and any notional updated_at would
        be within the same retention window anyway).

        Args:
            db: Async database session.
            cutoff: Rows with ``created_at < cutoff`` are eligible for deletion.
            terminal_statuses: Statuses considered terminal. Default covers all
                non-pending values currently produced by ``DuelService``.
            commit: When False, flush without committing (caller owns transaction).

        Returns:
            Count of deleted rows.
        """
        try:
            result = await db.execute(
                delete(DuelRequest)
                .where(
                    and_(
                        DuelRequest.status.in_(terminal_statuses),
                        DuelRequest.created_at < cutoff,
                    )
                )
                .execution_options(synchronize_session="fetch")
            )
            if commit:
                await db.commit()
            else:
                await db.flush()
            count = result.rowcount or 0
            flogger.info(
                f"Deleted {count} terminal duel row(s) older than {cutoff.isoformat()} "
                f"(statuses={list(terminal_statuses)})"
            )
            return count
        except Exception as e:
            flogger.error(f"Error deleting terminal duels older than {cutoff.isoformat()}: {e}")
            if commit:
                await db.rollback()
            raise

    async def delete_expired(self, db: AsyncSession, current_time: datetime, *, commit: bool = True) -> int:
        """Delete all expired duel requests. Returns count of deleted rows.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            result = await db.execute(
                delete(DuelRequest).where(
                    and_(
                        DuelRequest.expires_at.isnot(None),
                        DuelRequest.expires_at <= current_time,
                        DuelRequest.status == "pending",
                    )
                )
            )
            if commit:
                await db.commit()
            else:
                await db.flush()
            count = result.rowcount
            flogger.info(f"Deleted {count} expired duel requests")
            return count
        except Exception as e:
            flogger.error(f"Error deleting expired duel requests: {e}")
            if commit:
                await db.rollback()
            raise

    async def get_active_by_guild(self, db: AsyncSession, guild_id: int) -> list[DuelRequest]:
        """Get all currently-pending duel requests for a given guild.

        Filters on BOTH status='pending' AND (expires_at IS NULL OR expires_at > NOW())
        (B.14 sibling fix — same defensive dual-layer pattern as BountyRepository).
        Duels without an expires_at are treated as non-expiring and always included.

        Methods intentionally left without the time filter:
          - delete_expired()  — explicitly operates on expired rows; time filter is its purpose
          - get_pending_by_players() — point-lookup before accepting; include even at-expiry-edge
        """
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.guild_id == guild_id,
                        DuelRequest.status == "pending",
                        # B.14 sibling: exclude duels that have passed their expiry
                        (DuelRequest.expires_at.is_(None) | (DuelRequest.expires_at > func.now())),  # pylint: disable=not-callable
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting active duel requests for guild {guild_id}: {e}")
            raise

    async def get_pending_by_challenger(self, db: AsyncSession, challenger_id: int, guild_id: int) -> list[DuelRequest]:
        """Get all currently-pending duel requests where the given player is the challenger.

        Used for outgoing-duel autocomplete (for /duel-cancel).
        Applies the same expires_at > NOW() guard (B.14 sibling fix) so that
        stale un-expired duels do not pollute autocomplete results.
        """
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.challenger_id == challenger_id,
                        DuelRequest.guild_id == guild_id,
                        DuelRequest.status == "pending",
                        # B.14 sibling: exclude duels that have passed their expiry
                        (DuelRequest.expires_at.is_(None) | (DuelRequest.expires_at > func.now())),  # pylint: disable=not-callable
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting pending duels for challenger={challenger_id} guild={guild_id}: {e}")
            raise

    async def get_pending_by_target(self, db: AsyncSession, target_id: int, guild_id: int) -> list[DuelRequest]:
        """Get all currently-pending duel requests where the given player is the target.

        Used for autocomplete: shows challenges the user needs to accept/reject.
        Applies the same expires_at > NOW() guard (B.14 sibling fix) so that
        stale un-expired duels do not pollute autocomplete results.
        """
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.target_id == target_id,
                        DuelRequest.guild_id == guild_id,
                        DuelRequest.status == "pending",
                        # B.14 sibling: exclude duels that have passed their expiry
                        (DuelRequest.expires_at.is_(None) | (DuelRequest.expires_at > func.now())),  # pylint: disable=not-callable
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting pending duels for target={target_id} guild={guild_id}: {e}")
            raise

    async def get_total_pending_stakes_for_player(
        self,
        db: AsyncSession,
        player_id: int,
        *,
        exclude_duel_id: int | None = None,
    ) -> int:
        """Sum of stakes across all pending duels where player_id is challenger OR target.

        Counts total exposure regardless of role — a player with 10k credits who
        is challenger in a 6k duel and target in another 6k duel has 12k total
        exposure, exceeding their balance.

        Args:
            db: Async database session.
            player_id: Player whose pending exposure to sum.
            exclude_duel_id: If provided, excludes this duel from the sum.
                Used by accept_duel to compute OTHER pending stakes (everything
                except the duel being resolved).

        Returns:
            Total pending stakes as an integer (0 if none).
        """
        try:
            query = select(func.coalesce(func.sum(DuelRequest.stakes), 0)).where(
                and_(
                    DuelRequest.status == "pending",
                    or_(
                        DuelRequest.challenger_id == player_id,
                        DuelRequest.target_id == player_id,
                    ),
                )
            )
            if exclude_duel_id is not None:
                query = query.where(DuelRequest.id != exclude_duel_id)
            result = await db.execute(query)
            return int(result.scalar_one() or 0)
        except Exception as e:
            flogger.error(f"Error summing pending stakes for player_id={player_id}: {e}")
            raise

    async def get_all_pending_involving_player(
        self, db: AsyncSession, player_id: int
    ) -> list[DuelRequest]:
        """All pending duels where player_id is challenger or target.

        Used by DuelService.cancel_underfunded_duels to evaluate each pending
        duel against the player's current balance. Does NOT apply the
        expires_at > NOW() guard intentionally — auto-cancel should still fire
        on duels at/near expiry; the expire job handles TTL separately.

        Args:
            db: Async database session.
            player_id: Player to look up.

        Returns:
            List of pending DuelRequest rows involving this player.
        """
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.status == "pending",
                        or_(
                            DuelRequest.challenger_id == player_id,
                            DuelRequest.target_id == player_id,
                        ),
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing pending duels involving player_id={player_id}: {e}")
            raise

    async def get_all_pending_by_guild(self, db: AsyncSession, guild_id: int) -> list[DuelRequest]:
        """Get all currently-pending duel requests for any challenger/target in a guild.

        Used by admin autocomplete and admin-cancel-all to show or cancel all
        pending duels guild-wide.  Applies the same expires_at > NOW() guard
        (B.14 sibling fix) so stale un-expired duels are excluded.
        """
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.guild_id == guild_id,
                        DuelRequest.status == "pending",
                        # B.14 sibling: exclude duels that have passed their expiry
                        (DuelRequest.expires_at.is_(None) | (DuelRequest.expires_at > func.now())),  # pylint: disable=not-callable
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting all pending duels for guild={guild_id}: {e}")
            raise
