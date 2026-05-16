"""Shared payload builder for unified bounty announcements (A.48).

After the A.48 unified-loadout-render refactor, bounty announcements no longer
emit a pre-rendered Discord embed dict. Instead, they post a structured
payload to the gateway's `/announcements/bounty/...` endpoints, which use the
shared `build_loadout_embed` to produce the final embed (with 1024-char
continuation-field handling).

This module owns the bounty-side payload assembly: title/color/footer
overrides, prefix and suffix field rendering, captured-state handling, and
LoadoutResponse fetching via `LoadoutResponseService.build_bounty_loadout`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Faction color lookup (case-insensitive keys — store lowercase).
# Mirrors the constants previously held in
# message_builders/builders/bounty_announcement.py (now removed).
# ---------------------------------------------------------------------------
FACTION_COLORS: dict[str, int] = {
    "terran": 15844367,  # #F1C40F
    "vossk": 1752220,  # #1ABC9C
    "midorian": 10038562,  # #992D22
    "nivelian": 2123412,  # #206694
}

_DEFAULT_COLOR: int = 10181046  # #9B59B6
_CAPTURED_COLOR: int = 3066993  # #2ECC71 (green)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_bounty_announcement_request(
    db: AsyncSession,
    bounty,
    *,
    criminal_icon: str | None = None,
    route_map_url: str | None = None,
    bounty_hunter_role_id: int | None = None,
    captured: bool = False,
) -> dict[str, Any]:
    """Build the request body for the gateway's bounty-announcement endpoint.

    Returns a dict matching `BountyAnnouncementRequest`:
      {
        "text_content": "<@&role_id>" | None,
        "loadout_response": LoadoutResponse-shaped dict,
        "metadata": {
          "title": str,
          "color": int,
          "footer_text": str | None,
          "image_url": str | None,
          "prefix_fields": [...],
          "suffix_fields": [...],
        }
      }
    """
    # Deferred import — keeps module importable in test envs without ORM bootstrap.
    from services.loadout_response_service import LoadoutResponseService

    loadout_service = LoadoutResponseService()
    loadout_response = await loadout_service.build_bounty_loadout(db, bounty.id)

    if loadout_response is None:
        # Bounty was deleted between spawn and announcement, or test-mock
        # didn't wire it up. Fall back to an empty-shape response with the
        # 'message' field so the gateway error-renders gracefully.
        loadout_dict: dict[str, Any] = {
            "subject_kind": "criminal",
            "subject_name": bounty.criminal_name or "Unknown Criminal",
            "subject_description": bounty.criminal_faction,
            "bounty_id": bounty.id,
            "tech_level": bounty.tech_level,
            "message": "Criminal ship data unavailable",
        }
    else:
        # Override thumbnail to use the criminal icon (already handled by
        # LoadoutResponseService, but spawn callers may pass an explicit
        # criminal_icon they fetched themselves; honour the explicit one).
        loadout_dict = loadout_response.model_dump()
        if criminal_icon and not loadout_dict.get("thumbnail_url"):
            loadout_dict["thumbnail_url"] = criminal_icon

    # Title / color follow the captured/normal state rule.
    title = _build_title(bounty.criminal_name or "Unknown", captured)
    color = _build_color(bounty.criminal_faction or "", captured)

    # When captured, pass an empty string as image_url so the gateway edit
    # handler clears the route map instead of preserving it.
    effective_image_url = "" if captured else route_map_url

    prefix_fields = _build_prefix_fields(bounty, captured)
    prefix_fields.extend(_build_suffix_fields(bounty))  # Route + Checked now before Active Ship

    metadata = {
        "title": title,
        "color": color,
        "footer_text": bounty.criminal_faction or None,
        "image_url": effective_image_url,
        "captured": captured,
        "prefix_fields": prefix_fields,
        "suffix_fields": [],  # moved to prefix
        "reward": bounty.reward,
        "reward_per_sys": bounty.reward_per_sys,
        "route_length": len(list(bounty.route or [])),
    }

    text_content = f"<@&{bounty_hunter_role_id}>" if bounty_hunter_role_id is not None else None

    return {
        "text_content": text_content,
        "loadout_response": loadout_dict,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_title(criminal_name: str, captured: bool) -> str:
    """Return the embed title string.

    Captured: ``"✅ {name} — CAPTURED"``. Normal: just the criminal name.
    """
    if captured:
        return f"✅ {criminal_name} — CAPTURED"
    return criminal_name


def _build_color(criminal_faction: str, captured: bool) -> int:
    """Return the embed color int.

    Captured beats faction color (green wins). Otherwise, faction color is
    looked up case-insensitively; unknown / empty factions fall back to
    `_DEFAULT_COLOR`.
    """
    if captured:
        return _CAPTURED_COLOR
    return FACTION_COLORS.get(criminal_faction.lower(), _DEFAULT_COLOR)


def _build_prefix_fields(bounty, captured: bool) -> list[dict]:
    """Build the inline header fields rendered above the loadout sections."""
    if captured:
        bounty_ends_value = "**Captured**"
    elif bounty.end_time is not None:
        bounty_ends_value = f"<t:{int(bounty.end_time.timestamp())}:R>"
    else:
        bounty_ends_value = "—"

    return [
        {"name": "Difficulty", "value": f"T{bounty.tech_level}", "inline": True},
        {"name": "Reward Pool", "value": f"{bounty.reward:,} credits", "inline": True},
        {"name": "Bounty Ends", "value": bounty_ends_value, "inline": True},
    ]


def _build_suffix_fields(bounty) -> list[dict]:
    """Build the trailing fields (Route, Checked Systems) below the loadout sections."""
    route: list[str] = list(bounty.route or [])
    checked = _project_checked(bounty)

    return [
        {"name": "Route", "value": _build_route_value(route, checked), "inline": False},
        {"name": "Checked Systems", "value": _build_checked_systems_value(checked), "inline": False},
    ]


def _project_checked(bounty) -> dict | None:
    """Translate Bounty.checked (player-id map) into the gateway-display map.

    Status values produced:
      - "found"            → answer system has been hit
      - "recently_spotted" → 1-2 stops before the answer
      - "checked"          → already-checked but not answer
    """
    checked_map = getattr(bounty, "checked", None)
    if not checked_map:
        return None

    answer = getattr(bounty, "answer", None)
    route: list[str] = list(getattr(bounty, "route", None) or [])

    answer_idx: int | None = None
    if answer and route:
        try:
            answer_idx = route.index(answer)
        except (ValueError, IndexError):
            answer_idx = None

    out: dict[str, str] = {}
    for system_name, checker_id in checked_map.items():
        if checker_id == -1:  # unchecked sentinel
            continue
        if system_name == answer:
            out[system_name] = "found"
            continue
        is_recently_spotted = False
        if answer_idx is not None and route:
            try:
                sys_idx = route.index(system_name)
                distance = answer_idx - sys_idx
                if 1 <= distance <= 2:
                    is_recently_spotted = True
            except (ValueError, IndexError):
                pass
        out[system_name] = "recently_spotted" if is_recently_spotted else "checked"
    return out or None


def _build_route_value(route: list[str], checked: dict | None) -> str:
    """Build the Route field value with markdown highlighting per status.

    Status mapping:
      "checked"          → ~~system~~
      "recently_spotted" → **~~system~~**
      "found"            → **system**
      anything else      → plain
    """
    if not route:
        return "—"
    if not checked:
        return ", ".join(route)

    parts: list[str] = []
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


def _build_checked_systems_value(checked: dict | None) -> str:
    """Build the Checked Systems field with blockquote prefix per status group."""
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
