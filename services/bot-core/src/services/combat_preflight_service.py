"""Combat preflight service — Monte-Carlo win-rate estimator.

Runs a small number of simulated fights between a player's current loadout and
representative criminals at a target tier, returning a verdict the gateway can
surface in the /promote confirmation embed.

Design notes
------------
- Designed to be **advisory only**. The verdict never blocks an action; it
  exists so a Bronze player promoting to Silver in a starter Betty isn't
  surprised by a mandatory-combat wall on first /check.
- 20 simulations per call by default — enough to distinguish a clear edge from
  a coin-flip without burning compute. The result is not deterministic; that's
  fine for advisory UI.
- The criminal sample is first built from real active bounties in the target tier
  for the guild. If none exist (e.g. fresh guild), the service synthesizes a
  small pool of criminals using BountyService.generate_loadout() so that the
  verdict is always actionable. The NO_DATA fallback is reserved only for
  genuine failures (e.g. synthesis itself errors out).

Verdict thresholds (per /promote design spec):
- 🟢 GREEN: player_win_rate >= 0.75
- 🔴 RED:   criminal_win_rate >= 0.75 (i.e. player_win_rate <= 0.25)
- 🟡 YELLOW: otherwise (the middle band)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum
from types import SimpleNamespace

from compute.combat_worker import run_fight_batch
from persist.repositories.bounty_repository import BountyRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession
from utils.offload import offload_cpu

from services.bounty_service import BountyService
from services.game_constants import GameConstants
from services.loadout_builder import LoadoutBuilder

flogger = bblogger.get_logger("combat-preflight-service")


class PreflightVerdict(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    NO_DATA = "no_data"


@dataclass(frozen=True)
class PreflightResult:
    verdict: PreflightVerdict
    player_win_rate: float  # 0.0-1.0
    criminal_win_rate: float  # 0.0-1.0
    sims_run: int
    target_tier: str  # canonical (e.g. "Silver")
    sample_size: int  # number of criminals sampled across the sims


class CombatPreflightService:
    """Estimate the player's likelihood of winning mandatory Silver+ combat.

    Public API
    ----------
    ``estimate(db, player_id, guild_id, target_tier, num_sims=20)`` — runs N
    simulated fights against criminals drawn from active bounties at
    ``target_tier`` (or synthesized from BountyService when none exist) and
    returns a ``PreflightResult``.
    """

    def __init__(self):
        self.bounty_repo = BountyRepository()

    async def _synthesize_criminals(
        self,
        db: AsyncSession,
        division: str,
        count: int = 5,
    ) -> list[object]:
        """Synthesize fake criminal bounty-like objects for a given division.

        Used when no active bounties exist at the target tier. Generates
        ``count`` loadouts via ``BountyService.generate_loadout()`` at random
        tech levels appropriate for the division.

        Args:
            db:       Async database session.
            division: Division name (e.g. "silver").
            count:    Number of synthetic criminals to generate.

        Returns:
            List of SimpleNamespace objects with a ``criminal_ship`` attribute
            (duck-typed like a ``Bounty`` record). Empty list if synthesis fails.
        """
        max_tl = GameConstants.DIVISION_MAX_TL.get(division, GameConstants.MAX_TECH_LEVEL)
        min_tl = GameConstants.MIN_TECH_LEVEL

        bounty_svc = BountyService()
        synthetics: list[object] = []
        for _ in range(count):
            tl = random.randint(min_tl, max_tl)
            try:
                loadout = await bounty_svc.generate_loadout(db, tl)
                synthetics.append(SimpleNamespace(criminal_ship=loadout))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"preflight: synthesis failed at tl={tl} division={division}: {exc}")

        flogger.info(
            f"preflight: synthesized {len(synthetics)}/{count} criminals for division={division} "
            f"(tl range {min_tl}-{max_tl})"
        )
        return synthetics

    async def estimate(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        guild_id: int,
        target_tier: str,
        num_sims: int = 20,
    ) -> PreflightResult:
        # Canonical -> lowercase division match for bounty filter.
        division = (target_tier or "").lower()
        active = await self.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)
        criminals = [b for b in active if b.criminal_ship]

        if not criminals:
            flogger.info(
                f"preflight: no active bounties with criminal_ship for guild={guild_id} "
                f"division={division} — synthesizing criminals"
            )
            criminals = await self._synthesize_criminals(db, division)
            if not criminals:
                flogger.warning(
                    f"preflight: synthesis returned empty for guild={guild_id} division={division} — NO_DATA"
                )
                return PreflightResult(
                    verdict=PreflightVerdict.NO_DATA,
                    player_win_rate=0.0,
                    criminal_win_rate=0.0,
                    sims_run=0,
                    target_tier=target_tier,
                    sample_size=0,
                )

        try:
            player_loadout = await LoadoutBuilder.from_player(db, player_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # This should not occur in production — active ship sale is blocked at service layer.
            flogger.error(
                f"preflight: could not build player loadout for player_id={player_id}: {exc} "
                f"(This should not occur in production — active ship sale is blocked at service layer.)"
            )
            return PreflightResult(
                verdict=PreflightVerdict.NO_DATA,
                player_win_rate=0.0,
                criminal_win_rate=0.0,
                sims_run=0,
                target_tier=target_tier,
                sample_size=0,
            )

        # P2-T7: pre-draw criminals (with replacement) and build matchup list,
        # then dispatch a SINGLE process-pool call for all num_sims fights.
        # The draw replicates the old per-sim random.choice(criminals) exactly —
        # same RNG object, same number of draws, same order.
        matchups: list[tuple] = []
        for _ in range(num_sims):
            bounty = random.choice(criminals)
            criminal_loadout = LoadoutBuilder.from_criminal_ship(bounty.criminal_ship or {})
            # seed=None matches the default-RNG behaviour of the old fight_ships path.
            matchups.append((player_loadout, criminal_loadout, None, "", ""))

        # ONE dispatch: all num_sims fights run inside a single worker process.
        # compact=True → each result is (winner_side, is_stalemate).
        # player = combatant1 = side 1; criminal = combatant2 = side 2.
        sim_results: list[tuple] = await offload_cpu(
            run_fight_batch,
            matchups,
            pvc_damage_reduction=GameConstants.PVC_DAMAGE_REDUCTION,
            compact=True,
        )

        player_wins = 0
        criminal_wins = 0
        for winner_side, is_stalemate in sim_results:
            # Stalemate counts as a player win (same semantics as the old
            # `fight.is_stalemate or fight.winner_name == player_loadout.ship_name`
            # check — stalemate was always a player win in that branch too).
            if is_stalemate or winner_side == 1:
                player_wins += 1
            else:
                criminal_wins += 1

        player_rate = player_wins / num_sims
        criminal_rate = criminal_wins / num_sims
        if player_rate >= 0.75:
            verdict = PreflightVerdict.GREEN
        elif criminal_rate >= 0.75:
            verdict = PreflightVerdict.RED
        else:
            verdict = PreflightVerdict.YELLOW

        return PreflightResult(
            verdict=verdict,
            player_win_rate=player_rate,
            criminal_win_rate=criminal_rate,
            sims_run=num_sims,
            target_tier=target_tier,
            sample_size=len(criminals),
        )
