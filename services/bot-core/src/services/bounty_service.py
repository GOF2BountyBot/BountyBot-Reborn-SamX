"""
Bounty Service for the BountyBot system.

Handles business logic for bounty generation including:
- Criminal selection (faction-aware, division-filtered)
- Ship and equipment loadout generation via bidirectional TL search
- Tech-level appropriate gear assignment with damage-weapon preference
- Full bounty spawning via A* pathfinding route generation
- Bounty checking mechanic with cooldown and proximity hint support
"""

import contextlib
import enum
import itertools
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from persist.models.bounty import Bounty
from persist.models.criminal import Criminal
from persist.repositories.bounty_repository import BountyRepository
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.criminal_repository import CriminalRepository
from persist.repositories.item_repository import ItemRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession
from utils.bounty_announcement_payload import is_recently_spotted, resolve_spotted_window

from services.cargo_utils import compute_free_cargo, is_over_cap
from services.combat_models import DEFERRED_SECONDARY_SUBTYPES, ShipLoadout
from services.combat_service import CombatService
from services.game_constants import GameConstants, resolve_constant
from services.game_maths import (
    pick_random_item_tl,
    reward_per_sys_check,
    ship_tech_level_for_value,
)
from services.inventory_service import InventoryService
from services.loot_service import LootService
from services.pathfinding_service import PathfindingService
from services.system_graph_service import SystemGraphService

flogger = bblogger.get_logger("bounty-service")


async def _resolve_combat_label(db, player, user_repo=None) -> str:
    """CI-20: Resolve a player to a display label for combat-log thread naming.

    Preference order: player.display_name → user.discord_username → "Player {id}".
    Always returns a string — never raises.

    NOTE: duel_service.DuelService._resolve_player_label is a near-identical copy.
    A shared extraction was deferred because bounty_service is a module-level function
    while duel_service uses a method (accesses self.user_repo).  If a third caller
    appears, extract to services/combat_label_utils.py.
    """
    try:
        if getattr(player, "display_name", None):
            return player.display_name
        if user_repo is None:
            from persist.repositories.user_repository import UserRepository

            user_repo = UserRepository()
        user = await user_repo.get_by_id(db, player.user_id)
        if user and user.discord_username:
            return user.discord_username
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.debug(f"Could not resolve combat label for player_id={getattr(player, 'id', '?')}: {exc}")
    return f"Player {getattr(player, 'id', '?')}"


def _extract_weapon_combat_fields(item) -> dict:
    """Extract combat fields from a weapon ORM object's extra_atts.

    DB storage nests combat-relevant snake_case fields inside an inner
    ``extra_atts`` dict (e.g. ``{"extra_atts": {"loading_speed_ms": 220, ...}}``).
    The canonical unwrap pattern is ``outer.get("extra_atts", outer)`` — fall
    back to the outer dict for flat/legacy seeds.

    Returns a dict with keys: ``damage_per_shot``, ``loading_speed_ms``,
    ``range_m``, ``subtype``.  All default to safe zero/empty values so the
    dict is always present even for weapons that lack extra_atts entirely.
    """
    outer: dict = getattr(item, "extra_atts", None) or {}
    inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
    return {
        "damage_per_shot": inner.get("damage_per_shot"),
        "loading_speed_ms": int(inner.get("loading_speed_ms", 0) or 0),
        "range_m": float(inner.get("range_m", 0.0) or 0.0),
        "subtype": inner.get("subtype", "") or "",
    }


def get_secondary_subtype(item) -> str:
    """Unwrap the secondary-weapon subtype from an ORM object's extra_atts.

    The subtype lives in the INNER extra_atts dict (DB nesting pattern).
    This is the single-source implementation; shop_service imports this
    function to avoid duplicating the unwrap logic (drift risk).

    Args:
        item: Any object with an ``extra_atts`` attribute (SecondaryWeapon ORM
              instance, SimpleNamespace, or similar).

    Returns:
        Subtype string (e.g. ``"nuke"``, ``"missile"``), or empty string if absent.
    """
    outer: dict = getattr(item, "extra_atts", None) or {}
    inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
    return inner.get("subtype", "") if isinstance(inner, dict) else ""


def _extract_secondary_combat_fields(item) -> dict:
    """Extract ALL combat fields from a secondary-weapon ORM object.

    Unlike ``_extract_weapon_combat_fields`` (which omits secondary-specific
    fields and reads ``damage_per_shot``), this helper reads the ``damage``
    column (the secondary weapon's explosion/hit damage) and also extracts
    ``burst_count``, ``emp_damage``, ``magnitude_m``, and ``steerable`` which
    are required for the tick resolver to fire the weapon correctly.

    DB nesting pattern identical to primaries/turrets:
        outer = item.extra_atts  (e.g. ``{"loading speed": ..., "extra_atts": {...}}``)
        inner = outer["extra_atts"]  (snake_case combat fields live here)

    Args:
        item: SecondaryWeapon ORM instance (or SimpleNamespace with matching attrs).

    Returns:
        Dict with keys: ``damage``, ``loading_speed_ms``, ``range_m``, ``subtype``,
        ``burst_count``, ``emp_damage``, ``magnitude_m``, ``steerable``.
        All default to safe zero/empty values.
    """
    outer: dict = getattr(item, "extra_atts", None) or {}
    inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
    # ``damage`` comes from the ORM column (item.damage), NOT from extra_atts.
    damage = int(getattr(item, "damage", 0) or 0)
    return {
        "damage": damage,
        "loading_speed_ms": int(inner.get("loading_speed_ms", 0) or 0),
        "range_m": float(inner.get("range_m", 0.0) or 0.0),
        "subtype": inner.get("subtype", "") or "",
        "burst_count": int(inner.get("burst_count", 0) or 0),
        "emp_damage": int(inner.get("emp_damage", 0) or 0),
        "magnitude_m": float(inner.get("magnitude_m", 0.0) or 0.0),
        "steerable": bool(inner.get("steerable", False)),
    }


# ----------------------------------------------------------------------
# Criminal loadout generation — module/primary selection (Task 2)
# ----------------------------------------------------------------------
# Module categories are keyed on the Item.type discriminator (e.g.
# "CloakModule"), confirmed against the live item catalog.
#
# Priority walk (§ Consolidated Spec C / Thread 4 decision-log): combat
# modules are slotted in this strict order until ``ship.max_modules`` is
# reached.  Each entry is (module_type, gate_kind, chance_key):
#   - gate_kind "guaranteed": always nearest_tl_pick if a slot is free.
#   - gate_kind "two_gate":  roll randint(1,100) <= per-division chance %;
#                            on pass, nearest_tl_pick.  chance_key names the
#                            GameConstants per-division dict + its lowercase
#                            GuildConfig override field (same string).
_GUARANTEED = "guaranteed"
_TWO_GATE = "two_gate"

_MODULE_PRIORITY_ORDER: list[tuple[str, str, str | None]] = [
    ("ScannerModule", _GUARANTEED, None),
    ("ArmourModule", _GUARANTEED, None),
    ("ShieldModule", _GUARANTEED, None),
    ("CloakModule", _TWO_GATE, "criminal_cloak_chance_by_division"),
    ("BoosterModule", _TWO_GATE, "criminal_booster_chance_by_division"),
    ("EmergencySystemModule", _TWO_GATE, "criminal_emergency_chance_by_division"),
    ("RepairBotModule", _GUARANTEED, None),
    ("PrimaryWeaponModModule", _TWO_GATE, "criminal_weaponmod_chance_by_division"),
    ("ThrusterModule", _GUARANTEED, None),
]

# GameConstants attribute names paired to each chance_key (per-division dict
# default lookups via resolve_constant).
_CHANCE_KEY_TO_CONSTANT: dict[str, str] = {
    "criminal_cloak_chance_by_division": "CRIMINAL_CLOAK_CHANCE_BY_DIVISION",
    "criminal_booster_chance_by_division": "CRIMINAL_BOOSTER_CHANCE_BY_DIVISION",
    "criminal_emergency_chance_by_division": "CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION",
    "criminal_weaponmod_chance_by_division": "CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION",
}

# Filler tail (no combat effect).  Filler-A: each limit-1, drawn at random
# WITHOUT replacement.  Filler-B: ∞-limit, drawn at random WITH replacement
# to fill any remaining slots.
_FILLER_A_TYPES: list[str] = [
    "GammaShieldModule",
    "SpectralFilterModule",
    "RepairBeamModule",
    "SignatureModule",
    "MiningDrillModule",
    "TractorBeamModule",
]
_FILLER_B_TYPES: list[str] = ["CompressorModule", "CabinModule"]

# Never equipped (misleading no-ops + banned).  Jump Drive is also limit-0 in
# MODULE_EQUIP_LIMITS; listed here for an explicit, self-documenting guard.
_NEVER_EQUIP_TYPES: frozenset[str] = frozenset(
    {
        "TransfusionBeamModule",
        "ShieldInjectorModule",
        "TimeExtenderModule",
        "JumpDriveModule",
    }
)

# Enforced never-equip invariant (import-time guard): the banned set MUST be
# disjoint from every equippable list.  Exclusion is otherwise purely structural
# (banned types simply happen to appear in none of the equippable lists), giving
# zero protection against a future filler-list typo silently re-admitting a
# banned module.  This assertion makes the exclusion a checked invariant.
_EQUIPPABLE_MODULE_TYPES: frozenset[str] = frozenset(
    {entry[0] for entry in _MODULE_PRIORITY_ORDER} | set(_FILLER_A_TYPES) | set(_FILLER_B_TYPES)
)
assert not (_NEVER_EQUIP_TYPES & _EQUIPPABLE_MODULE_TYPES), (
    "Never-equip module type(s) leaked into an equippable list: "
    f"{sorted(_NEVER_EQUIP_TYPES & _EQUIPPABLE_MODULE_TYPES)}"
)

# Divisions whose nearest-TL tie-break prefers the HIGHER tech level.
_HIGHER_TL_TIE_DIVISIONS: frozenset[str] = frozenset({"gold", "platinum"})


def _weapon_range_m(weapon) -> float:
    """Read a weapon's ``range_m`` via the canonical doubly-nested unwrap."""
    outer: dict = getattr(weapon, "extra_atts", None) or {}
    inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
    return float(inner.get("range_m", 0.0) or 0.0)


def _weapon_emp_damage(weapon) -> float:
    """Read a weapon's ``emp_damage`` via the canonical doubly-nested unwrap.

    Defaults to 0.0 when absent (the common case — most weapons carry no EMP
    component).  The engine currently bakes ``emp_damage`` for combat-log
    fidelity but applies 0 HP delta (phase-2+ deferred feature), so this value
    is "cosmetic" damage for balance purposes.
    """
    outer: dict = getattr(weapon, "extra_atts", None) or {}
    inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
    return float(inner.get("emp_damage", 0.0) or 0.0)


def _is_primarily_emp(weapon, *, is_secondary: bool) -> bool:
    """True iff a weapon's EMP component exceeds its real (HP) damage.

    BALANCE_JOURNAL §A Thread 6 (locked): exclude a primary or secondary from
    the CRIMINAL candidate pool when ``emp_damage > real_damage``, because the
    combat engine applies 0 HP delta for ``emp_damage`` (phase-2+ deferred), so
    an EMP-dominant weapon does ~no real damage → free player win.

    real_damage source per weapon class (data-model correct):
      - PRIMARY:   ``damage_per_shot`` from the inner ``extra_atts`` dict
                   (``weapon.extra_atts["extra_atts"]["damage_per_shot"]``).
      - SECONDARY: the ``damage`` column on ``secondary_weapon`` (surfaced on
                   the ORM item as ``item.damage``).
    emp_damage is always read from the inner ``extra_atts`` dict (default 0).

    Strictly ``>`` (ties keep the weapon — e.g. Dephase EMP: real 120 ≥ emp 100).
    """
    emp_damage = _weapon_emp_damage(weapon)
    if is_secondary:
        real_damage = float(getattr(weapon, "damage", 0) or 0)
    else:
        outer: dict = getattr(weapon, "extra_atts", None) or {}
        inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
        real_damage = float(inner.get("damage_per_shot", 0.0) or 0.0)
    return emp_damage > real_damage


def nearest_tl_pick(variants: list, item_tl: int, division: str):
    """Pick one module variant whose TL is nearest to *item_tl*.

    *variants* is every item of a single category (module type), TL-unfiltered.
    Returns None if empty.  Chooses ``best_tl`` minimizing ``|tl - item_tl|``;
    on a tie the higher TL wins for gold/platinum, else the lower TL.  Among
    the variants at ``best_tl`` (a single TL may have several) one is chosen
    uniformly at random.
    """
    if not variants:
        return None
    prefer_higher = division in _HIGHER_TL_TIE_DIVISIONS

    def _key(tl: int) -> tuple[int, int]:
        # Primary: distance.  Secondary: tie-break — for "prefer higher" we
        # negate the TL so the larger TL sorts first (smaller key).
        return (abs(tl - item_tl), -tl if prefer_higher else tl)

    best_tl = min((v.tech_level for v in variants), key=_key)
    at_best = [v for v in variants if v.tech_level == best_tl]
    return random.choice(at_best)


def tl_band_pick(by_tl: dict[int, list], target: int, weights: dict[str, int]):
    """Pick a weapon by ±1 TL-band weighting around *target*.

    *by_tl* maps an exact TL -> list of candidate weapons of the chosen
    category (long/short).  *weights* is PRIMARY_TL_BAND_WEIGHTS
    (``{"center","minus1","plus1"}``).  A band TL is VALID iff 1<=tl<=10 AND
    ``by_tl`` has >=1 weapon at that exact TL.  Invalid bands redistribute:
    an invalid SIDE band pushes its weight to the OTHER side band; an invalid
    CENTER band splits evenly across the two side bands.  Returns a random
    weapon of the chosen band, or None if no band is valid.
    """

    def _valid(tl: int) -> bool:
        return GameConstants.MIN_TECH_LEVEL <= tl <= GameConstants.MAX_TECH_LEVEL and bool(by_tl.get(tl))

    center_tl, minus_tl, plus_tl = target, target - 1, target + 1
    w_center = weights.get("center", 0)
    w_minus = weights.get("minus1", 0)
    w_plus = weights.get("plus1", 0)

    eff_center = w_center if _valid(center_tl) else 0
    eff_minus = w_minus if _valid(minus_tl) else 0
    eff_plus = w_plus if _valid(plus_tl) else 0

    # Redistribute invalid SIDE bands to the OTHER side band.
    if not _valid(minus_tl):
        eff_plus += w_minus if _valid(plus_tl) else 0
    if not _valid(plus_tl):
        eff_minus += w_plus if _valid(minus_tl) else 0
    # Redistribute invalid CENTER band evenly across the two side bands.
    if not _valid(center_tl):
        half = w_center / 2
        if _valid(minus_tl):
            eff_minus += half
        if _valid(plus_tl):
            eff_plus += half

    band_tls = [center_tl, minus_tl, plus_tl]
    band_weights = [eff_center, eff_minus, eff_plus]
    pool_tls = [tl for tl, w in zip(band_tls, band_weights, strict=True) if w > 0]
    pool_weights = [w for w in band_weights if w > 0]
    if not pool_tls:
        return None
    chosen_tl = random.choices(pool_tls, weights=pool_weights, k=1)[0]
    return random.choice(by_tl[chosen_tl])


