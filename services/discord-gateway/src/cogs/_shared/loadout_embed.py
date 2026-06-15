"""Shared loadout embed builder for /loadout (playerCog) and /criminal-loadout (bountyCog).

Consumes the bot-core `LoadoutResponse` JSON and produces a fully-populated
`discord.Embed`. The builder implements the 4-tier truncation strategy from
spec §7.2, the 1024-char continuation-field algorithm from §3.3, and the
Ship Stats Total Value heuristic from §7.11.

Design constraints (from LOADOUT_EMBED_DESIGN_SPEC.md):
- Never call `set_thumbnail(url=None)` — always null-guard.
- Spacer field inserted before every section header (visual separator).
- Section headers use the `<N/M>` format (equipped/max).
- Missing emoji → `• ` bullet fallback (NOT unicode per-type fallbacks).
- GammaShieldModule and unknown types render name-only (empty effects list).
- No footer, no timestamp.
"""

from __future__ import annotations

from typing import Any

import discord

# Discord hard limits
MAX_FIELD_VALUE = 1024
MAX_EMBED_TOTAL = 6000
MAX_FIELDS = 25
SAFETY_RESERVE = 200  # leave headroom under the 6000-char ceiling

# Invisible character used for spacer field names and continuation headers.
# LEFT-TO-RIGHT MARK renders as zero width but counts as a non-empty name.
SPACER_NAME = "\u200e"

# Ship-stats suffix heuristic: if the core + total-value string would exceed
# this length, drop Total Value to avoid a crowded wrap in the Discord client.
# Realistic max core string is ~69–98 chars (armour/handling/hp/dps with 4-5 digit values),
# and the Total Value suffix is ~29–31 chars, so typical gameplay totals ~100 chars — safely
# under the 120 threshold.  Only degenerate or future high-stat values trigger the drop.
# Lowered from 200 (unreachable with game-scale stats) to 120 (reachable with 5-digit+ stats).
SHIP_STATS_TOTAL_VALUE_THRESHOLD = 120


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_loadout_embed(
    response: dict,
    viewer_is_owner_or_admin: bool,
    *,
    title_override: str | None = None,
    color_override: int | None = None,
    footer_text: str | None = None,
    image_url: str | None = None,
    prefix_fields: list[dict] | None = None,
    suffix_fields: list[dict] | None = None,
    captured: bool = False,
) -> discord.Embed:
    """Build the unified loadout embed for either player or criminal.

    Args:
        response: The JSON-decoded LoadoutResponse body from bot-core.
        viewer_is_owner_or_admin: True if the Cargo Hold section should be visible.
            - Player path: True when interaction.user.id == target.id OR admin.
            - Criminal path: pass True always (cargo header always shown per spec).
        title_override: When provided, replaces the default "Loadout — {name}" title.
            Used by bounty announcements (A.48 unified rendering) — title is the
            criminal name (or "✅ {name} — CAPTURED").
        color_override: Integer color for the embed border. When provided, overrides
            the default blurple. Bounty announcements pass faction color or green-on-capture.
        footer_text: When provided, sets the embed footer. Bounty announcements pass
            criminal_faction here.
        image_url: When provided, sets the large image (bottom of the embed). Bounty
            announcements pass the route map URL here.
        prefix_fields: Optional list of `{name, value, inline}` field dicts inserted
            BEFORE Active Ship. Continuation-split applies if any value > 1024 chars.
        suffix_fields: Optional list of `{name, value, inline}` field dicts appended
            AFTER all loadout sections. Continuation-split applies likewise.
        captured: When True, the Active Ship, Ship Stats, and all loadout sections
            (weapons, modules, cargo) are omitted. Only title, description, thumbnail,
            prefix fields, and suffix fields are rendered. Used for captured-state
            bounty announcements where the loadout detail is no longer relevant.

    Returns:
        A fully-populated discord.Embed, or a short red error embed if
        response.get("message") is set.
    """
    # Error path — response carries a message (e.g. "No active ship")
    if response.get("message"):
        subject = response.get("subject_name") or "Unknown"
        return build_loadout_error_embed(
            title=title_override or f"Loadout — {subject}",
            description=response["message"],
        )

    subject = response.get("subject_name") or "Unknown"
    title = title_override if title_override is not None else f"Loadout — {subject}"
    description = _format_description(response)

    color = discord.Color(color_override) if color_override is not None else discord.Color.blurple()
    embed = discord.Embed(title=title, color=color)
    if description:
        embed.description = description

    thumbnail_url = response.get("thumbnail_url")
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    if image_url:
        embed.set_image(url=image_url)

    if footer_text:
        embed.set_footer(text=footer_text)

    # Budget tracking: sum of title + description + field name/value lengths.
    budget = MAX_EMBED_TOTAL - SAFETY_RESERVE
    used = len(title) + len(description or "")

    # 0. Prefix fields (e.g. Difficulty / Reward Pool / Bounty Ends).
    if prefix_fields:
        used = _render_extra_fields(embed, prefix_fields, budget, used)

    if not captured:
        # 1. Active Ship (never truncated)
        name, value = _format_active_ship_field(response)
        _add_field_safe(embed, name, value)
        used += len(name) + len(value)

        # 2. Ship Stats (never dropped as a field, but Total Value may be omitted)
        name, value = _format_ship_stats_field(response)
        _add_field_safe(embed, name, value)
        used += len(name) + len(value)

        # Apply 4-tier truncation strategy and render remaining sections.
        sections = _apply_truncation_strategy(
            response,
            budget_remaining=budget - used,
            show_cargo=viewer_is_owner_or_admin,
        )

        for section_header, lines in sections:
            used = _render_section(embed, section_header, lines, budget, used)

    # Trailing suffix fields (e.g. Route, Checked Systems for bounty announcements).
    if suffix_fields:
        _render_extra_fields(embed, suffix_fields, budget, used)

    return embed


