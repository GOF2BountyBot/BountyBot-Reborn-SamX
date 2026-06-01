"""
Combat balance hooks for the Phase-1 tick-based resolver (§5).

SUBTYPE_ACCURACY_MOD: empty in Phase-1; future homing-vs-must-aim split slots here.
weapon_accuracy(): Phase-1 passthrough; the [0.05, 0.99] clamp lives in the resolver.
"""

from __future__ import annotations

from services.combat_models import WeaponStats

# Per-weapon-subtype accuracy modifier map — empty until a subtype split is added.
SUBTYPE_ACCURACY_MOD: dict[str, float] = {}


def weapon_accuracy(pilot_acc: float, weapon: WeaponStats) -> float:
    """Phase-1 passthrough. Future homing-vs-must-aim split slots in here."""
    return pilot_acc
