from enum import Enum

class TableNames(Enum):
    SchemaVersion       = "schema"
    Item                = "item"
    Module              = "module"
    Weapon              = "weapon"
    PrimaryWeapon       = "primary_weapon"
    SecondaryWeapon     = "secondary_weapon"
    TurretWeapon        = "turret_weapon"
    Ship                = "ship"
    System              = "system"
    Criminal            = "criminal"
    DiscordMessage      = "discord_message"
