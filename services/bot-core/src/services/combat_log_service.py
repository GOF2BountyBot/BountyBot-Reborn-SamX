"""CombatLogService — persists resolved fight records to combat_log (§12 / T10).

Receives the in-memory FightResults from the TickResolver and writes one
combat_log row per resolved fight. Serialises the CombatEvent timeline via
dataclasses.asdict() before JSON storage — raw object serialisation would fail.

The created_at column has no server_default (migration 0011); every insert MUST
go through the ORM so the application-side default supplies the value.
"""

from __future__ import annotations

import dataclasses

from persist.models.combat_log import CombatLog
from persist.repositories.combat_log_repository import CombatLogRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.combat_models import CombatMeta, FightResults

flogger = bblogger.get_logger("combat-log-service")

_VALID_CONTEXTS: frozenset[str] = frozenset({"duel", "bounty_pvc", "bounty_bonus"})


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
