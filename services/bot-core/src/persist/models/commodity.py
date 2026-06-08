from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# Portable JSON type: Postgres uses JSONB; SQLite unit-test suite falls back to JSON.
_JSONB = JSON().with_variant(JSONB(), "postgresql")

from persist.database.tablenames import TableNames
from persist.models.item import Item


class Commodity(Item):
    __tablename__ = TableNames.Commodity.value

    id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Item.value}.id"), primary_key=True)
    tech_level: Mapped[int] = mapped_column(Integer, nullable=True)
    subcategory: Mapped[str] = mapped_column(String, nullable=False)
    extra_atts: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=True, default=dict)

    __mapper_args__ = {
        "polymorphic_identity": "commodity",
        "concrete": False,
    }

    @property
    def price_source(self) -> str | None:
        return (self.extra_atts or {}).get("price_source")

    @property
    def price_range_min_credits(self) -> int | None:
        return (self.extra_atts or {}).get("price_range_min_credits")

    @property
    def price_range_max_credits(self) -> int | None:
        return (self.extra_atts or {}).get("price_range_max_credits")

    @property
    def price_range_min_system(self) -> str | None:
        return (self.extra_atts or {}).get("price_range_min_system")

    @property
    def price_range_max_system(self) -> str | None:
        return (self.extra_atts or {}).get("price_range_max_system")

    @property
    def highest_non_loma_price(self) -> int | None:
        return (self.extra_atts or {}).get("highest_non_loma_price")

    @property
    def highest_non_loma_system(self) -> str | None:
        return (self.extra_atts or {}).get("highest_non_loma_system")
