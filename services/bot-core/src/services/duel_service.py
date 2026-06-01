"""
Duel Service for the BountyBot system.

Manages the full duel lifecycle: challenge creation, acceptance (with combat
resolution and credit transfer), rejection, and expiry.

All methods expect a caller-managed SQLAlchemy AsyncSession. No transaction
boundaries are committed here; each operation delegates to repository methods
which commit individually (as implemented in the repository layer).
"""

from datetime import UTC, datetime, timedelta

from persist.models.duel_request import DuelRequest
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.duel_repository import DuelRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.user_repository import UserRepository
from shared import bblogger

from services.combat_service import CombatService
from services.game_constants import GameConstants, resolve_constant
from services.loadout_builder import LoadoutBuilder

flogger = bblogger.get_logger("duel-service")


class DuelService:
    """Service for the duel (PvP challenge) lifecycle."""

    def __init__(self) -> None:
        self.duel_repo = DuelRepository()
        self.player_repo = PlayerRepository()
        self.user_repo = UserRepository()
        self.config_repo = ConfigRepository()
        self.combat_service = CombatService()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _resolve_player_label(self, db, player) -> str:
        """Resolve a player to a display label for user-facing error messages.

        Preference order: player.display_name → user.discord_username → "Player {id}".
        Always returns a string — never raises.
        """
        try:
            if getattr(player, "display_name", None):
                return player.display_name
            user = await self.user_repo.get_by_id(db, player.user_id)
            if user and user.discord_username:
                return user.discord_username
        except Exception as exc:
            flogger.debug(f"Could not resolve display label for player_id={player.id}: {exc}")
        return f"Player {player.id}"

    # ------------------------------------------------------------------
    # Challenge
    # ------------------------------------------------------------------

    async def create_challenge(
        self,
        db,
        challenger_id: int,
        target_id: int,
        stakes: int,
        guild_id: int,
    ) -> DuelRequest:
        """Create a new duel challenge.

        Validates both players exist, have sufficient credits, and that no
        pending duel already exists between them in this guild.

        Args:
            db: SQLAlchemy async session.
            challenger_id: Player ID of the challenger.
            target_id: Player ID of the target.
            stakes: Credit amount wagered (must be >= 0).
            guild_id: Guild the duel is scoped to.

        Returns:
            The newly created DuelRequest with status "pending".

        Raises:
            ValueError: For any validation failure.
        """
        flogger.debug(
            f"create_challenge called: challenger_id={challenger_id} target_id={target_id} "
            f"stakes={stakes} guild_id={guild_id}"
        )

        # --- Cheap validation FIRST, before any I/O ---
        if challenger_id == target_id:
            flogger.warning(f"Self-duel attempt: player {challenger_id} tried to challenge themselves")
            raise ValueError("A player cannot challenge themselves to a duel.")

        if stakes < 0:
            flogger.warning(f"Invalid stakes attempted: {stakes} (must be non-negative)")
            raise ValueError(f"Stakes must be non-negative, got {stakes}.")

        # Fetch players — wrap repository calls so DB/ORM exceptions surface as
        # friendly 400 errors rather than leaking as raw 500s.
        try:
            challenger = await self.player_repo.get_by_id(db, challenger_id)
        except Exception as exc:
            flogger.error(f"DB error fetching challenger player_id={challenger_id}: {exc}", exc_info=True)
            raise ValueError(f"Challenger player with ID {challenger_id} could not be retrieved.") from exc
        if challenger is None:
            flogger.error(f"Challenger not found: player_id={challenger_id}")
            raise ValueError(f"Challenger player with ID {challenger_id} not found.")

        try:
            target = await self.player_repo.get_by_id(db, target_id)
        except Exception as exc:
            flogger.error(f"DB error fetching target player_id={target_id}: {exc}", exc_info=True)
            raise ValueError(f"Target player with ID {target_id} could not be retrieved.") from exc
        if target is None:
            flogger.error(f"Target not found: player_id={target_id}")
            raise ValueError(f"Target player with ID {target_id} not found.")

        # Validate available credits (balance minus total pending stakes in both roles).
        # This prevents the double-spend exploit where a player issues multiple challenges
        # backed by the same credits, or is simultaneously challenged by multiple people.
        challenger_pending = await self.duel_repo.get_total_pending_stakes_for_player(db, challenger_id)
        target_pending = await self.duel_repo.get_total_pending_stakes_for_player(db, target_id)
        challenger_available = challenger.credits - challenger_pending
        target_available = target.credits - target_pending

        if challenger_available < stakes:
            challenger_label = await self._resolve_player_label(db, challenger)
            flogger.warning(
                f"Challenger insufficient available credits: player_id={challenger_id} "
                f"credits={challenger.credits} pending_stakes={challenger_pending} "
                f"available={challenger_available} needs={stakes}"
            )
            raise ValueError(
                f"{challenger_label} has insufficient available credits: "
                f"{challenger_available:,} available "
                f"({challenger.credits:,} − {challenger_pending:,} in pending duels), "
                f"needs {stakes:,}."
            )
        if target_available < stakes:
            target_label = await self._resolve_player_label(db, target)
            flogger.warning(
                f"Target insufficient available credits: player_id={target_id} "
                f"credits={target.credits} pending_stakes={target_pending} "
                f"available={target_available} needs={stakes}"
            )
            raise ValueError(
                f"{target_label} has insufficient available credits to accept this challenge: "
                f"{target_available:,} available "
                f"({target.credits:,} − {target_pending:,} in pending duels), "
                f"needs {stakes:,}."
            )

        # Prevent duplicate pending duels
        existing = await self.duel_repo.get_pending_by_players(db, challenger_id, target_id, guild_id)
        if existing is not None:
            flogger.warning(
                f"Duplicate duel attempt: duel_id={existing.id} already pending between "
                f"challenger_id={challenger_id} and target_id={target_id} in guild_id={guild_id}"
            )
            challenger_label = await self._resolve_player_label(db, challenger)
            target_label = await self._resolve_player_label(db, target)
            raise ValueError(f"A pending duel already exists between {challenger_label} and {target_label}.")

        # Resolve per-guild duel expiry
        cfg = await self.config_repo.get_by_guild_id(db, guild_id)
        expiry_seconds = resolve_constant(cfg, "duel_request_expiry", GameConstants.DUEL_REQUEST_EXPIRY)

        # Create the duel request
        duel = DuelRequest(
            guild_id=guild_id,
            challenger_id=challenger_id,
            target_id=target_id,
            stakes=stakes,
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(seconds=expiry_seconds),
        )
        created = await self.duel_repo.create(db, duel)
        flogger.info(
            f"Duel challenge created: id={created.id} challenger={challenger_id} target={target_id} stakes={stakes}"
        )
        return created

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    async def accept_duel(self, db, duel_id: int) -> dict:
        """Accept a pending duel and resolve combat.

        Re-validates that both players still have sufficient credits, then
        runs a combat simulation. On a decisive result, credits are transferred
        and duel statistics updated. On a stalemate, no changes are made.

        Args:
            db: SQLAlchemy async session.
            duel_id: Primary key of the DuelRequest.

        Returns:
            Dict with keys: fight_results, challenger, target, stakes,
            credits_transferred.

        Raises:
            ValueError: If duel not found, not pending, or credits insufficient.
        """
        flogger.debug(f"accept_duel called: duel_id={duel_id}")

        duel = await self.duel_repo.get_by_id(db, duel_id)
        if duel is None:
            flogger.error(f"Duel not found for accept: duel_id={duel_id}")
            raise ValueError(f"Duel request with ID {duel_id} not found.")

        if duel.status != "pending":
            flogger.error(
                f"Invalid duel status for accept: duel_id={duel_id} status={duel.status} (expected 'pending')"
            )
            raise ValueError(f"Duel {duel_id} cannot be accepted — current status is {duel.status!r}.")

        stakes = duel.stakes

        # Re-validate credits at accept-time under row-level lock.
        # Lock in consistent ID order to prevent deadlocks.
        ids_ordered = sorted([duel.challenger_id, duel.target_id])
        locked = {}
        for pid in ids_ordered:
            player = await self.player_repo.get_by_id_for_update(db, pid)
            if player is None:
                flogger.error(f"Player not found during duel accept: duel_id={duel_id} player_id={pid}")
                raise ValueError(f"Player {pid} not found.")
            locked[pid] = player

        challenger = locked[duel.challenger_id]
        target = locked[duel.target_id]

        # Re-validate available credits at accept-time, excluding this duel from the
        # pending sum (it's being resolved, not additional exposure).
        challenger_other_pending = await self.duel_repo.get_total_pending_stakes_for_player(
            db, challenger.id, exclude_duel_id=duel_id
        )
        target_other_pending = await self.duel_repo.get_total_pending_stakes_for_player(
            db, target.id, exclude_duel_id=duel_id
        )
        challenger_available = challenger.credits - challenger_other_pending
        target_available = target.credits - target_other_pending

        if challenger_available < stakes:
            challenger_label = await self._resolve_player_label(db, challenger)
            flogger.warning(
                f"Challenger insufficient available credits at accept-time: duel_id={duel_id} "
                f"player_id={challenger.id} credits={challenger.credits} "
                f"other_pending={challenger_other_pending} available={challenger_available} needs={stakes}"
            )
            raise ValueError(
                f"{challenger_label} can no longer cover this duel: "
                f"{challenger_available:,} available "
                f"({challenger.credits:,} − {challenger_other_pending:,} in other pending duels), "
                f"needs {stakes:,}."
            )
        if target_available < stakes:
            target_label = await self._resolve_player_label(db, target)
            flogger.warning(
                f"Target insufficient available credits at accept-time: duel_id={duel_id} "
                f"player_id={target.id} credits={target.credits} "
                f"other_pending={target_other_pending} available={target_available} needs={stakes}"
            )
            raise ValueError(
                f"{target_label} can no longer cover this duel: "
                f"{target_available:,} available "
                f"({target.credits:,} − {target_other_pending:,} in other pending duels), "
                f"needs {stakes:,}."
            )

        # Build full ship loadouts (weapons, turrets, modules) from DB
        challenger_loadout = await LoadoutBuilder.from_player(db, challenger.id)
        target_loadout = await LoadoutBuilder.from_player(db, target.id)

        # Resolve combat
        fight_results = self.combat_service.fight_ships(challenger_loadout, target_loadout)

        credits_transferred = 0

        if not fight_results.is_stalemate:
            # Determine winner and loser by matching ship names
            if fight_results.winner_name == challenger_loadout.ship_name:
                winner, loser = challenger, target
            else:
                winner, loser = target, challenger

            # Transfer credits and update stats
            winner.credits += stakes
            winner.duel_wins += 1
            winner.duel_credits_won += stakes

            loser.credits -= stakes
            loser.duel_losses += 1
            loser.duel_credits_lost += stakes

            credits_transferred = stakes

            # Auto-cancel any other pending duels the loser can no longer cover.
            # The loser's balance just dropped by stakes; some of their remaining
            # pending duels may now be unbacked. Non-fatal: must never block duel resolution.
            try:
                await self.cancel_underfunded_duels(db, loser.id, commit=False)
            except Exception as _cancel_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"cancel_underfunded_duels failed after duel {duel_id} resolution for loser={loser.id}: {_cancel_exc}")

            flogger.info(f"Duel {duel_id} resolved: winner player={winner.id} stakes={stakes} transferred")
        else:
            flogger.info(f"Duel {duel_id} ended in a stalemate — no credits transferred.")

        # Mark duel as completed (commit=False so we own the explicit commit below).
        # B.34 closeout: previously this method relied on the duel_repo.update_status
        # default commit=True to flush ALL pending changes (the direct ORM
        # mutations on winner/loser players above). That works only by accident
        # — if any future change set commit=False here, the cross-table writes
        # would silently roll back. Now the service owns the transaction
        # explicitly: all mutations + the status update commit together as one.
        await self.duel_repo.update_status(db, duel_id, "completed", commit=False)
        await db.commit()
        await db.refresh(challenger)
        await db.refresh(target)

        # Resolve display names for both participants.
        # B.62: prefer player.display_name (per-guild nickname) over discord_username.
        # Defensive try/except — lookup failures must never break duel resolution.
        challenger_name: str | None = None
        try:
            if challenger.display_name:
                challenger_name = challenger.display_name
            else:
                challenger_user = await self.user_repo.get_by_id(db, challenger.user_id)
                if challenger_user and challenger_user.discord_username:
                    challenger_name = challenger_user.discord_username
        except Exception as exc:  # defensive — lookup failures must never break duel resolution
            flogger.debug(f"Could not resolve challenger name for duel {duel_id}: {exc}")

        target_name: str | None = None
        try:
            if target.display_name:
                target_name = target.display_name
            else:
                target_user = await self.user_repo.get_by_id(db, target.user_id)
                if target_user and target_user.discord_username:
                    target_name = target_user.discord_username
        except Exception as exc:  # defensive — lookup failures must never break duel resolution
            flogger.debug(f"Could not resolve target name for duel {duel_id}: {exc}")

        return {
            "fight_results": fight_results,
            "challenger": challenger,
            "target": target,
            "stakes": stakes,
            "credits_transferred": credits_transferred,
            "challenger_name": challenger_name,
            "target_name": target_name,
        }

    # ------------------------------------------------------------------
    # Reject
    # ------------------------------------------------------------------

    async def reject_duel(self, db, duel_id: int) -> DuelRequest:
        """Reject a pending duel challenge.

        Args:
            db: SQLAlchemy async session.
            duel_id: Primary key of the DuelRequest.

        Returns:
            The updated DuelRequest with status "rejected".

        Raises:
            ValueError: If duel not found or not in pending status.
        """
        flogger.debug(f"reject_duel called: duel_id={duel_id}")

        duel = await self.duel_repo.get_by_id(db, duel_id)
        if duel is None:
            flogger.error(f"Duel not found for reject: duel_id={duel_id}")
            raise ValueError(f"Duel request with ID {duel_id} not found.")

        if duel.status != "pending":
            flogger.error(
                f"Invalid duel status for reject: duel_id={duel_id} status={duel.status} (expected 'pending')"
            )
            raise ValueError(f"Duel {duel_id} cannot be rejected — current status is {duel.status!r}.")

        updated = await self.duel_repo.update_status(db, duel_id, "rejected")
        flogger.info(f"Duel {duel_id} rejected.")
        return updated

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    async def get_duel(self, db, duel_id: int) -> DuelRequest:
        """Return a DuelRequest by ID, or raise ValueError if not found.

        Args:
            db: SQLAlchemy async session.
            duel_id: Primary key of the DuelRequest.

        Returns:
            The DuelRequest instance.

        Raises:
            ValueError: If the duel does not exist.
        """
        duel = await self.duel_repo.get_by_id(db, duel_id)
        if duel is None:
            raise ValueError(f"Duel request with ID {duel_id} not found.")
        return duel

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_pending_for_target(self, db, target_id: int, guild_id: int) -> list[tuple[DuelRequest, str | None]]:
        """Return all pending duels where *target_id* is the target in the given guild,
        together with the challenger's Discord username.

        Used by the Discord gateway for autocomplete on /duel-accept and /duel-reject.

        Args:
            db: SQLAlchemy async session.
            target_id: Player ID of the potential acceptor.
            guild_id: Guild the duels are scoped to.

        Returns:
            List of (DuelRequest, challenger_name) tuples. challenger_name is None
            if the challenger's username cannot be resolved (e.g. missing user row).
        """
        duels = await self.duel_repo.get_pending_by_target(db, target_id, guild_id)

        result = []
        for duel in duels:
            challenger_name: str | None = None
            try:
                challenger = await self.player_repo.get_by_id(db, duel.challenger_id)
                if challenger is not None:
                    user = await self.user_repo.get_by_id(db, challenger.user_id)
                    if user and user.discord_username:
                        challenger_name = user.discord_username
            except Exception as exc:  # defensive — lookup failures must never break autocomplete
                flogger.debug(f"Could not resolve challenger name for duel {duel.id}: {exc}")
            result.append((duel, challenger_name))

        return result

    # ------------------------------------------------------------------
    # Cancel (B.64 / B.65)
    # ------------------------------------------------------------------

    async def cancel_duel(
        self,
        db,
        duel_id: int,
        requesting_player_id: int | None = None,
    ) -> "DuelRequest":
        """Cancel a pending duel challenge.

        B.64 (challenger self-cancel): pass requesting_player_id to enforce
        that only the challenger can cancel.
        B.65 (admin cancel): omit requesting_player_id (or pass None) to skip
        the ownership check.

        Args:
            db: SQLAlchemy async session.
            duel_id: Primary key of the DuelRequest.
            requesting_player_id: Player ID of the requester (None = admin bypass).

        Returns:
            The updated DuelRequest with status "cancelled".

        Raises:
            ValueError: If duel not found, not pending, or caller is not the challenger.
        """
        flogger.debug(f"cancel_duel called: duel_id={duel_id} requesting_player_id={requesting_player_id}")

        duel = await self.duel_repo.get_by_id(db, duel_id)
        if duel is None:
            flogger.error(f"Duel not found for cancel: duel_id={duel_id}")
            raise ValueError("Duel not found.")

        if duel.status != "pending":
            flogger.error(
                f"Invalid duel status for cancel: duel_id={duel_id} status={duel.status} (expected 'pending')"
            )
            raise ValueError("Only pending duels can be cancelled.")

        if requesting_player_id is not None and requesting_player_id != duel.challenger_id:
            flogger.warning(
                f"Unauthorised cancel attempt: duel_id={duel_id} "
                f"requesting_player_id={requesting_player_id} challenger_id={duel.challenger_id}"
            )
            raise ValueError("Only the challenger can cancel a duel.")

        updated = await self.duel_repo.update_status(db, duel_id, "cancelled")
        flogger.info(f"Duel {duel_id} cancelled (requesting_player_id={requesting_player_id}).")
        return updated

    # ------------------------------------------------------------------
    # Query helpers (outgoing)
    # ------------------------------------------------------------------

    async def get_outgoing_for_challenger(
        self, db, challenger_id: int, guild_id: int
    ) -> list[tuple["DuelRequest", str | None]]:
        """Return all pending duels where *challenger_id* is the challenger in the given guild,
        together with the target's Discord username.

        Used by the Discord gateway for autocomplete on /duel-cancel.

        Args:
            db: SQLAlchemy async session.
            challenger_id: Player ID of the challenger.
            guild_id: Guild the duels are scoped to.

        Returns:
            List of (DuelRequest, target_name) tuples. target_name is None
            if the target's username cannot be resolved (e.g. missing user row).
        """
        duels = await self.duel_repo.get_pending_by_challenger(db, challenger_id, guild_id)

        result = []
        for duel in duels:
            target_name: str | None = None
            try:
                target = await self.player_repo.get_by_id(db, duel.target_id)
                if target is not None:
                    user = await self.user_repo.get_by_id(db, target.user_id)
                    if user and user.discord_username:
                        target_name = user.discord_username
            except Exception as exc:  # defensive — lookup failures must never break autocomplete
                flogger.debug(f"Could not resolve target name for duel {duel.id}: {exc}")
            result.append((duel, target_name))

        return result

    # ------------------------------------------------------------------
    # Admin: get all pending for guild (autocomplete)
    # ------------------------------------------------------------------

    async def get_all_pending_for_guild(self, db, guild_id: int) -> list[tuple[DuelRequest, str | None, str | None]]:
        """Return all pending duels for a guild (any challenger, any target),
        together with both participants' Discord usernames.

        Used by the Discord gateway for admin autocomplete on /admin_duel.

        Args:
            db: SQLAlchemy async session.
            guild_id: Guild the duels are scoped to.

        Returns:
            List of (DuelRequest, challenger_name, target_name) tuples.
            Either name is None if it cannot be resolved.
        """
        duels = await self.duel_repo.get_all_pending_by_guild(db, guild_id)

        result = []
        for duel in duels:
            challenger_name: str | None = None
            target_name: str | None = None
            try:
                challenger = await self.player_repo.get_by_id(db, duel.challenger_id)
                if challenger is not None:
                    user = await self.user_repo.get_by_id(db, challenger.user_id)
                    if user and user.discord_username:
                        challenger_name = user.discord_username
            except Exception as exc:  # defensive — lookup failures must never break autocomplete
                flogger.debug(f"Could not resolve challenger name for duel {duel.id}: {exc}")
            try:
                target = await self.player_repo.get_by_id(db, duel.target_id)
                if target is not None:
                    user = await self.user_repo.get_by_id(db, target.user_id)
                    if user and user.discord_username:
                        target_name = user.discord_username
            except Exception as exc:  # defensive — lookup failures must never break autocomplete
                flogger.debug(f"Could not resolve target name for duel {duel.id}: {exc}")
            result.append((duel, challenger_name, target_name))

        return result

    # ------------------------------------------------------------------
    # Admin: cancel all pending duels for a guild
    # ------------------------------------------------------------------

    async def cancel_all_pending_duels(self, db, guild_id: int) -> list[DuelRequest]:
        """Cancel ALL pending duels for a guild in one call.

        Args:
            db: SQLAlchemy async session.
            guild_id: Guild whose pending duels should be cancelled.

        Returns:
            List of DuelRequest objects that were cancelled.
        """
        duels = await self.duel_repo.get_all_pending_by_guild(db, guild_id)

        cancelled = []
        for duel in duels:
            # update_status commits each row individually — acceptable for admin-only bulk op
            updated = await self.duel_repo.update_status(db, duel.id, "cancelled")
            if updated is not None:
                cancelled.append(updated)
            flogger.info(f"Admin bulk-cancelled duel {duel.id} in guild {guild_id}.")

        return cancelled

    # ------------------------------------------------------------------
    # Auto-cancel underfunded duels
    # ------------------------------------------------------------------

    async def cancel_underfunded_duels(
        self,
        db,
        player_id: int,
        *,
        commit: bool = False,
    ) -> list[DuelRequest]:
        """Cancel any pending duel involving player_id where the player's current
        balance is below that duel's stakes.

        Called from every credit-deduction site (see services/AGENTS.md →
        "Duel Pending-Stakes Invariant"). Idempotent; safe to call when no
        duels are underfunded (returns []).

        The caller normally owns the transaction (commit=False default); the
        method flushes per-update so subsequent in-transaction reads see the
        cancelled status.

        Args:
            db: AsyncSession.
            player_id: Player whose balance just dropped.
            commit: If True, commit at end. Default False — caller owns commit.

        Returns:
            List of cancelled DuelRequest rows.
        """
        flogger.debug(f"cancel_underfunded_duels: scanning player_id={player_id}")
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if player is None:
                return []

            pending = await self.duel_repo.get_all_pending_involving_player(db, player_id)
            cancelled: list[DuelRequest] = []
            for duel in pending:
                if duel.stakes > player.credits:
                    updated = await self.duel_repo.update_status(db, duel.id, "cancelled", commit=False)
                    if updated is not None:
                        cancelled.append(updated)
                        role = "challenger" if duel.challenger_id == player_id else "target"
                        flogger.info(
                            f"Auto-cancelled underfunded duel id={duel.id} "
                            f"player_id={player_id} credits={player.credits} "
                            f"stakes={duel.stakes} role={role}"
                        )
            if commit:
                await db.commit()
            return cancelled
        except Exception as e:
            flogger.error(f"cancel_underfunded_duels failed for player_id={player_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # Expire
    # ------------------------------------------------------------------

    async def expire_duel(self, db, duel_id: int) -> DuelRequest:
        """Expire a pending duel challenge (e.g. via scheduler).

        Args:
            db: SQLAlchemy async session.
            duel_id: Primary key of the DuelRequest.

        Returns:
            The updated DuelRequest with status "expired".

        Raises:
            ValueError: If duel not found or not in pending status.
        """
        flogger.debug(f"expire_duel called: duel_id={duel_id}")

        duel = await self.duel_repo.get_by_id(db, duel_id)
        if duel is None:
            flogger.error(f"Duel not found for expire: duel_id={duel_id}")
            raise ValueError(f"Duel request with ID {duel_id} not found.")

        if duel.status != "pending":
            flogger.error(
                f"Invalid duel status for expire: duel_id={duel_id} status={duel.status} (expected 'pending')"
            )
            raise ValueError(f"Duel {duel_id} cannot be expired — current status is {duel.status!r}.")

        updated = await self.duel_repo.update_status(db, duel_id, "expired")
        flogger.info(f"Duel {duel_id} expired.")
        return updated
