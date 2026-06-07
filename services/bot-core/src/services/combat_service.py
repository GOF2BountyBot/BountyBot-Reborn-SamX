"""
Combat Service for BountyBot.

Implements the tick-based Phase-1 combat resolver (TickResolver) plus
legacy-compatible stat collection helpers (get_dps, get_armour, get_shield).

T10: fight_ships is now async and routes exclusively through TickResolver.
SimpleTTKResolver and variance helpers are retired.

P2-T2: fight_ships routes through offload_cpu(run_fight, ...) to run combat
in a process-pool worker, keeping the event loop free. The worker returns a
plain-dict result; fight_ships re-hydrates it into a full FightResults with a
list[CombatEvent] combat_log so that all downstream code (persist, stat
increments, duel decode) is byte-identical to the pre-offload path.
P2-T6 will remove the re-hydration round-trip by making persist a passthrough.
"""

from typing import TYPE_CHECKING

from compute.combat_worker import run_fight
from shared import bblogger
from utils.offload import offload_cpu

from services.combat_models import (
    CombatEvent,
    CombatEventType,
    CombatMeta,
    CombatStats,
    FightResults,
    FightStats,
    ShipLoadout,
)
from services.combat_resolver import TickResolver

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger(__name__)


def _is_orm_model(obj: object) -> bool:
    """Return True if *obj* is a live SQLAlchemy ORM model instance.

    Used by fight_ships to guard the offload boundary (C1a-4): ORM rows carry
    lazy-load proxies that are not picklable and must never cross a process
    boundary.  The check is duck-typed (no SQLAlchemy import at call time)
    so it remains safe in forkserver child processes.
    """
    if obj is None:
        return False
    cls = type(obj)
    # SQLAlchemy mapped classes carry a '__mapper__' attribute set by the
    # mapper registry at class-definition time.  Plain dataclasses, Pydantic
    # models, and primitive values do not have it.
    return hasattr(cls, "__mapper__")


# ---------------------------------------------------------------------------
# CombatService — stat collection + fight orchestration (T10: async, TickResolver)
# ---------------------------------------------------------------------------