def build_loadout_error_embed(title: str, description: str) -> discord.Embed:
    """Red error embed used by cogs for pre-API errors and "no active ship" responses."""
    return discord.Embed(title=title, description=description, color=discord.Color.red())


# ---------------------------------------------------------------------------
# Private helpers — description and field formatters
# ---------------------------------------------------------------------------


def _format_description(response: dict) -> str | None:
    """Build the embed description.

    Player: `<@123>` mention.
    Criminal: `Faction · TL{n}` summary (best-effort; None if both are missing).
    """
    if response.get("subject_kind") == "player":
        return response.get("subject_mention")

    parts: list[str] = []
    faction = response.get("subject_description")
    if faction:
        parts.append(faction)
    tech_level = response.get("tech_level")
    if tech_level is not None:
        parts.append(f"TL{tech_level}")
    return " · ".join(parts) if parts else None


def _format_active_ship_field(response: dict) -> tuple[str, str]:
    """Active Ship field: 'Nickname (Ship Name)' when nickname exists; else 'Ship Name'."""
    ship_name = response.get("ship_name") or "Unknown"
    nickname = response.get("ship_nickname")
    emoji = response.get("ship_emoji") or ""
    display = f"{nickname} ({ship_name})" if nickname and nickname != ship_name else ship_name
    if emoji:
        display = f"{emoji} {display}"
    return ("Active Ship", display)


def _format_ship_stats_field(response: dict) -> tuple[str, str]:
    """Build the 'Ship Stats' single-line field.

    Core (always present, in order): Armour, Handling, HP, DPS.
    Conditional suffix: Total Value — appended only if the resulting string stays
    under SHIP_STATS_TOTAL_VALUE_THRESHOLD (spec §7.11).
    Cargo capacity is intentionally omitted — it's shown in the Cargo Hold
    section header to avoid duplication.
    """
    stats = response.get("ship_stats") or {}
    parts: list[str] = []

    def _add(label: str, value: Any) -> None:
        if value is None:
            return
        parts.append(f"{label}: **{value}**")

    _add("Armour", stats.get("armour"))
    _add("Handling", stats.get("handling"))
    _add("HP", stats.get("hp"))

    dps = stats.get("dps")
    if dps is not None:
        # Trim trailing .0 for cleaner output (e.g., 42.0 -> "42")
        dps_str = f"{dps:g}" if isinstance(dps, (int, float)) else str(dps)
        parts.append(f"DPS: **{dps_str}**")

    core = " | ".join(parts) if parts else "—"

    total_value = stats.get("total_value")
    if total_value is not None:
        suffix = f" | Total Value: **{total_value:,}**"
        if len(core) + len(suffix) <= SHIP_STATS_TOTAL_VALUE_THRESHOLD:
            core = core + suffix

    return ("Ship Stats", core)


