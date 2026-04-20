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
        captured: bool = data.get("captured", False)

        color = 3066993 if captured else FACTION_COLORS.get(criminal_faction.lower(), _DEFAULT_COLOR)  # Green: 0x2ECC71

        content = f"<@&{bounty_hunter_role_id}>" if bounty_hunter_role_id is not None else None

        ship_field_value, loadout_field_value = self._build_ship_and_loadout_fields(criminal_ship)

        bounty_ends_value = "**Captured**" if captured else f"<t:{end_time_unix}:R>"
        title = f"✅ {criminal_name} — CAPTURED" if captured else criminal_name

        fields = [
            {"name": "Difficulty", "value": f"T{tech_level}", "inline": True},
            {"name": "Reward Pool", "value": f"{reward:,} credits", "inline": True},
            {"name": "Bounty Ends", "value": bounty_ends_value, "inline": True},
            {"name": "Ship", "value": ship_field_value, "inline": False},
            {"name": "Loadout", "value": loadout_field_value, "inline": False},
            {"name": "Route", "value": self._build_route_value(route, checked), "inline": False},
            {"name": "Checked Systems", "value": self._build_checked_systems_value(checked), "inline": False},
        ]

        embed: dict[str, Any] = {
            "title": title,
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

    def _build_ship_and_loadout_fields(self, criminal_ship: dict | None) -> tuple[str, str]:
        """Build the Ship and Loadout field values as separate strings.

        Returns:
            A tuple of (ship_field_value, loadout_field_value).
        """
        if criminal_ship is None:
            return "*No ship data available*", "*No loadout data available*"

        ship_name = criminal_ship.get("ship_name", "Unknown")
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

        # Build Ship field: "**ShipName** — Armor: X | Shield: Y | Total HP: Z | DPS: N"
        if shield_hp and shield_hp > 0:
            stats_str = f"Armor: {armor_hp} | Shield: {shield_hp} | Total HP: {total_hp} | DPS: {dps_str}"
        else:
            stats_str = f"Armor: {armor_hp} | Total HP: {total_hp} | DPS: {dps_str}"
        ship_field_value = f"**{ship_name}** — {stats_str}"

        # Build Loadout field: bold category headers, item names only (no emoji), blank lines between sections
        loadout_sections: list[str] = []

        if weapons:
            section_lines = ["**Primary Weapons**"]
            for weapon in weapons:
                w_name = weapon.get("name", "")
                w_emoji = weapon.get("emoji") or ""
                section_lines.append(f"{w_name} {w_emoji}".rstrip() if w_emoji else w_name)
            loadout_sections.append("\n".join(section_lines))

        if turrets:
            section_lines = ["**Turrets**"]
            for turret in turrets:
                t_name = turret.get("name", "")
                t_emoji = turret.get("emoji") or ""
                section_lines.append(f"{t_name} {t_emoji}".rstrip() if t_emoji else t_name)
            loadout_sections.append("\n".join(section_lines))

        if modules:
            section_lines = ["**Modules**"]
            for module in modules:
                m_name = module.get("name", "")
                m_emoji = module.get("emoji") or ""
                section_lines.append(f"{m_name} {m_emoji}".rstrip() if m_emoji else m_name)
            loadout_sections.append("\n".join(section_lines))

        loadout_field_value = "*No equipment*" if not loadout_sections else "\n\n".join(loadout_sections)

        return ship_field_value, loadout_field_value

    def _build_route_value(self, route: list[str], checked: dict | None) -> str:
        """Build the Route field value string.

        Status values in checked:
          "checked"          → ~~system~~ (strikethrough only)
          "recently_spotted" → **~~system~~** (bold AND strikethrough)
          "found"            → **system** (bold only)
          anything else      → system (plain)
        """
        if not checked:
            return ", ".join(route)

        parts = []
        for system in route:
            status = checked.get(system)
            if status == "recently_spotted":
                parts.append(f"**~~{system}~~**")
            elif status == "checked":
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
        recently_spotted_systems = [s for s, v in checked.items() if v == "recently_spotted"]
        found_systems = [s for s, v in checked.items() if v == "found"]

        if not checked_systems and not recently_spotted_systems and not found_systems:
            return "> *No systems checked yet*"

        lines: list[str] = []

        if checked_systems:
            strikethrough_parts = " ".join(f"~~{s}~~" for s in checked_systems)
            lines.append(f"> {strikethrough_parts}")

        if recently_spotted_systems:
            recently_parts = " ".join(f"**~~{s}~~**" for s in recently_spotted_systems)
            lines.append(f"> {recently_parts}")

        for system in found_systems:
            lines.append(f"> **{system}**")

        return "\n".join(lines)
