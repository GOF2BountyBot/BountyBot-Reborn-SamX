from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DuelRequestCreate(BaseModel):
    challenger_id: int
    target_id: int
    stakes: int = 0
    guild_id: int


class DuelRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    guild_id: int
    challenger_id: int
    target_id: int
    stakes: int
    status: str
    created_at: datetime
    expires_at: datetime | None = None
    challenger_name: str | None = None
    target_name: str | None = None


class DuelResultResponse(BaseModel):
    winner_name: str
    loser_name: str
    is_stalemate: bool = False
    winner_credits: int = 0
    loser_credits: int = 0
