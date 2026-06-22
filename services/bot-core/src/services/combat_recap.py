"""combat_recap.py — New recap presentation layer (v3 redesign).

Receives the raw per-occurrence rows from _extract_key_events (collapse disabled)
and produces two structured sections:
  key_events: list[dict]   — chronological events, one row per event
  recurring:  list[str]    — bullet strings for cyclic patterns (>=3 occurrences)

Design rules (from RECAP_PROTO spec):
  1. Chronological Key Events — VERBATIM detail strings, no analysis, no invented phrasing.
  2. Mandatory: non-cyclic events + first "enters range" per weapon (anchor) + FIRST break
     of each (side, layer) ONLY + first activation of each (side, module).
  3. Gap-fill: when gap > RECAP_GAP_FILL_S, pull in a cyclic event near the midpoint.
     Priority: module re-activation (1) > secondary re-enter (2) > primary re-enter (3).
     Per-series diversity penalty rotates fill variety.  Layer re-breaks are NOT fill candidates.
  4. Recurring: one bullet per pattern with >= RECAP_COLLAPSE_MIN_RUN occurrences.
     Qualifier shown per-occurrence only when it varies; uniform qualifier uses "(all X)" suffix.
"""

from __future__ import annotations

import re

from services.game_constants import GameConstants

# Cyclic event types that can appear in Key Events and/or Recurring.
_LM_TYPES: frozenset[str] = frozenset({"Layer depleted", "Module activated"})


# ---------------------------------------------------------------------------
# Key-extraction helpers (mirror the prototype exactly)
# ---------------------------------------------------------------------------


def _lm_key(row: dict) -> tuple:
    """Stable grouping key for Layer depleted / Module activated rows."""
    et, d = row["event_type"], row["detail"]
    if et == "Layer depleted":
        m = re.match(r"(?P<who>.+?): (?P<layer>shield|armour|hull) depleted", d, re.IGNORECASE)
        return (et, m.group("who"), m.group("layer").lower()) if m else (et, d)
    m = re.match(r"(?P<who>.+?) activated (?P<mod>\w+)", d, re.IGNORECASE)
    return (et, m.group("who"), m.group("mod")) if m else (et, d)


def _lm_qualifier(row: dict) -> str | None:
    """Extract the per-occurrence qualifier from a Layer depleted / Module activated detail."""
    et, d = row["event_type"], row["detail"]
    if et == "Layer depleted":
        m = re.search(r"\(by (.+?)\)", d)
        return m.group(1) if m else None
    m = re.search(r"\(at (\d+)% HP\)", d)
    return f"≤{m.group(1)}%" if m else None


def _lm_label(key: tuple) -> str:
    """Human-readable label for a lm_key, used in Recurring bullets."""
    if key[0] == "Layer depleted":
        return f"{key[1]}: {key[2]} depleted"
    return f"{key[1]} activated {key[2]}"


def _wkey(detail: str) -> tuple | None:
    """Parse a 'Weapon in range' detail into (who, weapon, kind).

    Returns None if the detail does not match the expected pattern.
    kind is "enters" (first acquisition) or "re-enters".
    """
    m = re.match(r"(?P<who>.+?)'s (?P<wpn>.+?) (?P<kind>re-enters|enters) range", detail)
    return (m.group("who"), m.group("wpn"), m.group("kind")) if m else None


# ---------------------------------------------------------------------------
# Gap-fill
# ---------------------------------------------------------------------------


