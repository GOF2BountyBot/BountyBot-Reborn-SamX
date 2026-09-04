"""Internal push payload schemas for bot-core → gateway autocomplete cache updates."""

from pydantic import BaseModel


class ShopCachePush(BaseModel):
    """Payload for pushing shop stock into the gateway ShopCog autocomplete cache."""

    items: list[dict]  # same shape as GET /api/v1/shops/guild/{gid}/tier/{tier} response


class BountyCachePush(BaseModel):
    """Payload for pushing active bounty list into the gateway BountyCog autocomplete cache."""

    bounties: list[dict]  # full active bounty list for one guild


class EventCachePush(BaseModel):
    """Payload for pushing event list into the gateway EventsCog autocomplete cache."""

    events: list[dict]  # full event list for one guild (EventListItem shape)


class DuelCachePush(BaseModel):
    """Payload for pushing duel lists into the gateway DuelCog autocomplete caches.

    pending_duels: duels where player_id is the target (for /duel-accept, /duel-reject)
    outgoing_duels: duels where player_id is the challenger (for /duel-cancel)
    """

    pending_duels: list[dict]  # duels where this player is the target
    outgoing_duels: list[dict]  # duels where this player is the challenger
