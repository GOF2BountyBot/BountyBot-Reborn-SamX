"""Event type registry for custom stat-race challenges (issue #30, spec §10 + §4).

Every v1 type is declared here as an immutable EventType dataclass.
The DB stores (type_slug, params JSON) only — the registry provides all behaviour.

Parameterised types (weapon, subtype, module) produce metric keys of the form
  <base>:<param_value>
e.g. secondary_fired:nuke, module_activations:cloak, kills_by_weapon:specter.
The hook writes that key; the type's metrics dict uses a placeholder that event_service
expands when reading params.  The actual metric key is computed by
  _metric_key(slug, params)   (see below)
and stored in game_event_metrics.metric verbatim.
# ponytail: placeholder expansion is a simple f-string; if param variants proliferate
# beyond a handful, generalise to a formatter dict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from services.combat_resolver import _TICK_MS as _COMBAT_TICK_MS


@dataclass(frozen=True)
class EventType:
    slug: str
    display_name: str
    # rules_text is a template; call render_rules() to get the player-facing string.
    # Placeholders: {min_stakes:,} for duel/combat, {subtype}/{module}/{weapon} for parameterised types.
    rules_text: str
    category: str
    # metrics: {metric_name: agg_mode} where agg_mode is "sum" or "max"
    # For parameterised types the metric_name may contain "{param}" placeholder.
    metrics: dict[str, str]
    # activity: the metric key that counts "did the activity" (always "sum"-aggregated).
    # A player qualifies iff metrics[activity] >= effective_min_fights.
    # Duel family → "duel_fights"; combat family → "fights"; bounty family → "checks".
    activity: str = "fights"
    # value: rows (dict metric->float) → rank key; None = single metric value
    value: Callable[[dict], float] | None = None
    # qualified: rows → bool (None = always qualified)
    qualified: Callable[[dict], bool] | None = None
    # default_min_fights: per-type default for the per-event min_fights param.
    # 10 on max/ratio types (ceiling can be gamed with one lucky fight); 1 otherwise.
    default_min_fights: int = 1
    fmt: Callable[[float], str] = field(default=str, compare=False)


# ---------------------------------------------------------------------------
# Helpers for ratio/qualified
# ---------------------------------------------------------------------------

def _safe_ratio(num_key: str, den_key: str) -> Callable[[dict], float]:
    """Return a value function that computes num/den, 0 on missing/zero denominator."""
    def _fn(m: dict) -> float:
        den = m.get(den_key, 0)
        return m.get(num_key, 0) / den if den else 0.0
    return _fn


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

EVENT_TYPES: dict[str, EventType] = {
    # ---- bounty / scan ----
    "bounty_caps": EventType(
        slug="bounty_caps",
        display_name="Bounty Captures",
        rules_text=(
            "Capture the most bounties. "
            "A capture means you found the criminal's system and won the fight; "
            "in Bronze the capture is guaranteed once you find them and the battle is a bonus. "
            "Every /check counts as taking part, including checks where you found the criminal but lost."
        ),
        category="bounty",
        metrics={"captures": "sum", "checks": "sum"},
        activity="checks",
        value=lambda m: m.get("captures", 0.0),
    ),
    "systems_checked": EventType(
        slug="systems_checked",
        display_name="Systems Checked",
        rules_text=(
            "Check the most bounty systems. "
            "Every /check counts, whether or not you find anything; captures count as checks too."
        ),
        category="bounty",
        metrics={"checks": "sum"},
        activity="checks",
    ),
    "systems_checked_no_capture": EventType(
        slug="systems_checked_no_capture",
        display_name="Systems Checked (No Captures)",
        rules_text=(
            "Check the most bounty systems without capturing a single bounty. "
            "Every /check counts. One capture and you are out of the running."
        ),
        category="bounty",
        metrics={"checks": "sum", "captures": "sum"},
        activity="checks",
        value=lambda m: m.get("checks", 0.0),
        qualified=lambda m: m.get("captures", 0) == 0,
    ),
    # ---- duels ----
    "duels_won": EventType(
        slug="duels_won",
        display_name="Duels Won",
        # User-approved wording (verbatim); see ux-followups.md item 3.
        rules_text=(
            "Win the most duels. "
            "Duels count when stakes are at least {min_stakes:,} credits. "
            "Stalemates count as fights but not wins. "
            "Losing still counts as taking part."
        ),
        category="duel",
        metrics={"duel_wins": "sum", "duel_fights": "sum"},
        activity="duel_fights",
        value=lambda m: m.get("duel_wins", 0.0),
    ),
    "duels_lost": EventType(
        slug="duels_lost",
        display_name="Duels Lost",
        rules_text=(
            "Lose the most duels. "
            "Duels count when stakes are at least {min_stakes:,} credits. "
            "Stalemates count as fights but not losses. "
            "Winning still counts as taking part."
        ),
        category="duel",
        metrics={"duel_losses": "sum", "duel_fights": "sum"},
        activity="duel_fights",
        value=lambda m: m.get("duel_losses", 0.0),
    ),
    "duels_fought": EventType(
        slug="duels_fought",
        display_name="Duels Fought",
        rules_text=(
            "Fight the most duels. "
            "Duels count when stakes are at least {min_stakes:,} credits. "
            "Stalemates count as fights. "
            "Losing still counts as taking part."
        ),
        category="duel",
        metrics={"duel_fights": "sum"},
        activity="duel_fights",
    ),
    "duel_credits_won": EventType(
        slug="duel_credits_won",
        display_name="Duel Credits Won",
        rules_text=(
            "Walk away with the most credits from duels. "
            "Duels count when stakes are at least {min_stakes:,} credits. "
            "Losing still counts as taking part."
        ),
        category="duel",
        metrics={"credits_won": "sum", "duel_fights": "sum"},
        activity="duel_fights",
        value=lambda m: m.get("credits_won", 0.0),
    ),
    "duel_credits_lost": EventType(
        slug="duel_credits_lost",
        display_name="Duel Credits Lost",
        rules_text=(
            "Lose the most credits in duels. "
            "Duels count when stakes are at least {min_stakes:,} credits. "
            "Winning still counts as taking part."
        ),
        category="duel",
        metrics={"credits_lost": "sum", "duel_fights": "sum"},
        activity="duel_fights",
        value=lambda m: m.get("credits_lost", 0.0),
    ),
    # ---- kills ----
    "kills": EventType(
        slug="kills",
        display_name="Kills",
        rules_text=(
            "Rack up the most kills. "
            "Kills are bounty captures plus duel wins. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Losing still counts as taking part."
        ),
        category="combat",
        # captures from bounty hook, duel_wins from duel hook, fights from combat hook (both contexts)
        metrics={"captures": "sum", "duel_wins": "sum", "fights": "sum"},
        activity="fights",
        value=lambda m: m.get("captures", 0) + m.get("duel_wins", 0),
    ),
    "kills_by_weapon": EventType(
        slug="kills_by_weapon",
        display_name="Kills by Weapon",
        rules_text=(
            "Score the most kills where the finishing blow came from {weapon}. "
            "Damage from other weapons doesn't count toward the kill. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Losing still counts as taking part."
        ),
        category="combat",
        # metric key = kills_by_weapon:{params[weapon]}; expanded by event_service
        metrics={"kills_by_weapon:{weapon}": "sum", "fights": "sum"},
        activity="fights",
        value=lambda m: sum(v for k, v in m.items() if k != "fights"),
    ),
    # ---- secondaries / modules ----
    "secondary_fired": EventType(
        slug="secondary_fired",
        display_name="Secondaries Fired",
        rules_text=(
            "Fire the most {subtype} shots. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Losing still counts as taking part."
        ),
        category="combat",
        # metric key = secondary_fired:{params[subtype]}
        metrics={"secondary_fired:{subtype}": "sum", "fights": "sum"},
        activity="fights",
        value=lambda m: sum(v for k, v in m.items() if k != "fights"),
    ),
    "module_activations": EventType(
        slug="module_activations",
        display_name="Module Activations",
        rules_text=(
            "Activate the {module} module the most times. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Losing still counts as taking part."
        ),
        category="combat",
        # metric key = module_activations:{params[module]}
        metrics={"module_activations:{module}": "sum", "fights": "sum"},
        activity="fights",
        value=lambda m: sum(v for k, v in m.items() if k != "fights"),
    ),
    # ---- fights ----
    "fights_fought": EventType(
        slug="fights_fought",
        display_name="Fights Fought",
        rules_text=(
            "Fight the most battles. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Stalemates count as fights. "
            "Losing still counts as taking part."
        ),
        category="combat",
        metrics={"fights": "sum"},
        activity="fights",
    ),
    # ---- max / duration ----
    "longest_battle_won": EventType(
        slug="longest_battle_won",
        display_name="Longest Battle Won",
        rules_text=(
            "Win the single longest fight, measured in seconds. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Only your longest winning fight counts."
        ),
        category="combat",
        metrics={"duration_ticks_win": "max", "fights": "sum"},
        activity="fights",
        value=lambda m: m.get("duration_ticks_win", 0),
        default_min_fights=10,
        fmt=lambda v: f"{v * _COMBAT_TICK_MS / 1000:.1f}s",
    ),
    "longest_battle_lost": EventType(
        slug="longest_battle_lost",
        display_name="Longest Battle Lost",
        rules_text=(
            "Lose the single longest fight, measured in seconds. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Only your longest losing fight counts."
        ),
        category="combat",
        metrics={"duration_ticks_loss": "max", "fights": "sum"},
        activity="fights",
        value=lambda m: m.get("duration_ticks_loss", 0),
        default_min_fights=10,
        fmt=lambda v: f"{v * _COMBAT_TICK_MS / 1000:.1f}s",
    ),
    "max_damage_dealt_fight": EventType(
        slug="max_damage_dealt_fight",
        display_name="Max Damage Dealt in a Fight",
        rules_text=(
            "Deal the most damage in a single fight. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Only your single highest-damage fight counts."
        ),
        category="combat",
        metrics={"max_damage_dealt": "max", "fights": "sum"},
        activity="fights",
        value=lambda m: m.get("max_damage_dealt", 0),
        default_min_fights=10,
    ),
    "max_damage_taken_fight": EventType(
        slug="max_damage_taken_fight",
        display_name="Max Damage Taken in a Fight",
        rules_text=(
            "Absorb the most damage in a single fight. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Only your single highest-damage-taken fight counts."
        ),
        category="combat",
        metrics={"max_damage_taken": "max", "fights": "sum"},
        activity="fights",
        value=lambda m: m.get("max_damage_taken", 0),
        default_min_fights=10,
    ),
    "max_single_nuke_damage": EventType(
        slug="max_single_nuke_damage",
        display_name="Max Single Nuke Damage",
        rules_text=(
            "Land the biggest nuke hit on an opponent in a single shot. "
            "Self-damage from your own nuke does not count. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count."
        ),
        category="combat",
        metrics={"max_nuke_absorbed": "max", "fights": "sum"},
        activity="fights",
        value=lambda m: m.get("max_nuke_absorbed", 0),
        default_min_fights=10,
    ),
    # ---- volume ----
    "total_damage_dealt": EventType(
        slug="total_damage_dealt",
        display_name="Total Damage Dealt",
        rules_text=(
            "Deal the most total damage across all fights. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Losing still counts as taking part."
        ),
        category="combat",
        metrics={"total_damage_dealt": "sum", "fights": "sum"},
        activity="fights",
        value=lambda m: m.get("total_damage_dealt", 0.0),
    ),
    "shots_fired": EventType(
        slug="shots_fired",
        display_name="Shots Fired",
        rules_text=(
            "Fire the most shots. "
            "Nukes and shock-blasts do not count as shots. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count. "
            "Losing still counts as taking part."
        ),
        category="combat",
        metrics={"shots_fired": "sum", "fights": "sum"},
        activity="fights",
        value=lambda m: m.get("shots_fired", 0.0),
    ),
    # ---- ratio ----
    "avg_accuracy": EventType(
        slug="avg_accuracy",
        display_name="Average Accuracy",
        rules_text=(
            "Post the highest hit rate across all fights. "
            "Accuracy is hits divided by shots; nukes and shock-blasts are excluded from both. "
            "Duels count when stakes are at least {min_stakes:,} credits; bounty fights always count."
        ),
        category="combat",
        metrics={"hits": "sum", "shots": "sum", "fights": "sum"},
        activity="fights",
        value=_safe_ratio("hits", "shots"),
        default_min_fights=10,
        fmt=lambda v: f"{v:.1%}",
    ),
}


def resolve_metrics(slug: str, params: dict) -> dict[str, str]:
    """Expand placeholder metric keys for parameterised event types.

    Returns a {concrete_metric_key: agg_mode} dict ready for upsert.
    e.g. secondary_fired, params={subtype: nuke} → {"secondary_fired:nuke": "sum"}
    """
    et = EVENT_TYPES[slug]
    result: dict[str, str] = {}
    for tmpl, mode in et.metrics.items():
        key = tmpl
        for param_name, param_val in params.items():
            key = key.replace(f"{{{param_name}}}", str(param_val))
        result[key] = mode
    return result


def render_rules(
    et: EventType,
    *,
    min_stakes: int,
    min_fights: int,
    division: str | None,
    params: dict,
) -> str:
    """Render the rules template into the player-facing string.

    Substitutes {min_stakes:,}, {subtype}, {module}, {weapon} from params,
    then appends "Prizes require at least N battles/checks." (when min_fights > 1)
    and "{division} division only." (when set).
    """
    # Build substitution dict: formatted min_stakes plus any event params
    subs: dict[str, str] = {k: str(v) for k, v in params.items()}
    # Format min_stakes with comma separator; the template uses {min_stakes:,}
    # so we pre-format it and insert it as a plain {min_stakes} substitute.
    # We do this by manually replacing the format-spec variant first.
    text = et.rules_text.replace("{min_stakes:,}", f"{min_stakes:,}")
    # Then substitute any remaining plain placeholders (subtype, module, weapon, etc.)
    for key, val in subs.items():
        text = text.replace(f"{{{key}}}", val)

    parts = [text]
    if min_fights > 1:
        activity_label = "checks" if et.category == "bounty" else "battles"
        parts.append(f"Prizes require at least {min_fights} {activity_label}.")
    if division:
        parts.append(f"{division} division only.")

    return " ".join(parts)
