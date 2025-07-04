from enum import Enum

class TableNames(Enum):
    SchemaVersion       = "schema"
    Item                = "items"
    Module              = "modules"
    Weapon              = "weapon"
    PrimaryWeapon       = "primary_weapon"
    SecondaryWeapon     = "secondary_weapon"
    TurretWeapon        = "turret_weapon"