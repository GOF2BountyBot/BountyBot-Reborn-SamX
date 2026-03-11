from sqlalchemy import Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from persist.database.tablenames import TableNames
from persist.models.weapon import Weapon


class PrimaryWeapon(Weapon):
    __tablename__ = TableNames.PrimaryWeapon.value

    id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Weapon.value}.id"), primary_key=True)
    dps: Mapped[float] = mapped_column(Float, nullable=False)

    __mapper_args__ = {
        'polymorphic_identity': 'primary_weapon',
        'concrete': False,
    }
