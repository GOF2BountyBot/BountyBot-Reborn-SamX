"""Pydantic v2 schemas for the combat-log read API.

Endpoints:
  GET /api/v1/combat-log                     → list[CombatLogListItem]
  GET /api/v1/combat-log/{id}?user_id=...    → CombatLogDetail
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CombatLogListItem(BaseModel):
    """Lightweight summary of one fight — used for autocomplete and listing.

    Ordinal disambiguates multiple fights vs. the same opponent on the same day
    (most-recent = highest ordinal).  E.g. ordinal=2 means "second duel today".
    outcome is from the requesting user's point of view: "won", "lost", or "stalemate".

    CI-20: combatant1_name / combatant2_name are included so the gateway can render
    the full "C1 vs C2" dropdown label.  opponent_name is kept for backward-compat
    and as the NPC-friendly fallback.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    guild_id: int
    context: str
    opponent_name: str
    combatant1_name: str | None = None  # CI-20: full label support; None on legacy rows
    combatant2_name: str | None = None  # CI-20: full label support; None on legacy rows
    outcome: str  # "won" | "lost" | "stalemate"
    created_at: datetime
    ordinal: int = 1  # disambiguation counter (same opponent, same day)


class KeyEvent(BaseModel):
    """One highlighted timeline event for the detail view."""

    tick: int
    time_s: float  # tick × TICK_MS / 1000
    actor: str | None
    event_type: str  # human-readable label
    detail: str  # free-form description


class CombatantSummary(BaseModel):
    """Per-combatant stats for the detail embed."""

    name: str
    ship: str
    start_hp: dict  # {"hull": N, "armour": N, "shield": N}
    final_hp: dict
    shots_fired: int
    shots_hit: int
    accuracy: float | None  # None when shots_fired == 0
    damage_dealt: int
    damage_taken: int


class CombatLogDetail(BaseModel):
    """Full detail for a single combat — returned by GET /api/v1/combat-log/{id}."""

    id: int
    guild_id: int
    context: str
    combatant1_name: str
    combatant2_name: str
    combatant1_user_id: int | None
    combatant2_user_id: int | None
    winner_name: str | None
    is_stalemate: bool
    created_at: datetime

    # Invoker's point-of-view outcome
    outcome: str  # "won" | "lost" | "stalemate"

    # Parsed per-combatant stats from data.summary
    combatant1: CombatantSummary
    combatant2: CombatantSummary

    # Combat metadata
    duration_ticks: int
    duration_s: float
    pvc_damage_reduction: float  # 0.0 when not applicable (duel)

    # Key events highlight list (server-side condensed from data.timeline)
    key_events: list[KeyEvent]
