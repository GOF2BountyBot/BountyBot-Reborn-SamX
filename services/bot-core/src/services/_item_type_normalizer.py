"""Item-type normalization helper for BountyBot services.

This module is the single source of truth for translating generic item-type
aliases (``"weapon"``, ``"turret"``) into the concrete types stored in the
database (``"primary_weapon"``, ``"turret_weapon"`` etc.).

Design principles (per INVENTORY_VOCAB_FIX_DESIGN_SPEC.md):

1. **Storage stores concrete types.**  Generic aliases are never persisted;
   normalization happens at the service-layer entry point, never below.
2. **Normalization happens once, at the service layer.**  Repositories remain
   exact-match; they receive only concrete types.
3. **Surface gating via a single constant.**  ``GameConstants.CURRENTLY_ENABLED_TYPES``
   is the lever that controls what appears on the user-facing economy/equip
   surface.  No scattered ``if item_type == "secondary_weapon"`` branches.

Public API
----------
expand_item_type_to_concrete(item_type, *, context)
    Expand a generic or concrete item_type string into a tuple of concrete types.

    ``context="catalog"``  — used for read-only browsing; returns ALL concrete
                            types in the catalog, including secondary_weapon.
    ``context="playable"`` — used for economy/equip write paths; returns only
                            types in CURRENTLY_ENABLED_TYPES.  Raises
                            InvalidItemTypeError for disabled or unknown types.
"""

from __future__ import annotations

from typing import Literal

from services.exceptions import InvalidItemTypeError
from services.game_constants import GameConstants


def expand_item_type_to_concrete(
    item_type: str,
    *,
    context: Literal["catalog", "playable"],
) -> tuple[str, ...]:
    """Expand *item_type* to a tuple of concrete item-type strings.

    Args:
        item_type: A concrete type (``"primary_weapon"``) or a generic alias
            (``"weapon"``, ``"turret"``).  Case-sensitive — always lowercase.
        context: Either ``"catalog"`` (all types, including secondary_weapon)
            or ``"playable"`` (only ``GameConstants.CURRENTLY_ENABLED_TYPES``).

    Returns:
        A non-empty tuple of concrete type strings.  Single-element tuples are
        returned for concrete types; multi-element tuples for generic aliases.

    Raises:
        InvalidItemTypeError: When *item_type* is unrecognised OR when
            ``context="playable"`` and the type is not currently enabled.
    """
    # 1. Is it already a concrete type?
    if item_type in GameConstants.CATALOG_ITEM_TYPES:
        if context == "catalog":
            return (item_type,)
        # playable context — check if currently enabled
        if item_type in GameConstants.CURRENTLY_ENABLED_TYPES:
            return (item_type,)
        raise InvalidItemTypeError(
            f"Item type '{item_type}' is not currently enabled. "
            f"Enabled types: {sorted(GameConstants.CURRENTLY_ENABLED_TYPES)}"
        )

    # 2. Is it a generic alias?
    expansion = GameConstants.GENERIC_TO_CONCRETE_EXPANSION.get(item_type)
    if expansion is not None:
        if context == "catalog":
            return expansion
        # playable context — filter to currently-enabled types
        filtered = tuple(t for t in expansion if t in GameConstants.CURRENTLY_ENABLED_TYPES)
        if not filtered:
            raise InvalidItemTypeError(
                f"Generic alias '{item_type}' expands to no currently-enabled types. "
                f"Enabled types: {sorted(GameConstants.CURRENTLY_ENABLED_TYPES)}"
            )
        return filtered

    # 3. Unknown type
    raise InvalidItemTypeError(
        f"Unknown item type '{item_type}'. "
        f"Valid concrete types: {sorted(GameConstants.CATALOG_ITEM_TYPES)}. "
        f"Valid generic aliases: {sorted(GameConstants.GENERIC_TO_CONCRETE_EXPANSION)}"
    )