def _format_weapon_line(weapon: dict) -> str:
    """Format a weapon/turret line.

    Renders ':emoji: Name | DPS: **X** | Dmg/shot: **N** | Loading: **N ms**',
    appending only the segments whose values are present. Falls back to '• Name'
    when no emoji and name-only when no combat fields exist.
    """
    name = weapon.get("name") or "Unknown"
    emoji = weapon.get("emoji")
    dps = weapon.get("dps")
    prefix = f"{emoji} " if emoji else "• "
    line = f"{prefix}{name}"
    if dps is not None:
        dps_str = f"{dps:g}" if isinstance(dps, (int, float)) else str(dps)
        line = f"{line} | DPS: **{dps_str}**"
    dmg_shot = weapon.get("damage_per_shot")
    if dmg_shot is not None:
        line = f"{line} | Dmg/shot: **{dmg_shot}**"
    loading = weapon.get("loading_speed_ms")
    if loading is not None:
        line = f"{line} | Loading: **{loading} ms**"
    return line


def _format_module_line(module: dict) -> str:
    """Format a module line as ':emoji: Name | Label: **value** | ...' or ':emoji: Name'.

    Effects list from bot-core is rendered as-is (pre-formatted server-side).
    Empty effects → name-only line (GammaShield, unknown types).
    """
    name = module.get("name") or "Unknown"
    emoji = module.get("emoji")
    prefix = f"{emoji} " if emoji else "• "
    line = f"{prefix}{name}"
    effects = module.get("effects") or []
    if effects:
        parts = [f"{e['label']}: **{e['value']}**" for e in effects if "label" in e and "value" in e]
        if parts:
            line = f"{line} | {' | '.join(parts)}"
    return line


def _format_secondary_line(secondary: dict) -> str:
    """Format a secondary-weapon line.

    Renders ':emoji: Name | ×N | Dmg/shot: **N** | Loading: **N ms**', appending
    only the segments whose values are present (rounds, per-shot damage, reload).
    No DPS is shown — secondaries are ammo-limited. Falls back to '• ' when no emoji.
    """
    name = secondary.get("name") or "Unknown"
    emoji = secondary.get("emoji")
    rounds = secondary.get("rounds")
    prefix = f"{emoji} " if emoji else "• "
    line = f"{prefix}{name}"
    if rounds is not None:
        line = f"{line} | ×{rounds}"
    # Secondaries show per-shot damage + reload, but NOT DPS (ammo-limited).
    dmg_shot = secondary.get("damage_per_shot")
    if dmg_shot is not None:
        line = f"{line} | Dmg/shot: **{dmg_shot}**"
    loading = secondary.get("loading_speed_ms")
    if loading is not None:
        line = f"{line} | Loading: **{loading} ms**"
    return line


def _format_cargo_line(item: dict) -> str:
    """Format a cargo line as ':emoji: Item Name (xN)' or '• Item Name (xN)'.

    Quantity handling:
    - None → default to 1 (legacy/missing field fallback).
    - 0 or negative → treated as 0; no (xN) suffix is shown.
      Design choice: zero/negative-quantity items are rendered as name-only
      (same visual as a single item) rather than being silently coerced to 1.
      This avoids the previous `or 1` coercion where `quantity=0` was treated
      as if 1 unit were present (DEF-007 fix).
    """
    name = item.get("item_name") or "Unknown"
    emoji = item.get("emoji")
    quantity = item.get("quantity")
    if quantity is None:
        quantity = 1  # explicit default for missing field
    prefix = f"{emoji} " if emoji else "• "
    if quantity > 1:
        return f"{prefix}{name} (x{quantity})"
    return f"{prefix}{name}"


# ---------------------------------------------------------------------------
# Truncation strategy (spec §7.2)
# ---------------------------------------------------------------------------


