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
from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.user_repository import UserRepository
from shared import bblogger

from services.cargo_utils import compute_free_cargo, is_over_cap
from services.combat_service import CombatService
from services.exceptions import OverCapError
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
        self.inventory_repo = InventoryRepository()
        self.combat_service = CombatService()

    # ------------------------------------------------------------------
    # Over-cap lockout (T7 / LOOT_JOURNAL §5.5 C-3a)
    # ------------------------------------------------------------------

    async def _assert_under_cap(self, db, player) -> None:
        """Raise :class:`OverCapError` if ``player`` is over their cargo cap.

        The over-cap lockout: a player who is "leaving station" for a duel must
        be at-or-under their cargo cap. Over-cap is STRICTLY ``load > cap`` (being
        exactly AT cap is allowed). Plain read — a stale borderline read
        self-corrects next command (§5.5 C-3b). Equip/unequip/buy are NOT gated.
        """
        _free, load, cap = await compute_free_cargo(db, self.inventory_repo, player)
        if is_over_cap(load, cap):
            flogger.info(
                f"Duel over-cap lockout: player_id={getattr(player, 'id', None)} cargo_load={load} cargo_cap={cap}"
            )
            raise OverCapError(current_load=load, effective_cap=cap, player_id=getattr(player, "id", None))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _resolve_player_label(self, db, player) -> str:
        """Resolve a player to a display label for user-facing error messages.

        Preference order: player.display_name → user.discord_username → "Player {id}".
        Always returns a string — never raises.

        NOTE: bounty_service._resolve_combat_label is a near-identical copy.
        A shared extraction was deferred because this method accesses self.user_repo
        while the bounty version is a module-level function with an optional user_repo
        arg.  If a third caller appears, extract to services/combat_label_utils.py.
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

        # D5-T2: lock BOTH players' aggregate-root rows FOR UPDATE in ascending
        # player_id order (mirrors accept_duel / transfer_credits) before reading
        # credits + pending stakes for the available-credit validation.
        #
        # DEFENSE-IN-DEPTH / HARDENING — NOT a live exploit: the stake is only
        # actually DEDUCTED at accept_duel, which is lock-protected and FULLY
        # re-validates available credits net of OTHER pending stakes under its own
        # FOR UPDATE locks (duel_service.accept_duel, "Re-validate available credits
        # at accept-time").  So even if two challenges race at create-time and both
        # pass an unlocked available-credit check, at most one can be ACCEPTED while
        # underfunded — the other accept fails its re-validation.  Locking here keeps
        # create_challenge consistent with the canonical D5-T2 lock-ordering rule and
        # removes the inconsistent-read window for the create-time advisory check.
        #
        # Wrap repository calls so DB/ORM exceptions surface as friendly 400 errors
        # rather than leaking as raw 500s.
        ids_ordered = sorted({challenger_id, target_id})
        locked: dict[int, object] = {}
        for pid in ids_ordered:
            try:
                player = await self.player_repo.get_by_id_for_update(db, pid)
            except Exception as exc:
                flogger.error(f"DB error fetching player_id={pid} for challenge: {exc}", exc_info=True)
                raise ValueError(f"Player with ID {pid} could not be retrieved.") from exc
            locked[pid] = player

        challenger = locked[challenger_id]
        if challenger is None:
            flogger.error(f"Challenger not found: player_id={challenger_id}")
            raise ValueError(f"Challenger player with ID {challenger_id} not found.")

        target = locked[target_id]
        if target is None:
            flogger.error(f"Target not found: player_id={target_id}")
            raise ValueError(f"Target player with ID {target_id} not found.")

        # T7 over-cap lockout (LOOT_JOURNAL §5.5 C-3a): the CHALLENGER is "leaving
        # station" at challenge time, so gate them FIRST — before the credit /
        # duplicate-duel checks and before any duel row is created. The target is
        # gated separately at accept-time (they choose to leave then). Raises
        # OverCapError on a strict over-cap (load > cap); equip/unequip/buy not gated.
        await self._assert_under_cap(db, challenger)

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

        # Fast-fail on the unlocked stale object — avoids acquiring any lock for
        # clearly terminal duels (completed, expired, etc.).  This check is NOT
        # the idempotency guard; the authoritative guard is the re-read under lock
        # below.
        if duel.status != "pending":
            flogger.error(
                f"Invalid duel status for accept: duel_id={duel_id} status={duel.status} (expected 'pending')"
            )
            raise ValueError(f"Duel {duel_id} cannot be accepted — current status is {duel.status!r}.")

        # CONCURRENCY FIX (X3-duel): LOCK ORDERING — acquire the Duel row lock
        # FIRST, then Player rows in ascending player_id order.  This global
        # ordering (aggregate first, then players ascending) prevents AB-BA
        # deadlocks with other paths that also lock Player rows.
        #
        # populate_existing=True is MANDATORY because expire_on_commit=False means
        # the duel row is already in the session identity map from the unlocked
        # get_by_id above.  Without it, SQLAlchemy returns the cached stale object
        # and the status guard reads pre-commit state even though the lock was
        # acquired — the classic "lock looks correct, tests green" trap.
        duel = await self.duel_repo.get_by_id_for_update(db, duel_id)
        if duel is None:
            flogger.error(f"Duel disappeared between load and lock: duel_id={duel_id}")
            raise ValueError(f"Duel request with ID {duel_id} not found.")

        # Idempotency guard under lock: if a concurrent accept already completed
        # this duel, the second accept is a NO-OP rather than a double-payout.
        if duel.status != "pending":
            flogger.info(
                f"accept_duel idempotent NO-OP: duel_id={duel_id} status={duel.status!r} "
                f"(already resolved by a concurrent accept)"
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

        # T7 over-cap lockout (LOOT_JOURNAL §5.5 C-3a): the ACCEPTER (the target,
        # enforced by the router's target-only authorization) is "leaving station"
        # at accept time — gate them FIRST, before the credit re-validation and
        # before combat resolves. Raises OverCapError on a strict over-cap
        # (load > cap); the duel stays pending and no combat runs. The challenger
        # was already gated at challenge time. Equip/unequip/buy are NOT gated.
        await self._assert_under_cap(db, target)

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

        # Load per-guild config for combat-engine constant resolution (issue #70 A1).
        _duel_guild_cfg = await self.config_repo.get_by_guild_id(db, duel.guild_id)

        # CI-20: resolve display labels for combat-log thread naming
        _c1_label = await self._resolve_player_label(db, challenger)
        _c2_label = await self._resolve_player_label(db, target)

        # Resolve combat via TickResolver (T10: async, routes through persist + stat increment)
        fight_results = await self.combat_service.fight_ships(
            challenger_loadout,
            target_loadout,
            context="duel",
            log_result=True,
            pvc_damage_reduction=0.0,
            guild_config=_duel_guild_cfg,
            session=db,
            guild_id=duel.guild_id,
            combatant1_user_id=challenger.user_id,
            combatant2_user_id=target.user_id,
            combatant1_label=_c1_label,
            combatant2_label=_c2_label,
        )

        credits_transferred = 0

        if not fight_results.is_stalemate:
            # P2-T8a: Decode winner via winner_side (1 = challenger/loadout1, 2 = target/loadout2).
            # NEVER by ship name — ship names are presentation-only and are not unique within a guild.
            # winner_side == 1 → challenger passed as loadout1; winner_side == 2 → target passed as loadout2.
            if fight_results.winner_side == 1:
                winner, loser = challenger, target
            else:
                # winner_side == 2 (or unexpected None with is_stalemate=False — log and treat target as winner)
                if fight_results.winner_side != 2:
                    flogger.warning(
                        f"Duel {duel_id}: is_stalemate=False but winner_side={fight_results.winner_side!r} "
                        f"— expected 1 or 2; defaulting to target wins"
                    )
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
                flogger.warning(
                    f"cancel_underfunded_duels failed after duel {duel_id} "
                    f"resolution for loser={loser.id}: {_cancel_exc}"
                )

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

        P6-T4: replaces N×2 sequential player+user ``get_by_id`` calls with two
        batched ``WHERE id IN (...)`` fetches (one for players, one for users),
        capping the query count to 3 regardless of how many pending duels exist.

        Args:
            db: SQLAlchemy async session.
            guild_id: Guild the duels are scoped to.

        Returns:
            List of (DuelRequest, challenger_name, target_name) tuples.
            Either name is None if it cannot be resolved.
        """
        duels = await self.duel_repo.get_all_pending_by_guild(db, guild_id)
        if not duels:
            return []

        # Collect all player IDs we need to resolve in one shot.
        player_ids = list({duel.challenger_id for duel in duels} | {duel.target_id for duel in duels})
        try:
            players_list = await self.player_repo.get_by_ids(db, player_ids)
        except Exception as exc:  # defensive — lookup failures must never break autocomplete
            flogger.debug(f"Batch player lookup failed for guild {guild_id}: {exc}")
            players_list = []

        player_by_id: dict[int, object] = {p.id: p for p in players_list}

        # Collect all user_ids from resolved players.
        user_ids = list({p.user_id for p in players_list})
        try:
            users_list = await self.user_repo.get_by_ids(db, user_ids)
        except Exception as exc:  # defensive — lookup failures must never break autocomplete
            flogger.debug(f"Batch user lookup failed for guild {guild_id}: {exc}")
            users_list = []

        user_by_id: dict[int, object] = {u.id: u for u in users_list}

        def _resolve_name(player_id: int) -> str | None:
            player = player_by_id.get(player_id)
            if player is None:
                return None
            user = user_by_id.get(player.user_id)
            if user and user.discord_username:
                return user.discord_username
            return None

        return [(duel, _resolve_name(duel.challenger_id), _resolve_name(duel.target_id)) for duel in duels]

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
