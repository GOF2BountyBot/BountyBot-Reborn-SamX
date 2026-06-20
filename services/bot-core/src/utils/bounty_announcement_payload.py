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

import contextlib
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
# "Recently spotted" window resolution (shared by the check path in
# bounty_service and the route-embed rendering below, so they never drift).
#
# The look-ahead width B is rolled once per bounty at spawn from
# [0, recently_spotted_max_window] and persisted as Bounty.spotted_window.
# A checked system is "recently spotted" iff it sits 1..B stops *before* the
# answer; B=0 means the bounty shows no "recently spotted" hint at all.
# Legacy bounties (spotted_window NULL) fall back to the historical fixed
# window of 2.
# ---------------------------------------------------------------------------
LEGACY_SPOTTED_WINDOW: int = 2


def resolve_spotted_window(bounty: Any) -> int:
    """Return the per-bounty 'recently spotted' look-ahead width B.

    New bounties persist B in ``Bounty.spotted_window``; legacy bounties (NULL)
    fall back to ``LEGACY_SPOTTED_WINDOW`` (the historical fixed 1–2 behavior).
    """
    window = getattr(bounty, "spotted_window", None)
    return LEGACY_SPOTTED_WINDOW if window is None else window


def is_recently_spotted(distance: int, window: int) -> bool:
    """True iff a checked system is 1..``window`` stops before the answer.

    ``distance`` is ``answer_idx - system_idx`` (positive == answer is ahead).
    """
    return 1 <= distance <= window


# Canonical tier color palette (ENH-02 — also used by bountyCog.py)
TIER_COLORS: dict[str, int] = {
    "bronze": 0xCD7F32,  # 13467442
    "silver": 0xC0C0C0,  # 12632256
    "gold": 0xFFD700,  # 16766720
    "platinum": 0xE5E4E2,  # 15066082
}


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
# Capture payout embed — posted to #bounty-hunting after a successful capture
# ---------------------------------------------------------------------------