def _apply_truncation_strategy(
    response: dict,
    budget_remaining: int,
    show_cargo: bool,
) -> list[tuple[str, list[str]]]:
    """Build (section_header, lines) tuples applying the 4-tier truncation.

    Priority (items dropped first → last):
      1. Cargo items
      2. Utility-tier modules
      3. Combat-tier modules
      4. Weapons

    Always-keep sections (Active Ship, Ship Stats) are handled outside this
    function. The return order is:
      - Primary Weapons
      - Turrets (only when non-empty)
      - Secondaries (only when non-empty)
      - Modules
      - Cargo Hold (only when show_cargo=True)
    """
    sections: list[tuple[str, list[str]]] = []

    stats = response.get("ship_stats") or {}
    weapons = response.get("weapons") or []
    turrets = response.get("turrets") or []
    secondaries = response.get("secondaries") or []
    modules = response.get("modules") or []
    cargo = response.get("cargo") or []

    max_primaries = stats.get("max_primaries") or 0
    max_turrets = stats.get("max_turrets") or 0
    max_secondaries = stats.get("max_secondaries") or 0
    max_modules = stats.get("max_modules") or 0
    cargo_capacity = stats.get("cargo") or 0
    cargo_total_count = response.get("cargo_total_count") or 0
    modules_total_count = response.get("modules_total_count") or len(modules)

    # Pre-format candidate lines (full rendering) so we can measure.
    weapon_lines_full = [_format_weapon_line(w) for w in weapons]
    turret_lines_full = [_format_weapon_line(t) for t in turrets]
    secondary_lines_full = [_format_secondary_line(s) for s in secondaries]
    module_lines_full = [_format_module_line(m) for m in modules]
    cargo_lines_full = [_format_cargo_line(c) for c in cargo]

    # Compute initial total cost (headers + spacers + all lines).
    weapon_header = _build_section_header("Primary Weapons", len(weapons), max_primaries)
    turret_header = _build_section_header("Turrets", len(turrets), max_turrets)
    secondary_header = _build_section_header("Secondaries", len(secondaries), max_secondaries)
    module_header = _build_section_header("Modules", modules_total_count, max_modules)
    cargo_header = _build_section_header("Cargo Hold", cargo_total_count, cargo_capacity)

    # Approximate cost per section: header len + spacer len + lines len + newlines
    def _section_cost(header: str, lines: list[str]) -> int:
        # 2 chars for spacer (name+value), header name, joined value length
        value_len = len("\n".join(lines)) if lines else len("Empty")
        return 2 + len(header) + value_len

    cost_weapons = _section_cost(weapon_header, weapon_lines_full)
    cost_turrets = _section_cost(turret_header, turret_lines_full) if turrets else 0
    cost_secondaries = _section_cost(secondary_header, secondary_lines_full) if secondaries else 0
    cost_modules = _section_cost(module_header, module_lines_full)
    cost_cargo = _section_cost(cargo_header, cargo_lines_full or [])

    sections_cost = cost_weapons + cost_turrets + cost_secondaries + cost_modules
    if show_cargo:
        sections_cost += cost_cargo

    # If everything fits, emit in full.
    if sections_cost <= budget_remaining:
        sections.append((weapon_header, weapon_lines_full))
        if turrets:
            sections.append((turret_header, turret_lines_full))
        if secondaries:
            sections.append((secondary_header, secondary_lines_full))
        sections.append((module_header, module_lines_full))
        if show_cargo:
            # Use "Empty" placeholder when cargo is empty, but always show header
            sections.append((cargo_header, cargo_lines_full if cargo_lines_full else ["Empty"]))
        return sections

    # Budget exceeded — apply tiered truncation.
    # Build mutable candidate lists and drop items in priority order.
    weapons_out = list(weapon_lines_full)
    modules_out = list(module_lines_full)
    cargo_out = list(cargo_lines_full)

    dropped_cargo = 0
    dropped_utility = 0
    dropped_combat = 0
    dropped_weapons = 0

    # Identify utility vs combat modules by their tier tag (preserve original order).
    # We need to drop FROM the original modules list, but keep rendered lines in sync.
    # Strategy: iterate original modules, mark indexes to keep.
    module_tiers = [m.get("combat_tier", "combat") for m in modules]

    def _recompute_cost() -> int:
        w = _section_cost(weapon_header, weapons_out)
        # Turrets and secondaries are not truncated individually — include their fixed cost.
        tr = cost_turrets
        sc = cost_secondaries
        m = _section_cost(module_header, modules_out)
        c = _section_cost(cargo_header, cargo_out or ["Empty"]) if show_cargo else 0
        # Add estimated "… and N more" suffix costs (~20 chars each)
        extra = 0
        if dropped_cargo:
            extra += 20
        if dropped_utility + dropped_combat:
            extra += 20
        if dropped_weapons:
            extra += 20
        return w + tr + sc + m + c + extra

    # Tier 1: drop cargo items oldest-first.
    while show_cargo and cargo_out and _recompute_cost() > budget_remaining:
        cargo_out.pop(0)
        dropped_cargo += 1

    # Tiers 2 & 3: drop utility modules first, then combat modules, rebuilding
    # modules_out from a kept-mask to preserve original ordering.
    if _recompute_cost() > budget_remaining:
        modules_out, dropped_utility, dropped_combat = _drop_modules_by_tier(
            module_lines_full,
            module_tiers,
            current_cost_fn=_recompute_cost,
            budget_remaining=budget_remaining,
            modules_out_ref=modules_out,
        )

    # Tier 4: last-resort weapon drops.
    while weapons_out and _recompute_cost() > budget_remaining:
        weapons_out.pop(0)
        dropped_weapons += 1

    # Append truncation suffix to the last remaining line of each truncated section.
    if dropped_cargo:
        if cargo_out:
            cargo_out[-1] = f"{cargo_out[-1]}\n… and {dropped_cargo} more"
        else:
            cargo_out = [f"… and {dropped_cargo} more"]
    if dropped_utility + dropped_combat:
        total_mod_dropped = dropped_utility + dropped_combat
        if modules_out:
            modules_out[-1] = f"{modules_out[-1]}\n… and {total_mod_dropped} more"
        else:
            modules_out = [f"… and {total_mod_dropped} more"]
    if dropped_weapons:
        if weapons_out:
            weapons_out[-1] = f"{weapons_out[-1]}\n… and {dropped_weapons} more"
        else:
            weapons_out = [f"… and {dropped_weapons} more"]

    sections.append((weapon_header, weapons_out))
    if turrets:
        sections.append((turret_header, turret_lines_full))
    if secondaries:
        sections.append((secondary_header, secondary_lines_full))
    sections.append((module_header, modules_out))
    if show_cargo:
        sections.append((cargo_header, cargo_out if cargo_out else ["Empty"]))
    return sections


