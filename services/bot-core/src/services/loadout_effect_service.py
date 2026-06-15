"""Loadout effect service — maps module types to display-ready effect strings.

Pre-formats module effects server-side so the discord-gateway embed builder can
render them as-is. This keeps game-domain knowledge (which `extra_atts` keys
matter for which module type, and how to format values) in bot-core rather than
duplicated in the gateway.

Spec reference: §2.4, §2.5, Appendix B of LOADOUT_EMBED_DESIGN_SPEC.md.
"""

from __future__ import annotations

from typing import Literal

from api.schemas.loadout_schema import EffectItem
from shared import bblogger

flogger = bblogger.get_logger("loadout-effect-service")


# ---------------------------------------------------------------------------
# Formatters (dict-dispatch, per Appendix B of spec)
# ---------------------------------------------------------------------------
_FORMATTERS = {
    "int": lambda v: str(int(v)),
    "int_pct": lambda v: f"{round(float(v) * 100)}%",
    "signed_pct": lambda v: f"{'+' if float(v) >= 0 else '-'}{abs(round(float(v)))}%",
    "float": lambda v: f"{float(v):g}",
    "seconds": lambda v: f"{int(v)}s",
    "x": lambda v: f"×{float(v):g}",
}


# ---------------------------------------------------------------------------
# MODULE_EFFECT_MAP — ordered list of (extra_atts_key, label, formatter) per type.
# Empty list = render name only (no effect suffix).
#
# Policy (spec §2.5):
# - Documented types (8): populated with ordered effect tuples.
# - GammaShieldModule: ALWAYS empty (lore-only, not combat-relevant — user decision).
# - Undocumented types (12 remaining): empty list — render name only until seed-data
#   audit extends this catalog.
# ---------------------------------------------------------------------------
MODULE_EFFECT_MAP: dict[str, list[tuple[str, str, str]]] = {
    # Documented (8 types from Stage 1 analysis of seed data)
    "ArmourModule": [("armour", "Armour", "int")],
    "ShieldModule": [("shield", "Shield", "int")],
    "GammaShieldModule": [],  # SPECIAL CASE: lore-only, always empty
    "ShieldInjectorModule": [("plasmaConsumption", "Plasma Cost", "int")],
    "BoosterModule": [
        ("duration", "Duration", "seconds"),
        ("effect", "Speed", "int_pct"),
    ],
    "CabinModule": [("cabinSize", "Crew", "int")],
    "CloakModule": [("duration", "Duration", "seconds")],
    "CompressorModule": [("cargoMultiplier", "Cargo Bonus", "x")],
    # PrimaryWeaponMod (Overcharge/Overdrive): damage_pct/fire_rate_pct live in the
    # nested inner extra_atts; dpsMultiplier is camelCase at the outer level. Both
    # are resolved via the merged lookup in format_module_effects().
    "PrimaryWeaponModModule": [
        ("damage_pct", "Damage", "signed_pct"),
        ("fire_rate_pct", "Fire Rate", "signed_pct"),
        ("dpsMultiplier", "Net DPS", "x"),
    ],
    # Undocumented — render name only
    "EmergencySystemModule": [],
    "JumpDriveModule": [],
    "MiningDrillModule": [],
    "RepairBeamModule": [],
    "RepairBotModule": [],
    "ScannerModule": [],
    "SignatureModule": [],
    "SpectralFilterModule": [],
    "ThrusterModule": [],
    "TimeExtenderModule": [],
    "TractorBeamModule": [],
    "TransfusionBeamModule": [],
}


# ---------------------------------------------------------------------------
# MODULE_COMBAT_TIER — classification for truncation priority in the gateway.
# Unknown types default to "combat" (fail-safe: keep visible).
# Spec §2.4.
# ---------------------------------------------------------------------------
MODULE_COMBAT_TIER: dict[str, Literal["combat", "utility"]] = {
    # Combat-relevant
    "ArmourModule": "combat",
    "ShieldModule": "combat",
    "ShieldInjectorModule": "combat",
    "BoosterModule": "combat",
    "CloakModule": "combat",
    "ThrusterModule": "combat",
    "RepairBotModule": "combat",
    "RepairBeamModule": "combat",
    "TransfusionBeamModule": "combat",
    "EmergencySystemModule": "combat",
    "PrimaryWeaponModModule": "combat",
    # Non-combat / utility
    "CabinModule": "utility",
    "CompressorModule": "utility",
    "MiningDrillModule": "utility",
    "ScannerModule": "utility",
    "JumpDriveModule": "utility",
    "TractorBeamModule": "utility",
    "SignatureModule": "utility",
    "SpectralFilterModule": "utility",
    "TimeExtenderModule": "utility",
    # GammaShield: utility-tier (GOF2 lore stat; no combat impact — effects already empty)
    "GammaShieldModule": "utility",
}


class LoadoutEffectService:
    """Maps a module (by type + extra_atts) to display-ready metadata for the embed.

    All methods are static — this service holds no state.
    """

    @staticmethod
    def format_module_effects(module_type: str | None, extra_atts: dict | None) -> list[EffectItem]:
        """Return an ordered, pre-formatted list of effects for the embed.

        Empty list = no effects (gateway renders module name only).

        Behaviour (spec §2.5):
        - GammaShieldModule: always returns [] regardless of extra_atts.
        - Unknown module type (not in MODULE_EFFECT_MAP): returns [] and logs WARN.
        - Known type with extra_atts=None or empty: returns [].
        - Known type with missing key: silently omits that entry (others still render).
        - Formatter failure (TypeError/ValueError): silently skips that entry
          (bad seed data should not spam logs).
        """
        if not module_type:
            return []

        spec = MODULE_EFFECT_MAP.get(module_type)
        if spec is None:
            # Unknown type — log for ops visibility, return empty
            keys = list(extra_atts.keys()) if isinstance(extra_atts, dict) else None
            flogger.warning(f"Unknown module_type for effects formatting: {module_type!r} (extra_atts keys={keys})")
            return []

        if not spec:
            # Known type with intentionally empty effect list (e.g., GammaShieldModule)
            return []

        if not isinstance(extra_atts, dict) or not extra_atts:
            return []

        # The DB nests combat-relevant keys under an inner `extra_atts` dict
        # (e.g. PrimaryWeaponMod damage_pct/fire_rate_pct), while some scalar keys
        # (e.g. dpsMultiplier, armour) sit at the outer level. Merge both levels so
        # a spec key resolves from either. Inner wins on the rare duplicate, which
        # always carries the same value (e.g. ArmourModule armour at both levels).
        inner = extra_atts.get("extra_atts")
        lookup = {**extra_atts, **inner} if isinstance(inner, dict) else extra_atts

        items: list[EffectItem] = []
        for key, label, fmt_name in spec:
            if key not in lookup:
                continue
            raw_value = lookup[key]
            if raw_value is None:
                continue
            formatter = _FORMATTERS.get(fmt_name)
            if formatter is None:
                # Misconfigured catalog — skip silently
                continue
            try:
                formatted = formatter(raw_value)
            except (TypeError, ValueError):
                # Bad seed data — skip silently (do not log, would spam)
                continue
            items.append(EffectItem(label=label, value=formatted))

        return items

    @staticmethod
    def get_module_combat_tier(module_type: str | None) -> Literal["combat", "utility"]:
        """Return the combat-tier classification for a module type.

        Unknown types default to 'combat' (fail-safe — keep visible).
        """
        if not module_type:
            return "combat"
        return MODULE_COMBAT_TIER.get(module_type, "combat")