# Sentinel values used in bounty.checked maps.
# >0 = player_id who locked the slot; <0 = special state.
UNCHECKED = -1  # System has not been checked yet — fair game.
FORFEITED_CHECK = -2  # System checked by a player who has since promoted past this
# bounty's division. Slot stays "claimed" (other players see
# ALREADY_CHECKED), but the original checker is no longer
# eligible for the per-system payout. See scrub_player_checks_below_tier.


def _serialize_fight_results(fight_results) -> dict | None:
    """Serialize a FightResults dataclass to a plain dict for API responses.

    Returns None if fight_results is None. Includes the combat_log_id when present.
    variance_percent is omitted (retired in T10 alongside SimpleTTKResolver).

    pvc_damage_reduction is included from FightResults.metadata; it is 0.0 for
    PvP fights and the configured DR value (e.g. 0.33) for PvC bounty fights.

    Also includes the actual after-action summary (final_hp, damage_dealt,
    damage_taken, shots_fired, shots_hit, accuracy, outcome, reason,
    duration_ticks) from metadata["summary"] when available.  Consumers that
    only need the legacy projection fields can ignore the new keys; they are
    always optional so the shape stays backward-compatible.

    Returns:
        Dict representation suitable for JSON serialization, or None.
    """
    if fight_results is None:
        return None

    def _stats_to_dict(fs) -> dict:
        return {
            "ship_name": fs.ship_name,
            "raw_hp": fs.raw_hp,
            "raw_dps": fs.raw_dps,
            "varied_hp": fs.varied_hp,
            "varied_dps": fs.varied_dps,
            "ttk": fs.ttk,
        }

    metadata = getattr(fight_results, "metadata", None) or {}
    inner_meta = metadata.get("metadata", {}) or {}
    summary = metadata.get("summary", {}) or {}
    pvc_damage_reduction = float(inner_meta.get("pvc_damage_reduction", metadata.get("pvc_damage_reduction", 0.0)))
    tick_ms = int(inner_meta.get("tick_ms", 10))
    duration_ticks = int(summary.get("duration_ticks", 0))
    duration_s = (duration_ticks * tick_ms) / 1000.0 if duration_ticks else None

    result: dict = {
        "winner_name": fight_results.winner_name,
        "loser_name": fight_results.loser_name,
        "is_stalemate": fight_results.is_stalemate,
        "ship1_stats": _stats_to_dict(fight_results.ship1_stats),
        "ship2_stats": _stats_to_dict(fight_results.ship2_stats),
        "combat_log_id": fight_results.combat_log_id,
        "pvc_damage_reduction": pvc_damage_reduction,
        # After-action summary fields (populated from tick-resolver summary; None when unavailable)
        "outcome": summary.get("outcome"),
        "reason": summary.get("reason"),
        "duration_ticks": duration_ticks or None,
        "duration_s": duration_s,
        "combatants": summary.get("combatants"),
    }
    return result


class CheckResult(enum.Enum):
    """Result codes for the bounty check mechanic."""

    NOT_FOUND = "not_found"
    ALREADY_CHECKED = "already_checked"
    INCORRECT = "incorrect"
    CORRECT = "correct"
    ON_COOLDOWN = "on_cooldown"
    # T7: over cargo cap — checking player is locked out before ANY bounty is
    # resolved (LOOT_JOURNAL §5.5 C-3a). Carries cargo_current / cargo_max so the
    # gateway can render "Cargo Overloaded — NN/XX. Unable to leave station."
    OVER_CAP = "over_cap"


@dataclass
class LootOutcome:
    """Internal per-bounty loot result of a PvC combat-win (LOOT_JOURNAL §5.9, T5).

    Produced by the win-branch loot write and stashed on :attr:`CheckResponse.loot`
    so T6 can attach it to the API response and T8 can render the embed line.  T5
    only makes this available internally — it does NOT add the response-schema
    field nor render any embed.

    ``outcome`` is one of the five §5.9 states:

    * ``looted``     — full haul taken (``qty_looted == qty_total``).
    * ``partial``    — cargo filled mid-haul; ``qty_looted < qty_total`` (§5.4 clamp).
    * ``failed``     — beam equipped + room, but the tractor RNG missed (§5.3).
    * ``cargo_full`` — 0 free cargo at win; roll skipped entirely (M-1).
    * ``none``       — no tractor beam equipped (or nothing to loot); T6 omits the
                       Loot field entirely.

    ``tractor_emoji`` / ``tractor_name`` identify the equipped beam (for the §5.9
    ``<beam-emoji> Loot`` render); both are ``None`` for ``outcome == "none"``.
    ``cargo_current`` / ``cargo_max`` back the ``cargo_full`` "(NN/XX)" message.
    """

    outcome: str
    item_name: str | None = None
    item_type: str | None = None
    qty_looted: int = 0
    qty_total: int = 0
    tractor_name: str | None = None
    tractor_emoji: str | None = None
    cargo_current: int | None = None
    cargo_max: int | None = None


@dataclass
class CheckResponse:  # pylint: disable=too-many-instance-attributes
    """Per-bounty outcome of a single :meth:`BountyService.check_bounty` invocation.

    A single ``/check`` call may produce one or more :class:`CheckResponse`
    objects (one per bounty in the player's division whose route contains the
    checked system). They are aggregated inside a :class:`MultiCheckResponse`.
    """

    result: CheckResult
    bounty_id: int | None = None
    message: str = ""
    proximity_hint: bool = False
    distance_to_answer: int | None = None
    combat_won: bool | None = None  # None if no combat, True/False if combat occurred
    new_tier: str | None = None  # If the player's tier changed after this check
    # Division / criminal metadata (populated on CORRECT results)
    division: str | None = None
    criminal_name: str | None = None
    reward: int | None = None
    # Combat result details
    combat_result: dict | None = None  # FightResults serialized as dict
    # Bronze-specific fields
    bonus_won: bool = False  # True if bronze player won the optional combat bonus
    total_reward: int | None = None  # Final reward earned (may be 2x for bronze win)
    criminal_ship: dict | None = None  # Criminal ship data; returned for bronze so cog can offer bonus
    # Payout breakdown (populated on CORRECT/capture outcomes so the cog can render the full embed)
    reward_per_sys: int | None = None
    route_length: int | None = None
    # Per-player payout breakdown: list of dicts with player_display_name, role, amount
    # Populated on CORRECT/capture outcomes so the cog can render the full per-player breakdown.
    payout_breakdown: list[dict] = field(default_factory=list)
    # Recently spotted: criminal was at this system 1-2 stops ago
    recently_spotted: bool = False
    # Cooldown timestamp (Unix): when the cooldown expires (populated on ON_COOLDOWN results)
    cooldown_until: int | None = None
    # T7 over-cap lockout: current per-unit cargo load (NN) and effective cap (XX),
    # populated ONLY on an OVER_CAP result so the gateway can render NN/XX.
    cargo_current: int | None = None
    cargo_max: int | None = None
    # PvC loot result (T5): populated on a player COMBAT WIN only (§5.2/§5.9).
    # None on any non-win outcome. T6 reads this to build the response loot payload.
    loot: "LootOutcome | None" = None