def _drop_modules_by_tier(
    lines: list[str],
    tiers: list[str],
    current_cost_fn,
    budget_remaining: int,
    modules_out_ref: list[str],
) -> tuple[list[str], int, int]:
    """Drop utility-tier modules first, then combat-tier, until budget fits.

    Returns (kept_lines, dropped_utility_count, dropped_combat_count).
    """
    kept_mask = [True] * len(lines)
    dropped_utility = 0
    dropped_combat = 0

    def _rebuild():
        return [ln for ln, keep in zip(lines, kept_mask, strict=False) if keep]

    # Drop utility modules oldest-first.
    for i, tier in enumerate(tiers):
        if tier != "utility":
            continue
        kept_mask[i] = False
        dropped_utility += 1
        modules_out_ref[:] = _rebuild()
        if current_cost_fn() <= budget_remaining:
            return _rebuild(), dropped_utility, dropped_combat

    # Still over budget — drop combat modules (last-resort within modules section).
    for i, tier in enumerate(tiers):
        if tier != "combat":
            continue
        if not kept_mask[i]:
            continue
        kept_mask[i] = False
        dropped_combat += 1
        modules_out_ref[:] = _rebuild()
        if current_cost_fn() <= budget_remaining:
            break

    return _rebuild(), dropped_utility, dropped_combat


# ---------------------------------------------------------------------------
# Section rendering (spacer + header + continuation fields)
# ---------------------------------------------------------------------------


def _build_section_header(base: str, equipped: int, maximum: int) -> str:
    """Build a section header like 'Primary Weapons <3/4>'."""
    return f"{base} <{equipped}/{maximum}>"


