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
- Each sim uses a freshly-synthesized criminal loadout (never the active-bounty
  pool). This guarantees ``num_sims`` distinct, independently-rolled opponents
  at the correct division tier. The NO_DATA fallback is reserved only for
  genuine synthesis failures.
- The sims run SEQUENTIALLY and carry the player's consumable state forward
  (``run_fight_batch(carry_side1_resources=True)``): secondary-weapon ammo and
  the one-use EmergencySystem deplete across the run, so the win rate measures
  *sustained* performance over a 20-hunt sequence rather than 20 independent
  fresh-resource fights. HP/shields/armour still reset each fight (between-hunt
  healing) and criminals are still freshly rolled per fight, so the only carried
  state is secondary ammo + EmergencySystem consumption. The verdict thresholds
  below are unchanged; they now describe a sustained run.

Verdict thresholds (per /promote design spec):
- GREEN:  player_win_rate >= 0.75
- RED:    criminal_win_rate >= 0.75 (i.e. player_win_rate <= 0.25)
- YELLOW: otherwise (the middle band)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum
from types import SimpleNamespace

from compute.combat_worker import run_fight_batch
from persist.repositories.config_repository import ConfigRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession
from utils.offload import offload_cpu

from services.bounty_service import BountyService
from services.combat_models import CombatTuning
from services.combat_service import _is_orm_model
from services.game_constants import GameConstants, resolve_flattened
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
    simulated fights, each against a freshly-synthesized criminal loadout at
    ``target_tier``, and returns a ``PreflightResult``.
    """

    async def _synthesize_criminals(
        self,
        db: AsyncSession,
        division: str,
        count: int = 5,
        guild_cfg: object | None = None,
    ) -> list[object]:
        """Synthesize fake criminal bounty-like objects for a given division.

        Used when no active bounties exist at the target tier. Generates
        ``count`` loadouts via ``BountyService.generate_loadout()`` at random
        tech levels appropriate for the division.

        Args:
            db:        Async database session.
            division:  Division name (e.g. "silver").
            count:     Number of synthetic criminals to generate.
            guild_cfg: Per-guild config row (or None) for max-TL override resolution.

        Returns:
            List of SimpleNamespace objects with a ``criminal_ship`` attribute
            (duck-typed like a ``Bounty`` record). Empty list if synthesis fails.
        """
        # Per-guild max-TL resolution (issue #70 unit A1).
        # Flat scalar (division_max_tl_<division>) wins over legacy JSONB dict fallback.
        max_tl = resolve_flattened(
            guild_cfg,
            f"division_max_tl_{division}",
            "division_max_tl",
            division,
            GameConstants.DIVISION_MAX_TL.get(division, GameConstants.MAX_TECH_LEVEL),
        )
        min_tl = GameConstants.MIN_TECH_LEVEL

        bounty_svc = BountyService()
        synthetics: list[object] = []
        for _ in range(count):
            tl = random.randint(min_tl, max_tl)
            try:
                loadout = await bounty_svc.generate_loadout(db, tl, division=division)
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
        # Canonical -> lowercase division for tier lookups.
        division = (target_tier or "").lower()

        # Load per-guild config for criminal TL cap and combat constant resolution.
        guild_cfg = await ConfigRepository().get_by_guild_id(db, guild_id)

        # Always synthesize: each sim gets a freshly-rolled, division-TL-capped criminal.
        criminals = await self._synthesize_criminals(db, division, count=num_sims, guild_cfg=guild_cfg)

        # Top-up: if synthesis returned fewer than num_sims (some generate_loadout calls
        # failed), attempt additional generations to reach num_sims distinct loadouts.
        if len(criminals) < num_sims:
            shortage = num_sims - len(criminals)
            extras = await self._synthesize_criminals(db, division, count=shortage, guild_cfg=guild_cfg)
            criminals.extend(extras)

        # Cap pool to num_sims so sample_size == sims actually run (top-up may over-produce).
        criminals = criminals[:num_sims]

        if not criminals:
            flogger.warning(f"preflight: synthesis returned empty for guild={guild_id} division={division} — NO_DATA")
            return PreflightResult(
                verdict=PreflightVerdict.NO_DATA,
                player_win_rate=0.0,
                criminal_win_rate=0.0,
                sims_run=0,
                target_tier=target_tier,
                sample_size=0,
            )

        # If still short after top-up, warn and fall back to modulo indexing below.
        if len(criminals) < num_sims:
            flogger.warning(
                f"preflight: synthesized only {len(criminals)}/{num_sims} criminals for "
                f"guild={guild_id} division={division} — degraded pool, using modulo pairing"
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

        # 1:1 pairing: each of the num_sims sims uses criminals[i] (modulo guard for
        # degraded-pool edge case — never raises IndexError).
        matchups: list[tuple] = []
        pool_size = len(criminals)
        for i in range(num_sims):
            bounty = criminals[i % pool_size]
            criminal_loadout = LoadoutBuilder.from_criminal_ship(bounty.criminal_ship or {})
            # seed=None matches the default-RNG behaviour of the old fight_ships path.
            matchups.append((player_loadout, criminal_loadout, None, "", ""))

        # Build per-guild tuning struct (all-scalar → picklable; None falls back to GameConstants).
        tuning = CombatTuning.from_guild_config(guild_cfg)

        # Resolve per-guild PvC DR (matches the bounties.py path).
        _pvc_dr = float(
            guild_cfg.pvc_damage_reduction
            if guild_cfg is not None and guild_cfg.pvc_damage_reduction is not None
            else GameConstants.PVC_DAMAGE_REDUCTION
        )

        # C1a-4 parity guard: ensure no live ORM model crosses the process boundary.
        assert not _is_orm_model(_pvc_dr), "estimate: pvc_damage_reduction must not be a live ORM model (C1a-4)"
        assert not _is_orm_model(tuning), "estimate: tuning must not be a live ORM model (C1a-4)"
        for _idx, _matchup in enumerate(matchups):
            for _pos, _elem in enumerate(_matchup):
                assert not _is_orm_model(_elem), (
                    f"estimate: matchup[{_idx}][{_pos}] must not be a live ORM model — "
                    "extract scalar fields before offload (C1a-4)"
                )

        # ONE dispatch: all num_sims fights run inside a single worker process.
        # compact=True → each result is (winner_side, is_stalemate).
        # player = combatant1 = side 1; criminal = combatant2 = side 2.
        # carry_side1_resources=True → the fights run sequentially and the player's
        # secondary ammo + one-use EmergencySystem deplete across the run, so the
        # win rate reflects sustained readiness rather than 20 fresh-resource fights.
        sim_results: list[tuple] = await offload_cpu(
            run_fight_batch,
            matchups,
            pvc_damage_reduction=_pvc_dr,
            compact=True,
            carry_side1_resources=True,
            tuning=tuning,
        )

        player_wins = 0
        criminal_wins = 0
        for winner_side, is_stalemate in sim_results:
            # Stalemate mirrors the real PvC outcome (spec §9: criminal escapes —
            # same path as a loss), so it counts toward the criminal side.
            if winner_side == 1 and not is_stalemate:
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
