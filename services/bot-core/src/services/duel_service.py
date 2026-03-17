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
from persist.repositories.duel_repository import DuelRepository
from persist.repositories.player_repository import PlayerRepository
from shared import bblogger

from services.combat_models import ShipLoadout
from services.combat_service import CombatService

flogger = bblogger.get_logger("duel-service")


class DuelService:
    """Service for the duel (PvP challenge) lifecycle."""

    def __init__(self) -> None:
        self.duel_repo = DuelRepository()
        self.player_repo = PlayerRepository()
        self.combat_service = CombatService()

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

        if challenger_id == target_id:
            flogger.warning(f"Self-duel attempt: player {challenger_id} tried to challenge themselves")
            raise ValueError("A player cannot challenge themselves to a duel.")

        if stakes < 0:
            flogger.warning(f"Invalid stakes attempted: {stakes} (must be non-negative)")
            raise ValueError(f"Stakes must be non-negative, got {stakes}.")

        # Fetch players
        challenger = await self.player_repo.get_by_id(db, challenger_id)
        if challenger is None:
            flogger.error(f"Challenger not found: player_id={challenger_id}")
            raise ValueError(f"Challenger player with ID {challenger_id} not found.")

        target = await self.player_repo.get_by_id(db, target_id)
        if target is None:
            flogger.error(f"Target not found: player_id={target_id}")
            raise ValueError(f"Target player with ID {target_id} not found.")

        # Validate credits
        if challenger.credits < stakes:
            flogger.warning(
                f"Challenger insufficient stakes: player_id={challenger_id} "
                f"has {challenger.credits} credits, needs {stakes}"
            )
            raise ValueError(
                f"Challenger has insufficient credits: "
                f"has {challenger.credits}, needs {stakes}."
            )
        if target.credits < stakes:
            flogger.warning(
                f"Target insufficient stakes: player_id={target_id} "
                f"has {target.credits} credits, needs {stakes}"
            )
            raise ValueError(
                f"Target has insufficient credits: "
                f"has {target.credits}, needs {stakes}."
            )

        # Prevent duplicate pending duels
        existing = await self.duel_repo.get_pending_by_players(
            db, challenger_id, target_id, guild_id
        )
        if existing is not None:
            flogger.warning(
                f"Duplicate duel attempt: duel_id={existing.id} already pending between "
                f"challenger_id={challenger_id} and target_id={target_id} in guild_id={guild_id}"
            )
            raise ValueError(
                f"A pending duel already exists between player {challenger_id} "
                f"and player {target_id} in guild {guild_id}."
            )

        # Create the duel request
        duel = DuelRequest(
            guild_id=guild_id,
            challenger_id=challenger_id,
            target_id=target_id,
            stakes=stakes,
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        created = await self.duel_repo.create(db, duel)
        flogger.info(
            f"Duel challenge created: id={created.id} "
            f"challenger={challenger_id} target={target_id} stakes={stakes}"
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
                f"Invalid duel status for accept: duel_id={duel_id} status={duel.status} "
                f"(expected 'pending')"
            )
            raise ValueError(
                f"Duel {duel_id} cannot be accepted — current status is {duel.status!r}."
            )

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

        if challenger.credits < stakes:
            flogger.warning(
                f"Challenger insufficient credits at accept-time: duel_id={duel_id} "
                f"player_id={challenger.id} has {challenger.credits}, needs {stakes}"
            )
            raise ValueError(
                f"Challenger has insufficient credits at accept-time: "
                f"has {challenger.credits}, needs {stakes}."
            )
        if target.credits < stakes:
            flogger.warning(
                f"Target insufficient credits at accept-time: duel_id={duel_id} "
                f"player_id={target.id} has {target.credits}, needs {stakes}"
            )
            raise ValueError(
                f"Target has insufficient credits at accept-time: "
                f"has {target.credits}, needs {stakes}."
            )

        # Build minimal ship loadouts
        challenger_loadout = self._build_loadout(challenger)
        target_loadout = self._build_loadout(target)

        # Resolve combat
        fight_results = self.combat_service.fight_ships(
            challenger_loadout, target_loadout
        )

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

            flogger.info(
                f"Duel {duel_id} resolved: winner player={winner.id} "
                f"stakes={stakes} transferred"
            )
        else:
            flogger.info(f"Duel {duel_id} ended in a stalemate — no credits transferred.")

        # Mark duel as completed
        await self.duel_repo.update_status(db, duel_id, "completed")

        return {
            "fight_results": fight_results,
            "challenger": challenger,
            "target": target,
            "stakes": stakes,
            "credits_transferred": credits_transferred,
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
                f"Invalid duel status for reject: duel_id={duel_id} status={duel.status} "
                f"(expected 'pending')"
            )
            raise ValueError(
                f"Duel {duel_id} cannot be rejected — current status is {duel.status!r}."
            )

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

    async def get_pending_for_target(
        self, db, target_id: int, guild_id: int
    ) -> list[DuelRequest]:
        """Return all pending duels where *target_id* is the target in the given guild.

        Used by the Discord gateway for autocomplete on /duel-accept and /duel-reject.

        Args:
            db: SQLAlchemy async session.
            target_id: Player ID of the potential acceptor.
            guild_id: Guild the duels are scoped to.

        Returns:
            List of pending DuelRequest objects.
        """
        return await self.duel_repo.get_pending_by_target(db, target_id, guild_id)

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
                f"Invalid duel status for expire: duel_id={duel_id} status={duel.status} "
                f"(expected 'pending')"
            )
            raise ValueError(
                f"Duel {duel_id} cannot be expired — current status is {duel.status!r}."
            )

        updated = await self.duel_repo.update_status(db, duel_id, "expired")
        flogger.info(f"Duel {duel_id} expired.")
        return updated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_loadout(self, player) -> ShipLoadout:
        """Build a minimal ShipLoadout from a player's active ship.

        For Phase 3, only ship_name and base_armour are used. Full loadout
        assembly with equipped weapons/modules is a Phase 4 concern.

        If the player has no active ship, a default unarmed loadout is used.

        Args:
            player: Player ORM instance.

        Returns:
            ShipLoadout with minimal fields populated.
        """
        if player.active_ship is not None:
            ship = player.active_ship
            ship_name = getattr(ship, "ship_name", None) or getattr(ship, "name", "Unknown")
            base_armour = getattr(ship, "armour", 100)
        else:
            ship_name = "Unarmed"
            base_armour = 100

        return ShipLoadout(
            ship_name=ship_name,
            base_armour=base_armour,
        )
