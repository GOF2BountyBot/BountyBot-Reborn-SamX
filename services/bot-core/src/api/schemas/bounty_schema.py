from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _extract_cargo(criminal_ship: Any) -> dict | None:
    """Project a bounty's ``criminal_ship["cargo"]`` blob into a BountyCargo dict.

    Returns None (no cargo line — never an error) for every legacy / malformed
    shape: ``criminal_ship`` is None or not a dict, the ``cargo`` key is absent or
    not a dict, the name is missing/blank, or the quantity is missing/non-positive.
    """
    if not isinstance(criminal_ship, dict):
        return None
    cargo = criminal_ship.get("cargo")
    if not isinstance(cargo, dict):
        return None
    item_name = cargo.get("item_name")
    quantity = cargo.get("quantity")
    if not isinstance(item_name, str) or not item_name:
        return None
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        return None
    item_type = cargo.get("item_type")
    return {
        "item_name": item_name,
        "item_type": item_type if isinstance(item_type, str) else "",
        "quantity": quantity,
    }


def _inject_cargo(data: Any) -> Any:
    """``mode="before"`` validator body: populate ``cargo`` from ``criminal_ship``.

    Handles both validation inputs: a mapping (``dict``) and an ORM object
    (``from_attributes=True``).  Never raises — a malformed/absent blob yields no
    ``cargo`` key, so the field defaults to ``None``.  An explicitly-supplied
    ``cargo`` (e.g. in a hand-built dict) is left untouched.
    """
    if isinstance(data, dict):
        if data.get("cargo") is not None:
            return data
        derived = _extract_cargo(data.get("criminal_ship"))
        if derived is not None:
            data = {**data, "cargo": derived}
        return data
    # ORM object (or any attribute-bearing object) — always derive from
    # ``criminal_ship``; a real Bounty row has no ``cargo`` attribute, so we never
    # trust a pre-existing one here (it would be a mock artifact, not real data).
    criminal_ship = getattr(data, "criminal_ship", None)
    derived = _extract_cargo(criminal_ship)
    # Wrap so we can overlay the derived (or absent) cargo while preserving attribute
    # access for every other field.  The overlay forces ``cargo`` regardless of any
    # spurious attribute on the wrapped object.
    return _CargoOverlay(data, derived)


class _CargoOverlay:
    """Thin attribute proxy that overlays a derived ``cargo`` onto an ORM object.

    Lets the ``mode="before"`` validator inject ``cargo`` without mutating the ORM
    instance, while Pydantic's ``from_attributes`` reads every other field straight
    through ``getattr``.
    """

    __slots__ = ("_cargo", "_wrapped")

    def __init__(self, wrapped: Any, cargo: dict | None) -> None:
        self._wrapped = wrapped
        self._cargo = cargo

    def __getattr__(self, name: str) -> Any:
        if name == "cargo":
            return self._cargo
        return getattr(self._wrapped, name)


class BountyCargo(BaseModel):
    """The single loot item a criminal carries (LOOT_JOURNAL §5.1 / T4).

    Surfaced read-only on bounty payloads (T4b) so the gateway can advertise what
    is lootable pre-fight.  ``None`` on the parent payload when the bounty has no
    rolled cargo (legacy / no-roll bounties)."""

    model_config = ConfigDict(from_attributes=True)

    item_name: str
    item_type: str
    quantity: int


class BountyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    guild_id: int
    division: str
    criminal_name: str
    criminal_faction: str | None = None
    route: list[str]
    answer: str  # Only visible to admins — omit for player-facing responses
    reward: int
    reward_per_sys: int
    checked: dict[str, int]
    issue_time: datetime
    end_time: datetime | None = None
    tech_level: int
    criminal_ship: dict | None = None
    # T4b: the criminal's lootable cargo, derived read-only from criminal_ship["cargo"].
    cargo: BountyCargo | None = None
    status: str
    escape_count: int = 0
    win_user_id: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_cargo(cls, data: Any) -> Any:
        return _inject_cargo(data)


class BountyCreateRequest(BaseModel):
    guild_id: int
    division: str
    # Most fields are auto-generated during spawn, but allow manual override for admin:
    criminal_name: str | None = None
    tech_level: int | None = None