def _gapfill(mandatory: list[dict], cands: list[tuple[int, str, dict]], gap_fill_s: float) -> list[dict]:
    """Insert cyclic fill events into gaps > gap_fill_s between mandatory Key Events.

    cands: list of (base_priority, series_sig, row).
    Strategy: fill largest gap first; within it pick lowest (base_priority + fill_count)
    nearest the gap midpoint.  Per-series diversity penalty prevents hammering the same series.
    """
    emitted = sorted(mandatory, key=lambda r: (r["time_s"], r["_idx"]))
    used: set[int] = set()
    fill_count: dict[str, int] = {}
    while True:
        best: tuple | None = None
        for i in range(len(emitted) - 1):
            a, b = emitted[i], emitted[i + 1]
            gap = b["time_s"] - a["time_s"]
            if gap <= gap_fill_s:
                continue
            win = [c for c in cands if id(c[2]) not in used and a["time_s"] < c[2]["time_s"] < b["time_s"]]
            if win and (best is None or gap > best[0]):
                best = (gap, i, win)
        if best is None:
            break
        _, i, win = best
        a, b = emitted[i], emitted[i + 1]
        mid = (a["time_s"] + b["time_s"]) / 2
        win.sort(key=lambda c: (c[0] + fill_count.get(c[1], 0), abs(c[2]["time_s"] - mid), c[2]["_idx"]))
        _pri, sig, row = win[0]
        used.add(id(row))
        fill_count[sig] = fill_count.get(sig, 0) + 1
        emitted.append(row)
        emitted.sort(key=lambda r: (r["time_s"], r["_idx"]))
    return emitted


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_recap_sections(
    rows: list[dict],
    combatants_map: dict,
    tick_ms: int,
    wslot: dict[str, str] | None = None,
) -> dict[str, list]:
    """Build the two recap sections from raw _extract_key_events rows.

    Args:
        rows:           Raw per-occurrence rows from _extract_key_events (collapse disabled).
                        Each row must have _idx set to its position index before calling
                        (this function stamps them if absent).
        combatants_map: summary["combatants"] dict.
        tick_ms:        Tick duration in milliseconds.
        wslot:          Optional mapping of weapon_name -> slot ("primary"/"secondary").
                        If None, all re-enters are treated as primary priority.

    Returns:
        {"key_events": list[dict], "recurring": list[str]}
    """
    if wslot is None:
        wslot = {}

    # Stamp _idx if not present (callers that haven't done it yet).
    for i, r in enumerate(rows):
        if "_idx" not in r:
            r["_idx"] = i

    collapse_min: int = GameConstants.RECAP_COLLAPSE_MIN_RUN
    gap_fill_s: float = GameConstants.RECAP_GAP_FILL_S

    # ---- Group Layer depleted / Module activated by stable key ----
    lm_groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["event_type"] in _LM_TYPES:
            lm_groups.setdefault(_lm_key(r), []).append(r)

    lm_big: dict[tuple, list[dict]] = {k: v for k, v in lm_groups.items() if len(v) >= collapse_min}

    # ---- Group Weapon in range events by (who, weapon) ----
    wgroups: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["event_type"] == "Weapon in range":
            k = _wkey(r["detail"])
            if k is None:
                continue
            who, wpn, kind = k
            g = wgroups.setdefault((who, wpn), {"enter": None, "re": []})
            if kind == "re-enters":
                g["re"].append(r)
            else:
                g["enter"] = r

    # ---- Classify rows into mandatory vs fill candidates ----
    mandatory: list[dict] = []
    cands: list[tuple[int, str, dict]] = []

    # Track which (side, layer) has been seen in Key Events already.
    # Only the FIRST break is mandatory; subsequent re-breaks go into Recurring only.
    first_layer_break: set[tuple] = set()

    for r in rows:
        et = r["event_type"]
        if et == "Weapon in range":
            continue  # handled via wgroups below
        if et == "Layer depleted":
            lk = _lm_key(r)
            grp = lm_groups.get(lk, [r])
            if r is grp[0]:
                # First occurrence of this (who, layer): mandatory Key Event
                first_layer_break.add(lk)
                mandatory.append(r)
            # Re-breaks: NOT fill candidates (spec: "layer re-breaks are NOT fill candidates")
        elif et == "Module activated":
            lk = _lm_key(r)
            grp = lm_groups.get(lk, [r])
            if r is grp[0]:
                mandatory.append(r)  # first activation = anchor
            else:
                # Re-activations = preferred filler (priority 1)
                cands.append((1, lk, r))
        else:
            mandatory.append(r)  # non-cyclic: Engagement, milestones, nuke, ammo, outcome

    # Add weapon range events
    for (who, wpn), g in wgroups.items():
        if g["enter"] is not None:
            mandatory.append(g["enter"])
        slot = wslot.get(wpn, "primary")
        pri = 2 if slot == "secondary" else 3
        for r in g["re"]:
            cands.append((pri, ("re", who, wpn), r))

    # ---- Gap-fill ----
    key_events = _gapfill(mandatory, cands, gap_fill_s)

    # ---- Build Recurring section ----
    entries: list[tuple[int, int, str, object, list[dict]]] = []

    for k, occ in lm_big.items():
        entries.append((occ[0]["tick"], occ[0]["_idx"], "lm", k, occ))

    for (who, wpn), g in wgroups.items():
        if len(g["re"]) >= collapse_min:
            entries.append((g["re"][0]["tick"], g["re"][0]["_idx"], "wpn", (who, wpn), g["re"]))

    entries.sort(key=lambda e: (e[0], e[1]))

    recurring: list[str] = []
    for _t, _i, kind, key, occ in entries:
        if kind == "lm":
            quals = [_lm_qualifier(r) for r in occ]
            distinct = {q for q in quals if q is not None}
            parts = [
                f"{r['time_s']:.1f}s" + (f" ({q})" if q and len(distinct) > 1 else "")
                for r, q in zip(occ, quals, strict=True)
            ]
            suffix = f"  (all {next(iter(distinct))})" if len(distinct) == 1 else ""
            recurring.append(f"• {_lm_label(key)} ×{len(occ)} -> " + ", ".join(parts) + suffix)
        else:
            who, wpn = key
            parts = [f"{r['time_s']:.1f}s" for r in occ]
            recurring.append(f"• {who}'s {wpn} re-enters range ×{len(occ)} -> " + ", ".join(parts))

    # Strip internal _idx field before returning — it is a sorting aid only and
    # must not appear in the persisted blob or in callers' output dicts.
    clean_key_events = [{k: v for k, v in r.items() if k != "_idx"} for r in key_events]
    return {"key_events": clean_key_events, "recurring": recurring}


def extract_wslot(timeline: list[dict]) -> dict[str, str]:
    """Extract weapon -> slot mapping from a combat timeline.

    Used by build_recap_sections to assign fill priorities to weapon re-enters.
    Secondaries get lower fill priority number (pulled before primaries).
    """
    wslot: dict[str, str] = {}
    for ev in timeline:
        if ev.get("type") == "weapon_fire":
            d = ev.get("data", {}) or {}
            wpn = d.get("weapon")
            slot = d.get("slot")
            if wpn and slot:
                wslot.setdefault(wpn, slot)
    return wslot
