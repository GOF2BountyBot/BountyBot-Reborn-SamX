"""Shared loadout response schema used by both player and bounty loadout endpoints.

Both `/players/{id}/loadout` and `/bounties/{id}/loadout` return a `LoadoutResponse`
discriminated by `subject_kind`. The unified schema is consumed by the shared
embed builder in the discord-gateway service (`utils/loadout_embed.py`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EffectItem(BaseModel):
    """A single pre-formatted effect entry for a module.

    The gateway renders these as: `<label>: **<value>**` joined by ` | `.
    """

    model_config = ConfigDict(from_attributes=True)

    label: str  # e.g. "Armour", "Shield", "Duration", "Cargo Bonus"
    value: str  # e.g. "160", "300", "10s", "+25%"


class LoadoutWeaponItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    emoji: str | None = None
    dps: float | None = None
    value: int | None = None
    rounds: int | None = None  # Secondary weapons only — ammo count (None = not applicable)


class LoadoutModuleItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    emoji: str | None = None
    type: str | None = None  # Module polymorphic identity ("ArmourModule", etc.)
    value: int | None = None
    tech_level: int | None = None
    effects: list[EffectItem] = Field(default_factory=list)  # Pre-formatted per-module effects
    combat_tier: Literal["combat", "utility"] = "combat"  # Drives truncation priority in gateway


class CargoItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_name: str
    # Concrete item_type from PlayerInventory (ship|primary_weapon|secondary_weapon|turret_weapon|module).
    item_type: str
    quantity: int
    emoji: str | None = None


class ShipStats(BaseModel):
    """Base ship stats used for the 'Ship Stats' embed field."""

    model_config = ConfigDict(from_attributes=True)

    armour: int | None = None  # Ship.armour (base)
    cargo: int | None = None  # Effective capacity (ship.cargo × CompressorModule multipliers)
    handling: int | None = None  # Ship.handling
    hp: int | None = None  # Computed armor_hp + shield_hp
    dps: float | None = None  # Sum of weapon + turret DPS
    total_value: int | None = None  # Sum of all item values
    # Slot counts surfaced for potential future use (not rendered in embed today):
    max_primaries: int | None = None
    max_secondaries: int | None = None
    max_turrets: int | None = None
    max_modules: int | None = None


class LoadoutResponse(BaseModel):
    """Unified loadout response consumed by both /loadout and /criminal-loadout cogs."""

    model_config = ConfigDict(from_attributes=True)

    # Discriminator
    subject_kind: Literal["player", "criminal"]

    # Subject identity (used for embed title + description)
    subject_name: str  # Discord display name (player) OR criminal name
    subject_mention: str | None = None  # e.g. "<@123456789>" — player path only
    subject_description: str | None = None  # Free-form descriptor (e.g. faction for criminal)

    # Kind-specific identifiers
    player_id: int | None = None
    user_discord_id: int | None = None
    bounty_id: int | None = None
    tech_level: int | None = None  # criminal path only

    # Ship
    ship_name: str | None = None
    ship_nickname: str | None = None
    ship_icon: str | None = None  # URL for embed thumbnail (player path — Ship.icon)
    ship_emoji: str | None = None  # Custom Discord emoji string for inline ship mention

    # Thumbnail override (criminal path uses Criminal.icon here instead of Ship.icon)
    thumbnail_url: str | None = None

    # Stats
    ship_stats: ShipStats = Field(default_factory=ShipStats)

    # Equipped items
    weapons: list[LoadoutWeaponItem] = Field(default_factory=list)
    secondaries: list[LoadoutWeaponItem] = Field(default_factory=list)  # populated (CI-28)
    turrets: list[LoadoutWeaponItem] = Field(default_factory=list)  # reserved for future
    modules: list[LoadoutModuleItem] = Field(default_factory=list)

    # Cargo
    cargo: list[CargoItem] = Field(default_factory=list)
    cargo_total_count: int = 0  # Sum of CargoItem.quantity — used in 'Cargo Hold <N/M>' header

    # Modules
    modules_total_count: int = 0  # True equipped module count (pre-dedup) — used in 'Modules <N/M>' header

    # Error/info
    message: str | None = None  # "No active ship", etc. — gateway renders error embed
