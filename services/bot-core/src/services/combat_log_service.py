"""CombatLogService — persists resolved fight records to combat_log (§12 / T10).

Receives the in-memory FightResults from the TickResolver and writes one
combat_log row per resolved fight. Serialises the CombatEvent timeline via
dataclasses.asdict() before JSON storage — raw object serialisation would fail.

The created_at column has no server_default (migration 0011); every insert MUST
go through the ORM so the application-side default supplies the value.

Read path (added for the /combat-log Discord command):
  - list_for_player()  — guild-scoped fight list with POV outcomes + ordinals
  - get_detail()        — ownership-gated full detail with key-event extraction
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict

from persist.models.combat_log import CombatLog
from persist.repositories.combat_log_repository import CombatLogRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.combat_models import CombatMeta, FightResults

flogger = bblogger.get_logger("combat-log-service")

_VALID_CONTEXTS: frozenset[str] = frozenset({"duel", "bounty_pvc", "bounty_bonus"})

# Tick duration used when persisting (10 ms per tick — from GameConstants default).
# Key-event time conversion uses this value.
_TICK_MS: int = 10

# Secondary weapon subtypes that count as "notable" fires for key-events.
# CI-16/CI-13: added "ionizing-missile" (was missing; it fires and should log); "emp-bomb" is deferred but kept.
_SECONDARY_SUBTYPES: frozenset[str] = frozenset(
    {"rocket", "missile", "cluster-missile", "nuke", "shock-blast", "emp-bomb", "ionizing-missile"}
)

# Module types that count as "notable" activations.
_NOTABLE_MODULE_TYPES: frozenset[str] = frozenset(
    {"CloakModule", "BoosterModule", "ThrusterModule", "EmergencySystemModule"}
)

# HP layer labels (used for layer_depleted event detail)
# CI-15: "hull" added to match the new layer_depleted/hull event emitted by _apply_damage.
_LAYER_LABELS: dict[str, str] = {
    "shield": "Shield depleted",
    "armour": "Armour depleted",
    "hull": "Hull depleted (dead)",
}


def _ticks_to_seconds(tick: int, tick_ms: int = _TICK_MS) -> float:
    """Convert a tick number to elapsed seconds."""
    return round(tick * tick_ms / 1000, 3)


class CombatLogService:
    """Persist resolved fight records and enforce §12 invariants."""

    def __init__(self) -> None:
        self._repo = CombatLogRepository()

    async def persist(
        self,
        combat_meta: CombatMeta,
        fight_results: FightResults,
        context: str,
        session: AsyncSession,
    ) -> int:
        """Persist a FightResults to combat_log and return the new row id.

        Args:
            combat_meta:   Caller context (guild_id).
            fight_results: Tick-resolver output (§12 — metadata carries projected columns).
            context:       Fight context; must be one of {"duel","bounty_pvc","bounty_bonus"}.
            session:       Async SQLAlchemy session (caller owns transaction context).

        Returns:
            The new combat_log.id for the caller to store on FightResults.combat_log_id.

        Raises:
            ValueError: if context is invalid or the NPC-vs-NPC invariant is violated.
        """
        if context not in _VALID_CONTEXTS:
            raise ValueError(f"Invalid combat context {context!r}. Must be one of {sorted(_VALID_CONTEXTS)}.")

        metadata = fight_results.metadata
        summary = metadata.get("summary", {})
        combatants_summary = summary.get("combatants", {})

        # Project columns from the §12-shaped metadata (T9 emits these)
        c1_block = combatants_summary.get("1", {})
        c2_block = combatants_summary.get("2", {})

        combatant1_name: str = c1_block.get("name", "")
        combatant2_name: str = c2_block.get("name", "")
        winner_name: str | None = fight_results.winner_name

        # combatant user_ids — sourced from metadata.combatant_user_ids (set by fight_ships caller)
        combatant_ids = metadata.get("combatant_user_ids", {})
        combatant1_user_id: int | None = combatant_ids.get("c1")
        combatant2_user_id: int | None = combatant_ids.get("c2")

        # NPC-vs-NPC invariant (§12) — validated before any DB call
        if combatant1_user_id is None and combatant2_user_id is None:
            raise ValueError(
                "NPC invariant violated: both combatant1_user_id and combatant2_user_id are NULL. "
                "At least one combatant must be a real player (§12)."
            )

        # Serialize the timeline — CombatEvent dataclass objects → plain dicts
        # Raw json.dumps over dataclass objects fails; dataclasses.asdict() converts nested DCs too.
        raw_timeline = fight_results.combat_log
        serialised_timeline: list[dict] = [
            dataclasses.asdict(ev) if dataclasses.is_dataclass(ev) and not isinstance(ev, type) else ev
            for ev in raw_timeline
        ]

        # Build the data blob (§12 "data column — internal schema")
        data_blob: dict = {
            "schema_version": metadata.get("schema_version", 1),
            "summary": summary,
            "timeline": serialised_timeline,
            "metadata": metadata.get("metadata", {}),
        }

        row = CombatLog(
            guild_id=combat_meta.guild_id,
            context=context,
            combatant1_name=combatant1_name,
            combatant2_name=combatant2_name,
            combatant1_user_id=combatant1_user_id,
            combatant2_user_id=combatant2_user_id,
            winner_name=winner_name,
            is_stalemate=fight_results.is_stalemate,
            data=data_blob,
            # created_at supplied by ORM application-side default (no server_default — migration 0011)
        )

        try:
            persisted = await self._repo.add(session, row)
            flogger.info(
                f"CombatLog persisted: id={persisted.id} context={context!r} "
                f"guild_id={combat_meta.guild_id} "
                f"c1={combatant1_name!r}(uid={combatant1_user_id}) "
                f"c2={combatant2_name!r}(uid={combatant2_user_id}) "
                f"winner={winner_name!r}"
            )
            return persisted.id
        except Exception as exc:
            flogger.error(f"CombatLog persist failed: context={context!r} guild={combat_meta.guild_id}: {exc}")
            await session.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Read path — /combat-log Discord command                              #
    # ------------------------------------------------------------------ #

    async def list_for_player(
        self,
        db: AsyncSession,
        user_id: int,
        guild_id: int,
        limit: int = 25,
    ) -> list[dict]:
        """Return lightweight fight summaries for a player in a guild.

        Each dict has:
            id, guild_id, context, opponent_name, outcome, created_at, ordinal

        outcome is from the requesting user's POV ("won" / "lost" / "stalemate").
        ordinal disambiguates multiple fights vs. the same opponent on the same calendar day
        (most-recent = highest ordinal within the day-opponent group).

        NPC fights are included (opponent_name = the NPC ship name, outcome from user's POV).
        """
        rows = await self._repo.list_for_player(db, user_id, limit=limit, guild_id=guild_id)

        result: list[dict] = []
        for row in rows:
            opponent_name, outcome = self._pov_outcome(row, user_id)
            # CI-20: include both combatant names so the cog can render "X vs Y" format
            result.append(
                {
                    "id": row.id,
                    "guild_id": row.guild_id,
                    "context": row.context,
                    "opponent_name": opponent_name,
                    "combatant1_name": row.combatant1_name,
                    "combatant2_name": row.combatant2_name,
                    "outcome": outcome,
                    "created_at": row.created_at,
                    "ordinal": 1,  # filled in below
                }
            )

        # Compute ordinals: group by (opponent_name, date), sort ascending → assign 1..N
        # Most-recent row within a group gets the highest ordinal.
        _group_seen: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
        for i, item in enumerate(result):
            day = item["created_at"].date().isoformat()
            key = (item["opponent_name"], day)
            _group_seen[key].append(i)

        for indices in _group_seen.values():
            if len(indices) > 1:
                # indices are already newest-first (list_for_player orders desc).
                # Assign ordinals N..1 (newest = N, oldest = 1).
                for rank, idx in enumerate(reversed(indices), start=1):
                    result[idx]["ordinal"] = rank
            # If only one entry for this group, ordinal stays 1 (default).

        flogger.info(f"list_for_player: user_id={user_id} guild_id={guild_id} found={len(result)}")
        return result

    async def get_detail(
        self,
        db: AsyncSession,
        battle_id: int,
        user_id: int,
    ) -> dict:
        """Return full combat detail for one battle.

        Raises:
            KeyError: if the battle does not exist OR the user is not a combatant
                      (both map to 404 — never leak existence).

        Returns a dict suitable for CombatLogDetail schema validation:
            id, guild_id, context, combatant1_name, combatant2_name,
            combatant1_user_id, combatant2_user_id, winner_name, is_stalemate,
            created_at, outcome, combatant1, combatant2, duration_ticks,
            duration_s, pvc_damage_reduction, key_events
        """
        row = await self._repo.get_by_id(db, battle_id)

        # Ownership gate — return the same KeyError for "not found" and "not a combatant"
        # so callers render 404 in both cases (don't leak existence).
        if row is None or (row.combatant1_user_id != user_id and row.combatant2_user_id != user_id):
            raise KeyError(f"combat_log id={battle_id} not found or user_id={user_id} not a combatant")

        _opponent_name, outcome = self._pov_outcome(row, user_id)

        data = row.data or {}
        summary = data.get("summary", {})
        combatants_map = summary.get("combatants", {})
        metadata = data.get("metadata", {})
        timeline = data.get("timeline", [])

        tick_ms = int(metadata.get("tick_ms", _TICK_MS))
        duration_ticks = int(summary.get("duration_ticks", 0))
        pvc_dr = float(metadata.get("pvc_damage_reduction", 0.0) or 0.0)

        c1 = self._parse_combatant(combatants_map.get("1", {}))
        c2 = self._parse_combatant(combatants_map.get("2", {}))

        key_events = self._extract_key_events(timeline, tick_ms, combatants_map=combatants_map)

        flogger.info(f"get_detail: battle_id={battle_id} user_id={user_id} outcome={outcome!r}")
        return {
            "id": row.id,
            "guild_id": row.guild_id,
            "context": row.context,
            "combatant1_name": row.combatant1_name,
            "combatant2_name": row.combatant2_name,
            "combatant1_user_id": row.combatant1_user_id,
            "combatant2_user_id": row.combatant2_user_id,
            "winner_name": row.winner_name,
            "is_stalemate": row.is_stalemate,
            "created_at": row.created_at,
            "outcome": outcome,
            "combatant1": c1,
            "combatant2": c2,
            "duration_ticks": duration_ticks,
            "duration_s": _ticks_to_seconds(duration_ticks, tick_ms),
            "pvc_damage_reduction": pvc_dr,
            "key_events": key_events,
        }

    # ------------------------------------------------------------------ #
    # Private helpers                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _pov_outcome(row: CombatLog, user_id: int) -> tuple[str, str]:
        """Return (opponent_name, outcome) from a given user's point of view.

        Determines which slot the user occupies (c1 or c2), then reads
        final_hp from summary.combatants to unambiguously determine the winner
        even when both ships share a name.

        Returns:
            opponent_name: the OTHER combatant's ship name.
            outcome: "won" | "lost" | "stalemate".
        """
        if row.is_stalemate:
            # Determine opponent regardless of slot
            if row.combatant1_user_id == user_id:
                return row.combatant2_name, "stalemate"
            else:
                return row.combatant1_name, "stalemate"

        # Determine user's slot and opponent name
        if row.combatant1_user_id == user_id:
            user_slot = "1"
            opponent_name = row.combatant2_name
        else:
            user_slot = "2"
            opponent_name = row.combatant1_name

        # Use final_hp stored in summary.combatants to unambiguously determine
        # which slot died.  String-matching winner against ship names is unreliable
        # when both ships share a name — the hull-based check is authoritative.
        data = row.data or {}
        summary = data.get("summary", {})
        combatants_map = summary.get("combatants", {})

        c1_hull = combatants_map.get("1", {}).get("final_hp", {}).get("hull", 1)
        c2_hull = combatants_map.get("2", {}).get("final_hp", {}).get("hull", 1)

        if c1_hull <= 0 and c2_hull > 0:
            winner_slot = "2"
        elif c2_hull <= 0 and c1_hull > 0:
            winner_slot = "1"
        else:
            # Both hulls positive (or final_hp absent): use c1_hull vs c2_hull
            # as a tiebreaker — higher surviving hull wins.
            winner_slot = "1" if c1_hull >= c2_hull else "2"

        outcome = "won" if winner_slot == user_slot else "lost"
        return opponent_name, outcome

    @staticmethod
    def _parse_combatant(c: dict) -> dict:
        """Extract per-combatant stats from a summary.combatants entry."""
        shots_fired = int(c.get("shots_fired", 0))
        shots_hit = int(c.get("shots_hit", 0))
        accuracy: float | None = (shots_hit / shots_fired) if shots_fired > 0 else None
        return {
            "name": c.get("name", ""),
            "ship": c.get("ship", ""),
            "start_hp": c.get("start_hp", {"hull": 0, "armour": 0, "shield": 0}),
            "final_hp": c.get("final_hp", {"hull": 0, "armour": 0, "shield": 0}),
            "shots_fired": shots_fired,
            "shots_hit": shots_hit,
            "accuracy": accuracy,
            "damage_dealt": int(c.get("damage_dealt", 0)),
            "damage_taken": int(c.get("damage_taken", 0)),
        }

    @staticmethod
    def _extract_key_events(
        timeline: list[dict],
        tick_ms: int = _TICK_MS,
        combatants_map: dict | None = None,
    ) -> list[dict]:
        """Condense the full timeline into notable highlight events.

        CI-22: Now includes Tier-A baseline lines (engagement, first-hit per side, outcome)
        and Tier-C HP-milestone lines (50%/25% per side), synthesised from fight_start,
        weapon_fire, damage and fight_end events — no new resolver event types needed.

        CI-20: Uses data["side"] → combatants_map to resolve actor labels. Falls back to
        raw actor string when side is absent (old rows / no combatants_map).

        Included events (in tick order):
          - fight_start → Engagement line (Tier A baseline)
          - first weapon_fire hit per side → First-hit line (Tier A baseline)
          - damage → per-side 50%/25% HP milestone lines (Tier C) — crossed only once each
          - secondary weapon_fire events
          - module_activation events
          - layer_depleted events
          - secondary_depleted events
          - fight_end → Outcome line (Tier A baseline)

        All events are sorted by tick before the caller's 1024-char truncation so baseline
        lines are not starved by heavy secondary/module event volume.
        """
        _cmap = combatants_map or {}

        def _label_for_side(side_val) -> str | None:
            """Resolve display label from slot number via combatants_map."""
            if side_val is None:
                return None
            return _cmap.get(str(side_val), {}).get("name")

        def _actor_label(actor: str | None, data: dict) -> str:
            """Resolve the best display label for an actor."""
            side = data.get("side")
            label = _label_for_side(side)
            return label if label else (actor or "?")

        key_events: list[dict] = []

        # --- Per-side HP tracking for milestones (Tier C) ---
        # Each side starts at None (not-yet-seen); once start_hp is known from fight_start,
        # we track per-side total HP and milestone crossing.
        _start_total: dict[str, int] = {}     # slot str → total start HP
        _milestone_fired: dict[str, set] = {"1": set(), "2": set()}  # slot → {50, 25}

        # --- First-hit-per-side tracking (Tier A) ---
        _first_hit_done: set[str] = set()     # set of slot strs that have had first hit

        for ev in timeline:
            ev_type = ev.get("type", "")
            tick = int(ev.get("tick", 0))
            time_s = _ticks_to_seconds(tick, tick_ms)
            actor = ev.get("actor")
            data = ev.get("data", {}) or {}

            # ----------------------------------------------------------------
            # Tier A: fight_start → Engagement line
            # ----------------------------------------------------------------
            if ev_type == "fight_start":
                combatants_data = data.get("combatants", [])
                if len(combatants_data) >= 2:
                    c1d = combatants_data[0]
                    c2d = combatants_data[1]
                    c1_label = c1d.get("display_name") or c1d.get("name", "?")
                    c2_label = c2d.get("display_name") or c2d.get("name", "?")
                    c1_ship = c1d.get("ship", c1d.get("name", "?"))
                    c2_ship = c2d.get("ship", c2d.get("name", "?"))
                    dist_m = data.get("initial_distance", 0)
                    # Pre-populate start_hp for milestone tracking
                    for i, cbd in enumerate(combatants_data):
                        s_key = str(i + 1)
                        hp = cbd.get("hp", {})
                        total = hp.get("hull", 0) + hp.get("armour", 0) + hp.get("shield", 0)
                        _start_total[s_key] = total
                    key_events.append({
                        "tick": tick,
                        "time_s": time_s,
                        "actor": None,
                        "event_type": "Engagement",
                        "detail": (
                            f"Engagement: {c1_label} ({c1_ship}) vs {c2_label} ({c2_ship}) — {int(dist_m)}m"
                        ),
                    })
                continue  # don't fall through to other branches

            # ----------------------------------------------------------------
            # Tier A: weapon_fire hit → first-hit per side (primary OR secondary)
            # ----------------------------------------------------------------
            if ev_type == "weapon_fire":
                side_val = data.get("side")
                slot_str = str(side_val) if side_val is not None else None
                if slot_str and slot_str not in _first_hit_done and data.get("hit") is True:
                    _first_hit_done.add(slot_str)
                    a_label = _actor_label(actor, data)
                    weapon_name = data.get("weapon", "weapon")
                    key_events.append({
                        "tick": tick,
                        "time_s": time_s,
                        "actor": actor,
                        "event_type": "First hit",
                        "detail": f"{a_label} scores first hit with {weapon_name}",
                    })

                # Secondary weapon fire events (existing logic)
                slot_field = data.get("slot", "")
                subtype = data.get("subtype", "")
                if slot_field != "primary" and subtype in _SECONDARY_SUBTYPES:
                    a_label = _actor_label(actor, data)
                    weapon = data.get("weapon", "unknown weapon")
                    if subtype == "nuke":
                        opp_dmg = data.get("opponent_damage", 0)
                        self_dmg = data.get("self_damage", 0)
                        hit_str = f"detonated (opp: {opp_dmg}, self: {self_dmg})"
                    elif subtype == "shock-blast":
                        hit_str = "distance reset"
                    else:
                        hit = data.get("hit", False)
                        hit_str = "hit" if hit else "miss"
                    key_events.append({
                        "tick": tick,
                        "time_s": time_s,
                        "actor": actor,
                        "event_type": f"Secondary fire ({subtype})",
                        "detail": f"{a_label} fired {weapon} — {hit_str}",
                    })
                continue

            # ----------------------------------------------------------------
            # Tier C: damage → per-side 50%/25% HP milestones
            # ----------------------------------------------------------------
            if ev_type == "damage":
                side_val = data.get("side")
                if side_val is not None:
                    slot_str = str(side_val)
                    start_hp_total = _start_total.get(slot_str, 0)
                    if start_hp_total > 0:
                        hp_after = data.get("hp_after", {})
                        current_total = (
                            hp_after.get("hull", 0)
                            + hp_after.get("armour", 0)
                            + hp_after.get("shield", 0)
                        )
                        current_pct = current_total / start_hp_total
                        side_label = _label_for_side(side_val) or slot_str
                        for milestone in (50, 25):
                            if milestone not in _milestone_fired[slot_str] and current_pct <= milestone / 100:
                                _milestone_fired[slot_str].add(milestone)
                                key_events.append({
                                    "tick": tick,
                                    "time_s": time_s,
                                    "actor": None,
                                    "event_type": f"HP milestone ({milestone}%)",
                                    "detail": f"{side_label} dropped to ≤{milestone}% HP",
                                })
                continue

            # ----------------------------------------------------------------
            # Secondary depleted (CI-16)
            # ----------------------------------------------------------------
            if ev_type == "secondary_depleted":
                a_label = _actor_label(actor, data)
                weapon = data.get("weapon", "unknown weapon")
                key_events.append({
                    "tick": tick,
                    "time_s": time_s,
                    "actor": actor,
                    "event_type": "Secondary depleted",
                    "detail": f"{a_label} ran out of {weapon}",
                })
                continue

            # ----------------------------------------------------------------
            # Module activation
            # ----------------------------------------------------------------
            if ev_type == "module_activation":
                a_label = _actor_label(actor, data)
                module_name = data.get("module", data.get("name", "module"))
                module_type = data.get("module_type", "")
                key_events.append({
                    "tick": tick,
                    "time_s": time_s,
                    "actor": actor,
                    "event_type": "Module activated",
                    "detail": f"{a_label} activated {module_name}",
                })
                _ = module_type  # available for future filtering
                continue

            # ----------------------------------------------------------------
            # Layer depleted
            # ----------------------------------------------------------------
            if ev_type == "layer_depleted":
                layer = data.get("layer", "")
                label = _LAYER_LABELS.get(layer, f"{layer} depleted")
                a_label = _actor_label(actor, data)
                target = ev.get("target") or a_label
                key_events.append({
                    "tick": tick,
                    "time_s": time_s,
                    "actor": actor,
                    "event_type": label,
                    "detail": f"{target}: {label}",
                })
                continue

            # ----------------------------------------------------------------
            # Tier A: fight_end → Outcome line
            # ----------------------------------------------------------------
            if ev_type == "fight_end":
                winner = data.get("winner")
                dur_ticks = int(data.get("duration_ticks", 0))
                dur_s = _ticks_to_seconds(dur_ticks, tick_ms)
                if winner:
                    # Resolve winner SLOT from final_hp to avoid same-ship-name ambiguity.
                    # fight_end.data["final_hp"] has keys "c1" and "c2" (matching combat_service emit).
                    final_hp_data = data.get("final_hp", {})
                    c1_hull = final_hp_data.get("c1", {}).get("hull", 1)
                    c2_hull = final_hp_data.get("c2", {}).get("hull", 1)
                    if c1_hull <= 0 and c2_hull > 0:
                        winner_slot, loser_slot = "2", "1"  # c1 died → c2 won
                    elif c2_hull <= 0 and c1_hull > 0:
                        winner_slot, loser_slot = "1", "2"  # c2 died → c1 won
                    else:
                        # final_hp absent or both alive — stalemate handled below; here
                        # fall back to slot "1" only if combatants_map is available, else
                        # use the raw winner string.
                        winner_slot, loser_slot = None, None
                    if winner_slot is not None:
                        winner_label = _cmap.get(winner_slot, {}).get("name", winner)
                        loser_label = _cmap.get(loser_slot, {}).get("name", "opponent")
                    else:
                        winner_label = winner
                        loser_label = "opponent"
                    key_events.append({
                        "tick": tick,
                        "time_s": time_s,
                        "actor": None,
                        "event_type": "Outcome",
                        "detail": f"{winner_label} wins — {loser_label} destroyed ({dur_s:.1f}s)",
                    })
                else:
                    key_events.append({
                        "tick": tick,
                        "time_s": time_s,
                        "actor": None,
                        "event_type": "Outcome",
                        "detail": f"Stalemate (time cap — {dur_s:.1f}s)",
                    })
                continue

        # CI-22: sort all key events by tick so baseline lines aren't starved
        key_events.sort(key=lambda e: (e["tick"], e["event_type"]))
        return key_events
