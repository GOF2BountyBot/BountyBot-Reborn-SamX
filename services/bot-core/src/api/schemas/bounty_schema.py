from datetime import datetime

from pydantic import BaseModel, ConfigDict


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


class BountyCheckResponse(BaseModel):
    result: str  # NOT_FOUND, ALREADY_CHECKED, INCORRECT, CORRECT, ON_COOLDOWN
    bounty_id: int | None = None
    message: str = ""
    new_tier: str | None = None


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
