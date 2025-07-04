from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Float, Boolean, Integer, ForeignKey
from persist.models.weapon import Weapon
from persist.database.tablenames import TableNames

class TurretWeapon(Weapon):
    __tablename__ = TableNames.TurretWeapon.value

    id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Weapon.value}.id"), primary_key=True)
    dps: Mapped[float] = mapped_column(Float, nullable=False)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)

    __mapper_args__ = {
        'polymorphic_identity': 'turret_weapon',
        'concrete': False,
    }