def _render_section(
    embed: discord.Embed,
    header: str,
    lines: list[str],
    budget: int,
    used: int,
) -> int:
    """Add a spacer + header + value fields to the embed, with 1024-char continuation.

    Returns the new `used` character count. Never exceeds MAX_FIELD_VALUE per
    field. If adding more fields would exceed the overall field-count ceiling,
    appends a truncation suffix to the last field instead of adding more.
    """
    # Spacer field BEFORE the section header (visual separator).
    if len(embed.fields) >= MAX_FIELDS - 1:
        return used
    embed.add_field(name=SPACER_NAME, value=SPACER_NAME, inline=False)
    used += len(SPACER_NAME) * 2

    # Split lines across one-or-more fields, respecting the 1024-char cap.
    first_field = True
    current_buf: list[str] = []
    current_len = 0

    def _flush():
        nonlocal first_field, current_buf, current_len
        if not current_buf:
            return
        name = header if first_field else SPACER_NAME
        value = "\n".join(current_buf)
        # Respect field-count ceiling
        if len(embed.fields) >= MAX_FIELDS:
            return
        embed.add_field(name=name, value=value, inline=False)
        first_field = False
        current_buf = []
        current_len = 0

    used_delta = 0
    for line in lines:
        added = len(line) + (1 if current_buf else 0)  # + newline when not first
        if current_len + added > MAX_FIELD_VALUE:
            _flush()
            current_buf = [line]
            current_len = len(line)
        else:
            current_buf.append(line)
            current_len += added

    _flush()
    # Approximate used-delta: header + all line content + field separators.
    used_delta = len(header) + sum(len(line) + 1 for line in lines)
    return used + used_delta


def _add_field_safe(embed: discord.Embed, name: str, value: str) -> None:
    """Add a field to the embed, truncating the value if it exceeds the per-field cap."""
    if len(embed.fields) >= MAX_FIELDS:
        return
    if len(value) > MAX_FIELD_VALUE:
        value = value[: MAX_FIELD_VALUE - 1] + "…"
    embed.add_field(name=name, value=value, inline=False)


def _render_extra_fields(
    embed: discord.Embed,
    fields: list[dict],
    budget: int,
    used: int,
) -> int:
    """Render a flat list of `{name, value, inline}` fields with 1024-char continuation.

    Each field's value is split across multiple fields if it exceeds the per-field cap,
    using the same SPACER_NAME convention as section continuations to indicate the
    visual continuation. The first chunk keeps the original `name` and `inline`; later
    chunks use SPACER_NAME and are forced to `inline=False` to preserve readability.

    Returns the new running `used` byte count (best-effort approximation).
    """
    for spec in fields:
        if len(embed.fields) >= MAX_FIELDS:
            break
        name = str(spec.get("name") or SPACER_NAME)
        value = str(spec.get("value") or "")
        inline = bool(spec.get("inline", False))

        if len(value) <= MAX_FIELD_VALUE:
            embed.add_field(name=name, value=value, inline=inline)
            used += len(name) + len(value)
            continue

        # Split across multiple fields at line boundaries, with a fallback
        # hard-cut for pathological single-line values > 1024 chars.
        first = True
        chunks = _split_value_at_boundaries(value, MAX_FIELD_VALUE)
        for chunk in chunks:
            if len(embed.fields) >= MAX_FIELDS:
                break
            field_name = name if first else SPACER_NAME
            field_inline = inline if first else False
            embed.add_field(name=field_name, value=chunk, inline=field_inline)
            used += len(field_name) + len(chunk)
            first = False
    return used


def _split_value_at_boundaries(value: str, cap: int) -> list[str]:
    """Split a long string into <=cap chunks at newline boundaries when possible.

    Falls back to a hard-cut split if any single line exceeds the cap.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in value.split("\n"):
        # If a single line is itself bigger than cap, hard-cut it.
        if len(line) > cap:
            # Flush any pending buffer first.
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            # Hard-cut the oversized line.
            i = 0
            while i < len(line):
                chunks.append(line[i : i + cap])
                i += cap
            continue

        added = len(line) + (1 if current else 0)  # +newline when joining
        if current_len + added > cap:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += added
    if current:
        chunks.append("\n".join(current))
    return chunks
