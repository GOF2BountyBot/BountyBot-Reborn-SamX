from sqlalchemy import ARRAY, JSON, Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from persist.database.tablenames import TableNames
from persist.models.base import Base


class Ship(Base):
    __tablename__ = TableNames.Ship.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)

    armour: Mapped[int] = mapped_column(Integer)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    cargo: Mapped[int] = mapped_column(Integer)
    compatible_skins: Mapped[dict[str, str]] = mapped_column(JSON, nullable=True)

    emoji: Mapped[str] = mapped_column(String, nullable=True)
    icon: Mapped[str] = mapped_column(String, nullable=True)
    manufacturer: Mapped[str] = mapped_column(String)

    handling: Mapped[int] = mapped_column(Integer)
    shop_spawn_rate: Mapped[float] = mapped_column(Float, nullable=True)
    skinnable: Mapped[bool] = mapped_column(Boolean, default=False)

    max_modules: Mapped[int] = mapped_column(Integer)
    max_primaries: Mapped[int] = mapped_column(Integer)
    max_secondaries: Mapped[int] = mapped_column(Integer)
    max_turrets: Mapped[int] = mapped_column(Integer)
    builtin_modules: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)

    texture_regions: Mapped[int] = mapped_column(Integer)
    save_due: Mapped[bool] = mapped_column(Boolean, default=False)

    model: Mapped[str] = mapped_column(String, nullable=True)
    norm_spec: Mapped[str] = mapped_column(String, nullable=True)

    value: Mapped[int] = mapped_column(Integer)
    wiki: Mapped[str] = mapped_column(String, nullable=True)

    assets: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)

    __mapper_args__ = {
        "polymorphic_identity": "ship",
        "concrete": False,
    }
