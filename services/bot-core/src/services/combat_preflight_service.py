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
- The criminal sample is built from real active bounties in the target tier
  for the guild. If none exist (rare, e.g. fresh guild), the service returns
  the ``NO_DATA`` verdict and the cog should suppress the panel rather than
  showing misleading 0% / 100% numbers.

Verdict thresholds (per /promote design spec):
- 🟢 GREEN: player_win_rate >= 0.75
- 🔴 RED:   criminal_win_rate >= 0.75 (i.e. player_win_rate <= 0.25)
- 🟡 YELLOW: otherwise (the middle band)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum

from persist.repositories.bounty_repository import BountyRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.combat_service import CombatService
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
    ``target_tier`` and returns a ``PreflightResult``.
    """

    def __init__(self):
        self.bounty_repo = BountyRepository()
        self.combat_service = CombatService()

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
            flogger.warning(
                f"preflight: could not build player loadout for player_id={player_id}: {exc}"
            )
            return PreflightResult(
                verdict=PreflightVerdict.NO_DATA,
                player_win_rate=0.0,
                criminal_win_rate=0.0,
                sims_run=0,
                target_tier=target_tier,
                sample_size=0,
            )

        player_wins = 0
        criminal_wins = 0
        for _ in range(num_sims):
            bounty = random.choice(criminals)
            criminal_loadout = LoadoutBuilder.from_criminal_ship(bounty.criminal_ship or {})
            fight = self.combat_service.fight_ships(player_loadout, criminal_loadout)
            if fight.is_stalemate or fight.winner_name == player_loadout.ship_name:
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