class CombatService:
    """Service for ship combat stat computation and fight resolution.

    T10: fight_ships now routes exclusively through TickResolver (async).
    SimpleTTKResolver is retired. Per-fight DB persistence and Player stat
    increments happen inside fight_ships when log_result=True.

    Usage:
        service = CombatService()
        result = await service.fight_ships(
            loadout1, loadout2,
            context="duel", log_result=True, pvc_damage_reduction=0.0,
            session=db,
            combatant1_user_id=player1.user_id,
            combatant2_user_id=None,
            guild_id=guild_id,
        )
    """

    def __init__(self) -> None:
        """Initialize CombatService with the tick-based resolver."""
        self._tick_resolver = TickResolver()
        flogger.debug("CombatService initialized with TickResolver")

    # ------------------------------------------------------------------
    # Stat Collection — Legacy-compatible formulas
    # ------------------------------------------------------------------

    @staticmethod
    def get_dps(loadout: ShipLoadout) -> float:
        """Calculate total effective DPS for a ship loadout.

        Formula (from legacy shipBase.py:456-467):
            totalDPS = (sum(weapon.dps) + sum(turret.dps) + sum(module.dps))
                       * product(module.dps_multiplier)

        Module DPS multipliers stack multiplicatively (e.g. two x1.2
        modules = x1.44 total).

        Args:
            loadout: Ship loadout with weapons, turrets, and modules.

        Returns:
            Total effective DPS as float.
        """
        flogger.debug(f"Calculating DPS for {loadout.ship_name}")
        total = 0.0
        multiplier = 1.0

        for weapon in loadout.weapons:
            total += weapon.dps
            flogger.trace(f"DPS calc: added weapon {weapon.name} dps={weapon.dps}, cumulative_total={total}")

        for turret in loadout.turrets:
            total += turret.dps
            flogger.trace(f"DPS calc: added turret {turret.name} dps={turret.dps}, cumulative_total={total}")

        for module in loadout.modules:
            total += module.dps
            multiplier *= module.dps_multiplier
            flogger.trace(
                f"DPS calc: added module {module.name} dps={module.dps}, dps_mult={module.dps_multiplier}, "
                f"cumulative_total={total}, cumulative_multiplier={multiplier}"
            )

        final_dps = total * multiplier
        flogger.debug(
            f"DPS calculation complete for {loadout.ship_name}: "
            f"base_total={total}, multiplier={multiplier}, final_dps={final_dps:.1f}"
        )
        return final_dps

    @staticmethod
    def get_armour(loadout: ShipLoadout) -> int:
        """Calculate total effective armour for a ship loadout.

        Formula (from legacy shipBase.py:491-512):
            totalArmour = int(
                (baseArmour + sum(module.armour) + sum(upgrade.armour))
                * product(module.armour_multiplier)
                * product(upgrade.armour_multiplier)
            )

        Args:
            loadout: Ship loadout with base armour, modules, and upgrades.

        Returns:
            Total effective armour as int (truncated).
        """
        flogger.debug(f"Calculating armour for {loadout.ship_name}")
        total = loadout.base_armour
        multiplier = 1.0
        flogger.trace(f"Armour calc: base_armour={total}")

        for module in loadout.modules:
            total += module.armour
            multiplier *= module.armour_multiplier
            flogger.trace(
                f"Armour calc: added module {module.name} armour={module.armour}, "
                f"armour_mult={module.armour_multiplier}, cumulative_total={total}, cumulative_multiplier={multiplier}"
            )

        for upgrade in loadout.upgrades:
            total += upgrade.armour
            multiplier *= upgrade.armour_multiplier
            flogger.trace(
                f"Armour calc: added upgrade {upgrade.name} armour={upgrade.armour}, "
                f"armour_mult={upgrade.armour_multiplier}, cumulative_total={total}, cumulative_multiplier={multiplier}"
            )

        final_armour = int(total * multiplier)
        flogger.debug(
            f"Armour calculation complete for {loadout.ship_name}: "
            f"base_total={total}, multiplier={multiplier}, final_armour={final_armour}"
        )
        return final_armour

    @staticmethod
    def get_shield(loadout: ShipLoadout) -> int:
        """Calculate total effective shield for a ship loadout.

        Formula (from legacy shipBase.py:470-488):
            totalShield = int(sum(module.shield) * product(module.shield_multiplier))

        Ships have no intrinsic shield. All shield HP comes from modules.

        Args:
            loadout: Ship loadout with modules.

        Returns:
            Total effective shield as int (truncated).
        """
        flogger.debug(f"Calculating shield for {loadout.ship_name}")
        total = 0
        multiplier = 1.0
        flogger.trace("Shield calc: starting with total=0 (no base shield)")

        for module in loadout.modules:
            total += module.shield
            multiplier *= module.shield_multiplier
            flogger.trace(
                f"Shield calc: added module {module.name} shield={module.shield}, "
                f"shield_mult={module.shield_multiplier}, cumulative_total={total}, cumulative_multiplier={multiplier}"
            )

        final_shield = int(total * multiplier)
        flogger.debug(
            f"Shield calculation complete for {loadout.ship_name}: "
            f"base_total={total}, multiplier={multiplier}, final_shield={final_shield}"
        )
        return final_shield

    def collect_stats(self, loadout: ShipLoadout) -> CombatStats:
        """Compute all combat statistics for a ship loadout.

        Combines get_dps, get_armour, and get_shield into a single
        CombatStats object. total_hp = armour + shield.

        Args:
            loadout: Complete ship loadout.

        Returns:
            CombatStats with all computed values.
        """
        flogger.debug(f"Stat collection started for {loadout.ship_name}")
        dps = self.get_dps(loadout)
        armour = self.get_armour(loadout)
        shield = self.get_shield(loadout)
        total_hp = armour + shield

        flogger.debug(
            f"Ship stats: {loadout.ship_name} dps={dps:.1f} armour={armour} shield={shield} total_hp={total_hp}"
        )
        flogger.trace(f"Accuracy: {loadout.base_accuracy}, Evasion: {loadout.base_evasion}")

        return CombatStats(
            ship_name=loadout.ship_name,
            dps=dps,
            armour=armour,
            shield=shield,
            total_hp=total_hp,
            accuracy=loadout.base_accuracy,
            evasion=loadout.base_evasion,
        )

    # ------------------------------------------------------------------
    # Fight Resolution (T10: async, routes through TickResolver)
    # ------------------------------------------------------------------

    async def fight_ships(
        self,
        loadout1: ShipLoadout,
        loadout2: ShipLoadout,
        *,
        context: str | None = None,
        log_result: bool = True,
        pvc_damage_reduction: float = 0.0,
        guild_config=None,
        # DB context required when log_result=True
        session: "AsyncSession | None" = None,
        guild_id: int | None = None,
        combatant1_user_id: int | None = None,
        combatant2_user_id: int | None = None,
        # CI-20: display labels for thread naming / dropdown
        combatant1_label: str = "",
        combatant2_label: str = "",
    ) -> FightResults:
        """Simulate a fight between two ship loadouts via TickResolver.

        Args:
            loadout1: C1 — challenger (player in PvC when pvc_damage_reduction > 0).
            loadout2: C2 — opponent (NPC in PvC; player2 in PvP).
            context: Fight context string ("duel"|"bounty_pvc"|"bounty_bonus").
                     Required when log_result=True.
            log_result: When True, persist the combat_log row and update Player stats.
                        When False (preflight Monte-Carlo), no DB writes are made.
            pvc_damage_reduction: Keith T. Maxwell DR (§3). 0.33 for PvC, 0.0 for PvP.
            guild_config: Per-guild config for constant overrides (reserved).
            session: Async SQLAlchemy session — required when log_result=True.
            guild_id: Guild ID for combat_log.guild_id — required when log_result=True.
            combatant1_user_id: Discord user_id for C1 (None = NPC).
            combatant2_user_id: Discord user_id for C2 (None = NPC).

        Returns:
            FightResults with combat_log timeline, metadata, and combat_log_id.

        Raises:
            ValueError: if log_result=True and context is None.
        """
        if log_result and context is None:
            raise ValueError("fight_ships: context is required when log_result=True")

        flogger.debug(
            f"fight_ships: {loadout1.ship_name} vs {loadout2.ship_name} "
            f"context={context!r} log_result={log_result} pvc_dr={pvc_damage_reduction}"
        )

        # C1a-4: guard — guild_config MUST NOT cross the process boundary as an ORM row.
        # Extract any needed scalar fields before offload; pass None for now (reserved).
        # If guild_config were an SQLAlchemy model its lazy-load proxies are not picklable.
        assert not _is_orm_model(guild_config), (
            "fight_ships: guild_config must not be a live ORM model — extract scalar fields before offload (C1a-4)"
        )

        # P2-T2: run the tick resolver in a process-pool worker via offload_cpu.
        # seed=None matches current default-RNG behaviour (non-deterministic production).
        # compact=False → full result dict with timeline, summary, metadata, stats.
        raw = await offload_cpu(
            run_fight,
            loadout1,
            loadout2,
            pvc_damage_reduction=pvc_damage_reduction,
            seed=None,
            combatant1_label=combatant1_label,
            combatant2_label=combatant2_label,
            compact=False,
        )

        # Re-hydrate the worker's list[dict] timeline back into list[CombatEvent] so that
        # CombatLogService.persist's existing dataclasses.asdict loop, the post-fight stat-
        # increment scans, and the duel decode all work UNCHANGED — persisted output is
        # byte-identical to pre-offload.  P2-T6 will remove this round-trip by making
        # persist a dict-passthrough.
        combat_log: list[CombatEvent] = [
            CombatEvent(
                tick=ev["tick"],
                type=ev["type"],
                actor=ev["actor"],
                target=ev["target"],
                data=ev["data"],
            )
            for ev in raw["timeline"]
        ]

        # Reconstruct FightStats from the plain-dict slices returned by the worker.
        def _stats_from_dict(d: dict) -> FightStats:
            return FightStats(
                ship_name=d["ship_name"],
                raw_hp=d["raw_hp"],
                raw_dps=d["raw_dps"],
                varied_hp=d["varied_hp"],
                varied_dps=d["varied_dps"],
                ttk=d["ttk"],
            )

        fight_results = FightResults(
            winner_name=raw["winner_name"],
            loser_name=raw["loser_name"],
            is_stalemate=raw["is_stalemate"],
            ship1_stats=_stats_from_dict(raw["ship1_stats"]),
            ship2_stats=_stats_from_dict(raw["ship2_stats"]),
            winner_side=raw["winner_side"],
            combat_log=combat_log,  # type: ignore[arg-type]  — list[CombatEvent], annotation is list[dict]
            metadata=raw["metadata"],
        )

        if not log_result:
            # Preflight / simulation path — no DB writes
            flogger.debug(
                f"fight_ships (log_result=False): winner={fight_results.winner_name} "
                f"stalemate={fight_results.is_stalemate}"
            )
            return fight_results

        # ------------------------------------------------------------------ #
        # log_result=True path: persist + Player stat increments             #
        # ------------------------------------------------------------------ #
        if session is None:
            raise ValueError("fight_ships: session is required when log_result=True")
        if guild_id is None:
            raise ValueError("fight_ships: guild_id is required when log_result=True")

        # Annotate metadata with combatant user_ids so CombatLogService can project them
        fight_results.metadata["combatant_user_ids"] = {
            "c1": combatant1_user_id,
            "c2": combatant2_user_id,
        }

        # Persist combat_log row
        from services.combat_log_service import CombatLogService  # deferred to avoid circular import

        combat_log_svc = CombatLogService()
        meta = CombatMeta(guild_id=guild_id)
        combat_log_id = await combat_log_svc.persist(meta, fight_results, context, session)

        # Rebuild FightResults with combat_log_id populated (frozen dataclass — replace)
        fight_results = FightResults(
            winner_name=fight_results.winner_name,
            loser_name=fight_results.loser_name,
            is_stalemate=fight_results.is_stalemate,
            ship1_stats=fight_results.ship1_stats,
            ship2_stats=fight_results.ship2_stats,
            winner_side=fight_results.winner_side,  # P2-T0b: carry through rebuild
            combat_log_id=combat_log_id,
            combat_log=fight_results.combat_log,
            metadata=fight_results.metadata,
        )

        # Player stat increments (§13) — one per HUMAN combatant
        await self._increment_player_stats(
            session=session,
            fight_results=fight_results,
            combatant1_user_id=combatant1_user_id,
            combatant2_user_id=combatant2_user_id,
            guild_id=guild_id,
        )

        # CI-16: secondary ammo write-back — must be AFTER _increment_player_stats,
        # inside log_result=True branch (sim guard returns at ~line 2137 before this)
        await self._consume_secondary_ammo(
            session=session,
            fight_results=fight_results,
            combatant1_user_id=combatant1_user_id,
            combatant2_user_id=combatant2_user_id,
            guild_id=guild_id,
        )

        flogger.info(
            f"fight_ships: persisted combat_log_id={combat_log_id} "
            f"winner={fight_results.winner_name} stalemate={fight_results.is_stalemate}"
        )
        return fight_results

    async def _increment_player_stats(
        self,
        *,
        session: "AsyncSession",
        fight_results: FightResults,
        combatant1_user_id: int | None,
        combatant2_user_id: int | None,
        guild_id: int,
    ) -> None:
        """Increment Player combat-stat counters for human combatants (§13).

        total_fights += 1 always.
        total_nukes_fired += count of weapon_fire/nuke events for this combatant.
        total_module_activations += count of module_activation events for this combatant.

        NPC side (user_id is None): skip cleanly, no DB call.
        """
        from persist.repositories.player_repository import PlayerRepository

        player_repo = PlayerRepository()

        summary = fight_results.metadata.get("summary", {})
        combatants_summary = summary.get("combatants", {})

        # Map slot key → user_id
        slot_map: list[tuple[str, int | None]] = [
            ("1", combatant1_user_id),
            ("2", combatant2_user_id),
        ]

        for slot_key, user_id in slot_map:
            if user_id is None:
                continue  # NPC side — skip cleanly

            cb_block = combatants_summary.get(slot_key, {})

            # Count nuke fires for this combatant from the event timeline
            combatant_name = cb_block.get("name", "")
            nukes_fired = 0
            module_activations = 0
            for ev in fight_results.combat_log:
                # combat_log holds CombatEvent dataclass objects (not dicts); read attributes
                if hasattr(ev, "type") and hasattr(ev, "actor"):
                    ev_actor = ev.actor
                    ev_type = ev.type
                    ev_data = ev.data if hasattr(ev, "data") else {}
                else:
                    # Fallback if somehow a dict slipped through
                    ev_actor = ev.get("actor") if isinstance(ev, dict) else None
                    ev_type = ev.get("type") if isinstance(ev, dict) else None
                    ev_data = ev.get("data", {}) if isinstance(ev, dict) else {}

                if ev_actor != combatant_name:
                    continue
                if ev_type == CombatEventType.weapon_fire:
                    if ev_data.get("subtype") == "nuke":
                        nukes_fired += 1
                elif ev_type == CombatEventType.module_activation:
                    module_activations += 1

            try:
                player = await player_repo.get_by_user_and_guild(session, user_id, guild_id)
                if player is None:
                    flogger.warning(
                        f"fight_ships stat increment: player not found user_id={user_id} guild_id={guild_id} — skipping"
                    )
                    continue
                player.total_fights += 1
                player.total_nukes_fired += nukes_fired
                player.total_module_activations += module_activations
                await session.flush()
                flogger.debug(
                    f"Player stats incremented: user_id={user_id} guild_id={guild_id} "
                    f"total_fights={player.total_fights} nukes_fired+={nukes_fired} "
                    f"module_activations+={module_activations}"
                )
            except Exception as exc:
                # Non-fatal — stat increment failure should not abort the fight
                flogger.error(f"Player stat increment failed: user_id={user_id} guild_id={guild_id}: {exc}")

    async def _consume_secondary_ammo(
        self,
        *,
        session: "AsyncSession",
        fight_results: FightResults,
        combatant1_user_id: int | None,
        combatant2_user_id: int | None,
        guild_id: int,
    ) -> None:
        """Write back per-secondary ammo consumption for human combatants (CI-16).

        Scans the combat_log timeline for weapon_fire events (slot=secondary) per human
        combatant, counts rounds fired per weapon name, decrements secondary_ammo on the
        player's active ship, and auto-unequips (removes name + ammo key) if rounds reach 0.

        Criminal side (user_id is None): skip — no cross-fight persistence for NPCs (CI-17 deferred).
        Mirrors the non-fatal try/except style of _increment_player_stats.
        """
        from persist.repositories.player_repository import PlayerRepository
        from persist.repositories.player_ship_repository import PlayerShipRepository

        player_repo = PlayerRepository()
        player_ship_repo = PlayerShipRepository()

        summary = fight_results.metadata.get("summary", {})
        combatants_summary = summary.get("combatants", {})

        slot_map: list[tuple[str, int | None]] = [
            ("1", combatant1_user_id),
            ("2", combatant2_user_id),
        ]

        for slot_key, user_id in slot_map:
            if user_id is None:
                continue  # NPC side — no cross-fight ammo persistence

            cb_block = combatants_summary.get(slot_key, {})
            combatant_name = cb_block.get("name", "")

            # Count rounds fired per weapon name from combat_log timeline
            rounds_fired: dict[str, int] = {}
            for ev in fight_results.combat_log:
                # combat_log holds CombatEvent dataclass objects — use attribute access
                if hasattr(ev, "type") and hasattr(ev, "actor"):
                    ev_actor = ev.actor
                    ev_type = ev.type
                    ev_data = ev.data if hasattr(ev, "data") else {}
                else:
                    ev_actor = ev.get("actor") if isinstance(ev, dict) else None
                    ev_type = ev.get("type") if isinstance(ev, dict) else None
                    ev_data = ev.get("data", {}) if isinstance(ev, dict) else {}

                if ev_actor != combatant_name:
                    continue
                if ev_type == CombatEventType.weapon_fire and ev_data.get("slot") == "secondary":
                    w_name = ev_data.get("weapon", "")
                    if w_name:
                        rounds_fired[w_name] = rounds_fired.get(w_name, 0) + 1

            if not rounds_fired:
                continue  # no secondary fires — nothing to write back

            try:
                player = await player_repo.get_by_user_and_guild(session, user_id, guild_id)
                if player is None:
                    flogger.warning(
                        f"_consume_secondary_ammo: player not found user_id={user_id} guild_id={guild_id} — skipping"
                    )
                    continue

                ship = await player_ship_repo.get_active_ship(session, player.id)
                if ship is None:
                    flogger.warning(f"_consume_secondary_ammo: no active ship for player_id={player.id} — skipping")
                    continue

                # Read current ammo dict; never mutate in-place (JSON SQLAlchemy gotcha — must reassign)
                ammo: dict[str, int] = dict(ship.secondary_ammo or {})
                sw_names: list[str] = list(ship.secondary_weapons or [])

                for w_name, fired in rounds_fired.items():
                    if w_name not in ammo:
                        flogger.debug(
                            f"_consume_secondary_ammo: weapon {w_name!r} not in ammo dict for ship {ship.id} — skip"
                        )
                        continue
                    new_qty = max(0, ammo[w_name] - fired)
                    ammo[w_name] = new_qty
                    if new_qty == 0:
                        # Auto-unequip: remove name from secondary_weapons and del from ammo
                        sw_names = [n for n in sw_names if n != w_name]
                        del ammo[w_name]
                        flogger.info(
                            f"_consume_secondary_ammo: {w_name!r} depleted — auto-unequipped "
                            f"from ship {ship.id} (player_id={player.id})"
                        )

                # Reassign both JSON columns (never in-place mutation — SQLAlchemy JSON gotcha)
                ship.secondary_ammo = ammo
                ship.secondary_weapons = sw_names
                await session.flush()

                flogger.debug(
                    f"_consume_secondary_ammo: ship {ship.id} ammo updated: {ammo!r}, secondary_weapons={sw_names!r}"
                )

            except Exception as exc:
                # Non-fatal — ammo write-back failure should not abort the fight record
                flogger.error(f"_consume_secondary_ammo failed: user_id={user_id} guild_id={guild_id}: {exc}")