class BountyPublicResponse(BaseModel):
    """Player-facing bounty info — hides the answer."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    guild_id: int
    division: str
    criminal_name: str
    criminal_faction: str | None = None
    route: list[str]
    reward: int
    reward_per_sys: int
    checked: dict[str, int]
    issue_time: datetime
    end_time: datetime | None = None
    tech_level: int
    status: str
    # T4b: the criminal's lootable cargo, derived read-only from criminal_ship["cargo"].
    cargo: BountyCargo | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_cargo(cls, data: Any) -> Any:
        return _inject_cargo(data)


class BountyCheckRequest(BaseModel):
    player_id: int
    system_name: str


class LootResult(BaseModel):
    """PvC loot result attached to a player COMBAT-WIN ``/check`` outcome.

    The bot-core -> gateway contract for the §5.9 ``<beam-emoji> Loot`` embed
    line (rendered by T8, NOT here).  Populated from the internal
    :class:`~services.bounty_service.LootOutcome` produced on a combat win
    (T5); ``None`` on the parent outcome whenever there is no loot to render
    (no tractor beam equipped / nothing looted — the internal ``none`` state),
    so the gateway omits the Loot field entirely (§5.9 omission rule).

    Only the four *renderable* states are ever emitted here — ``none`` maps to a
    ``None`` parent field, never to a ``LootResult`` instance, hence it is
    excluded from :attr:`outcome`'s ``Literal``.

    Fields (the §5.9 set):

    * ``outcome``       — ``looted`` | ``partial`` | ``failed`` | ``cargo_full``.
    * ``item_name``     — looted item's display name (``None`` for failed/cargo_full).
    * ``qty_looted``    — units actually taken (``< qty_total`` on ``partial``).
    * ``qty_total``     — units that were available to loot.
    * ``tractor_emoji`` — the equipped beam's custom Discord emoji (field-name emoji).
    * ``cargo_current`` / ``cargo_max`` — back the ``cargo_full`` "(NN/XX)" line.
    """

    model_config = ConfigDict(from_attributes=True)

    outcome: Literal["looted", "partial", "failed", "cargo_full"]
    item_name: str | None = None
    qty_looted: int = 0
    qty_total: int = 0
    tractor_emoji: str | None = None
    # Present (NN/XX) on the cargo_full outcome; None otherwise.
    cargo_current: int | None = None
    cargo_max: int | None = None


class BountyCheckOutcome(BaseModel):
    """Per-bounty outcome of a single ``/check`` invocation.

    A single ``POST /bounties/check`` request may produce multiple outcomes
    when several active bounties in the player's division share the checked
    system (B.12 multi-bounty fix). The :class:`BountyCheckResponse` wraps
    a list of these outcomes.
    """

    result: str  # NOT_FOUND, ALREADY_CHECKED, INCORRECT, CORRECT, ON_COOLDOWN
    # bronze: "correct" (auto-captured), silver+: "correct" (combat_win), "correct" (combat_loss)
    bounty_id: int | None = None
    message: str = ""
    new_tier: str | None = None
    # Division / criminal metadata
    division: str | None = None
    criminal_name: str | None = None
    reward: int | None = None
    # Combat result fields (present when combat occurred)
    combat_result: dict | None = None  # FightResults serialized as dict
    combat_won: bool | None = None  # True/False when combat occurred, None otherwise
    # Bronze-specific fields
    bonus_won: bool = False  # True if bronze player won the optional combat bonus
    total_reward: int | None = None  # Final reward (may be 2x for bronze combat win)
    criminal_ship: dict | None = None  # Criminal ship data; returned for bronze so cog can offer bonus duel
    # Payout breakdown (populated on capture outcomes so the cog can render the full embed)
    reward_per_sys: int | None = None
    route_length: int | None = None
    # Per-player payout breakdown: list of dicts with player_display_name, role, amount
    payout_breakdown: list[dict] | None = None
    # Recently spotted: criminal was at this system 1-2 stops ago
    recently_spotted: bool = False
    # Per-outcome proximity hint (mainly for INCORRECT outcomes)
    proximity_hint: bool = False
    distance_to_answer: int | None = None
    # PvC loot result (T6): present only on a player COMBAT WIN with a renderable
    # loot outcome (§5.9). None when there is no loot to render (no beam / nothing
    # looted) — the gateway then omits the Loot field entirely (§5.9 omission rule).
    loot: LootResult | None = None


class BountyCheckResponse(BaseModel):
    """Aggregate response for a single ``/check`` invocation.

    Always contains at least one entry in :attr:`outcomes`. For backwards
    compatibility with single-bounty clients the top-level ``result``,
    ``bounty_id`` and other fields mirror ``outcomes[0]`` whenever the
    invocation produced exactly one outcome.
    """

    # ---- New top-level fields (B.12) ----
    outcomes: list[BountyCheckOutcome] = []
    result_count: int = 0  # len(outcomes) — convenience for clients
    # ---- Legacy single-bounty fields (mirror outcomes[0] for backwards compat) ----
    result: str = "not_found"
    bounty_id: int | None = None
    message: str = ""
    new_tier: str | None = None
    division: str | None = None
    criminal_name: str | None = None
    reward: int | None = None
    combat_result: dict | None = None
    combat_won: bool | None = None
    bonus_won: bool = False
    total_reward: int | None = None
    criminal_ship: dict | None = None
    recently_spotted: bool = False
    # Cooldown timestamp (Unix): when the cooldown expires (populated on ON_COOLDOWN results)
    cooldown_until: int | None = None
    # PvC loot result (T6): legacy single-bounty mirror of outcomes[0].loot, per the
    # existing combat-field mirror convention. None when the first outcome has no loot.
    loot: LootResult | None = None


class CombatBonusRequest(BaseModel):
    """Request body for POST /bounties/combat-bonus (Bronze division only)."""

    player_id: int
    base_reward: int = Field(ge=0, description="Base bounty reward to double on combat win")
    criminal_ship: dict  # The criminal's ship/loadout data to fight against


class CombatBonusResponse(BaseModel):
    """Response for POST /bounties/combat-bonus."""

    won: bool
    bonus_credits: int  # 0 if lost, base_reward if won (total payout becomes 2x)
    combat_result: dict
    message: str


class ClearBountiesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guild_id: int
    tier: str | None = None
    cleared_count: int
    bounty_ids: list[int]
    announcements_deleted: int


class AdminSpawnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guild_id: int
    spawned: list[BountyResponse]
    skipped_tiers: list[str]
    errors: list[str]
