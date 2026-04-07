"""
Bounty announcement message payload builder.

This module contains the business logic for constructing and
parsing bounty announcement embed payloads for Discord.
"""

import json
from typing import Any

from shared import bblogger

from message_builders.base import MessagePayloadBuilder

logger = bblogger.get_logger("bounty-announcement-builder")

# ---------------------------------------------------------------------------
# Module-level faction color lookup (case-insensitive keys — store lowercase)
# ---------------------------------------------------------------------------
FACTION_COLORS: dict[str, int] = {
    "terran": 15844367,  # #F1C40F
    "vossk": 1752220,  # #1ABC9C
    "midorian": 10038562,  # #992D22
    "nivelian": 2123412,  # #206694
}

_DEFAULT_COLOR: int = 10181046  # #9B59B6


class BountyAnnouncementBuilder(MessagePayloadBuilder):
    """Builder for bounty announcement messages."""

    def get_message_type(self) -> str:
        logger.debug("get_message_type called")
        return "bounty_announcement"

    def validate_input(self, data: dict[str, Any]) -> bool:
        logger.debug(f"validate_input called with data keys={list(data.keys()) if data else []}")
        try:
            if not isinstance(data.get("criminal_name"), str) or not data["criminal_name"]:
                return False
            if not isinstance(data.get("criminal_faction"), str):
                return False
            if not isinstance(data.get("division"), str):
                return False
            if not isinstance(data.get("tech_level"), int) or isinstance(data.get("tech_level"), bool):
                return False
            if not isinstance(data.get("reward"), int) or isinstance(data.get("reward"), bool):
                return False
            route = data.get("route")
            if not isinstance(route, list) or len(route) == 0:
                return False
            if not isinstance(data.get("end_time_unix"), int) or isinstance(data.get("end_time_unix"), bool):
                return False
        except (KeyError, TypeError):
            return False
        logger.debug("validate_input result=True")
        return True

    def build_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        logger.debug(f"build_payload called with data keys={list(data.keys()) if data else []}")
        if not self.validate_input(data):
            logger.error(f"Invalid input data for bounty announcement: {data}")
            raise ValueError("Invalid input data for bounty announcement")

        criminal_name: str = data["criminal_name"]
        criminal_faction: str = data["criminal_faction"]
        tech_level: int = data["tech_level"]
        reward: int = data["reward"]
        route: list[str] = data["route"]
        end_time_unix: int = data["end_time_unix"]
        criminal_icon: str | None = data.get("criminal_icon")
        criminal_ship: dict | None = data.get("criminal_ship")
        checked: dict | None = data.get("checked")
        bounty_hunter_role_id: int | None = data.get("bounty_hunter_role_id")
        route_map_url: str | None = data.get("route_map_url")

        color = FACTION_COLORS.get(criminal_faction.lower(), _DEFAULT_COLOR)

        content = f"<@&{bounty_hunter_role_id}>" if bounty_hunter_role_id is not None else None

        fields = [
            {"name": "Difficulty", "value": f"T{tech_level}", "inline": True},
            {"name": "Reward Pool", "value": f"{reward:,} credits", "inline": True},
            {"name": "Bounty Ends", "value": f"<t:{end_time_unix}:R>", "inline": True},
            {"name": "Loadout", "value": self._build_loadout_value(criminal_ship, criminal_name), "inline": False},
            {"name": "Route", "value": self._build_route_value(route, checked), "inline": False},
            {"name": "Checked Systems", "value": self._build_checked_systems_value(checked), "inline": False},
        ]

        embed: dict[str, Any] = {
            "title": criminal_name,
            "color": color,
            "thumbnail_url": criminal_icon,
            "fields": fields,
            "image_url": route_map_url,
            "footer_text": criminal_faction,
        }

        payload = {"content": content, "embed": embed}
        logger.info(f"build_payload generated payload for criminal={criminal_name!r}")
        return payload

    def extract_data(self, payload: str) -> dict[str, Any] | None:
        logger.debug("extract_data called")
        try:
            payload_dict = json.loads(payload)
            embed = payload_dict.get("embed")
            if not isinstance(embed, dict):
                logger.debug("extract_data: missing or non-dict 'embed' key")
                return None
            title = embed.get("title")
            footer_text = embed.get("footer_text")
            if title is None or footer_text is None:
                logger.debug("extract_data: missing title or footer_text")
                return None
            result = {"criminal_name": title, "criminal_faction": footer_text}
            logger.info(f"extract_data extracted data: {result}")
            return result
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in extract_data: {e}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(f"Unexpected error in extract_data: {e}")
        logger.debug("extract_data returning None")
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_loadout_value(self, criminal_ship: dict | None, criminal_name: str) -> str:
        """Build the Loadout field value string."""
        if criminal_ship is None:
            return "*No loadout data available*"

        lines: list[str] = []

        ship_name = criminal_ship.get("ship_name", "Unknown")
        ship_emoji = criminal_ship.get("ship_emoji") or ""
        weapons: list[dict] = criminal_ship.get("weapons") or []
        modules: list[dict] = criminal_ship.get("modules") or []
        turrets: list[dict] = criminal_ship.get("turrets") or []

        # Use computed HP fields when available, fall back to legacy armour value
        armor_hp = criminal_ship.get("armor_hp")
        shield_hp = criminal_ship.get("shield_hp")
        total_hp = criminal_ship.get("total_hp")
        if armor_hp is None:
            # Legacy loadout: fall back to ship_armour / armour
            armor_hp = criminal_ship.get("ship_armour") or criminal_ship.get("armour", 0)
            shield_hp = 0
            total_hp = armor_hp

        total_dps = sum(w.get("dps", 0) for w in weapons) + sum(t.get("dps", 0) for t in turrets)
        # Format DPS: round to 1 decimal, drop trailing .0 if whole number
        rounded = round(total_dps, 1)
        dps_str = str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"

        # Build HP display string
        if shield_hp and shield_hp > 0:
            hp_str = f"Armor: {armor_hp} | Shield: {shield_hp} | Total HP: {total_hp}"
        else:
            hp_str = f"HP: {armor_hp}"

        header_parts = []
        if ship_emoji:
            header_parts.append(ship_emoji)
        header_parts.append(f"**{ship_name}**")
        header_parts.append(f"— {hp_str} | DPS: {dps_str}")
        lines.append(" ".join(header_parts))

        for weapon in weapons:
            w_name = weapon.get("name", "")
            w_emoji = weapon.get("emoji") or ""
            if w_emoji:
                lines.append(f"{w_emoji} {w_name}")
            else:
                lines.append(w_name)

        for module in modules:
            m_name = module.get("name", "")
            m_emoji = module.get("emoji") or ""
            if m_emoji:
                lines.append(f"{m_emoji} {m_name}")
            else:
                lines.append(m_name)

        for turret in turrets:
            t_name = turret.get("name", "")
            t_emoji = turret.get("emoji") or ""
            if t_emoji:
                lines.append(f"{t_emoji} {t_name}")
            else:
                lines.append(t_name)

        lines.append(f"Use `/criminal-loadout {criminal_name}` for full details")
        return "\n".join(lines)

    def _build_route_value(self, route: list[str], checked: dict | None) -> str:
        """Build the Route field value string."""
        if not checked:
            return ", ".join(route)

        parts = []
        for system in route:
            status = checked.get(system)
            if status == "checked":
                parts.append(f"~~{system}~~")
            elif status == "found":
                parts.append(f"**{system}**")
            else:
                parts.append(system)
        return ", ".join(parts)

    def _build_checked_systems_value(self, checked: dict | None) -> str:
        """Build the Checked Systems field value string."""
        if not checked:
            return "> *No systems checked yet*"

        checked_systems = [s for s, v in checked.items() if v == "checked"]
        found_systems = [s for s, v in checked.items() if v == "found"]

        if not checked_systems and not found_systems:
            return "> *No systems checked yet*"

        lines: list[str] = []

        if checked_systems:
            strikethrough_parts = " ".join(f"~~{s}~~" for s in checked_systems)
            lines.append(f"> {strikethrough_parts}")

        for system in found_systems:
            lines.append(f"> **{system}**")

        return "\n".join(lines)
