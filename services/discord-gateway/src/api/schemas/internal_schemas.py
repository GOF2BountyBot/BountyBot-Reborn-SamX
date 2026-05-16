"""Internal push payload schemas for bot-core → gateway autocomplete cache updates."""

from pydantic import BaseModel


class ShopCachePush(BaseModel):
    """Payload for pushing shop stock into the gateway ShopCog autocomplete cache."""

    items: list[dict]  # same shape as GET /api/v1/shops/guild/{gid}/tier/{tier} response


class BountyCachePush(BaseModel):
    """Payload for pushing active bounty list into the gateway BountyCog autocomplete cache."""

    bounties: list[dict]  # full active bounty list for one guild