def build_capture_payout_embed(
    criminal_name: str,
    division: str,
    reward: int,
    winner_name: str = "A bounty hunter",
    total_reward: int | None = None,
    bonus_won: bool = False,
    reward_per_sys: int | None = None,
    route_length: int | None = None,
    combat_result: dict | None = None,
) -> dict[str, Any]:
    """Build a rich "💰 Bounty Captured!" embed for posting to the hunting channel.

    Produces a gold embed with an optional combat summary section (when
    ``combat_result`` is provided) followed by the payout breakdown fields.

    Args:
        criminal_name: Name of the captured criminal.
        division: Division tier (bronze / silver / gold / platinum) — used for color.
        reward: Base reward for the bounty.
        winner_name: Display name (already resolved) of the player who captured.
        total_reward: Final reward including capture bonus + system checks.
            When None, computed from reward alone.
        bonus_won: Kept for backwards compatibility; not used in the new embed layout.
        reward_per_sys: Per-system-check payout amount. When provided alongside
            ``route_length``, a 📍 System Checks field is included.
        route_length: Number of systems in the bounty route. Used with
            ``reward_per_sys`` to compute the max system check payout.
        combat_result: Optional serialized ``FightResults`` dict.  When provided,
            combat summary fields are prepended above the payout fields.  When
            ``None`` the embed is produced without a combat summary (graceful
            degradation).

    Returns:
        A Discord embed payload dict compatible with the gateway message builder.
    """
    capture_bonus = int(reward * 0.25)

    # Compute system checks payout
    max_sys_payout: int | None = None
    if reward_per_sys is not None and route_length is not None:
        max_sys_payout = reward_per_sys * route_length

    # Compute total payout
    if total_reward is not None:
        effective_total = total_reward
    else:
        effective_total = capture_bonus + (max_sys_payout if max_sys_payout is not None else 0)

    fields: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Combat summary section (prepended when combat_result is provided)
    # ------------------------------------------------------------------
    if combat_result is not None:
        s1 = combat_result.get("ship1_stats") or {}
        s2 = combat_result.get("ship2_stats") or {}
        metadata = combat_result.get("metadata") or {}

        def _ttk_str(ttk: float | None) -> str:
            return f"{ttk:.1f}s" if ttk is not None else "∞"

        fields.append(
            {
                "name": "⚔️ Your Ship",
                "value": (
                    f"{s1.get('ship_name', '?')} — "
                    f"HP: {s1.get('varied_hp', 0)} | "
                    f"DPS: {s1.get('varied_dps', 0.0):.1f} | "
                    f"TTK: {_ttk_str(s1.get('ttk'))}"
                ),
                "inline": True,
            }
        )
        fields.append(
            {
                "name": "🤖 Criminal Ship",
                "value": (
                    f"{s2.get('ship_name', '?')} — "
                    f"HP: {s2.get('varied_hp', 0)} | "
                    f"DPS: {s2.get('varied_dps', 0.0):.1f} | "
                    f"TTK: {_ttk_str(s2.get('ttk'))}"
                ),
                "inline": True,
            }
        )

        # Keith T Maxwell armour buff (only when explicitly applied)
        if metadata.get("pvc_armour_buff_applied"):
            buff_factor = metadata.get("pvc_armour_buff_factor", 1.5)
            fields.append(
                {
                    "name": "🛡️ Keith T Maxwell Buff",
                    "value": f"Armour buff active (×{buff_factor:.1f} HP)",
                    "inline": False,
                }
            )

        is_stalemate = combat_result.get("is_stalemate", False)
        fields.append(
            {
                "name": "✅ Result",
                "value": "Stalemate" if is_stalemate else "Combat victory!",
                "inline": False,
            }
        )

    # ------------------------------------------------------------------
    # Payout fields
    # ------------------------------------------------------------------
    fields.extend(
        [
            {"name": "🏆 Division", "value": (division or "Unknown").capitalize(), "inline": True},
            {"name": "⚔️ Claimed by", "value": winner_name, "inline": True},
            {"name": "💵 Base Reward", "value": f"{reward:,} cr", "inline": False},
            {"name": "🎯 Capture Bonus", "value": f"{capture_bonus:,} cr", "inline": True},
        ]
    )

    if max_sys_payout is not None:
        fields.append(
            {
                "name": "📍 System Checks",
                "value": f"{reward_per_sys:,} cr × {route_length} = {max_sys_payout:,} cr",
                "inline": True,
            }
        )

    fields.append({"name": "🏆 Total Payout", "value": f"**{effective_total:,} cr**", "inline": True})

    return {
        "title": "💰 Bounty Captured!",
        "description": f"{criminal_name} has been brought in.",
        "color": 0xFFD700,  # Gold
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# Bounty cap payout embed (Sub-task B)
# ---------------------------------------------------------------------------


def build_bounty_cap_payout_embed(active_bounties: list, capped_tier: str) -> dict[str, Any]:
    """Build a second embed dict summarizing active bounty payouts when a tier cap is hit.

    Groups active bounties by tier and shows count + payout range per tier.
    Only includes tiers that have active bounties.

    Args:
        active_bounties: List of Bounty ORM objects (or dicts with 'division' and 'reward' keys).
        capped_tier: The tier that just hit its cap (used for the embed color).

    Returns:
        A Discord embed payload dict compatible with the gateway message builder.
    """
    # Group bounties by tier
    tiers_data: dict[str, list[int]] = {}
    for bounty in active_bounties:
        if isinstance(bounty, dict):
            division = (bounty.get("division") or "").lower()
            reward = bounty.get("reward", 0)
        else:
            division = (getattr(bounty, "division", None) or "").lower()
            reward = getattr(bounty, "reward", 0)
        if not division:
            continue
        tiers_data.setdefault(division, []).append(reward)

    # Build fields — show in canonical tier order
    tier_order = ["bronze", "silver", "gold", "platinum"]
    fields: list[dict] = []
    for tier in tier_order:
        rewards = tiers_data.get(tier)
        if not rewards:
            continue
        count = len(rewards)
        min_reward = min(rewards)
        max_reward = max(rewards)
        if min_reward == max_reward:
            payout_range = f"{min_reward:,} cr each"
        else:
            payout_range = f"{min_reward:,}–{max_reward:,} cr each"
        fields.append(
            {
                "name": tier.title(),
                "value": f"{count} active · {payout_range}",
                "inline": True,
            }
        )

    color = TIER_COLORS.get(capped_tier.lower(), _DEFAULT_COLOR)

    return {
        "title": "💰 Active Bounty Payouts",
        "color": color,
        "fields": fields,
        "footer": {"text": "Capture a bounty with /check"},
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
      - "recently_spotted" → 1..B stops before the answer (B = per-bounty window)
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

    window = resolve_spotted_window(bounty)

    out: dict[str, str] = {}
    for system_name, checker_id in checked_map.items():
        if checker_id == -1:  # unchecked sentinel
            continue
        if system_name == answer:
            out[system_name] = "found"
            continue
        spotted = False
        if answer_idx is not None and route:
            with contextlib.suppress(ValueError, IndexError):
                sys_idx = route.index(system_name)
                distance = answer_idx - sys_idx
                spotted = is_recently_spotted(distance, window)
        out[system_name] = "recently_spotted" if spotted else "checked"
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
