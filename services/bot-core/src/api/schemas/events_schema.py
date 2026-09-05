"""Pydantic v2 schemas for the events router (issue #30, spec §5–6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateEventRequest(BaseModel):
    guild_id: int
    type_slug: str
    duration_days: int = Field(default=7, ge=1, le=60)
    params: dict = Field(default_factory=dict)


class UpdateEventRequest(BaseModel):
    """Editable fields of a draft/scheduled event; omitted fields are unchanged. `params` replaces the whole map."""

    duration_days: int | None = Field(default=None, ge=1, le=60)
    params: dict | None = None


class AddPrizeRequest(BaseModel):
    rank_from: int | None = Field(default=None, ge=1, le=10)
    rank_to: int | None = Field(default=None, ge=1, le=10)
    kind: str = Field(pattern="^(credits|item|ship)$")
    item_ref: str | None = None
    qty: int = Field(ge=1)


class StartEventRequest(BaseModel):
    scheduled_start_at: datetime | None = None


class EndEventRequest(BaseModel):
    payout: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    guild_id: int
    type_slug: str
    state: str
    params: dict
    duration_days: int
    scheduled_start_at: datetime | None = None
    started_at: datetime | None = None
    ends_at: datetime | None = None
    created_by_user_id: int | None = None
    created_at: datetime
    updated_at: datetime


class EventListItem(BaseModel):
    """Compact summary returned by GET /events/guild/{guild_id} — keep small for gateway cache."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    guild_id: int
    type_slug: str
    type_display: str
    state: str
    params: dict
    duration_days: int
    scheduled_start_at: datetime | None = None
    started_at: datetime | None = None
    ends_at: datetime | None = None
    prize_count: int = 0


class PrizeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    rank_from: int | None = None
    rank_to: int | None = None
    kind: str
    item_ref: str | None = None
    qty: int


class EventDetailResponse(EventResponse):
    prizes: list[PrizeResponse] = Field(default_factory=list)
    rules_text: str = ""
    effective_min_fights: int = 1


class StandingEntry(BaseModel):
    player_id: int
    user_id: int
    display_name: str
    value: float
    value_display: str = ""
    rank: int | None
    qualified: bool


class MedalEntry(BaseModel):
    player_id: int
    user_id: int
    display_name: str
    gold: int
    silver: int
    bronze: int
    events: int


class EventTypeInfo(BaseModel):
    slug: str
    display_name: str
    category: str
    params: list[str]  # which param keys this type uses, e.g. ["division"] or ["weapon"]
    param_values: dict[str, list[str]] = Field(default_factory=dict)  # allowed values per key
    rules_template: str = ""  # raw template; call render_rules() for the player-facing string
