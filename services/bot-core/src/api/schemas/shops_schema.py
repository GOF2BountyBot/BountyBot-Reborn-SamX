from pydantic import BaseModel, Field


def _guild_shop_to_response(item: object) -> "ShopItemResponse":
    """Convert a GuildShop ORM object (or dict) to a ShopItemResponse.

    This mirrors the construction used by the GET tier / tech-level / single-item
    endpoints so that ``/refresh`` returns the SAME item shape as those GETs.

    Enrichment fields (``emoji``, ``dps``, ``shield``, ``armour``, ``hull_hp``)
    are not available at refresh time without an extra DB round-trip, so they
    are left as their ``None`` defaults — matching the nullable schema fields.
    """
    if isinstance(item, dict):
        src = item
        get = lambda field, default=None: src.get(field, default)  # noqa: E731
    else:
        get = lambda field, default=None: getattr(item, field, default)  # noqa: E731

    last_restocked = get("last_restocked")
    if hasattr(last_restocked, "isoformat"):
        last_restocked_str: str = last_restocked.isoformat()
    else:
        last_restocked_str = str(last_restocked) if last_restocked is not None else ""

    return ShopItemResponse(
        id=get("id"),
        guild_id=get("guild_id"),
        tier=get("tier"),
        tech_level=get("tech_level"),
        item_type=get("item_type"),
        item_name=get("item_name"),
        quantity=get("quantity"),
        price=get("price"),
        last_restocked=last_restocked_str,
        refresh_interval_hours=get("refresh_interval_hours"),
        # Enrichment fields not populated at refresh time — consistent with schema defaults
        emoji=get("emoji"),
        dps=get("dps"),
        shield=get("shield"),
        armour=get("armour"),
        hull_hp=get("hull_hp"),
    )


def serialize_refresh_response(refresh_details: dict) -> dict:
    """Serialize refresh_shop() result to a JSON-safe dict.

    ``refresh_shop()`` returns ``{"items": [GuildShop, ...], ...}`` where the
    list contains raw SQLAlchemy ORM objects.  FastAPI cannot serialize those
    directly (PydanticSerializationError: Unable to serialize unknown type:
    GuildShop).  This helper replaces the ``"items"`` value with dicts produced
    by ``ShopItemResponse.model_dump(mode="json")`` so that the shape is
    identical to what the GET tier/tech-level/single-item endpoints return.
    """
    serialized = dict(refresh_details)
    raw_items = serialized.get("items")
    if raw_items is not None:
        serialized["items"] = [_guild_shop_to_response(item).model_dump(mode="json") for item in raw_items]
    return serialized


# Response Models
class ShopItemResponse(BaseModel):
    id: int
    guild_id: int
    tier: str
    tech_level: int
    item_type: str
    item_name: str
    quantity: int
    price: int
    last_restocked: str
    refresh_interval_hours: int
    emoji: str | None = None
    # Item stat fields (populated from the underlying item definition)
    dps: float | None = None  # weapons: DPS value
    shield: int | None = None  # modules: shield HP from extra_atts
    armour: int | None = None  # modules: armour HP from extra_atts
    hull_hp: int | None = None  # ships: hull/armour HP


class ShopSummaryResponse(BaseModel):
    guild_id: int
    total_items: int
    shops: dict[str, dict[str, int]]


class PurchaseRequest(BaseModel):
    player_id: int
    shop_item_id: int
    quantity: int = Field(gt=0, default=1)


class SellRequest(BaseModel):
    player_id: int
    item_name: str
    quantity: int = Field(gt=0, default=1)


class TransactionResponse(BaseModel):
    player_id: int
    item_type: str
    item_name: str
    quantity: int
    total_cost: int | None = None
    total_value: int | None = None
    remaining_credits: int
    transaction_type: str


class RefreshShopRequest(BaseModel):
    guild_id: int
    tier: str = Field(pattern="^(Bronze|Silver|Gold|Platinum)$")
    force_tech_level: int | None = Field(None, ge=1, le=10)


class ShipPurchaseRequest(BaseModel):
    player_id: int
    shop_item_id: int


class ShipSellRequest(BaseModel):
    player_id: int
    ship_id: int
    clear_equipment: bool = False
    target_tier: str = Field(default="Bronze", pattern="^(Bronze|Silver|Gold|Platinum)$")
