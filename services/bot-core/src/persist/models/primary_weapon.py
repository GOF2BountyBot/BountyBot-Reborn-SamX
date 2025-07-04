from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Float, Integer, ForeignKey
from persist.models.weapon import Weapon
from persist.database.tablenames import TableNames

class PrimaryWeapon(Weapon):
    __tablename__ = TableNames.PrimaryWeapon.value

    id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Weapon.value}.id"), primary_key=True)
    dps: Mapped[float] = mapped_column(Float, nullable=False)

    __mapper_args__ = {
        'polymorphic_identity': 'primary_weapon',
        'concrete': False,
    }