@dataclass
class MultiCheckResponse:
    """Aggregate response for a single ``/check`` invocation.

    ``/check`` on a system may affect multiple bounties simultaneously when
    several active bounties in the player's division share that system.
    :class:`MultiCheckResponse` collects one :class:`CheckResponse` per
    affected bounty in :attr:`outcomes`.

    For pre-multi-bounty callers (single-outcome cases) attribute access
    transparently delegates to ``outcomes[0]`` so old code that did
    ``result.result``, ``result.bounty_id`` etc. keeps working.
    """

    outcomes: list[CheckResponse]
    # Aggregated/top-level fields useful to the gateway:
    cooldown_until: int | None = None
    division: str | None = None

    def __getattr__(self, name: str):
        # Only invoked for attributes NOT defined on the dataclass itself.
        # Delegates to the first outcome when present; raises otherwise.
        outcomes = self.__dict__.get("outcomes")
        if outcomes:
            first = outcomes[0]
            if hasattr(first, name):
                return getattr(first, name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")


@dataclass
class RewardInfo:
    """Reward info for a single player.

    B.48: removed vestigial ``level_before`` / ``level_after`` / ``leveled_up``
    fields when the hardcoded level system was deleted. Bounty rewards never
    auto-advance tier (tier change requires explicit ``/promote``), so there
    is no level-equivalent surface to expose here.
    """

    player_id: int
    credits_earned: int
    xp_earned: int
    is_winner: bool = False
    systems_checked_count: int = 0


class BountyService:
    """Service for bounty generation and criminal selection logic."""

    def __init__(self) -> None:
        self.bounty_repo = BountyRepository()
        self.criminal_repo = CriminalRepository()
        self.item_repo = ItemRepository()
        self.player_repo = PlayerRepository()
        self.config_repo = ConfigRepository()
        self.graph_service = SystemGraphService()
        self.pathfinding_service = PathfindingService(self.graph_service)
        self.combat_service = CombatService()
        self.loot_service = LootService()
        # T5: the win-branch loot write goes through the shared inventory service
        # (concrete-type validation + FOR UPDATE re-lock) and reads cargo load via
        # its repo.  Constructed here so the loot path needs no ad-hoc wiring.
        self.inventory_service = InventoryService()
        self.inventory_repo = self.inventory_service.inventory_repo

    # ------------------------------------------------------------------
    # Criminal Selection
    # ------------------------------------------------------------------

    async def select_criminal(self, db: AsyncSession, guild_id: int, division: str) -> Criminal | None:
        """Select a random criminal for a new bounty in (guild, division).

        Algorithm:
            1. Load all non-player criminals.
            2. Filter out criminals already active in *this division* of the guild.
            3. If any remain → pick uniformly across factions, then uniformly within
               the chosen faction (matches legacy faction-uniform behaviour).
            4. If the filter empties the pool ("pool exhausted" — every criminal
               already has an active bounty in this division), log a WARNING and
               fall back to the FULL non-player pool, permitting same-division
               reuse. This supports large/active guilds with high
               ``bounty_max_per_tier`` values that exceed the criminal pool.
            5. Only return ``None`` when the criminal table itself is empty
               (configuration/seeding failure).

        Concurrency note: select_criminal is NOT itself race-safe across
        concurrent transactions. Race-safety for the spawn pipeline is provided
        by the orchestrator's gap-aware fire-time scheduling combined with the
        early-commit pattern in ``execute_bounty_spawn_one_job``. Two concurrent
        spawn jobs for the same (guild, division) firing within milliseconds
        could still pick the same criminal here; the gap-aware scheduling in the
        orchestrator (≥10s spacing) ensures the prior bounty has committed
        before the next read happens.

        Args:
            db:        Async database session.
            guild_id:  Discord guild ID.
            division:  Division name (e.g. "bronze", "silver", "gold", "platinum").

        Returns:
            A Criminal object, or None if no non-player criminals exist at all.
        """
        # Load all non-player criminals (the full eligible pool).
        all_criminals: list[Criminal] = await self.criminal_repo.list_all(db)
        full_pool = [c for c in all_criminals if not c.is_player]

        if not full_pool:
            flogger.error(
                f"select_criminal: criminal table contains no non-player criminals "
                f"(guild={guild_id} division={division}) — check seed data"
            )
            return None

        # Filter out criminals already active in THIS division of this guild.
        active_bounties = await self.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)
        active_names = {b.criminal_name for b in active_bounties}
        available = [c for c in full_pool if c.name not in active_names]

        if not available:
            # Pool exhausted: every non-player criminal already has an active
            # bounty in this division. Allow same-division reuse.
            flogger.warning(
                f"select_criminal: criminal pool exhausted for guild={guild_id} "
                f"division={division} ({len(active_names)} active, "
                f"{len(full_pool)} non-player criminals) — allowing same-division reuse"
            )
            available = full_pool

        # Pick a random faction, then a random criminal from that faction.
        factions = list({c.faction for c in available})
        chosen_faction = random.choice(factions)
        faction_criminals = [c for c in available if c.faction == chosen_faction]
        return random.choice(faction_criminals)

    # ------------------------------------------------------------------
    # Tech-Level Search
    # ------------------------------------------------------------------

    async def find_item_tl(
        self,
        db: AsyncSession,
        center: int,
        min_tl: int,
        max_tl: int,
        upper_bound: int,
        item_type: str,
    ) -> int:
        """Bidirectional search for a tech level that has items available.

        Searches downward from *center* to *min_tl* first, then upward
        from *center + 1* to ``min(max_tl, center + upper_bound)``.

        Ships are handled specially because they have no ``tech_level``
        column — we derive their TL from value via
        :func:`ship_tech_level_for_value`.

        Args:
            db:          Async database session.
            center:      Starting (preferred) tech level.
            min_tl:      Minimum tech level to search.
            max_tl:      Maximum tech level to search.
            upper_bound: How many TL levels above *center* to search upward.
            item_type:   Item type string (e.g. ``"ship"``, ``"primary_weapon"``).

        Returns:
            The first tech level that has at least one matching item, or
            ``-1`` if no items are found in the search range.
        """
        if item_type == "ship":
            return await self._find_ship_tl(db, center, min_tl, max_tl, upper_bound)

        # Downward search: center → min_tl
        tl = center
        while tl >= min_tl:
            items = await self.item_repo.get_all_by_tech_level(db, tl, item_type=item_type)
            if items:
                return tl
            tl -= 1

        # Upward search: center+1 → min(max_tl, center + upper_bound)
        if center < max_tl:
            tl = center + 1
            max_search = min(max_tl, center + upper_bound)
            while tl <= max_search:
                items = await self.item_repo.get_all_by_tech_level(db, tl, item_type=item_type)
                if items:
                    return tl
                tl += 1

        return -1

    async def _find_ship_tl(
        self,
        db: AsyncSession,
        center: int,
        min_tl: int,
        max_tl: int,
        upper_bound: int,
    ) -> int:
        """Variant of find_item_tl for ships (value-derived TL).

        Ships have no tech_level column; we filter all ships by
        ``ship_tech_level_for_value(ship.value)``.
        """
        from persist.models.ship import Ship
        from sqlalchemy import select

        result = await db.execute(select(Ship))
        all_ships = list(result.scalars().all())

        def ships_at_tl(target_tl: int) -> list:
            return [s for s in all_ships if ship_tech_level_for_value(s.value) == target_tl]

        # Downward search
        tl = center
        while tl >= min_tl:
            if ships_at_tl(tl):
                return tl
            tl -= 1

        # Upward search
        if center < max_tl:
            tl = center + 1
            max_search = min(max_tl, center + upper_bound)
            while tl <= max_search:
                if ships_at_tl(tl):
                    return tl
                tl += 1

        return -1

    # ------------------------------------------------------------------
    # Loadout Generation
    # ------------------------------------------------------------------

    async def generate_loadout(self, db: AsyncSession, tech_level: int, division: str = "bronze", cfg=None) -> dict:
        """Generate a criminal's ship loadout for the given tech level.

        At tech level 0 a fixed beginner loadout (Betty) is returned.
        Otherwise selects a ship at the appropriate tech level and equips
        primary weapons (long-range-floor, ±1 TL band — § Spec D) and modules
        (priority-walk with per-division two-gate equips — § Spec C).

        Args:
            db:          Async database session.
            tech_level:  Criminal tech level (0-10).
            division:    Criminal division (bronze/silver/gold/platinum).
                         Drives per-division equip chances and nearest-TL
                         tie-breaks.  Defaults to "bronze".
            cfg:         Optional GuildConfig for per-guild knob overrides.

        Returns:
            Dict containing ship info, equipped weapons, modules, and
            ``total_value``.
        """
        if tech_level == 0:
            return {
                "ship_name": "Betty",
                "ship_value": 0,
                "ship_armour": 50,
                "armor_hp": 50,
                "shield_hp": 0,
                "total_hp": 50,
                "ship_max_primaries": 0,
                "ship_max_modules": 0,
                "ship_max_turrets": 0,
                "ship_max_secondaries": 0,
                "weapons": [],
                "modules": [],
                "turrets": [],
                "secondaries": [],
                "total_value": 0,
            }

        # item_tl is one below criminal TL (minimum 1)
        item_tl = max(1, tech_level - 1)

        # ----------------------------------------------------------------
        # Ship selection
        # ----------------------------------------------------------------
        _criminal_max_gear_upgrade = resolve_constant(
            cfg, "criminal_max_gear_upgrade", GameConstants.CRIMINAL_MAX_GEAR_UPGRADE
        )
        ship_tl = await self.find_item_tl(
            db,
            center=item_tl,
            min_tl=GameConstants.MIN_TECH_LEVEL,
            max_tl=GameConstants.MAX_TECH_LEVEL,
            upper_bound=_criminal_max_gear_upgrade,
            item_type="ship",
        )

        ship = None
        all_ships: list | None = None  # P6-T9b: cached to avoid second identical query on fallback
        if ship_tl != -1:
            from persist.models.ship import Ship
            from sqlalchemy import select

            result = await db.execute(select(Ship).where(Ship.max_primaries > 0))
            all_ships = list(result.scalars().all())
            matching_ships = [s for s in all_ships if ship_tech_level_for_value(s.value) == ship_tl]
            if matching_ships:
                ship = random.choice(matching_ships)

        if ship is None:
            # Fallback: pick any combat-capable ship (max_primaries > 0).
            # P6-T9b: reuse all_ships from the TL-match query above if it was
            # already fetched; only execute a new query when ship_tl == -1 (i.e.
            # the TL-match branch was skipped and all_ships was never populated).
            if all_ships is None:
                from persist.models.ship import Ship
                from sqlalchemy import select

                result = await db.execute(select(Ship).where(Ship.max_primaries > 0))
                all_ships = list(result.scalars().all())
            if not all_ships:
                flogger.warning("No combat-capable ships (max_primaries > 0) found in DB — this should never happen")
            if all_ships:
                ship = random.choice(all_ships)

        if ship is None:
            flogger.warning(f"No ships available for tech_level={tech_level}")
            return {
                "ship_name": "Unknown",
                "ship_value": 0,
                "ship_armour": 100,
                "armor_hp": 100,
                "shield_hp": 0,
                "total_hp": 100,
                "ship_max_primaries": 0,
                "ship_max_modules": 0,
                "ship_max_turrets": 0,
                "ship_max_secondaries": 0,
                "weapons": [],
                "modules": [],
                "turrets": [],
                "secondaries": [],
                "total_value": 0,
            }

        # ----------------------------------------------------------------
        # Primary weapon selection (§ Spec D — long-range floor + ±1 TL band)
        # ----------------------------------------------------------------
        equipped_weapons = []
        if ship.max_primaries > 0:
            equipped_weapons = await self._select_primaries(db, item_tl, ship.max_primaries, cfg)

        # ----------------------------------------------------------------
        # Module selection (§ Spec C — priority walk + per-division two-gate)
        # ----------------------------------------------------------------
        equipped_modules = []
        if ship.max_modules > 0:
            equipped_modules = await self._select_modules(db, item_tl, division, ship.max_modules, cfg)

        # ----------------------------------------------------------------
        # Calculate partial values (weapons + modules) before turret selection
        # ----------------------------------------------------------------
        weapon_value = sum(getattr(w, "value", 0) for w in equipped_weapons)
        module_value = sum(getattr(m, "value", 0) for m in equipped_modules)

        # ----------------------------------------------------------------
        # Turret weapon selection
        # ----------------------------------------------------------------
        equipped_turrets = []
        if ship.max_turrets > 0:
            turret_tl = await self.find_item_tl(
                db,
                center=item_tl,
                min_tl=GameConstants.MIN_TECH_LEVEL,
                max_tl=GameConstants.MAX_TECH_LEVEL,
                upper_bound=_criminal_max_gear_upgrade,
                item_type="turret_weapon",
            )
            if turret_tl != -1:
                all_turrets = await self.item_repo.get_all_by_tech_level(db, turret_tl, item_type="turret_weapon")
                for _ in range(ship.max_turrets):
                    if all_turrets:
                        equipped_turrets.append(random.choice(all_turrets))

        turret_value = sum(getattr(t, "value", 0) for t in equipped_turrets)

        # ----------------------------------------------------------------
        # Secondary weapon selection (CI-17)
        # Subtype-aware pool: never hand out deferred subtypes or dead-weight
        # (zero-damage) items.  Sample distinct-by-name WITHOUT replacement.
        # Graceful empty: max_secondaries==0 or empty pool → secondaries=[]
        # ----------------------------------------------------------------
        equipped_secondaries: list = []
        if getattr(ship, "max_secondaries", 0) > 0:
            # Build candidate pool across a TL window, mirroring find_item_tl's
            # bidirectional intent without calling it (that can return a TL
            # populated only by deferred/dead-weight items with no fallback).
            _sw_repo = SecondaryWeaponRepository()
            _all_secondary = await _sw_repo.list_all(db)

            # Thread 6: exclude primarily-EMP secondaries (emp_damage > damage)
            # from the candidate pool (toggle, default ON).  Catches the two
            # EMP Rockets that survive the deferred-subtype + min-damage filters;
            # keeps Dephase EMP (real 120 ≥ emp 100).
            _exclude_emp = resolve_constant(
                cfg, "criminal_exclude_emp_weapons", GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS
            )

            # Compute TL window: prefer item_tl, search down to MIN_TECH_LEVEL
            # then up by criminal_max_gear_upgrade (mirrors primary/turret logic).
            _tl_candidates: list[int] = list(range(item_tl, GameConstants.MIN_TECH_LEVEL - 1, -1)) + list(
                range(item_tl + 1, min(GameConstants.MAX_TECH_LEVEL, item_tl + _criminal_max_gear_upgrade) + 1)
            )
            _seen_names: set[str] = set()
            for _sw in _all_secondary:
                if getattr(_sw, "tech_level", -1) not in _tl_candidates:
                    continue
                _subtype = get_secondary_subtype(_sw)
                if _subtype in DEFERRED_SECONDARY_SUBTYPES:
                    continue
                _sw_damage = int(getattr(_sw, "damage", 0) or 0)
                if _sw_damage <= GameConstants.CRIMINAL_SECONDARY_MIN_DAMAGE:
                    continue
                if _exclude_emp and _is_primarily_emp(_sw, is_secondary=True):
                    continue
                if _sw.name not in _seen_names:
                    _seen_names.add(_sw.name)
                    equipped_secondaries.append(_sw)

            # Sample min(max_secondaries, pool_size) distinct items WITHOUT replacement
            n_pick = min(ship.max_secondaries, len(equipped_secondaries))
            equipped_secondaries = random.sample(equipped_secondaries, n_pick) if n_pick > 0 else []

        # Knob #4: count each secondary's value ONCE per equipped type (not scaled by rounds).
        secondary_value = sum(getattr(sw, "value", 0) for sw in equipped_secondaries)
        total_value = ship.value + weapon_value + module_value + turret_value + secondary_value

        # ----------------------------------------------------------------
        # Calculate HP from base ship + modules
        # ----------------------------------------------------------------
        base_armour = getattr(ship, "armour", 100)
        module_armour = 0
        shield_hp = 0
        for m in equipped_modules:
            mtype = getattr(m, "type", "")
            extra = getattr(m, "extra_atts", {}) or {}
            if mtype == "ArmourModule":
                module_armour += extra.get("armour", 0)
            elif mtype in ("ShieldModule", "GammaShieldModule"):
                shield_hp += extra.get("shield", 0)

        armor_hp = base_armour + module_armour
        total_hp = armor_hp + shield_hp

        return {
            "ship_name": ship.name,
            "ship_emoji": getattr(ship, "emoji", None),
            "ship_value": ship.value,
            "ship_armour": base_armour,
            "armor_hp": armor_hp,
            "shield_hp": shield_hp,
            "total_hp": total_hp,
            "ship_max_primaries": ship.max_primaries,
            "ship_max_modules": ship.max_modules,
            "ship_max_turrets": ship.max_turrets,
            "ship_max_secondaries": getattr(ship, "max_secondaries", 0),
            "weapons": [
                {
                    "name": w.name,
                    "emoji": getattr(w, "emoji", None),
                    "value": w.value,
                    "dps": w.dps,
                    **_extract_weapon_combat_fields(w),
                }
                for w in equipped_weapons
            ],
            "modules": [
                {
                    "name": m.name,
                    "emoji": getattr(m, "emoji", None),
                    "value": m.value,
                    "tech_level": m.tech_level,
                    "type": getattr(m, "type", ""),
                    "extra_atts": getattr(m, "extra_atts", {}) or {},
                }
                for m in equipped_modules
            ],
            "turrets": [
                {
                    "name": t.name,
                    "emoji": getattr(t, "emoji", None),
                    "value": t.value,
                    "dps": t.dps,
                    **_extract_weapon_combat_fields(t),
                }
                for t in equipped_turrets
            ],
            "secondaries": [
                {
                    "name": sw.name,
                    "emoji": getattr(sw, "emoji", None),
                    "value": sw.value,
                    "dps": float(getattr(sw, "dps", 0) or 0),
                    "rounds": max(1, GameConstants.CRIMINAL_SECONDARY_ROUNDS.get(get_secondary_subtype(sw), 1)),
                    **_extract_secondary_combat_fields(sw),
                }
                for sw in equipped_secondaries
            ],
            "total_value": total_value,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _select_primaries(self, db: AsyncSession, item_tl: int, n_slots: int, cfg) -> list:
        """Select *n_slots* primary weapons per § Spec D (long-range floor).

        Classifies every primary weapon as LONG/SHORT by ``range_m`` vs the
        tunable threshold.  Dedicates ``ceil(pct*N)`` slots to LONG, rolls the
        remaining slots LONG at ``pct``, then picks each slot's weapon by a ±1
        TL band weighting around ``item_tl`` within the slot's category bucket.
        """
        threshold = resolve_constant(cfg, "long_range_threshold_m", GameConstants.LONG_RANGE_THRESHOLD_M)
        pct = resolve_constant(cfg, "criminal_long_range_pct", GameConstants.CRIMINAL_LONG_RANGE_PCT)
        band_weights = resolve_constant(cfg, "primary_tl_band_weights", GameConstants.PRIMARY_TL_BAND_WEIGHTS)
        exclude_emp = resolve_constant(cfg, "criminal_exclude_emp_weapons", GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS)

        all_weapons = await self.item_repo.get_all(db, "primary_weapon")
        if not all_weapons:
            return []

        # Thread 6: drop primarily-EMP primaries (emp_damage > damage_per_shot)
        # from the candidate pool BEFORE long/short bucketing + TL-band pick, so
        # criminals are never handed a ~0-real-damage weapon (toggle, default ON).
        if exclude_emp:
            all_weapons = [w for w in all_weapons if not _is_primarily_emp(w, is_secondary=False)]
            if not all_weapons:
                return []

        # Bucket weapons by category, indexed by exact TL.
        long_by_tl: dict[int, list] = {}
        short_by_tl: dict[int, list] = {}
        for w in all_weapons:
            tl = getattr(w, "tech_level", None)
            if tl is None:
                continue
            bucket = long_by_tl if _weapon_range_m(w) > threshold else short_by_tl
            bucket.setdefault(tl, []).append(w)

        # Step 1: assign a category to each slot — floor min, RNG may exceed.
        min_long = math.ceil(pct * n_slots)
        categories = ["long"] * min_long
        for _ in range(n_slots - min_long):
            categories.append("long" if random.random() < pct else "short")

        # Step 2: pick a weapon per slot (category-first, then TL band).
        equipped: list = []
        for category in categories:
            by_tl = long_by_tl if category == "long" else short_by_tl
            weapon = tl_band_pick(by_tl, item_tl, band_weights)
            if weapon is None:
                # No weapon of this category anywhere in the catalog — fall back
                # to the other bucket so the slot is still filled.
                other = short_by_tl if category == "long" else long_by_tl
                weapon = tl_band_pick(other, item_tl, band_weights)
            if weapon is not None:
                equipped.append(weapon)
        return equipped

    async def _select_modules(self, db: AsyncSession, item_tl: int, division: str, max_modules: int, cfg) -> list:
        """Select up to *max_modules* modules per § Spec C (priority walk).

        Walks the fixed priority order (guaranteed + per-division two-gate),
        appending until the module budget is full, then appends a filler tail
        (Filler-A unique without-replacement, then Filler-B repeatable).  No
        displacement: once full, the walk stops.
        """
        all_modules = await self.item_repo.get_all(db, "module")
        # Index every variant by its type discriminator (DLC ignored).
        by_type: dict[str, list] = {}
        for m in all_modules:
            mtype = getattr(m, "type", "")
            if mtype:
                by_type.setdefault(mtype, []).append(m)

        equipped: list = []

        def _append(module) -> None:
            if module is not None:
                equipped.append(module)

        # 1) Priority walk over the 9 combat categories.
        for module_type, gate_kind, chance_key in _MODULE_PRIORITY_ORDER:
            if len(equipped) >= max_modules:
                break
            variants = by_type.get(module_type, [])
            if gate_kind == _GUARANTEED:
                _append(nearest_tl_pick(variants, item_tl, division))
            else:  # two-gate
                const_name = _CHANCE_KEY_TO_CONSTANT[chance_key]
                chance_by_div = resolve_constant(cfg, chance_key, getattr(GameConstants, const_name))
                chance = chance_by_div.get(division, 0)
                if random.randint(1, 100) <= chance:
                    _append(nearest_tl_pick(variants, item_tl, division))
            # A failed gate / empty category leaves the slot for the next category.

        # 2) Filler-A: each limit-1, random type WITHOUT replacement.
        if len(equipped) < max_modules:
            pool_a = _FILLER_A_TYPES[:]
            random.shuffle(pool_a)
            while len(equipped) < max_modules and pool_a:
                _append(nearest_tl_pick(by_type.get(pool_a.pop(), []), item_tl, division))

        # 3) Filler-B: ∞-limit, random type WITH replacement, repeat to fill.
        #    Restrict to B types that actually have variants so the loop always
        #    makes progress (avoids spinning on an empty type).
        filler_b = [t for t in _FILLER_B_TYPES if by_type.get(t)]
        while len(equipped) < max_modules and filler_b:
            ftype = random.choice(filler_b)
            _append(nearest_tl_pick(by_type.get(ftype, []), item_tl, division))

        return equipped

    def _build_player_loadout(self, player, player_ship=None) -> ShipLoadout:
        """Build a minimal ShipLoadout from a player's active ship.

        If the player has no active ship, a default unarmed loadout is used.

        Args:
            player:      Player ORM instance.
            player_ship: Explicitly loaded PlayerShip instance (avoids lazy-loading).
                         If None, falls back to ``player.active_ship`` for backward
                         compatibility (e.g. in tests using SimpleNamespace).

        Returns:
            ShipLoadout with minimal fields populated.
        """
        ship = player_ship if player_ship is not None else getattr(player, "active_ship", None)
        if ship is not None:
            ship_name = getattr(ship, "ship_name", None) or "Unknown"
            base_armour = getattr(ship, "armour", 100)
        else:
            ship_name = "Unarmed"
            base_armour = 100
        return ShipLoadout(ship_name=ship_name, base_armour=base_armour)

    async def scrub_player_checks_outside_tier(
        self,
        db: AsyncSession,
        player_id: int,
        guild_id: int,
        new_tier: str,
    ) -> int:
        """Replace this player's checked entries with FORFEITED_CHECK on all active
        bounties the player can no longer reach after a tier change.

        Called by ``PlayerService.promote_player``, ``demote_player``, and
        ``prestige_player`` (each is a tier transition that orphans existing checks).

        Semantics (see /promote design notes):
        - The systems remain "checked" — other players in the affected divisions
          still see ALREADY_CHECKED for them (the ``checked.get(...) != -1`` guard
          treats any non-(-1) entry as locked).
        - The transitioning player's per-system payout is forfeited on capture:
          the payout iterator in ``calc_rewards`` skips any entry with
          ``checker_id <= 0``.
        - The forfeited credits stay un-issued; capture-bonus and other checkers'
          per-system payouts come out of the original pool unchanged.

        Iterates every division except the player's new tier. In practice only
        the player's prior-tier division has matching checks (the /check division
        filter prevents cross-tier checks at write time), so most divisions are
        zero-work scans — but covering all of them keeps the function correct
        regardless of direction (promote / demote / prestige).

        Args:
            db:        Async database session (caller-owned transaction; this method
                       uses ``commit=False``).
            player_id: Player whose check entries should be scrubbed.
            guild_id:  Guild scope.
            new_tier:  The tier the player has just transitioned to (canonical names:
                       "Bronze" / "Silver" / "Gold" / "Platinum").

        Returns:
            Number of bounties mutated.
        """
        new_tier_lower = (new_tier or "").lower()
        affected_divisions = [d for d in ("bronze", "silver", "gold", "platinum") if d != new_tier_lower]
        if not affected_divisions:
            return 0

        from sqlalchemy.orm.attributes import flag_modified

        mutated_count = 0
        for division in affected_divisions:
            active = await self.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)
            for bounty in active:
                checked = dict(bounty.checked)
                changed = False
                for system_name, checker_id in list(checked.items()):
                    if checker_id == player_id:
                        checked[system_name] = FORFEITED_CHECK
                        changed = True
                if changed:
                    bounty.checked = checked
                    # The JSON column otherwise doesn't reliably bump updated_at on
                    # dict mutations — flag it explicitly so the row is dirtied.
                    flag_modified(bounty, "checked")
                    mutated_count += 1
        flogger.info(
            f"scrub_player_checks_below_tier: player_id={player_id} guild_id={guild_id} "
            f"new_tier={new_tier} mutated_bounties={mutated_count}"
        )
        return mutated_count

    async def _reset_bounty_checks(self, db: AsyncSession, bounty) -> None:
        """Reset a bounty after a combat loss (Silver+): clear all checks, pick new location.

        When a Silver/Gold/Platinum player loses combat, the bounty "escapes" within
        its current route. All checked systems are cleared and a new correct location
        is randomly selected from the existing route. The bounty remains active so
        other players can continue hunting.

        Args:
            db:     Async database session.
            bounty: Active Bounty ORM instance to reset.
        """
        route = bounty.route  # list of system names
        # Clear all checked entries — every system is unchecked (-1)
        bounty.checked = {sys: -1 for sys in route}
        # Pick a new correct location randomly from the route
        bounty.answer = random.choice(route)
        await self.bounty_repo.update(db, bounty)
        flogger.info(f"Bounty {bounty.id}: checks reset after combat loss. New answer: {bounty.answer!r}")

    async def _award_combat_bonus(self, db: AsyncSession, player_id: int, bonus_credits: int) -> None:
        """Award a combat bonus to a bronze-division player.

        Called when a Bronze player wins the optional post-capture combat.
        Awards ``bonus_credits`` (equal to the full winner payout) to the player,
        effectively doubling their total payout.  Also awards XP proportional to
        the bonus credits via ``BOUNTY_REWARD_TO_XP_GAIN_MULT``.

        Args:
            db:            Async database session.
            player_id:     ID of the player to award.
            bonus_credits: Credits to add (should equal the full winner payout).
        """
        player = await self.player_repo.get_by_id(db, player_id)
        if player is None:
            flogger.warning(f"Cannot award combat bonus: player {player_id} not found")
            return
        # Note: guild_config not available in this helper — use global default
        bonus_xp = int(bonus_credits * GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT)
        player.credits += bonus_credits
        player.lifetime_credits += bonus_credits
        if not player.classic_mode:
            player.xp += bonus_xp
        # COMMIT THE BONUS NOW (mirrors distribute_rewards' own commit).  The Bronze
        # call site invokes _apply_loot_on_win immediately after this returns; the
        # loot routine's first get_by_id_for_update would otherwise AUTOFLUSH these
        # pending credit/XP deltas into its transaction, and a loot-write failure's
        # rollback would silently undo the 2x combat bonus + XP (§7.6 / §5.5 C-3b).
        # Committing here guarantees the loot txn has nothing of ours left pending,
        # so its rollback can only ever undo the loot write itself.
        await db.flush()
        await db.commit()
        flogger.info(f"Awarded {bonus_credits:,} combat bonus (+{bonus_xp} XP) to player {player_id}")

    # ------------------------------------------------------------------
    # Bounty Spawning
    # ------------------------------------------------------------------

    async def clear_bounties(self, db: AsyncSession, guild_id: int, tier: str | None = None) -> dict:
        """Clear active bounties for a guild (and optionally a specific tier).

        Sets matching active bounties to status='cleared', deletes the actual
        Discord announcement messages via the discord-gateway API, then removes
        the DiscordMessage DB records.

        Args:
            db:       Async database session.
            guild_id: Discord guild ID.
            tier:     Optional division filter ('bronze', 'silver', 'gold').
                      If None, clears all tiers.

        Returns:
            Dict with keys: guild_id, tier, cleared_count, bounty_ids, announcements_deleted.
        """
        import os

        import httpx
        from persist.repositories.discord_message_repository import DiscordMessageRepository

        gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
        gateway_port = os.getenv("GATEWAY_PORT", "7999")
        gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"

        # Clear active bounties at the repository level
        bounty_ids = await self.bounty_repo.clear_active_by_guild(db, guild_id, tier)

        # Delete corresponding Discord announcement messages (gateway + DB)
        announcements_deleted = 0
        if bounty_ids:
            try:
                msg_repo = DiscordMessageRepository()
                for bounty_id in bounty_ids:
                    try:
                        # Fetch the Discord message record to get the message_id
                        discord_msg = await msg_repo.get_by_guild_type_and_reference(
                            db, guild_id, "bounty_announcement", bounty_id
                        )
                        if discord_msg is not None:
                            # Best-effort: delete the actual Discord message via gateway
                            try:
                                channel_id = discord_msg.channel_id
                                async with httpx.AsyncClient() as client:
                                    resp = await client.delete(
                                        f"{gateway_url}/channels/{channel_id}/messages/{discord_msg.message_id}",
                                        timeout=10,
                                    )
                                # 404 is acceptable — message may have been manually deleted
                                if resp.status_code not in (200, 204, 404):
                                    flogger.warning(
                                        f"Non-fatal: gateway returned {resp.status_code} "
                                        f"deleting Discord message {discord_msg.message_id} "
                                        f"for bounty {bounty_id}"
                                    )
                                else:
                                    flogger.info(
                                        f"Deleted Discord message {discord_msg.message_id} for bounty {bounty_id}"
                                    )
                            except Exception as gw_exc:
                                flogger.warning(
                                    f"Non-fatal: failed to delete Discord message "
                                    f"{discord_msg.message_id} for bounty {bounty_id}: {gw_exc}"
                                )

                        # Delete the DB record regardless of gateway result
                        deleted = await msg_repo.delete_by_guild_type_and_reference(
                            db, guild_id, "bounty_announcement", bounty_id
                        )
                        if deleted:
                            announcements_deleted += 1
                    except Exception as e:
                        flogger.warning(f"Non-fatal: failed to delete announcement for bounty {bounty_id}: {e}")
            except Exception as e:
                flogger.warning(f"Non-fatal: failed to delete announcements for guild {guild_id}: {e}")

        # A.11: Clean up any scheduled bounty_expire / bounty_respawn jobs that
        # reference the bounties we just cleared. Orphaned jobs would otherwise
        # fire against already-cleared bounties (non-fatal downstream but noisy).
        # We use the scheduler REST API rather than querying apscheduler_jobs
        # directly, matching the HTTP-boundary pattern already used for gateway
        # announcements above. Failures here are non-fatal — the DB clear and
        # announcement cleanup remain authoritative.
        scheduler_jobs_deleted = 0
        if bounty_ids:
            executor_host = os.getenv("EXECUTOR_HOST", "bot-core")
            executor_port = os.getenv("EXECUTOR_PORT", "8000")
            scheduler_url = f"http://{executor_host}:{executor_port}/api/v1"
            bounty_id_set = set(bounty_ids)

            try:
                async with httpx.AsyncClient() as client:
                    list_resp = await client.get(f"{scheduler_url}/jobs", timeout=10)
                    if list_resp.status_code == 200:
                        for job in list_resp.json():
                            try:
                                args = job.get("args") or []
                                if len(args) < 2 or not isinstance(args[1], dict):
                                    continue
                                payload = args[1]
                                job_type = payload.get("job_type")
                                if job_type not in ("bounty_expire", "bounty_respawn"):
                                    continue
                                if payload.get("bounty_id") not in bounty_id_set:
                                    continue

                                job_id = job.get("id")
                                del_resp = await client.delete(
                                    f"{scheduler_url}/jobs/{job_id}",
                                    timeout=10,
                                )
                                # 404 is acceptable — job already fired or
                                # was removed concurrently.
                                if del_resp.status_code in (200, 204):
                                    scheduler_jobs_deleted += 1
                                    flogger.info(
                                        f"Deleted scheduler job {job_id} "
                                        f"(type={job_type}, bounty_id={payload.get('bounty_id')}, "
                                        f"guild_id={guild_id})"
                                    )
                                elif del_resp.status_code == 404:
                                    # Silent — expected for already-fired jobs.
                                    pass
                                else:
                                    flogger.warning(
                                        f"Non-fatal: scheduler returned {del_resp.status_code} "
                                        f"deleting job {job_id} for bounty {payload.get('bounty_id')}"
                                    )
                            except Exception as job_exc:  # pylint: disable=broad-exception-caught
                                flogger.warning(f"Non-fatal: failed to process scheduler job during cleanup: {job_exc}")
                    else:
                        flogger.warning(
                            f"Non-fatal: scheduler list returned {list_resp.status_code} "
                            f"during clear_bounties for guild {guild_id}"
                        )
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Non-fatal: scheduler cleanup failed for guild {guild_id}: {e}")

        flogger.info(
            f"Cleared {len(bounty_ids)} bounties for guild {guild_id} tier={tier}, "
            f"deleted {announcements_deleted} announcements, "
            f"removed {scheduler_jobs_deleted} scheduler jobs"
        )
        return {
            "guild_id": guild_id,
            "tier": tier,
            "cleared_count": len(bounty_ids),
            "bounty_ids": bounty_ids,
            "announcements_deleted": announcements_deleted,
            "scheduler_jobs_deleted": scheduler_jobs_deleted,
        }

    async def _ensure_loot_cache_loaded(self, db: AsyncSession) -> None:
        """Lazily warm the LootService static cache before a spawn-time loot roll.

        T3 deliberately left the preload call to the consumer (no central startup
        hook exists — ``spawn_bounty`` is invoked from several executors/routers,
        each constructing a fresh ``BountyService``).  This guard makes the spawn
        roll total: if the per-instance cache is cold, build it once; ``is_loaded``
        gates the rebuild so warm caches are never re-queried (LOOT_JOURNAL §5.8.4).
        """
        if not self.loot_service.is_loaded:
            await self.loot_service.preload_static_data(db)

    async def _player_free_cargo(self, db: AsyncSession, player_locked) -> tuple[int, int, int]:
        """Compute ``(free, current_load, effective_cap)`` for the loot clamp (§5.4/M-1).

        MUST be called with *player_locked* already held under a ``FOR UPDATE``
        lock (the loot write re-locks the same row, so this read + the subsequent
        write are race-safe vs concurrent buy/sell — LOOT_JOURNAL §5.5 C-3(b)).

        Effective cap = active ``ship.cargo`` × Π(CompressorModule ``cargoMultiplier``),
        matching ``loadout_response_service`` (§7.1).  Current load = per-unit
        ``sum(PlayerInventory.quantity)`` (cargo only; equipped gear excluded, §7.4).
        No active ship ⇒ cap 0 (the no-ship branch never reaches loot anyway).

        Delegates to the shared :func:`services.cargo_utils.compute_free_cargo`
        so the T5 loot clamp and the T7 over-cap gate share one definition.
        """
        return await compute_free_cargo(db, self.inventory_repo, player_locked)

    async def _apply_loot_on_win(
        self,
        db: AsyncSession,
        *,
        player,
        player_id: int,
        bounty: Bounty,
        player_loadout: ShipLoadout,
        cfg=None,
    ) -> LootOutcome:
        """Write PvC loot on a player COMBAT WIN — its OWN player-locked transaction (T5).

        Called ONLY from the player-combat-WIN branches of
        :meth:`_process_single_bounty_check` (Bronze ``combat_player_won``; Silver+
        ``fight_results is not None and winner_side == 1``).  Reads the criminal's
        already-rolled cargo (``bounty.criminal_ship['cargo']``, persisted at spawn
        by T4 — NOT re-rolled), gates on the equipped tractor beam (§5.3/M-5) and
        free cargo (M-1), rolls success (§5.3), clamps to free space (§5.4), and
        writes via ``add_item_to_inventory(commit=False)`` + its OWN ``db.commit()``.

        FAILURE-ISOLATED & NON-ATOMIC (user-confirmed, §7.6): the reward/XP write
        already committed inside ``distribute_rewards`` before this runs, so a loot
        failure here (any exception) is caught, rolled back (loot txn only), logged,
        and surfaced as a benign outcome — it NEVER rolls back the bounty rewards
        nor fails the ``/check``.  No ``audit_service`` (player action, §7.7).

        Returns a :class:`LootOutcome` for T6 (stashed on the CheckResponse).
        """
        try:
            cargo = (bounty.criminal_ship or {}).get("cargo")
            if not isinstance(cargo, dict):
                return LootOutcome(outcome="none")
            item_type = cargo.get("item_type")
            item_name = cargo.get("item_name")
            qty_total = cargo.get("quantity")
            if not item_type or not item_name or not isinstance(qty_total, int) or qty_total < 1:
                # Absent/None/malformed cargo → no loot, no error (§5.2).
                return LootOutcome(outcome="none")

            # Tractor gate (§5.3/M-5): resolve chance from the in-scope loadout's
            # equipped-module names.  No/unknown beam ⇒ chance 0 ⇒ outcome "none".
            await self._ensure_loot_cache_loaded(db)
            module_names = [m.name for m in (player_loadout.modules or [])]
            chance = self.loot_service.loot_chance(module_names, guild_config=cfg)
            if chance <= 0:
                return LootOutcome(outcome="none")

            beam_name = self.loot_service.equipped_tractor_name(module_names)
            beam_emoji = await self._resolve_tractor_emoji(db, beam_name) if beam_name else None

            # M-1 free-cargo gate — read under the player FOR UPDATE lock (race-safe
            # vs concurrent buy/sell).  add_item_to_inventory re-locks this same row
            # (intra-txn no-op), so the clamp read and the write share one lock.
            player_locked = await self.player_repo.get_by_id_for_update(db, player_id)
            if player_locked is None:
                flogger.warning(f"Loot: player {player_id} vanished under lock (bounty {bounty.id}); skipping loot")
                return LootOutcome(outcome="none")
            free_cargo, current_load, effective_cap = await self._player_free_cargo(db, player_locked)
            if free_cargo < 1:
                flogger.info(
                    f"Loot: player {player_id} cargo full ({current_load}/{effective_cap}) "
                    f"on bounty {bounty.id} win — skipping roll (M-1)"
                )
                return LootOutcome(
                    outcome="cargo_full",
                    item_name=item_name,
                    item_type=item_type,
                    qty_total=qty_total,
                    tractor_name=beam_name,
                    tractor_emoji=beam_emoji,
                    cargo_current=current_load,
                    cargo_max=effective_cap,
                )

            # Tractor success roll (§5.3) — rng matches the spawn convention.
            rng = random.Random()
            if not self.loot_service.roll_loot_success(chance, rng):
                flogger.info(
                    f"Loot: player {player_id} tractor MISS ({chance}% via {beam_name!r}) "
                    f"on bounty {bounty.id} ({qty_total}x {item_name})"
                )
                return LootOutcome(
                    outcome="failed",
                    item_name=item_name,
                    item_type=item_type,
                    qty_total=qty_total,
                    tractor_name=beam_name,
                    tractor_emoji=beam_emoji,
                )

            # §5.4 clamp: take what fits; the rest is "lost in space".  The clamp
            # read above and this write are under the SAME player lock.
            taken = min(qty_total, free_cargo)
            await self.inventory_service.add_item_to_inventory(db, player_id, item_type, item_name, taken, commit=False)
            await db.commit()  # OWN commit — isolates loot from the (already-committed) rewards.

            outcome = "partial" if taken < qty_total else "looted"
            flogger.info(
                f"Loot: player {player_id} tractored {taken}/{qty_total}x {item_name} ({item_type}) "
                f"from bounty {bounty.id} via {beam_name!r} ({chance}%) — outcome={outcome}"
            )
            return LootOutcome(
                outcome=outcome,
                item_name=item_name,
                item_type=item_type,
                qty_looted=taken,
                qty_total=qty_total,
                tractor_name=beam_name,
                tractor_emoji=beam_emoji,
                cargo_current=current_load,
                cargo_max=effective_cap,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # FAILURE ISOLATION (§7.6): roll back ONLY the loot txn; rewards/XP were
            # already committed by distribute_rewards and must survive.  Never re-raise
            # — a loot failure must not fail the /check.
            with contextlib.suppress(Exception):
                await db.rollback()
            flogger.error(
                f"Loot write failed for player {player_id} on bounty {bounty.id}: {exc} "
                "(rewards/XP preserved; /check unaffected)",
                exc_info=True,
            )
            return LootOutcome(outcome="none")

    async def _resolve_tractor_emoji(self, db: AsyncSession, beam_name: str) -> str | None:
        """Best-effort lookup of an equipped tractor beam's custom Discord emoji (T6/§5.9).

        Cheap, isolated read — any failure returns ``None`` (the loot still lands;
        only the emoji render is affected).
        """
        try:
            item = await self.item_repo.get_by_name(db, beam_name, item_type="module")
            if item is None:
                item = await self.item_repo.get_by_name(db, beam_name)
            return getattr(item, "emoji", None) if item else None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.debug(f"Could not resolve tractor emoji for {beam_name!r}: {exc}")
            return None

    def _generate_route(
        self,
        jump_gate_systems: list[str],
        min_systems: int,
        attempts: int = 8,
    ) -> list[str] | None:
        """Generate a bounty route of at least ``min_systems`` systems.

        Picks random distinct jump-gate endpoints and runs A* shortest-path,
        retrying up to ``attempts`` times until the route meets the minimum
        length. If none reach the minimum (e.g. a tiny/sparse map), the longest
        route found is returned best-effort so a spawn never fails purely on a
        too-short route. Returns ``None`` only if no route could be built at all.
        """
        best: list[str] | None = None
        for _attempt in range(max(1, attempts)):
            start = random.choice(jump_gate_systems)
            end = random.choice(jump_gate_systems)
            while end == start:
                end = random.choice(jump_gate_systems)

            result = self.pathfinding_service.make_route(start, end)
            if not isinstance(result, list):
                continue
            if len(result) >= min_systems:
                return result
            if best is None or len(result) > len(best):
                best = result

        if best is not None and len(best) < min_systems:
            flogger.warning(
                f"Route generation could not reach min_systems={min_systems} after "
                f"{attempts} attempts; using longest found ({len(best)} systems)"
            )
        return best

    @staticmethod
    def _roll_waypoint_count(cfg) -> int:
        """Roll how many intermediate waypoints a route should have (0, 1 or 2).

        Cascade: roll the dual-waypoint chance first; if it passes the route gets
        2 waypoints. Otherwise roll the single-waypoint chance; if it passes the
        route gets 1. Otherwise the route is a standard A→C (0 waypoints). The
        single roll is therefore *conditional* on the dual roll having failed, so
        the realised marginals are P(dual)=d, P(single)=(1-d)·s, P(standard) the
        remainder. Both probabilities are per-guild overridable.
        """
        dual_p = resolve_constant(cfg, "bounty_dual_waypoint_prob", GameConstants.BOUNTY_DUAL_WAYPOINT_PROB)
        single_p = resolve_constant(cfg, "bounty_single_waypoint_prob", GameConstants.BOUNTY_SINGLE_WAYPOINT_PROB)
        if random.random() < dual_p:
            return 2
        if random.random() < single_p:
            return 1
        return 0

    def _available_degree(self, system: str, blocked: frozenset[str]) -> int:
        """Number of ``system``'s neighbours not already consumed by an earlier leg."""
        return sum(1 for n in self.graph_service.get_neighbours(system) if n not in blocked)

    def _eligible_waypoints(
        self,
        jump_gate_systems: list[str],
        blocked: frozenset[str],
        exclude: set[str],
        min_degree: int,
    ) -> list[str]:
        """Systems usable as a waypoint right now.

        A candidate must not already be an endpoint/consumed system and must
        retain at least ``min_degree`` available neighbours after the systems in
        ``blocked`` are removed — this is the "≥2 valid neighbours even after
        earlier-route removal" rubric that gives a waypoint a distinct inbound and
        outbound corridor.
        """
        out: list[str] = []
        for name in jump_gate_systems:
            if name in exclude or name in blocked:
                continue
            if self._available_degree(name, blocked) >= min_degree:
                out.append(name)
        return out

    def _build_anchor_route(self, anchors: list[str], min_degree: int) -> list[str] | None:
        """Build a simple route visiting ``anchors`` in order, or None if a leg fails.

        Each leg is an independent A* hop with every previously-used system blocked
        (except the leg's own start), so the concatenated route never repeats a
        system. The next anchor must not already be consumed (building a leg *to* a
        used system would revisit it), and every interior waypoint must still
        satisfy the degree rubric against the post-removal graph.
        """
        route: list[str] = [anchors[0]]
        used: set[str] = {anchors[0]}
        for i in range(len(anchors) - 1):
            cur, nxt = route[-1], anchors[i + 1]
            # The next anchor must be fresh — otherwise the leg to it revisits it.
            if nxt in used:
                return None
            # Interior waypoints (not the final endpoint) must keep their degree.
            if i + 1 < len(anchors) - 1 and self._available_degree(nxt, frozenset(used - {nxt})) < min_degree:
                return None
            leg = self.pathfinding_service.make_route(cur, nxt, blocked=frozenset(used - {cur}))
            if not isinstance(leg, list):
                return None
            route.extend(leg[1:])
            used.update(leg)
        return route

    def _build_waypoint_route(
        self,
        jump_gate_systems: list[str],
        num_waypoints: int,
        attempts: int,
        min_degree: int,
    ) -> list[str] | None:
        """Try to build a simple route with exactly ``num_waypoints`` waypoints.

        Each attempt rolls fresh endpoints and a fresh waypoint set, then tries
        every interior ordering ("midpoint swap") before re-rolling. Returns None
        if no simple route could be built within ``attempts`` — the caller then
        falls back to a standard A→C route.
        """
        for _ in range(max(1, attempts)):
            start = random.choice(jump_gate_systems)
            end = random.choice(jump_gate_systems)
            while end == start:
                end = random.choice(jump_gate_systems)

            pool = self._eligible_waypoints(jump_gate_systems, frozenset(), {start, end}, min_degree)
            if len(pool) < num_waypoints:
                continue
            waypoints = random.sample(pool, num_waypoints)

            orders = list(itertools.permutations(waypoints))
            random.shuffle(orders)
            for order in orders:
                route = self._build_anchor_route([start, *order, end], min_degree)
                if route is not None:
                    return route
        return None

    def _generate_waypoint_route(
        self,
        jump_gate_systems: list[str],
        min_systems: int,
        cfg=None,
    ) -> list[str] | None:
        """Generate a bounty route, optionally lengthened with 1–2 waypoints.

        Rolls the waypoint cascade (:meth:`_roll_waypoint_count`); a 0-waypoint
        roll, or any failure to build a simple waypoint route within the attempt
        budget, yields a standard A→C route via :meth:`_generate_route` — so a
        spawn never fails on routing. The returned route is always a simple path,
        keeping every downstream consumer (distance hints, reward-per-system,
        ``checked`` map) correct.
        """
        num_waypoints = self._roll_waypoint_count(cfg)
        if num_waypoints == 0:
            return self._generate_route(jump_gate_systems, min_systems)

        attempts = resolve_constant(cfg, "bounty_waypoint_attempts", GameConstants.BOUNTY_WAYPOINT_ATTEMPTS)
        min_degree = resolve_constant(cfg, "bounty_waypoint_min_degree", GameConstants.BOUNTY_WAYPOINT_MIN_DEGREE)
        route = self._build_waypoint_route(jump_gate_systems, num_waypoints, attempts, min_degree)
        if route is not None:
            return route

        flogger.debug(
            f"Could not build a {num_waypoints}-waypoint route within {attempts} attempts; falling back to standard A→C"
        )
        return self._generate_route(jump_gate_systems, min_systems)

    @staticmethod
    def _roll_spotted_window(cfg) -> int:
        """Roll the per-bounty 'recently spotted' look-ahead width B in [0, max].

        The lower bound is 0: a bounty that rolls B=0 shows no "recently spotted"
        hint at all (is_recently_spotted is False for every distance), which adds
        a further layer of uncertainty on top of the randomized window width.
        """
        max_window = resolve_constant(cfg, "recently_spotted_max_window", GameConstants.RECENTLY_SPOTTED_MAX_WINDOW)
        return random.randint(0, max(0, max_window))

    async def spawn_bounty(
        self,
        db: AsyncSession,
        guild_id: int,
        division: str,
        tech_level: int | None = None,
        expiry_minutes: int | None = None,
    ) -> Bounty | None:
        """Spawn a new bounty for a guild division.

        Orchestrates the full bounty generation:
        1. Select criminal (exclude active ones)
        2. Determine tech level (if not provided, use pick_random_item_tl)
        3. Generate route via A* pathfinding (≥ min_route_systems, via _generate_route)
        4. Select answer (random system from route)
        5. Generate criminal loadout
        6. Calculate reward
        7. Set timing
        8. Initialize checked dict
        9. Persist to database

        Args:
            db:             Async database session.
            guild_id:       Discord guild ID.
            division:       Division name (e.g. "bronze", "silver", "gold").
            tech_level:     Optional override for criminal tech level.
            expiry_minutes: Optional override for bounty expiry duration in minutes.
                            Defaults to 480 (8 hours) if not provided.

        Returns:
            Bounty object if spawn succeeds, None if it fails.
        """
        # Load per-guild config for override resolution
        cfg = await self.config_repo.get_by_guild_id(db, guild_id)

        # Step 1: Select criminal
        criminal = await self.select_criminal(db, guild_id, division)
        if criminal is None:
            flogger.warning(f"No available criminals for guild={guild_id} div={division}")
            return None

        # Step 2: Determine tech level
        if tech_level is None:
            # Division TL centers: bronze=1, silver=3, gold=6, platinum=8
            division_tl_map = {"bronze": 1, "silver": 3, "gold": 6, "platinum": 8}
            center_tl = division_tl_map.get(division, 5)
            tech_level = pick_random_item_tl(center_tl)
            # Enforce per-division TL cap so new players are never overwhelmed
            _division_max_tl = resolve_constant(cfg, "division_max_tl", GameConstants.DIVISION_MAX_TL)
            max_tl = _division_max_tl.get(division, 10)
            tech_level = min(tech_level, max_tl)

        # Step 3: Generate route (≥ min_route_systems, optionally with waypoints)
        await self.graph_service.load_graph(db)

        jump_gate_systems = self.graph_service.get_systems_with_jump_gates()
        if len(jump_gate_systems) < 2:
            flogger.warning("Not enough systems with jump gates for route generation")
            return None

        min_systems = resolve_constant(cfg, "min_route_systems", GameConstants.MIN_ROUTE_SYSTEMS)
        route = self._generate_waypoint_route(jump_gate_systems, min_systems, cfg)

        if route is None:
            flogger.warning(f"Failed to generate route for guild={guild_id}")
            return None

        # Step 4: Select answer + roll the per-bounty "recently spotted" window B
        answer = random.choice(route)
        spotted_window = self._roll_spotted_window(cfg)

        # Step 5: Generate loadout
        loadout = await self.generate_loadout(db, tech_level, division=division, cfg=cfg)

        # Step 5b: Roll the criminal's single cargo loot item (LOOT_JOURNAL §5.1 /
        # T4).  The item is selected ONCE, at spawn, anchored on the division-derived
        # ``tech_level`` (§7.3), and persisted inside the ``criminal_ship`` JSONB under
        # a ``cargo`` key so the win-branch loot write (T5) reads it rather than
        # re-rolling, and T4b can advertise it pre-fight.  Lazy-ensure the static loot
        # cache is warm first so a cold cache can NEVER roll an empty pool and silently
        # drop the §5.1 100%-carry guarantee.
        await self._ensure_loot_cache_loaded(db)
        loot_roll = self.loot_service.roll_loot(tech_level, random.Random(), guild_config=cfg)
        if loot_roll is not None:
            loadout["cargo"] = {
                "item_type": loot_roll.item_type,
                "item_name": loot_roll.item_name,
                "quantity": loot_roll.quantity,
            }
        else:
            # Defensive: an empty chosen-band pool yields None.  Per §5.1 every
            # criminal carries exactly one item, so a None here means seed data is
            # missing the band's pool entirely — log loudly; the bounty still spawns
            # (no cargo key) so the kill path is crash-safe (T5 treats absent cargo as
            # "nothing to loot").
            flogger.warning(
                f"Loot roll returned no item for guild={guild_id} div={division} tl={tech_level}; "
                "criminal spawns with no cargo (check loot pools / seed data)"
            )

        # Step 6: Calculate reward using the winner-reserve / consolation-pool model.
        # The total reward is seeded by the legacy per-sys formula, but reward_per_sys
        # is now derived from the consolation pool (total minus the winner's reserve),
        # split evenly across the route length.
        _legacy_rps = reward_per_sys_check(tech_level, loadout["total_value"])
        total_reward = _legacy_rps * len(route)

        # Apply the per-division prize-pool scaler (balance knob) to the whole
        # pool BEFORE the winner-reserve split, so the winner reserve and the
        # consolation pool scale together. Defaults to 1.0 for every division
        # except silver (2.0), which lifts silver off the bronze floor so the
        # tier is a real step up. Per-guild override: bounty_division_reward_mult.
        _division_reward_mult = resolve_constant(
            cfg, "bounty_division_reward_mult", GameConstants.BOUNTY_DIVISION_REWARD_MULT
        )
        total_reward = int(total_reward * _division_reward_mult.get(division, 1.0))

        _winner_reserve_factor = resolve_constant(
            cfg, "bounty_winner_reserve_factor", GameConstants.BOUNTY_WINNER_RESERVE_FACTOR
        )
        winner_reserve = int(total_reward * _winner_reserve_factor)
        consolation_pool = total_reward - winner_reserve
        rps = consolation_pool // len(route) if route else 0

        # Step 7: Set timing
        issue_time = datetime.now(UTC)
        expiry = expiry_minutes if expiry_minutes is not None else 480
        end_time = issue_time + timedelta(minutes=expiry)

        # Step 8: Initialize checked dict
        checked = {system: -1 for system in route}

        # Step 9: Create bounty and persist
        bounty = Bounty(
            guild_id=guild_id,
            division=division,
            criminal_name=criminal.name,
            criminal_faction=criminal.faction,
            route=route,
            answer=answer,
            spotted_window=spotted_window,
            reward=total_reward,
            reward_per_sys=rps,
            checked=checked,
            issue_time=issue_time,
            end_time=end_time,
            tech_level=tech_level,
            criminal_ship=loadout,
            status="active",
        )
        created = await self.bounty_repo.create(db, bounty)
        flogger.info(f"Spawned bounty {created.id}: {criminal.name} in {division} for guild {guild_id}")
        return created

    # ------------------------------------------------------------------
    # Bounty Check Mechanic
    # ------------------------------------------------------------------

    async def check_bounty(
        self,
        db: AsyncSession,
        player_id: int,
        system_name: str,
        guild_id: int,
    ) -> MultiCheckResponse:
        """Check a star system against ALL active bounties for the player's division.

        Identifies every active bounty in the player's division whose route
        contains *system_name* and records the check on each. A single
        ``/check`` invocation may therefore terminate, mark "already checked",
        or "incorrect" for multiple bounties simultaneously when their routes
        overlap (B.12 fix).

        Behaviour summary:
        - Player not found → single :class:`CheckResponse` with NOT_FOUND.
        - Cooldown active  → single :class:`CheckResponse` with ON_COOLDOWN.
        - System not in any active route → single CheckResponse with NOT_FOUND.
        - Otherwise → one CheckResponse per matched bounty.

        Atomicity: all per-bounty mutations are committed in a single
        transaction. Announcement edits run AFTER the commit and are
        non-fatal — a failure to update one bounty's announcement does not
        roll back the credit / state changes for the others.

        Idempotency: the per-bounty "already checked" guard (``checked[system]
        != -1``) is unchanged. A second invocation of ``/check`` for the same
        ``(player, system)`` pair will see the system already marked and emit
        ALREADY_CHECKED for those bounties — the second call is safe and
        will not double-credit.

        Cooldown: applied ONCE per ``/check`` invocation regardless of how
        many bounties were touched (B.12 design decision — players are not
        penalised for hunting overlapping bounties).

        Args:
            db:          Async database session.
            player_id:   ID of the player performing the check.
            system_name: Name of the star system being checked.
            guild_id:    Discord guild ID.

        Returns:
            A :class:`MultiCheckResponse` whose ``outcomes`` list contains
            one :class:`CheckResponse` per bounty processed (or a single
            top-level outcome for cooldown / no-match cases).
        """
        # Step 1: Get player
        player = await self.player_repo.get_by_id(db, player_id)
        if player is None:
            return MultiCheckResponse(
                outcomes=[CheckResponse(result=CheckResult.NOT_FOUND, message="Player not found")],
            )

        # T7: Over-cap lockout — the FIRST thing evaluated, before the cooldown
        # check or ANY bounty resolution (LOOT_JOURNAL §5.5 C-3a). /check resolves
        # kills + grants loot, so blocking before resolution prevents resolving
        # an over-cap player into a worse state. Plain read (no lock): a stale
        # borderline read self-corrects on the next command (§5.5 C-3b). Escapes
        # (sell / equip-Compressor) stay available — only the 3 combat entries gate.
        _free, _load, _cap = await compute_free_cargo(db, self.inventory_repo, player)
        if is_over_cap(_load, _cap):
            flogger.info(
                f"/check over-cap lockout: player_id={player_id} guild_id={guild_id} "
                f"cargo_load={_load} cargo_cap={_cap}"
            )
            return MultiCheckResponse(
                outcomes=[
                    CheckResponse(
                        result=CheckResult.OVER_CAP,
                        message=f"Cargo Overloaded — {_load}/{_cap}. Unable to leave station.",
                        cargo_current=_load,
                        cargo_max=_cap,
                    )
                ],
            )

        # Step 2: Check cooldown
        now = datetime.now(UTC)
        if player.bounty_cooldown_end and player.bounty_cooldown_end > now:
            remaining = (player.bounty_cooldown_end - now).total_seconds()
            cooldown_until = int(player.bounty_cooldown_end.timestamp())
            return MultiCheckResponse(
                outcomes=[
                    CheckResponse(
                        result=CheckResult.ON_COOLDOWN,
                        message=f"On cooldown for {int(remaining)} more seconds",
                        cooldown_until=cooldown_until,
                    )
                ],
                cooldown_until=cooldown_until,
            )

        # Step 3: Determine player's division
        division = "bronze" if player.classic_mode else player.tier.lower() if player.tier else "bronze"

        # Step 4: Get active bounties for this division
        active_bounties = await self.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)

        # Step 5: Filter to bounties whose route contains the system
        matching_bounties = [b for b in active_bounties if system_name in b.route]

        if not matching_bounties:
            # System not in any active bounty route
            return MultiCheckResponse(
                outcomes=[
                    CheckResponse(
                        result=CheckResult.NOT_FOUND,
                        message=f"System {system_name} not in any active bounty route",
                    )
                ],
                division=division,
            )

        # Step 6: Process each matching bounty.
        # We process all bounties in-memory first (mutating state), commit ONCE
        # at the end, and then run announcement edits per outcome.
        outcomes: list[CheckResponse] = []
        bounties_to_announce: list[tuple[Bounty, bool, CheckResponse]] = []  # (bounty, captured, outcome)
        cooldown_applied = False
        cfg = await self.config_repo.get_by_guild_id(db, guild_id)
        cooldown_seconds = resolve_constant(cfg, "check_cooldown", GameConstants.CHECK_COOLDOWN)
        tier_before = player.tier

        # LOCK ORDERING (X3-bounty): acquire Bounty row locks in ascending bounty.id
        # order to prevent AB-BA deadlocks when two concurrent /check calls touch
        # the same set of bounties in different orders.  The actual lock is taken
        # inside _process_single_bounty_check via get_by_id_for_update.
        matching_bounties = sorted(matching_bounties, key=lambda b: b.id)

        for bounty in matching_bounties:
            outcome, announce_info = await self._process_single_bounty_check(
                db,
                player=player,
                player_id=player_id,
                bounty=bounty,
                system_name=system_name,
                division=division,
                now=now,
                cfg=cfg,
            )
            outcomes.append(outcome)
            if announce_info is not None:
                announce_bounty, announce_captured = announce_info
                bounties_to_announce.append((announce_bounty, announce_captured, outcome))

            # Apply cooldown ONCE per /check call, only after a real (non-already-checked) state change.
            if not cooldown_applied and outcome.result in (CheckResult.CORRECT, CheckResult.INCORRECT):
                player.bounty_cooldown_end = now + timedelta(seconds=cooldown_seconds)
                cooldown_applied = True

        # Single transactional commit covering all per-bounty mutations.
        await db.commit()
        await db.refresh(player)

        # Refresh tier-change tracking ONCE for the whole invocation
        # (any tier promotion was caused by reward distribution above).
        tier_after = player.tier
        new_tier = tier_after if tier_after != tier_before else None
        if new_tier is not None:
            for outcome in outcomes:
                if outcome.result == CheckResult.CORRECT and outcome.combat_won:
                    outcome.new_tier = new_tier
                    break  # Tier-change applies once; surface on the first capture outcome.

        # Per-bounty announcement edits. Each is best-effort & non-fatal —
        # _edit_bounty_announcement already swallows its own exceptions.
        for bounty, captured, _outcome in bounties_to_announce:
            try:
                await self._edit_bounty_announcement(db, bounty, captured=captured)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"Per-bounty announcement edit failed (bounty {bounty.id}, "
                    f"player {player_id}, system {system_name!r}): {e}"
                )
            # Capture payout embed is rendered by the cog from the check response fields.

        # If any bounty was captured, push the updated active bounty list to the gateway
        # autocomplete cache so the captured bounty is immediately removed from the /route
        # and /bounties dropdowns.  Non-fatal — a push failure never blocks the check response.
        any_captured = any(outcome.result == CheckResult.CORRECT for outcome in outcomes)
        if any_captured:
            await self._push_bounty_cache_after_capture(db, guild_id)

        # Per-bounty structured logging for observability (B.12)
        for outcome in outcomes:
            flogger.info(
                f"check_bounty outcome: bounty_id={outcome.bounty_id} player_id={player_id}"
                f" system={system_name!r} result={outcome.result.value}"
            )

        # cooldown_until on MultiCheckResponse is reserved for the ON_COOLDOWN
        # case (clients show the user when they can retry). It is NOT populated
        # when a new cooldown is just applied — that information lives in the
        # player's state on the next call.
        return MultiCheckResponse(
            outcomes=outcomes,
            cooldown_until=None,
            division=division,
        )

    async def _process_single_bounty_check(
        self,
        db: AsyncSession,
        *,
        player,
        player_id: int,
        bounty: Bounty,
        system_name: str,
        division: str,
        now: datetime,
        cfg=None,
    ) -> tuple[CheckResponse, tuple[Bounty, bool] | None]:
        """Process a single bounty for a /check invocation.

        Mutates ``bounty`` and ``player`` state in-memory but does NOT
        commit — the caller commits once for all bounties (atomicity).
        Returns the per-bounty :class:`CheckResponse` and an optional
        ``(bounty, captured)`` tuple identifying which announcement to
        re-render after the commit.

        Note: combat (silver+) is run independently per bounty, mirroring
        the original single-bounty contract. Multi-bounty terminal hits
        therefore distribute rewards independently per matching bounty.
        """
        # CONCURRENCY FIX (X3-bounty): acquire a row-level lock on this bounty
        # BEFORE reading its ``checked`` map.  Two concurrent /check calls both
        # loaded the unlocked row in Step 4; whichever session arrives here first
        # wins the lock, the second blocks until the first commits.
        #
        # populate_existing=True is MANDATORY because expire_on_commit=False means
        # the row is already in the session identity map from the unlocked SELECT
        # above.  Without it, SQLAlchemy returns the cached in-memory object and
        # the guard reads pre-commit stale state even though the lock was acquired.
        bounty = await self.bounty_repo.get_by_id_for_update(db, bounty.id)
        if bounty is None:
            # Bounty disappeared between the initial load and the lock (expired/deleted).
            return (
                CheckResponse(
                    result=CheckResult.NOT_FOUND,
                    message="Bounty no longer active",
                ),
                None,
            )

        # Re-check status under lock: another session may have captured this bounty
        # between our unlocked load and now.
        if bounty.status != "active":
            return (
                CheckResponse(
                    result=CheckResult.ALREADY_CHECKED,
                    bounty_id=bounty.id,
                    criminal_name=bounty.criminal_name,
                    message=f"Bounty {bounty.id} already resolved",
                    division=division,
                ),
                None,
            )

        # Read the FRESH checked map (just populated by the locked fetch).
        checked = dict(bounty.checked)  # Copy to modify

        if checked.get(system_name, -1) != -1:
            # Already checked by someone — no state change, no announce.
            return (
                CheckResponse(
                    result=CheckResult.ALREADY_CHECKED,
                    bounty_id=bounty.id,
                    criminal_name=bounty.criminal_name,
                    message=f"System {system_name} already checked",
                    division=division,
                ),
                None,
            )

        # Mark system as checked by this player
        checked[system_name] = player_id
        bounty.checked = checked

        # Check if this is the answer
        if bounty.answer == system_name:
            # CORRECT — found the criminal!
            await self.bounty_repo.update(db, bounty)

            flogger.info(f"Player {player_id} found {bounty.criminal_name} at {system_name} (bounty {bounty.id})")

            # Division-based combat gating
            is_bronze = division == "bronze" or player.classic_mode

            # Load player ship (used for all non-classic-mode cases)
            player_ship = None
            if hasattr(player, "active_ship_id") and player.active_ship_id is not None:
                from persist.models.player_ship import PlayerShip

                player_ship = await db.get(PlayerShip, player.active_ship_id)
            else:
                # Fallback for SimpleNamespace test objects that expose active_ship directly
                player_ship = getattr(player, "active_ship", None)

            _no_ship = player_ship is None

            # Build loadouts for combat (always — used for both bronze bonus and silver+ gate)
            from services.loadout_builder import LoadoutBuilder

            fight_results = None
            player_loadout = await LoadoutBuilder.from_player(db, player_id)
            criminal_loadout = LoadoutBuilder.from_criminal_ship(bounty.criminal_ship or {})

            # CI-20: resolve display labels for combat-log thread naming
            _player_label = await _resolve_combat_label(db, player)
            _criminal_label = bounty.criminal_name or criminal_loadout.ship_name

            if is_bronze:
                # BRONZE: Auto-capture always succeeds. Optional combat bonus.
                rewards = await self.calc_rewards(db, bounty, cfg=cfg)
                await self.distribute_rewards(db, bounty, rewards)
                payout_breakdown = await self._build_payout_breakdown(db, rewards)

                winner_reward = next((r.credits_earned for r in rewards if r.is_winner), 0)

                bonus_won = False
                total_reward = winner_reward
                loot_outcome = None
                if not _no_ship:
                    _pvc_dr = resolve_constant(cfg, "pvc_damage_reduction", GameConstants.PVC_DAMAGE_REDUCTION)
                    fight_results = await self.combat_service.fight_ships(
                        player_loadout,
                        criminal_loadout,
                        context="bounty_bonus",
                        log_result=True,
                        pvc_damage_reduction=_pvc_dr,
                        session=db,
                        guild_id=player.guild_id,
                        combatant1_user_id=player.user_id,
                        combatant2_user_id=None,  # NPC side
                        combatant1_label=_player_label,
                        combatant2_label=_criminal_label,
                    )
                    # P2-T8b: player is always combatant1 (loadout1 / side-1).
                    # winner_side==1 → player won; winner_side==2 → criminal won.
                    # Stalemate counts as a loss — no 2× bonus (spec §9 PvC draw semantics).
                    combat_player_won = fight_results.winner_side == 1
                    if combat_player_won:
                        bonus_won = True
                        total_reward = winner_reward * 2
                        await self._award_combat_bonus(db, player_id, winner_reward)
                        # T5 LOOT HOOK (Bronze) — fires ONLY on the bonus-fight WIN,
                        # never on the bare auto-capture and never on a loss/draw/
                        # no-ship (this block is inside `if not _no_ship:` and gated
                        # on combat_player_won).  Own player-locked, failure-isolated
                        # write — see _apply_loot_on_win (§5.2/§7.6).
                        loot_outcome = await self._apply_loot_on_win(
                            db,
                            player=player,
                            player_id=player_id,
                            bounty=bounty,
                            player_loadout=player_loadout,
                            cfg=cfg,
                        )

                bonus_msg = " (2x combat bonus!)" if bonus_won else ""
                return (
                    CheckResponse(
                        result=CheckResult.CORRECT,
                        bounty_id=bounty.id,
                        message=f"Bounty captured! +{total_reward:,}cr{bonus_msg}",
                        combat_won=True,
                        division=division,
                        criminal_name=bounty.criminal_name,
                        reward=winner_reward,
                        combat_result=_serialize_fight_results(fight_results) if fight_results else None,
                        bonus_won=bonus_won,
                        total_reward=total_reward,
                        criminal_ship=bounty.criminal_ship,
                        reward_per_sys=getattr(bounty, "reward_per_sys", None),
                        route_length=len(list(getattr(bounty, "route", None) or [])),
                        payout_breakdown=payout_breakdown,
                        loot=loot_outcome,
                    ),
                    (bounty, True),
                )

            # SILVER / GOLD / PLATINUM: Mandatory combat gate.
            if _no_ship:
                duel_won = True
                fight_results = None
            else:
                _pvc_dr_silver = resolve_constant(cfg, "pvc_damage_reduction", GameConstants.PVC_DAMAGE_REDUCTION)
                fight_results = await self.combat_service.fight_ships(
                    player_loadout,
                    criminal_loadout,
                    context="bounty_pvc",
                    log_result=True,
                    pvc_damage_reduction=_pvc_dr_silver,
                    session=db,
                    guild_id=player.guild_id,
                    combatant1_user_id=player.user_id,
                    combatant2_user_id=None,  # NPC side
                    combatant1_label=_player_label,
                    combatant2_label=_criminal_label,
                )
                # P2-T8b: player is always combatant1 (loadout1 / side-1).
                # winner_side==1 → player won; winner_side==2 → criminal won.
                # Stalemate follows the loss path — criminal escapes, checks reset (spec §9).
                duel_won = fight_results.winner_side == 1

            if duel_won:
                rewards = await self.calc_rewards(db, bounty, cfg=cfg)
                await self.distribute_rewards(db, bounty, rewards)
                payout_breakdown = await self._build_payout_breakdown(db, rewards)
                winner_reward = next((r.credits_earned for r in rewards if r.is_winner), 0)
                # T5 LOOT HOOK (Silver+) — fires ONLY on a real combat KILL:
                # `fight_results is not None and winner_side == 1`.  The
                # `fight_results is not None` guard EXCLUDES the no-ship shortcut
                # (`duel_won=True, fight_results=None`) — a non-kill capture grants
                # NO loot (§5.2 defensive branch).  Stalemate/loss never reach here
                # (winner_side None/2 → duel_won False).  Own player-locked,
                # failure-isolated write (§5.2/§7.6).
                loot_outcome = None
                if fight_results is not None and fight_results.winner_side == 1:
                    loot_outcome = await self._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=player_id,
                        bounty=bounty,
                        player_loadout=player_loadout,
                        cfg=cfg,
                    )
                return (
                    CheckResponse(
                        result=CheckResult.CORRECT,
                        bounty_id=bounty.id,
                        message=f"Combat victory! Defeated {bounty.criminal_name}! +{winner_reward:,}cr",
                        combat_won=True,
                        division=division,
                        criminal_name=bounty.criminal_name,
                        reward=winner_reward,
                        total_reward=winner_reward,
                        combat_result=_serialize_fight_results(fight_results) if fight_results else None,
                        reward_per_sys=getattr(bounty, "reward_per_sys", None),
                        route_length=len(list(getattr(bounty, "route", None) or [])),
                        payout_breakdown=payout_breakdown,
                        loot=loot_outcome,
                    ),
                    (bounty, True),
                )

            # LOSS or STALEMATE: Criminal escapes checks — reset bounty location (spec §9)
            await self._reset_bounty_checks(db, bounty)
            _is_stalemate = fight_results is not None and fight_results.is_stalemate
            escape_msg = (
                f"{bounty.criminal_name} fought you to a stalemate and escaped!"
                if _is_stalemate
                else f"{bounty.criminal_name} defeated you in combat and escaped!"
            )
            return (
                CheckResponse(
                    result=CheckResult.CORRECT,
                    bounty_id=bounty.id,
                    message=escape_msg,
                    combat_won=False,
                    division=division,
                    criminal_name=bounty.criminal_name,
                    combat_result=_serialize_fight_results(fight_results) if fight_results else None,
                ),
                (bounty, False),
            )

        # INCORRECT — system was in the route but not the answer
        proximity_hint = False
        recently_spotted = False
        distance = None
        with contextlib.suppress(ValueError, IndexError):
            answer_idx = bounty.route.index(bounty.answer)
            system_idx = bounty.route.index(system_name)
            distance = answer_idx - system_idx
            close_threshold = resolve_constant(cfg, "close_bounty_threshold", GameConstants.CLOSE_BOUNTY_THRESHOLD)
            if 0 < distance < close_threshold:
                proximity_hint = True
            # recently_spotted: criminal was here 1..B stops ago, where B is the
            # per-bounty look-ahead window rolled at spawn (resolve_spotted_window).
            recently_spotted = is_recently_spotted(distance, resolve_spotted_window(bounty))

        await self.bounty_repo.update(db, bounty)
        flogger.debug(f"Player {player_id} checked {system_name} on bounty {bounty.id}: incorrect")

        if recently_spotted:
            inc_message = f"{bounty.criminal_name} was recently spotted here! They're close..."
        else:
            inc_message = f"No sign of {bounty.criminal_name} at {system_name}"

        return (
            CheckResponse(
                result=CheckResult.INCORRECT,
                bounty_id=bounty.id,
                criminal_name=bounty.criminal_name,
                message=inc_message,
                division=division,
                proximity_hint=proximity_hint,
                distance_to_answer=distance,
                recently_spotted=recently_spotted,
            ),
            (bounty, False),
        )

    # ------------------------------------------------------------------
    # Bounty Announcement Live-Edit
    # ------------------------------------------------------------------

    async def _edit_bounty_announcement(self, db: AsyncSession, bounty: Bounty, captured: bool = False) -> None:
        """Edit the bounty's Discord announcement to reflect updated checked systems.

        Post-A.48 unified rendering: the gateway is the rendering authority. We
        post the structured payload (LoadoutResponse + metadata) and let the
        gateway re-render with the shared `build_loadout_embed`.

        Non-fatal — if the message lookup or edit fails, logs a warning and continues.

        Args:
            db:       Async database session.
            bounty:   The bounty whose announcement should be updated.
            captured: When True, the embed shows the "CAPTURED" state (green
                      color, updated title, "**Captured**" Bounty Ends value).
        """
        try:
            import os

            import httpx
            from persist.repositories.criminal_repository import (  # pylint: disable=reimported,redefined-outer-name
                CriminalRepository,
            )
            from persist.repositories.discord_message_repository import DiscordMessageRepository
            from utils.bounty_announcement_payload import build_bounty_announcement_request

            msg_repo = DiscordMessageRepository()
            discord_msg = await msg_repo.get_by_guild_type_and_reference(
                db, bounty.guild_id, "bounty_announcement", bounty.id
            )

            if discord_msg is None:
                flogger.debug(f"No announcement message found for bounty {bounty.id}, skipping edit")
                return

            # Look up the criminal's icon (non-fatal if not found)
            criminal_icon: str | None = None
            try:
                criminal_repo = CriminalRepository()
                criminal = await criminal_repo.get_by_name(db, bounty.criminal_name)
                if criminal is not None:
                    criminal_icon = getattr(criminal, "icon", None) or None
                    flogger.debug(
                        f"_edit_bounty_announcement: criminal icon for {bounty.criminal_name!r}: {criminal_icon!r}"
                    )
            except Exception as _icon_exc:  # pylint: disable=broad-exception-caught
                flogger.debug(
                    f"_edit_bounty_announcement: could not fetch criminal icon for "
                    f"{bounty.criminal_name!r}: {_icon_exc}"
                )

            # Build the structured edit payload (LoadoutResponse + metadata).
            # role mention suppressed on edits; route_map_url=None preserves the
            # image URL that was set on the original send (the gateway's edit
            # path doesn't unset the image when image_url=None unless we pass
            # set_image; in practice the original embed retains the route map).
            edit_payload = await build_bounty_announcement_request(
                db,
                bounty,
                criminal_icon=criminal_icon,
                route_map_url=None,
                bounty_hunter_role_id=None,
                captured=captured,
            )

            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"

            channel_id = discord_msg.channel_id
            async with httpx.AsyncClient() as client:
                resp = await client.put(
                    f"{gateway_url}/announcements/bounty/channel/{channel_id}/message/{discord_msg.message_id}",
                    json=edit_payload,
                    timeout=10,
                )
            resp.raise_for_status()
            flogger.info(f"Edited bounty announcement for bounty {bounty.id} (message {discord_msg.message_id})")

        except Exception as e:
            flogger.warning(f"Failed to edit bounty announcement for bounty {bounty.id}: {e}")

    async def _push_bounty_cache_after_capture(self, db: AsyncSession, guild_id: int) -> None:
        """Push the updated active bounty list to the gateway autocomplete cache after a capture.

        Called from check_bounty when at least one bounty was captured so that the
        captured bounty is immediately removed from the /route and /bounties autocomplete
        dropdowns without waiting for the next spawn/expire push or TTL expiry.

        Non-fatal — logs a warning on failure and never blocks the check response.

        Args:
            db:       Async database session (within an active session block).
            guild_id: The Discord guild ID to push bounties for.
        """
        try:
            import os

            import httpx

            bounties_raw = await self.bounty_repo.get_active_by_guild(db, guild_id)

            # Serialise ORM objects to plain dicts (exclude SQLAlchemy internal keys).
            bounty_dicts: list[dict] = []
            for b in bounties_raw:
                if isinstance(b, dict):
                    bounty_dicts.append(b)
                else:
                    d = {k: v for k, v in b.__dict__.items() if not k.startswith("_")}
                    # Convert ALL datetime fields to ISO strings for JSON serialisation.
                    for key, val in list(d.items()):
                        if hasattr(val, "isoformat"):
                            d[key] = val.isoformat()
                    bounty_dicts.append(d)

            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"
            token = os.getenv("INTERNAL_AUTH_TOKEN", "")
            headers = {"X-Internal-Auth": token} if token else {}

            # SSRF guard: coerce to int — non-numeric values raise ValueError,
            # caught by the surrounding try/except as a warning.
            safe_guild = int(guild_id)
            from shared.http_retry import with_transient_retry  # deferred — avoids forkserver mock-shared collision

            async with httpx.AsyncClient() as client:
                await with_transient_retry(
                    client.post,
                    f"{gateway_url}/internal/autocomplete/bounty-cache/{quote(str(safe_guild), safe='')}",
                    json={"bounties": bounty_dicts},
                    headers=headers,
                    timeout=5.0,
                )
            flogger.debug(
                f"check_bounty: pushed bounty cache after capture for guild={guild_id} remaining={len(bounty_dicts)}"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"check_bounty: failed to push bounty cache to gateway after capture for guild={guild_id}: {e}"
            )

    async def _delete_bounty_announcement(self, db: AsyncSession, bounty: Bounty) -> None:
        """Delete the bounty announcement from Discord and clean up the DB record.

        Non-fatal — exceptions are caught and logged without propagating.

        Args:
            db:     Async database session.
            bounty: The bounty whose announcement should be deleted.
        """
        try:
            import os

            import httpx
            from persist.repositories.discord_message_repository import DiscordMessageRepository

            msg_repo = DiscordMessageRepository()
            discord_msg = await msg_repo.get_by_guild_type_and_reference(
                db, bounty.guild_id, "bounty_announcement", bounty.id
            )

            flogger.debug(
                f"_delete_bounty_announcement: bounty_id={bounty.id}, "
                f"message_found={discord_msg is not None}, "
                f"reference_id={getattr(discord_msg, 'reference_id', None) if discord_msg else None}"
            )

            if discord_msg is None:
                flogger.debug(f"No announcement message found for bounty {bounty.id}, skipping delete")
                return

            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"

            try:
                channel_id = discord_msg.channel_id
                async with httpx.AsyncClient() as client:
                    resp = await client.delete(
                        f"{gateway_url}/channels/{channel_id}/messages/{discord_msg.message_id}",
                        timeout=10,
                    )
                # 404 is OK — message may have been manually deleted
                if resp.status_code not in (200, 204, 404):
                    resp.raise_for_status()
                flogger.info(f"Deleted Discord message {discord_msg.message_id} for bounty {bounty.id}")
            except Exception as e:
                flogger.warning(f"Failed to delete Discord message for bounty {bounty.id}: {e}")

            await msg_repo.delete_by_guild_type_and_reference(db, bounty.guild_id, "bounty_announcement", bounty.id)
            flogger.info(f"Deleted announcement record for completed/escaped bounty {bounty.id}")

        except Exception as e:
            flogger.warning(f"Failed to delete announcement for bounty {bounty.id}: {e}")

    async def _post_capture_payout(self, db: AsyncSession, guild_id: int, bounty, outcome) -> None:
        """Non-fatal: POST a payout embed to hunting_channel_id after a capture.

        Resolves the winner's display name from their user record, builds a short
        "💰 Payout" embed, and POSTs it to the guild's configured hunting_channel_id.

        Args:
            db:       Async database session.
            guild_id: Discord guild ID.
            bounty:   The captured Bounty ORM instance.
            outcome:  The CheckResponse for this capture (carries reward info).
        """
        import os

        import httpx as _httpx
        from persist.repositories.user_repository import UserRepository
        from utils.bounty_announcement_payload import build_capture_payout_embed

        try:
            config_repo = ConfigRepository()
            config = await config_repo.get_by_guild_id(db, guild_id)
            if not config:
                return
            hunting_channel_id = getattr(config, "hunting_channel_id", None)
            if not hunting_channel_id:
                return

            # Resolve winner display name: prefer display_name, fall back to discord_username
            winner_name = "A bounty hunter"
            win_user_id = getattr(bounty, "win_user_id", None)
            if win_user_id:
                user_repo = UserRepository()
                user = await user_repo.get_by_discord_id(db, win_user_id)
                if user:
                    winner_name = (
                        getattr(user, "display_name", None)
                        or getattr(user, "discord_username", None)
                        or "A bounty hunter"
                    )

            reward = getattr(outcome, "reward", None) or getattr(bounty, "reward", 0)
            total_reward = getattr(outcome, "total_reward", None)
            bonus_won = getattr(outcome, "bonus_won", False)

            # Pass reward_per_sys and route_length from the bounty if available
            reward_per_sys = getattr(bounty, "reward_per_sys", None)
            route = getattr(bounty, "route", None)
            route_length = len(list(route)) if route is not None else None

            embed_dict = build_capture_payout_embed(
                criminal_name=bounty.criminal_name,
                division=getattr(bounty, "division", ""),
                reward=reward,
                winner_name=winner_name,
                total_reward=total_reward,
                bonus_won=bonus_won,
                reward_per_sys=reward_per_sys,
                route_length=route_length,
                combat_result=getattr(outcome, "combat_result", None),
            )

            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"

            async with _httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{gateway_url}/channels/{hunting_channel_id}/messages",
                    json={"content": embed_dict, "text_content": None},
                    timeout=5.0,
                )
            resp.raise_for_status()
            flogger.debug(
                f"_post_capture_payout: posted payout embed for bounty={bounty.id} "
                f"guild={guild_id} channel={hunting_channel_id}"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"_post_capture_payout: failed for bounty={bounty.id}: {e}")

    # ------------------------------------------------------------------
    # Reward Calculation & Distribution
    # ------------------------------------------------------------------

    async def calc_rewards(
        self,
        db: AsyncSession,
        bounty: Bounty,
        cfg=None,
    ) -> list[RewardInfo]:
        """Calculate rewards for all contributors to a completed bounty.

        Algorithm (winner-reserve / consolation-pool model):
        1. Compute winner_reserve = max(0, floor(bounty.reward * BOUNTY_WINNER_RESERVE_FACTOR))
        2. consolation_pool = bounty.reward - winner_reserve
        3. For each non-winner player: credit_reward = reward_per_sys * systems_checked
           (deducted from consolation_pool; pool is never allowed below 0)
        4. Winner gets: winner_reserve + max(0, remaining consolation_pool)
        5. XP rules:
           - Failed checkers: XP = 0 (no XP for missed checks)
           - Winner: xp_earned = int(total_winner_credits * BOUNTY_REWARD_TO_XP_GAIN_MULT)

        Args:
            db:     Async database session (unused directly, kept for API consistency).
            bounty: Completed bounty to calculate rewards for.

        Returns:
            List of RewardInfo for each contributing player.
        """
        _winner_reserve_factor = resolve_constant(
            cfg, "bounty_winner_reserve_factor", GameConstants.BOUNTY_WINNER_RESERVE_FACTOR
        )
        winner_reserve = max(0, int(bounty.reward * _winner_reserve_factor))
        consolation_pool = bounty.reward - winner_reserve
        rps = bounty.reward_per_sys
        winner_id = bounty.checked.get(bounty.answer, -1)

        # Count systems checked per player
        player_checks: dict[int, int] = {}
        for _system, checker_id in bounty.checked.items():
            # Sentinel values (< 0): -1 = unchecked, -2 = checked-but-forfeited
            # (player promoted past this bounty's division before payout).
            # Both are excluded from reward distribution.
            if checker_id > 0:
                player_checks[checker_id] = player_checks.get(checker_id, 0) + 1

        rewards: list[RewardInfo] = []

        # Non-winner contributors: pay rps * checks from consolation pool; XP = 0
        for player_id, check_count in player_checks.items():
            if player_id == winner_id:
                continue
            credit_reward = rps * check_count
            # Cap deduction so consolation_pool never goes negative
            credit_reward = min(credit_reward, consolation_pool)
            consolation_pool -= credit_reward

            rewards.append(
                RewardInfo(
                    player_id=player_id,
                    credits_earned=credit_reward,
                    xp_earned=0,  # No XP for failed (non-winning) checkers
                    is_winner=False,
                    systems_checked_count=check_count,
                )
            )

        # Winner gets winner_reserve + any remaining consolation pool
        if winner_id != -1:
            remaining_consolation = max(0, consolation_pool)
            total_winner_credits = winner_reserve + remaining_consolation
            _xp_mult = resolve_constant(
                cfg, "bounty_reward_to_xp_gain_mult", GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT
            )
            xp_reward = int(total_winner_credits * _xp_mult)
            winner_checks = player_checks.get(winner_id, 0)

            rewards.append(
                RewardInfo(
                    player_id=winner_id,
                    credits_earned=total_winner_credits,
                    xp_earned=xp_reward,
                    is_winner=True,
                    systems_checked_count=winner_checks,
                )
            )

        return rewards

    async def distribute_rewards(
        self,
        db: AsyncSession,
        bounty: Bounty,
        rewards: list[RewardInfo],
    ) -> list[RewardInfo]:
        """Apply calculated rewards to players and update bounty status.

        For each player:
        1. Add credits to player
        2. Add XP to player (skipped for classic mode players)
        3. Increment systems_checked count
        4. Increment bounty_wins for the winner
        5. Update bounty status to 'completed'

        B.48: previously also computed level-up flags, now removed.

        Args:
            db:      Async database session.
            bounty:  The bounty being completed.
            rewards: Pre-calculated reward list from :meth:`calc_rewards`.

        Returns:
            Updated RewardInfo list (post-mutation).
        """
        modified_players = []
        # D5-T2b: lock EACH rewarded player's row FOR UPDATE before the credit
        # read-modify-write so concurrent credit ops (shop buy / transfer / duel,
        # or another /check payout touching the same player) serialise instead of
        # losing an update.  Two ordering rules from the global lock-ordering
        # contract (persist/repositories/AGENTS.md) apply:
        #   1. Aggregate-first: distribute_rewards runs inside check_bounty, which
        #      already holds the Bounty row lock (P2-T10).  This adds the Player
        #      lock *after* the Bounty lock — Bounty → Players, never Player →
        #      Bounty — so no AB-BA cycle against /check itself is created.
        #   2. Players in ASCENDING player_id order — matches transfer_credits /
        #      ships.transfer_ship / duel accept (the only other multi-player
        #      credit paths), so a multi-checker payout (1 winner + N consolation)
        #      and a concurrent multi-player credit op acquire their shared player
        #      rows in the same order and never deadlock (40P01).
        # We iterate a player_id-sorted view of `rewards`; the returned `rewards`
        # list itself is left in its original order so callers' per-player display
        # ordering (_build_payout_breakdown, winner lookup) is unchanged.
        #
        # get_by_id_for_update carries populate_existing=True (D5-T1), so even
        # though check_bounty pre-loaded this same player UNLOCKED at the top of
        # the call, the FOR UPDATE re-fetch overwrites the in-memory object with
        # the freshly-committed credits read under the lock.  The mutation below
        # therefore operates on the fresh locked balance — no stale-read clobber.
        for reward in sorted(rewards, key=lambda r: r.player_id):
            player = await self.player_repo.get_by_id_for_update(db, reward.player_id)
            if player is None:
                flogger.warning(f"Player {reward.player_id} not found during reward distribution")
                continue

            # Apply credits
            player.credits += reward.credits_earned
            player.lifetime_credits += reward.credits_earned

            # Apply XP (skip for classic mode players)
            if not player.classic_mode:
                player.xp += reward.xp_earned

            # Update stats
            player.systems_checked += reward.systems_checked_count
            if reward.is_winner:
                player.bounty_wins += 1

            # B.48: no level-up detection — the level concept was deleted
            # along with the hardcoded XP_LEVEL_BOUNDARIES.

            modified_players.append(player)

        # Update bounty status (commit=False; this service owns the explicit commit below).
        # B.34 closeout: previously this method relied on bounty_repo.update()'s
        # default commit=True to flush ALL pending changes (the direct ORM
        # mutations on modified_players above). That works only by accident —
        # if any future change set commit=False here, the cross-table player
        # mutations would silently roll back. Now the service owns the
        # transaction explicitly. (Note: distribute_rewards is called from
        # check_bounty which already issues an explicit db.commit() at the end
        # of its loop, so this commit is the inner cross-table flush; the outer
        # check_bounty commit is a no-op when no further changes are pending.)
        bounty.status = "completed"
        # Store the Discord user ID (User.id = snowflake), NOT the player table PK.
        # modified_players already holds the fetched Player objects; the winning
        # player's .user_id FK is the Discord snowflake we need.
        _winning_player = next(
            (p for p in modified_players if any(r.player_id == p.id and r.is_winner for r in rewards)),
            None,
        )
        bounty.win_user_id = _winning_player.user_id if _winning_player else None
        await self.bounty_repo.update(db, bounty, commit=False)
        await db.commit()

        # Refresh all modified players for accurate state.
        for player in modified_players:
            await db.refresh(player)

        return rewards

    async def _build_payout_breakdown(
        self,
        db: AsyncSession,
        rewards: list[RewardInfo],
    ) -> list[dict]:
        """Build a per-player payout breakdown list for embed rendering.

        P6-T1: Fetches ALL players in a single ``WHERE id IN (...)`` query
        instead of one ``get_by_id`` per reward (N+1 → 1).  The output list
        preserves the original ``rewards`` ordering so callers' per-player
        display ordering is unchanged.

        Args:
            db:      Async database session.
            rewards: Reward list from :meth:`calc_rewards` (post-distribution).

        Returns:
            List of dicts with keys: player_display_name, role, amount.
            role is 'capture claim' for the winner, 'system check' for others.
        """
        if not rewards:
            return []

        # P6-T1: single batched fetch via player_repo.get_by_ids (WHERE id IN (...))
        # instead of one get_by_id per reward.  Result is indexed by player_id so
        # the output loop below can preserve the original rewards ordering.
        player_ids = [r.player_id for r in rewards]
        players = await self.player_repo.get_by_ids(db, player_ids)
        players_by_id = {p.id: p for p in players}

        payout_breakdown: list[dict] = []
        for reward in rewards:
            player = players_by_id.get(reward.player_id)
            if player is None:
                continue
            # Use display_name if available and non-empty, else fall back to str(user_id)
            display_name = getattr(player, "display_name", None)
            if not display_name:
                display_name = str(getattr(player, "user_id", reward.player_id))
            role = "capture claim" if reward.is_winner else "system check"
            payout_breakdown.append(
                {
                    "player_display_name": display_name,
                    "role": role,
                    "amount": reward.credits_earned,
                }
            )
        return payout_breakdown

    # ------------------------------------------------------------------
    # Bounty Expiry
    # ------------------------------------------------------------------

    async def expire_bounty(
        self,
        db: AsyncSession,
        bounty_id: int,
    ) -> Bounty | None:
        """Mark a bounty as expired.

        Called when a bounty's end_time is reached without being completed.
        Sets status to 'expired' and persists.

        Args:
            db: Async database session.
            bounty_id: ID of the bounty to expire.

        Returns:
            Updated Bounty object, or None if bounty not found.
        """
        bounty = await self.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            flogger.warning(f"Cannot expire bounty {bounty_id}: not found")
            return None

        # Idempotency guard: protects against the expiry job firing again
        # after a scheduler restart (re-fire of a job that already ran), or
        # a cadence overlap where the bounty was captured between the job
        # being scheduled and it actually executing.  NOT a concurrent-worker
        # guard — at WORKERS=1 only one expiry job runs at a time.
        if bounty.status != "active":
            flogger.warning(f"Cannot expire bounty {bounty_id}: status is {bounty.status}")
            return None

        bounty.status = "expired"
        await self.bounty_repo.update(db, bounty)
        flogger.info(f"Bounty {bounty_id} expired: {bounty.criminal_name}")
        return bounty

    # ------------------------------------------------------------------
    # Bounty Escape
    # ------------------------------------------------------------------

    async def escape_bounty(
        self,
        db: AsyncSession,
        bounty_id: int,
    ) -> tuple[Bounty | None, int]:
        """Mark a bounty as escaped and calculate respawn delay.

        Called when a player loses the combat duel against the criminal.
        Sets status to 'escaped', increments escape_count, and calculates
        respawn delay based on route length (1 minute per system in route).

        The respawn itself is NOT triggered here — the caller (scheduler)
        is responsible for scheduling the respawn after the returned delay.

        Args:
            db: Async database session.
            bounty_id: ID of the bounty where criminal escaped.

        Returns:
            Tuple of (updated Bounty, respawn_delay_minutes).
            Returns (None, 0) if bounty not found.
        """
        bounty = await self.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            flogger.warning(f"Cannot escape bounty {bounty_id}: not found")
            return None, 0

        if bounty.status != "active":
            flogger.warning(f"Cannot escape bounty {bounty_id}: status is {bounty.status}")
            return None, 0

        bounty.status = "escaped"
        bounty.escape_count += 1

        # Respawn delay = len(route) minutes
        respawn_delay = len(bounty.route) if bounty.route else 5
        bounty.respawn_time = datetime.now(UTC) + timedelta(minutes=respawn_delay)

        await self.bounty_repo.update(db, bounty)
        flogger.info(f"Bounty {bounty_id} escaped (count: {bounty.escape_count}), respawn in {respawn_delay} minutes")
        return bounty, respawn_delay

    # ------------------------------------------------------------------
    # Bounty Respawn
    # ------------------------------------------------------------------

    async def respawn_bounty(
        self,
        db: AsyncSession,
        bounty_id: int,
        expiry_minutes: int | None = None,
    ) -> Bounty | None:
        """Respawn an escaped bounty with a new route and answer.

        Keeps the same criminal but generates a fresh route via A*
        and picks a new answer. Resets checked dict and status to 'active'.

        Args:
            db:             Async database session.
            bounty_id:      ID of the escaped bounty to respawn.
            expiry_minutes: Optional override for bounty expiry in minutes.
                            Defaults to 480 (8 hours) if not provided.

        Returns:
            Updated Bounty object with new route/answer, or None on failure.
        """
        bounty = await self.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            flogger.warning(f"Cannot respawn bounty {bounty_id}: not found")
            return None

        if bounty.status != "escaped":
            flogger.warning(f"Cannot respawn bounty {bounty_id}: status is {bounty.status}")
            return None

        # Generate new route (same logic as spawn_bounty step 3)
        cfg = await self.config_repo.get_by_guild_id(db, bounty.guild_id)
        await self.graph_service.load_graph(db)
        jump_gate_systems = self.graph_service.get_systems_with_jump_gates()

        if len(jump_gate_systems) < 2:
            flogger.warning("Not enough systems for respawn route")
            return None

        min_systems = resolve_constant(cfg, "min_route_systems", GameConstants.MIN_ROUTE_SYSTEMS)
        route = self._generate_waypoint_route(jump_gate_systems, min_systems, cfg)

        if route is None:
            flogger.warning(f"Failed to generate respawn route for bounty {bounty_id}")
            return None

        # New answer, re-rolled spotted window, and fresh checked dict
        answer = random.choice(route)
        checked = {system: -1 for system in route}

        # Update bounty
        bounty.route = route
        bounty.answer = answer
        bounty.spotted_window = self._roll_spotted_window(cfg)
        bounty.checked = checked
        bounty.status = "active"
        bounty.respawn_time = None

        # Update end_time based on expiry_minutes (or default 480 minutes)
        expiry = expiry_minutes if expiry_minutes is not None else 480
        bounty.end_time = datetime.now(UTC) + timedelta(minutes=expiry)

        await self.bounty_repo.update(db, bounty)
        flogger.info(f"Bounty {bounty_id} respawned: {bounty.criminal_name} with new route ({len(route)} systems)")
        return bounty
