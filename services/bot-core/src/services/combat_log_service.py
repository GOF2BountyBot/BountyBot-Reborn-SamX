"""CombatLogService — persists resolved fight records to combat_log (§12 / T10).

Receives the in-memory FightResults from the TickResolver and writes one
combat_log row per resolved fight.

P2-T6: persist() accepts a timeline of plain dicts (offload path) or CombatEvent
dataclasses (legacy/in-process path).  Plain dicts pass through as-is; dataclasses
are converted via dataclasses.asdict() using the is_dataclass guard.

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
from services.combat_resolver import (
    _TICK_MS,
    _extract_key_events,
    _ticks_to_seconds,
)

flogger = bblogger.get_logger("combat-log-service")

_VALID_CONTEXTS: frozenset[str] = frozenset({"duel", "bounty_pvc", "bounty_bonus"})


class _SubpathAdapter:
    """Thin adapter: makes a sub-path Row namedtuple duck-type like CombatLog for _pov_outcome.

    P4-T7b: the sub-path select returns a SQLAlchemy Row with individual
    fields (``summary``, ``metadata``, ``key_events``) instead of the full
    ``data`` JSON blob. ``_pov_outcome`` is a static method that accesses
    ``row.data["summary"]["combatants"]`` for hull-based winner resolution.
    This adapter presents ``data`` as a minimal dict containing only the
    summary sub-path so ``_pov_outcome`` works unchanged.
    """

    __slots__ = (
        "combatant1_name",
        "combatant1_user_id",
        "combatant2_name",
        "combatant2_user_id",
        "context",
        "created_at",
        "data",
        "guild_id",
        "id",
        "is_stalemate",
        "winner_name",
    )

    def __init__(self, sub: object, summary: dict) -> None:
        self.id = sub.id  # type: ignore[attr-defined]
        self.guild_id = sub.guild_id  # type: ignore[attr-defined]
        self.context = sub.context  # type: ignore[attr-defined]
        self.combatant1_name = sub.combatant1_name  # type: ignore[attr-defined]
        self.combatant2_name = sub.combatant2_name  # type: ignore[attr-defined]
        self.combatant1_user_id = sub.combatant1_user_id  # type: ignore[attr-defined]
        self.combatant2_user_id = sub.combatant2_user_id  # type: ignore[attr-defined]
        self.winner_name = sub.winner_name  # type: ignore[attr-defined]
        self.is_stalemate = sub.is_stalemate  # type: ignore[attr-defined]
        self.created_at = sub.created_at  # type: ignore[attr-defined]
        # Reconstruct the minimal data shape _pov_outcome needs (summary only).
        self.data: dict = {"summary": summary}


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

        # Compute key_events at write time and store in the data blob.
        # Uses the same _extract_key_events from combat_resolver (the resolver leaf
        # imported above) so the stored value is byte-identical to what the
        # fallback read path produces on the same timeline — X2 parity by
        # construction (same function, same inputs).
        _tick_ms_write: int = int(metadata.get("metadata", {}).get("tick_ms", _TICK_MS))
        _combatants_map_write: dict = combatants_summary
        _key_events_write: list[dict] = _extract_key_events(
            serialised_timeline,
            tick_ms=_tick_ms_write,
            combatants_map=_combatants_map_write,
        )

        # Build the data blob (§12 "data column — internal schema")
        data_blob: dict = {
            "schema_version": metadata.get("schema_version", 1),
            "summary": summary,
            "timeline": serialised_timeline,
            "metadata": metadata.get("metadata", {}),
            # P4-T7a: precomputed key_events stored at write time.
            # Eliminates the full-timeline scan on every read.
            # Legacy rows (no "key_events" key) fall back to _extract_key_events
            # in get_detail(); 72h retention means they self-heal within 3 days.
            "key_events": _key_events_write,
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
            # Invalidate the gateway's per-user combat-log autocomplete cache for
            # both HUMAN combatants. _repo.add commits the row (commit=True default),
            # so this fires AFTER the combat_log row is durable. NPC combatants
            # (NULL user_id, PvC criminal side) are skipped inside the helper.
            # Fire-and-forget + strictly non-fatal: a gateway push failure must never
            # break the fight finalizer (this is the single chokepoint for BOTH PvC
            # and PvP; invalidate-only is rollback-safe — the next cold-fill re-reads
            # committed state).
            self._schedule_combatlog_invalidate(
                combat_meta.guild_id, combatant1_user_id, combatant2_user_id
            )
            return persisted.id
        except Exception as exc:
            flogger.error(f"CombatLog persist failed: context={context!r} guild={combat_meta.guild_id}: {exc}")
            await session.rollback()
            raise

    @staticmethod
    def _schedule_combatlog_invalidate(
        guild_id: int, combatant1_user_id: int | None, combatant2_user_id: int | None
    ) -> None:
        """Fire the gateway combat-log invalidate push without blocking the finalizer.

        Best-effort: schedules the push as a background task when an event loop is
        running; degrades to a synchronous best-effort call otherwise. Any failure
        is swallowed so the fight finalizer is never affected.
        """
        import asyncio

        from utils.gateway_push import push_combatlog_invalidate_both

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(  # noqa: RUF006 — fire-and-forget; non-fatal push
                push_combatlog_invalidate_both(guild_id, combatant1_user_id, combatant2_user_id),
                name=f"combatlog-invalidate-{guild_id}",
            )
        except RuntimeError:
            # No running loop (e.g. certain test contexts) — skip silently; the
            # 120s gateway TTL still self-heals stale entries.
            flogger.debug("_schedule_combatlog_invalidate: no running loop; skipping push")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.warning(f"_schedule_combatlog_invalidate: failed to schedule push: {type(exc).__name__}: {exc}")

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

        P4-T7b fast path: uses get_subpath_for_detail() to select only
        data->'summary', data->'metadata', data->'key_events' server-side so
        the multi-MB timeline sub-key is never shipped or loaded into Python.

        Legacy-row fallback: when the stored row has no "key_events" key (written
        before P4-T7a), the sub-path select returns key_events=None. In that case
        get_detail falls back to a full get_by_id() load and runs _extract_key_events
        on the timeline — exactly the T7a read path — so legacy rows remain correct.

        Raises:
            KeyError: if the battle does not exist OR the user is not a combatant
                      (both map to 404 — never leak existence).

        Returns a dict suitable for CombatLogDetail schema validation:
            id, guild_id, context, combatant1_name, combatant2_name,
            combatant1_user_id, combatant2_user_id, winner_name, is_stalemate,
            created_at, outcome, combatant1, combatant2, duration_ticks,
            duration_s, pvc_damage_reduction, key_events
        """
        # P4-T7b: fast-path — sub-path select (timeline never shipped)
        sub = await self._repo.get_subpath_for_detail(db, battle_id)

        # Not found
        if sub is None:
            raise KeyError(f"combat_log id={battle_id} not found or user_id={user_id} not a combatant")

        # Ownership gate — same KeyError for "not found" and "not a combatant"
        if user_id not in (sub.combatant1_user_id, sub.combatant2_user_id):
            raise KeyError(f"combat_log id={battle_id} not found or user_id={user_id} not a combatant")

        # sub.key_events is None ↔ legacy row (no "key_events" in stored data blob).
        # Fall back to full row load so _extract_key_events can scan the timeline.
        if sub.key_events is None:
            return await self._get_detail_legacy_fallback(db, battle_id, user_id, sub)

        # Fast path: all required sub-paths are present; timeline is never loaded.
        summary: dict = sub.summary or {}
        metadata: dict = sub.metadata or {}
        combatants_map: dict = summary.get("combatants", {})

        # _pov_outcome needs row.data.summary.combatants for hull-based winner
        # resolution. Construct a thin adapter so the static method works unchanged.
        _adapter = _SubpathAdapter(sub, summary)
        _opponent_name, outcome = self._pov_outcome(_adapter, user_id)  # type: ignore[arg-type]

        tick_ms = int(metadata.get("tick_ms", _TICK_MS))
        duration_ticks = int(summary.get("duration_ticks", 0))
        pvc_dr = float(metadata.get("pvc_damage_reduction", 0.0) or 0.0)

        c1 = self._parse_combatant(combatants_map.get("1", {}))
        c2 = self._parse_combatant(combatants_map.get("2", {}))
        key_events: list[dict] = sub.key_events

        flogger.info(f"get_detail: battle_id={battle_id} user_id={user_id} outcome={outcome!r} path=fast")
        return {
            "id": sub.id,
            "guild_id": sub.guild_id,
            "context": sub.context,
            "combatant1_name": sub.combatant1_name,
            "combatant2_name": sub.combatant2_name,
            "combatant1_user_id": sub.combatant1_user_id,
            "combatant2_user_id": sub.combatant2_user_id,
            "winner_name": sub.winner_name,
            "is_stalemate": sub.is_stalemate,
            "created_at": sub.created_at,
            "outcome": outcome,
            "combatant1": c1,
            "combatant2": c2,
            "duration_ticks": duration_ticks,
            "duration_s": _ticks_to_seconds(duration_ticks, tick_ms),
            "pvc_damage_reduction": pvc_dr,
            "key_events": key_events,
        }

    async def _get_detail_legacy_fallback(
        self,
        db: AsyncSession,
        battle_id: int,
        user_id: int,
        sub: object,
    ) -> dict:
        """Legacy fallback for get_detail when the stored row has no key_events.

        P4-T7b: called only when sub.key_events is None (row written before P4-T7a).
        Loads the full row (including timeline) and runs _extract_key_events — the
        same path T7a's read used. 72h retention means legacy rows self-heal in 3 days.
        """
        row = await self._repo.get_by_id(db, battle_id)
        if row is None:
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
        key_events: list[dict] = self._extract_key_events(timeline, tick_ms, combatants_map=combatants_map)

        flogger.info(f"get_detail: battle_id={battle_id} user_id={user_id} outcome={outcome!r} path=legacy")
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
        """Delegate to module-level _extract_key_events in combat_resolver."""
        return _extract_key_events(timeline, tick_ms=tick_ms, combatants_map=combatants_map)
