from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, ForeignKey
from persist.models.weapon import Weapon
from persist.database.tablenames import TableNames

class SecondaryWeapon(Weapon):
    __tablename__ = TableNames.SecondaryWeapon.value

    id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.Weapon.value}.id"), primary_key=True)
    damage: Mapped[int] = mapped_column(Integer, nullable=False)
    loading_speed: Mapped[int] = mapped_column(Integer, nullable=True)

    __mapper_args__ = {
        'polymorphic_identity': 'secondary_weapon',
        'concrete': False,
    }