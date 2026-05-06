from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    status: str
    escape_count: int = 0
    win_user_id: int | None = None


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


class BountyCheckRequest(BaseModel):
    player_id: int
    system_name: str


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
    # Recently spotted: criminal was at this system 1-2 stops ago
    recently_spotted: bool = False
    # Per-outcome proximity hint (mainly for INCORRECT outcomes)
    proximity_hint: bool = False
    distance_to_answer: int | None = None


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
